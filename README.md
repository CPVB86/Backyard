# Backyard

Lokale backend voor één tuin. Backyard bezit de detectiehistorie; BirdNET levert
later detecties. AvianVisitors en WordPress worden API-consumers.

## Scope en structuur

Dit is de zelfstandige repository https://github.com/CPVB86/Backyard.
De bestaande Backyard-fundering is overgenomen zonder AvianVisitors-code,
assets of Git-historie. AvianVisitors blijft een afzonderlijke consumer.

- `app/core/config.py`: getypeerde configuratie, alleen Backyard's eigen .env.
- `app/core/logging.py`: consolelogging, geen eigen logbestanden of polling.
- `app/core/database.py`: SQLAlchemy-engine, SQLite-initialisatie en UTC-type.
- `app/modules/birds/models.py`: centraal detectiemodel.
- `app/modules/birds/router.py`: begrensde read-only detectielijst.
- `app/main.py`: FastAPI, lifecycle en healthcheck.
- `tests/test_foundation.py`: database-, API- en configuratietests.
- `data/`, `logs/`: gereserveerde lokale uitvoer, buiten Git.
- `requirements*.txt`, `.env.example`, `.gitignore`: geïsoleerde setup.

Weather, Garden en Bats krijgen later eigen packages onder modules met eigen
routers en modellen; nu zijn daarvoor geen lege services of tabellen nodig.
Core importeert geen audio-, hardware- of AI-code.

## Local development / demo mode

Python 3.11+; deze fase is uitgevoerd op Windows met Python 3.14.
Voor een nieuwe checkout, PowerShell:

```powershell
git clone https://github.com/CPVB86/Backyard.git
cd Backyard
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --no-access-log
```

Linux / Raspberry Pi, voor een nieuwe checkout:

```bash
git clone https://github.com/CPVB86/Backyard.git
cd Backyard
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --no-access-log
```

Python met venv/pip moet beschikbaar zijn. Er worden geen OS-pakketten of
services automatisch geïnstalleerd. Stop met Ctrl+C. Poort 8010 voorkomt
conflict met de bestaande AvianVisitors-demo op 8000.

Open http://127.0.0.1:8010/api/health of test in een tweede terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/health
```

Op Linux: `curl --fail http://127.0.0.1:8010/api/health`.

Verwacht HTTP 200:
```json
{"status":"ok","service":"backyard","database":"ok"}
```

De healthcheck voert werkelijk SELECT 1 uit; databasefalen geeft HTTP 503.
`GET /api/birds/detections?limit=50` geeft aanvankelijk `[]`; maximum 100.
Er is nog geen invoerendpoint, demo-seeding, statistiek of afbeeldingregistratie.
Swagger staat op /docs (de Swagger-webinterface laadt CDN-assets; de API zelf
heeft geen internet nodig). /openapi.json werkt lokaal.

## Configuratie en opslag

Een .env is optioneel: kopieer .env.example naar .env **in de projectroot**.
Omgevingsvariabelen hebben voorrang. De AvianVisitors-.env wordt niet geladen.
BACKYARD_DATABASE_PATH is standaard data/backyard.sqlite3; relatieve paden
zijn altijd relatief aan de projectroot. BACKYARD_LOG_LEVEL is standaard INFO.
Een verkeerd logniveau of onbekende .env-instelling blokkeert de start.

De database wordt uitsluitend tijdens applicatiestart aangemaakt, inclusief
ontbrekende bovenliggende mappen. Herstart behoudt bestaande gegevens.
create_all maakt ontbrekende tabellen maar migreert geen bestaande schema's.
Voeg vóór een eerste schemawijziging versiebeheer/migraties toe.

Detecties bevatten UUID, waarnemingstijd, wetenschappelijke en optionele gewone
naam, confidence (0–1), bron, optionele audioverwijzing/modelversie, ruwe JSON
metadata en created_at. Audio wordt niet in SQLite opgeslagen. Metadata wordt
wel bewaard, maar niet standaard via de lijst gepubliceerd.
Tijden vereisen een tijdzone, worden naar UTC omgezet en als UTC teruggegeven.
Indexen ondersteunen tijdselecties en soorthistorie; dagstatistieken moeten later
expliciet een lokale tijdzone gebruiken.

SQLAlchemy houdt modellen los van SQLite. PostgreSQL vereist later een driver,
engineconfiguratie, schema-/datamigratie en integratietests; alleen een URL
wijzigen migreert geen data. SQLite gebruikt nu conservatieve standaard
durability, een lock-timeout van 5 seconden en één API-worker.
Geen periodieke writes, SQL-debuglogging of accesslogs bij het startcommando.
Bij echte ingest later transacties bundelen, retentie/back-ups en WAL afwegen.
Kopieer een actieve SQLite-database niet blind voor een back-up.

## Tests

Vanuit de projectroot, Windows:

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\pytest -v
```

Linux / Raspberry Pi, vanuit de projectroot:

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest -v
```

`pytest.ini` stelt `pythonpath = .` in relatief aan de projectroot en beperkt
testdetectie met `testpaths = tests`. Daardoor vinden zowel `pytest` als
`python -m pytest` het lokale `app`-package, zonder handmatig PYTHONPATH of
package-installatie. Alleen de module-aanroep voegt van zichzelf de huidige
map aan het importpad toe; dit verschil bestaat op Windows én Linux.
Zie de [pytest-importdocumentatie](https://pytest.org/en/stable/explanation/pythonpath.html).

Starlette 1.7.0 gebruikt bij voorkeur httpx2 voor TestClient. De ontwikkelset
gebruikt daarom `httpx2==2.13.1` (Python >=3.10, inclusief 3.13), in plaats van
httpx 0.28.1. Een al geïnstalleerde httpx mag blijven staan: Starlette kiest
httpx2 zodra dit beschikbaar is. Er worden geen warnings onderdrukt en geen
runtime-dependencies gewijzigd. Zie [Starlette-releases](https://starlette.dev/release-notes/)
en [httpx2](https://pypi.org/project/httpx2/2.13.1/).
Tests gebruiken tijdelijke databases; normale opslag blijft onaangeraakt.

## Volgende fase en Pi-services

Eerst deze fundering op de Pi starten en /api/health controleren. Daarna een
ingestcontract ontwerpen met validatie, bron/event-ID voor deduplicatie en
behoud van ruwe modeluitvoer. Pas daarna BirdNET als onafhankelijk proces
aansluiten via lokale HTTP-ingest met begrensde retries/buffering. Het
BirdNET-proces importeert of start de API niet; een detectorcrash laat de API
draaien. Installeer BirdNET/model en kies microfoon pas na controle op de Pi.

Later twee systemd-units: backyard-api en backyard-birds, met eigen
ExecStart, WorkingDirectory, EnvironmentFile en Restart=on-failure.
De API moet onafhankelijk starten, zonder Requires op de detector.
Consolelogs kunnen dan naar journald; retentie apart beoordelen voor de SD-kaart.
Er zijn nu geen unitbestanden geïnstalleerd of systeeminstellingen aangepast.
De API bindt standaard aan loopback, zonder authenticatie; LAN/publicatie en
schrijftoegang vereisen een afzonderlijke bewuste configuratiefase.

Het eerdere BirdNET-backendadvies in de afzonderlijke AvianVisitors-repository
is historisch advies voor AvianVisitors. De nieuwe richting is Backyard als eigenaar, zonder Docker.
ARM64, Raspberry Pi, audio en systemd zijn in deze Windows-ronde niet uitgevoerd.

## Uitgevoerde verificatie (29 september 2026)

- Nieuwe lokale venv, dependencies geïnstalleerd, pip check zonder conflicten.
- Zes pytest-cases geslaagd: lege start, persistentie na herstart, UTC-conversie,
  behoud van metadata, confidencegrenzen, tijdzonevalidatie, querylimieten,
  databasefout met 503 en configuratievoorrang/validatie.
- Echt Uvicorn-proces gestart op 127.0.0.1:8010; via HTTP health=200 en
  database=ok, detectielijst=[]; proces daarna gestopt.
- Database en venv worden door Git genegeerd. Geen bestaande applicatiecode gewijzigd.
- TestClient geeft een upstream deprecationwaarschuwing over httpx; tests slagen.
  Bij een dependency-update de testclientcompatibiliteit opnieuw beoordelen.

Directe dependencies zijn vastgezet op de geteste versies; transitieve dependencies
zijn nog niet volledig gelockt. Linux/ARM64-installatie blijft een acceptatiecheck.
Lifecycle-tests gebruiken de contextmanager uit de
[FastAPI-documentatie](https://fastapi.tiangolo.com/advanced/testing-events/).

## Aanvullende platformverificatie

De gebruiker heeft de API op Raspberry Pi 5, Debian 13 ARM64 en Python 3.13.5
uitgevoerd: starten via `python -m uvicorn app.main:app` en de healthcheck vanaf
het LAN werken. De bovenstaande eerdere Windows-verificatie blijft historisch.
De pytest-console-importfout is ook lokaal op Windows gereproduceerd.

Na de configuratie- en dependencywijziging lokaal uitgevoerd (Windows, Python
3.14.7): `pytest -v` en `python -m pytest -v -W error` geven elk 6 passed,
zonder warnings; PYTHONPATH was niet ingesteld. `pip check` meldt geen
conflicten en Uvicorn kan `app.main:app` importeren. Alleen voor de lokale
Windows-sandbox is een nieuwe TEMP/TMP-map gebruikt wegens bestaande
toegangsrechten; dit is geen projectinstelling of vereiste voor de Pi.
Deze gewijzigde tests zijn nog niet door Codex op Linux/ARM64 uitgevoerd.
