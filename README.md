# Backyard

Lokale backend voor Ã©Ã©n tuin. Backyard bewaart detecties, observations en audio; de
continue BirdNET-monitor is een HTTP-producer. AvianVisitors en WordPress worden API-consumers.
De losse diagnostische BirdNET-tools staan in [detector/](docs/DETECTOR_TEST.md).
Dit zijn hardwaretests met een eigen venv, geen productiedetector of ingestkoppeling.
De aparte continue monitor staat in [DETECTOR_MONITOR.md](docs/DETECTOR_MONITOR.md),
inclusief handmatige Pi-validatie, backpressure en audio-ingest.
De [observation-policyfase](docs/OBSERVATION_POLICY.md) voegt bird/bat observations,
overlapaggregatie, permanente/review evidence en confirm/reject toe.
Voor 24/7 bedrijf: volg [OPERATIONS.md](docs/OPERATIONS.md) voor systemd-installatie,
automatische boot/crash recovery, reboot-test en de gecombineerde dag/nacht-duurtest.
De policyfase hoeft daarvoor niet eerst apart handmatig geaccepteerd te worden.

## Local development / demo mode

Python 3.11+. De foundation is door de gebruiker geverifieerd op Raspberry Pi 5,
Debian 13 ARM64, Python 3.13.5: healthcheck via LAN en 6 foundation-tests geslaagd.
Ook de ingestfase is daar door de gebruiker bevestigd: 56 tests geslaagd en
healthcheck via het LAN werkt. De nieuwe BirdNET-hardwaretest volgt apart.

Linux / Raspberry Pi, nieuwe checkout:

```bash
git clone https://github.com/CPVB86/Backyard.git
cd Backyard
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m app.core.migrate
pytest -v
export BACKYARD_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --no-access-log
```

Windows PowerShell, vanuit de projectroot:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m app.core.migrate
.\.venv\Scripts\pytest -v
$env:BACKYARD_API_TOKEN = & .\.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(32))"
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --no-access-log
```

Voor alleen de API is requirements.txt voldoende. Geen nieuwe dependencies in
de ingestfase: audio gebruikt de Python-standaardbibliotheek.
Stop met Ctrl+C. GET http://127.0.0.1:8010/api/health controleert de database:

```json
{"status":"ok","service":"backyard","database":"ok"}
```

Databasefalen geeft HTTP 503. De bestaande Uvicorn-startwijze blijft geldig.
Voor bewust gebruik op het vertrouwde LAN: vervang --host 127.0.0.1 door
--host 0.0.0.0. Alle `/api/*` endpoints, inclusief health en audio, vereisen
`Authorization: Bearer <BACKYARD_API_TOKEN>`. Zonder geldig geconfigureerd token
start de API niet. Gebruik voor productie het gedeelde `/etc/backyard/backyard.env`;
zie [tokeninstallatie](docs/OPERATIONS.md#bearer-authenticatie-na-de-duurtest).
Voor lokale ontwikkeling kan het token ook in `.env` staan. De detector en losse
readinesschecks lezen de omgevingsvariabele; exporteer die bij handmatig gebruik.
Swagger `/docs` heeft een Authorize-knop. Rechtstreeks een audio-URL in een browser
openen stuurt geen Bearer-header: API-consumers moeten die zelf meesturen.
Bearer-auth versleutelt HTTP niet; gebruik HTTPS bij verkeer buiten het vertrouwde LAN.
Gebruik Ã©Ã©n API-worker. Swagger staat op /docs (interface gebruikt CDN-assets);
de API en /openapi.json werken zonder cloud.

## Projectstructuur

- app/core: configuratie, logging, database, versiegebonden SQLite-migraties.
- app/modules/birds/models.py: detecties en afzonderlijke audiometadata.
- app/modules/birds/schemas.py: gevalideerd API-contract en responses.
- app/modules/birds/service.py: transacties en idempotency.
- app/modules/birds/storage.py: PCM-WAV-controle en veilige bestandsopslag.
- app/modules/birds/router.py: ingest, filters en audio retrieval.
- observations: gedeelde pure policy/aggregatie, zonder ML/database-afhankelijkheid.
- app/modules/observations: generieke bird/bat observations, supports en review/evidence.
- detector/providers.py: BirdNET-taxonomie en optioneel locatie/week-signaal.
- tests: foundation, ingest/audio, policy/review, migratie- en foutscenario's.

Weather en Garden krijgen later eigen modules. Bats zijn voorbereid in de
generieke observationlaag, zonder echte detector. BirdNET gebruikt alleen de
publieke HTTP-contracten; het importeert geen database-internals.

## Configuratie

Kopieer optioneel .env.example naar .env in de projectroot. Omgevingsvariabelen
hebben voorrang. Er worden geen secrets of configuratie uit AvianVisitors gelezen.

| Variabele | Default | Betekenis |
| --- | --- | --- |
| BACKYARD_DATABASE_PATH | data/backyard.sqlite3 | SQLite-bestand |
| BACKYARD_STORAGE_ROOT | data | Root voor relatieve audio storage keys |
| BACKYARD_MAX_AUDIO_BYTES | 8388608 | Maximaal 8 MiB WAV per aanvraag; instelbaar 1024â€“67108864 bytes |
| BACKYARD_LOG_LEVEL | INFO | DEBUG, INFO, WARNING, ERROR of CRITICAL |
| BACKYARD_POLICY_PATH | policy.json indien aanwezig | Gedeelde observation-policy, anders ingebouwde defaults |

Relatieve configuratiepaden zijn relatief aan de projectroot, onafhankelijk
van de werkmap. Logs gaan naar stderr, zonder payloads, SQL-parameters of eigen
logbestanden. Geen periodieke writes of achtergrondjobs. data/, logs/, .env,
venv en caches blijven buiten Git; ook aangepaste opslaglocaties buiten Git houden.

## Database upgraden: bestaande data behouden

**Stop de API en eventuele writers eerst. Verwijder de database niet.**

Vanuit de projectroot, met de bestaande .env/omgeving:

```bash
source .venv/bin/activate
git switch main
git pull --ff-only origin main
python -m pip install -r requirements-dev.txt
python -m app.core.migrate
pytest -v
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --no-access-log
```

De migratie gebruikt dezelfde BACKYARD_DATABASE_PATH als de API. Een bestaande
foundation-database heeft schema-versie 0. VÃ³Ã³r wijziging maakt het commando
een consistente SQLite-back-up naast dat bestand:
backyard.sqlite3.backup-<UTC-tijd>-<uuid>. Het volledige back-uppad wordt getoond.
Bewaar die back-up; hij bevat de oude detecties. Er is geen automatische verwijdering.

Daarna voert migratie 0â†’1 onder BEGIN IMMEDIATE Ã©Ã©n transactie uit:

- Voegt event_id, source_version, ingest_hash en verification_status toe.
- Maakt een unieke index op (source, event_id).
- Maakt bird_audio met een foreign key naar de detectie.
- Behoudt de oorspronkelijke detecties en het ingestcontract.

De nieuwe migratie 1→2 voegt observations en observation_candidates toe.
PRAGMA user_version wordt pas na alle succesvolle stappen op **2** gezet.
Bestaande versie-1-data/audio wordt niet aangepast; ook de historische
chimpansee blijft bewaard. Zie docs/OBSERVATION_POLICY.md.

Bestaande IDs, timestamps, raw_metadata en audio_reference blijven bewaard.
Oude records krijgen event_id=null en verification_status=unreviewed; ze
worden niet achteraf gededupliceerd. Oude willekeurige audio_reference-paden
worden niet publiek gemaakt of als bestanden geopend. Import daarvan is later werk.

De migratie is herhaalbaar: op versie 2 doet ze niets en maakt geen extra back-up.
Fouten rollen schemawijzigingen terug; onbekende schema's/toekomstige versies
worden geweigerd. Een bestaande versie-0/1-database blokkeert de nieuwe API-start
met de migratie-instructie. Alleen een lege database mag de API zelf initialiseren.

Voor deze kleine, additieve SQLite-overgangen gebruiken we een kleine
expliciete migratiereeks in app/core/migrations.py, met regressietests voor
databehoud en DDL-rollback. Geen Alembic-dependency nodig voor deze stap.
Nieuwe schemawijzigingen vereisen een **nieuwe** migratie/versie en tests:
wijzig de gepubliceerde 0â†’1-migratie niet. Bij complexere migraties of PostgreSQL
kan dit naar Alembic overgaan. PostgreSQL is nog niet operationeel ondersteund.

Herstel bij een migratiefout: API gestopt houden, fout onderzoeken; bij een
noodzakelijke rollback de getoonde back-up Ã©n de bijbehorende oude codeversie
herstellen. Een oude back-up terugzetten nadat nieuwe ingest is gestart verliest
die nieuwe records; maak daarvoor eerst een actuele back-up. Audio en database
later gezamenlijk back-uppen; geen actieve SQLite-file blind kopiÃ«ren.

## Detection contract

POST /api/birds/detections accepteert application/json, maximaal 64 KiB:

```json
{
  "event_id": "pi-birds-20260929-000001",
  "detected_at": "2026-09-29T12:00:00+02:00",
  "scientific_name": "Columba livia",
  "common_name": "Rotsduif",
  "confidence": 0.944,
  "source": "backyard-birdnet",
  "source_version": "1.0",
  "model_version": "acoustic-3.0-onnx",
  "raw_metadata": {"analysis_window_seconds": 6}
}
```

Verplicht: event_id, detected_at, scientific_name, confidence, source.
common_name, source_version, model_version en raw_metadata zijn optioneel.
Onbekende velden worden geweigerd; strings worden getrimd, lege namen/IDs
geweigerd. Naam/source/event_id maximaal 255 tekens, versies maximaal 100.

- detected_at: ISO 8601 met T en expliciete offset of Z. Naive tijden en
  numerieke epoch-waarden zijn niet toegestaan bij ingest.
- Intern UTC; de bestaande databasekolom heet timestamp. Responses hebben
  detected_at Ã©n de compatibiliteitsalias timestamp, beide ondubbelzinnig UTC.
  created_at is server-UTC. Europe/Amsterdam is later presentatie/rapportage.
- confidence: werkelijk JSON-getal 0..1 inclusief grenzen; geen boolean,
  string, NaN, infinity of procentwaarde.
- raw_metadata: JSON-object voor aanvullende brongegevens, zonder niet-eindige
  getallen. Genormaliseerde hoofdvelden blijven leidend.
- verification_status: server zet unreviewed. Datamodel reserveert confirmed
  en rejected; deze fase biedt geen review/update-endpoint of revieweraccounts.

Nieuwe detectie: HTTP 201 met server-UUID en Location-header. Exacte retry:
HTTP 200 met dezelfde ID. Idempotency gebruikt (source, event_id) en een hash
van alle genormaliseerde ingestvelden. JSON-volgorde en equivalente tijdzones
maken geen verschil. De producer moet event_id stabiel bewaren bij retries.

Andere inhoud onder dezelfde sleutel: HTTP 409. Andere event_ids met dezelfde
soort/tijd blijven afzonderlijke detecties. Dezelfde event_id van een andere
source is ook een andere detectie. Geen heuristische tijdvenster-deduplicatie.

```bash
curl -H "Authorization: Bearer $BACKYARD_API_TOKEN" --fail-with-body -X POST http://127.0.0.1:8010/api/birds/detections \
  -H 'Content-Type: application/json' \
  --data '{"event_id":"example-001","detected_at":"2026-09-29T10:00:00Z","scientific_name":"Columba livia","common_name":"Rotsduif","confidence":0.944,"source":"manual-test"}'
```

Deze handmatige voorbeelden schrijven bewust data; gebruik een testdatabase
als je geen voorbeeldrecords in de echte database wilt. pytest gebruikt altijd
tijdelijke databases/opslag.

## Audio-contract: twee stappen

1. POST JSON-detectie, onthoud de teruggegeven id.
2. PUT /api/birds/detections/{id}/audio met Content-Type: audio/wav en **ruwe
   WAV-bytes**, dus geen multipart, base64 of lokaal pad.

Een detectie zonder audio is geldig: audio=null, audio_url=null. Een mislukte
tweede stap rolt de detectie niet terug; de producer herhaalt alleen de upload.
Dat is de expliciete tussenstatus van dit contract, geen half audiorecord.

```bash
DETECTION_ID='vervang-door-de-id-uit-de-response'
curl -H "Authorization: Bearer $BACKYARD_API_TOKEN" --fail-with-body -X PUT \
  "http://127.0.0.1:8010/api/birds/detections/$DETECTION_ID/audio" \
  -H 'Content-Type: audio/wav' --data-binary @fragment.wav

curl -H "Authorization: Bearer $BACKYARD_API_TOKEN" --fail \
  "http://127.0.0.1:8010/api/birds/detections/$DETECTION_ID/audio" \
  --output teruggeluisterd.wav
```

Er wordt geen audio opgenomen of geconverteerd. Toegestaan: complete RIFF/WAVE
met ongecomprimeerde integer PCM, 1â€“2 kanalen, 8â€“192 kHz, 8/16/24/32 bit,
duur >0 en maximaal 60 seconden. Het beoogde 6-secondenfragment past hierin.
Inhoud/header/chunks en beschikbare samples worden gecontroleerd; alleen een
MIME-type of extensie is onvoldoende. MP3, FLAC en float-WAV zijn nu uitgesloten.

De daadwerkelijk ontvangen bytes worden begrensd, ook zonder Content-Length.
De upload wordt tot de limiet in geheugen gevalideerd (default 8 MiB per aanvraag).
Een server-side tijdelijke file in dezelfde map wordt geflusht/fsync'd en
atomair gepubliceerd met een exclusieve hardlink. Opslag vereist dus een lokaal
filesystem met hardlinks, zoals ext4 of NTFS. Geen clientfilename wordt gebruikt
of bewaard: de raw-body-upload heeft geen relevante oorspronkelijke filename.

Intern: birds/audio/YYYY/MM/DD/<detection-uuid>.wav, gebaseerd op detected_at
in UTC, relatief aan BACKYARD_STORAGE_ROOT. EÃ©n bestand per detectie; geen
deduplicatie tussen verschillende detecties. De database bevat nooit audio-BLOBs.
Metadata: storage key, MIME, codec, sample rate, kanalen, sample width (bytes),
duur, grootte, SHA-256, created_at en status=available. Responses publiceren
metadata en een relatieve audio_url, geen storage key of absolute serverpaden.
audio_reference is in responses dezelfde API-URL voor compatibiliteit.

Eerste upload: 201. Exact dezelfde bytes opnieuw: 200, zonder extra definitief
bestand. Andere bytes: 409; bestaande audio wordt niet overschreven.
Database-unique constraints en schrijftransacties beschermen gelijktijdige retries.

GET audio levert audio/wav, ondersteunt HTTP Range voor players en gebruikt
nosniff. Ontbrekende detectie/audio/file: 404. De read-routes zijn:

- GET /api/birds/detections?limit=50 (1..100)
- GET /api/birds/detections/{id}
- GET /api/birds/latest (404 wanneer leeg)
- GET /api/birds/detections/{id}/audio

Lijstfilters: scientific_name (exact), since (inclusief), until (exclusief);
tijdsfilters moeten een tijdzone bevatten. since moet vÃ³Ã³r until liggen.
Volgorde: nieuwste detected_at eerst, ID als vaste tie-breaker.
Indexen op tijd en (soort, tijd) ondersteunen latere statistieken; er is nu geen
analytics-engine, paginering of review-UI.

```bash
curl -H "Authorization: Bearer $BACKYARD_API_TOKEN" --fail --get http://127.0.0.1:8010/api/birds/detections \
  --data-urlencode 'scientific_name=Columba livia' \
  --data-urlencode 'since=2026-09-29T00:00:00+02:00' \
  --data-urlencode 'until=2026-09-30T00:00:00+02:00' \
  --data-urlencode 'limit=20'
```

Foutcodes: 413 te groot, 415 verkeerd contenttype, 422 ongeldige inhoud,
409 inhoudelijk conflict, 503 database/storage tijdelijk niet beschikbaar.
Geen automatische betaalde diensten, retries of detectorinstallatie.

## Fouten, power loss en storage verplaatsen

Bij normale schrijf-/databasefouten worden tijdelijke en nieuw gepubliceerde
files teruggeruimd en audiometadata teruggerold. Het audiobestand wordt pas
via de API beschikbaar na succesvolle DB-commit. De API accepteert geen
storagepaden; ook ingelezen keys worden op containment en traversal gecontroleerd.
De opslagdirectory zelf moet door de applicatiebeheerder worden beheerd,
niet door onbetrouwbare lokale processen.

Filesystem en SQLite hebben geen gedeelde ACID-transactie. Bij abrupt killen
of stroomverlies kan een .tmp-file of een nog niet geregistreerd bestand
achterblijven. Een retry met identieke audio hergebruikt het definitieve bestand
op dezelfde deterministische key; afwijkende inhoud geeft 409 voor handmatige
controle. Achtergebleven .tmp-files worden niet automatisch opgeruimd. Een
geregistreerd maar verdwenen/beschadigd bestand wordt niet stilzwijgend hersteld.
Er is geen claim dat deze fase stroomuitval op de Pi heeft getest.

Nog geen retention/scheduler/delete-endpoint. Audiometadata heeft created_at
en status voor latere verwijdering zonder verlies van detectiehistorie.
De logische status van een detectie blijft onafhankelijk van beschikbaarheid
van de audio. Een latere retention-implementatie moet status daadwerkelijk bijwerken.

Naar NVMe: stop de API/writers, kopieer de volledige storage-directory met behoud
van relatieve structuur, controleer bestanden/checksums, wijzig
BACKYARD_STORAGE_ROOT en herstart. Databasekeys blijven gelijk. Verhuis SQLite
desgewenst apart via BACKYARD_DATABASE_PATH; behoud de database en back-ups.
Dit project voert die verhuizing niet automatisch uit.

## Tests en verificatie

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest -v
```

pytest.ini stelt pythonpath=. en testpaths=tests in, zodat het losse pytest-
commando werkt zonder PYTHONPATH of package-installatie. De testclient gebruikt
httpx2==2.13.1, passend bij Starlette 1.7.0; warnings worden niet onderdrukt.
Zie [pytest imports](https://pytest.org/en/stable/explanation/pythonpath.html)
en [Starlette releases](https://starlette.dev/release-notes/).

De tests gebruiken tijdelijke databases/storage, synthetische WAV-bytes en
foutinjectie. Er wordt geen microfoon, bestaande audio of gebruikersdatabase gebruikt.
Deze fase voegt geen dependencies toe; directe versies zijn gepind, transitieve
dependencies nog niet volledig gelockt.

De SQLite-migratie is bewust expliciet transactioneel; zie
[SQLite transacties](https://www.sqlite.org/lang_transaction.html).
WAV-ondersteuning volgt [Python wave](https://docs.python.org/3/library/wave.html).

Uitgevoerd voor deze fase op Windows/Python 3.14.7: **56 tests geslaagd**,
met warnings als fouten, inclusief alle 6 bestaande foundation-tests. Ook
uitgevoerd: migratie-CLI met back-up op een tijdelijke foundation-database,
echt Uvicorn-proces, HTTP health=200, detection POST=201, audio PUT=201,
byte-identieke audio retrieval, latest en geldige OpenAPI-referenties.
`pip check` meldt geen conflicten. Testserver gestopt en smoke-data verwijderd.
Er is geen bestaande gebruikersdatabase gemigreerd of audio ingelezen.
De nieuwe fase is nog niet op Linux/ARM64 uitgevoerd door Codex.

## BirdNET: geisoleerde hardwaretest

Zie [Pi-commando's, compatibiliteitsonderzoek en continue-capture-richting](docs/DETECTOR_TEST.md).
Installeer detector/requirements.txt uitsluitend in .venv-detector.
De sequentiele live-test heeft opnamegaten en is niet voor 24/7 productie.

Nederlandse/Duitse namen: Birds/observations-output bevat `common_name_nl` en
`common_name_de` uit de bestaande
[BirdNET-taxonomie](docs/SPECIES_NAMES.md), met de oorspronkelijke Engelse naam als fallback.

## Generator

Authenticated species illustrations and existing AvianVisitors assets are available
through the domain-neutral Generator. See [Generator setup and API](docs/GENERATOR.md).
