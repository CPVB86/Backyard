# Species-enrichment (schema 4)

De offline importer verrijkt uitsluitend bestaande `species_catalog`-records op de
exacte identiteit `domain + scientific_name`. Geen nieuwe taxa, verwijderingen,
bron-/identitywijzigingen, observations, audio, Generator-acties of externe requests.

## Schema en API

Schema 3 bevatte `wikipedia_nl_url`, `summary_nl`, `fact_nl`, `encyclopedia_source`.
Migratie 3 -> 4 voegt uitsluitend vijf nullable kolommen toe:
`wikipedia_title_nl`, `wikipedia_match_status`, `wikipedia_match_note`,
`wikipedia_en_url`, `wikipedia_title_en`. De bestaande migrator maakt een backup en
voert DDL transactioneel uit. Bestaande enrichment blijft intact.

`GET /api/species/{domain}/{scientific_name}?locale=nl` behoudt alle bestaande
encyclopedia-keys en voegt deze velden toe. Ontbrekende waarden zijn null.
`encyclopedia.source` blijft de API-naam voor `encyclopedia_source`.
De Engelse fallback verandert nooit de NL-link of NL-matchstatus. GET leest lokaal.

## Veiligheidscontract

Het hele CSV wordt eerst geparseerd en gevalideerd: 1647 records, 1610 bird, 37 bat,
1647 unieke identities, alle benodigde kolommen en alleen exact/probable/ambiguous/
not_found als matchstatus. Bronkolommen worden genegeerd als updatebron. Niet-lege
teksten blijven behouden; lege optionele velden worden NULL. Wikipedia-links moeten
HTTPS naar de juiste nl/en.wikipedia.org/wiki/ verwijzen.

Alle 1647 identities moeten exact eenmaal in de bestaande database voorkomen.
Missende, dubbele en onverwachte database-identities worden met naam gerapporteerd
en blokkeren iedere update. De importer maakt/migreert geen database.

Standaard dry-run gebruikt een technisch read-only SQLite-verbinding. Optioneel
`--dry-run`; uitsluitend `--apply` schrijft. Apply herhaalt de preflight en houdt
vanaf de database-matching een BEGIN IMMEDIATE-lock vast tegen concurrerende
writers. Alle updates vormen één transactie. Iedere fout veroorzaakt rollback.
Alleen gewijzigde enrichmentvelden en hun updated_at worden geschreven;
imported_at en alle bronvelden blijven intact. Een identieke herimport geeft
updated=0, unchanged=1647 en laat timestamps ongemoeid.

Het JSON-rapport toont aantallen, exacte foutidentities, diff en gevulde velden,
gevolgd door `SAFE TO APPLY: yes/no`. Fouten leveren een non-zero exitcode.
`--apply` is de expliciete schrijfopdracht, zonder extra interactieve bevestiging.

## CSV naar de Pi

De dataset wordt niet meegecommit. De map `import/` is Git-ignored. Vervang PI_HOST
door jouw bestaande hostnaam/IP. Vanaf Windows PowerShell:

```powershell
ssh cpvb86@PI_HOST "mkdir -p /home/cpvb86/Backyard/import"
scp "C:\Users\Chant\Downloads\backyard_species_catalog_birds_bats_final.csv" cpvb86@PI_HOST:/home/cpvb86/Backyard/import/
```

## Deploy en dry-run

Voer uit als cpvb86, met leestoegang tot het bestaande productie-env-bestand.
Plan onderhoud wanneer geen Generator-aanvraag draait. API/producers worden voor
de schema-migratie gestopt; detectorcode, policy en configuratie veranderen niet.

```sh
cd /home/cpvb86/Backyard
sudo systemctl stop backyard-detector.service backyard-api.service
git pull --ff-only origin main
source .venv/bin/activate
python -m app.core.migrate --environment-file /etc/backyard/backyard.env
python -m app.modules.species.import_enrichment \
  --environment-file /etc/backyard/backyard.env \
  --file /home/cpvb86/Backyard/import/backyard_species_catalog_birds_bats_final.csv
```

Controleer `SAFE TO APPLY: yes`, database_matches=1647 en nul mismatchmeldingen.
Bij `no`: niet toepassen; onderzoek de gerapporteerde identities zonder automatisch
hernoemen. Na geslaagde schema-migratie mogen de services ook zonder enrichment
weer starten; optionele velden blijven dan null.

## Bewust toepassen

```sh
python -m app.modules.species.import_enrichment \
  --environment-file /etc/backyard/backyard.env \
  --file /home/cpvb86/Backyard/import/backyard_species_catalog_birds_bats_final.csv \
  --apply
```

Daarna opnieuw een write-free controle; verwacht would_update=0, unchanged=1647:

```sh
python -m app.modules.species.import_enrichment \
  --environment-file /etc/backyard/backyard.env \
  --file /home/cpvb86/Backyard/import/backyard_species_catalog_birds_bats_final.csv
pytest -q
sudo systemctl restart backyard-api.service
sudo systemctl start backyard-detector.service
sudo systemctl status backyard-api.service --no-pager --full
```

Geen nieuwe dependencies of service-unitwijzigingen. Bewaar de migration-backup.
`--environment-file` wordt ook door de migrator ondersteund zodat een afwijkend
productie-DB-pad correct wordt gebruikt. Geen database of CSV hoort in Git.

## Verwachte inhoud van dit bestand

Bestandsvalidatie en volledige proefimport op een tijdelijke lokale catalogus zijn
uitgevoerd. De match met de echte Pi-database moet daar nog met dry-run gebeuren.

| Controle | Verwacht |
| --- | ---: |
| Species / unieke identities | 1647 |
| Birds / bats | 1610 / 37 |
| Summaries / facts | 1647 / 279 |
| Encyclopedia source gevuld | 1647 |
| NL-links / EN-links | 1303 / 21 |
| NL exact / probable | 1008 / 294 |
| NL ambiguous / not_found | 3 / 342 |

## SQL-verificatie

Dit commando opent de productie-database read-only. Het toont ook Houtduif en een
werkelijk aanwezige bat: Myotis mystacinus.

```sh
python - <<'PY'
from contextlib import closing
import json, os, sqlite3
from app.core.config import Settings
from operations.environment import read_environment
os.environ.update(read_environment('/etc/backyard/backyard.env'))
uri = Settings().resolved_database_path.resolve().as_uri() + '?mode=ro'
with closing(sqlite3.connect(uri, uri=True)) as db:
    db.row_factory = sqlite3.Row
    queries = {
        'schema': 'PRAGMA user_version',
        'domains': 'SELECT domain, COUNT(*) AS records FROM species_catalog GROUP BY domain',
        'totals': '''SELECT COUNT(*) AS total,
          SUM(NULLIF(TRIM(summary_nl),'') IS NOT NULL) AS summaries,
          SUM(NULLIF(TRIM(fact_nl),'') IS NOT NULL) AS facts,
          SUM(NULLIF(TRIM(wikipedia_nl_url),'') IS NOT NULL) AS nl_links,
          SUM(NULLIF(TRIM(wikipedia_en_url),'') IS NOT NULL) AS en_links,
          SUM(NULLIF(TRIM(encyclopedia_source),'') IS NOT NULL) AS sources,
          SUM(NULLIF(TRIM(domain),'') IS NULL OR NULLIF(TRIM(scientific_name),'') IS NULL) AS missing_identity
          FROM species_catalog''',
        'match_status': 'SELECT wikipedia_match_status, COUNT(*) AS records FROM species_catalog GROUP BY wikipedia_match_status',
        'duplicates': 'SELECT domain,scientific_name,COUNT(*) AS records FROM species_catalog GROUP BY domain,scientific_name HAVING COUNT(*) != 1',
        'examples': "SELECT domain,scientific_name,common_name_nl,wikipedia_nl_url,wikipedia_title_nl,wikipedia_match_status,wikipedia_match_note,wikipedia_en_url,wikipedia_title_en,summary_nl,fact_nl,encyclopedia_source FROM species_catalog WHERE (domain='bird' AND scientific_name='Columba palumbus') OR (domain='bat' AND scientific_name='Myotis mystacinus')",
    }
    for label, sql in queries.items():
        print(label, json.dumps([dict(row) for row in db.execute(sql)], ensure_ascii=False))
PY
```

Verwacht schema=4, missing_identity=0, duplicates=[]; overige aantallen als hierboven.

## Authenticated API-verificatie

Na herstart, zonder token in de shell-commandline of output te tonen:

```sh
python - <<'PY'
import json
from urllib.parse import quote
from urllib.request import Request, urlopen
from operations.environment import read_environment
config = read_environment('/etc/backyard/backyard.env')
base = config.get('BACKYARD_MONITOR_API_URL', 'http://127.0.0.1:8010').rstrip('/')
for domain, name in [('bird','Columba palumbus'), ('bat','Myotis mystacinus')]:
    url = base + '/api/species/' + domain + '/' + quote(name, safe='') + '?locale=nl'
    request = Request(url, headers={'Authorization':'Bearer ' + config['BACKYARD_API_TOKEN']})
    with urlopen(request, timeout=10) as response:
        result = json.load(response)
    print(json.dumps({'identity':result['identity'], 'encyclopedia':result['encyclopedia']}, ensure_ascii=False, indent=2))
PY
```

Dit benadert alleen je eigen Backyard-API. Species GET voert geen externe
Wikipedia-/OpenAI-verzoeken en geen enrichment of generatie uit.
