# Continue Backyard Birds-monitor

Start handmatig met `python -m detector.monitor`. Dit is de nieuwe continue
productiearchitectuur; betrouwbaarheid en capaciteit moeten nog op de echte Pi
worden gemeten. Geen systemd/autostart, schemawijziging of nieuwe dependency.

De bestaande `detector.cli capture|analyze|live` blijft diagnostiek.
`live` stopt capture tijdens analyse en blijft ongeschikt als 24/7-monitor.

## Architectuur

```text
een arecord-proces -> capturethread -> vaste PCM-ring in RAM
                                          |
                                    window-scheduler
                                          |
                              bounded inferencequeue
                                          |
                             blijvende BirdNET-sessie
                                          |
                               kandidaten >= threshold
                                          |
                                bounded clipqueue
                                          |
                         extractor leest ring, wacht post-roll
                                          |
                               bounded outboundqueue
                                          |
                              eigen HTTP-uploadthread
                                          |
                             POST detection -> PUT WAV
```

Capture doet korte raw PCM16-reads en RAM-writes. De ringlock beschermt alleen
RAM-kopieen; geen inferentie, HTTP, modeldownload of clipbestand onder die lock.
Scheduler, inferentie, clipextractie en HTTP hebben afzonderlijke threads.
Detector importeert geen databasecode en schrijft uitsluitend via de bestaande API.

ALSA wordt eenmaal geopend met `arecord -t raw -f S16_LE` en `--fatal-errors`.
Met -v wordt de eerste (outer PCM) onderhandelde rate gecontroleerd; een
ontbrekende of afwijkende rate stopt de monitor. Een plug mag intern resamplen.
Raw stdout heeft niet de WAV-2GiB-limiet. Zie
[ALSA broncode](https://github.com/alsa-project/alsa-utils/blob/master/aplay/aplay.c)
en [Debian Trixie arecord](https://manpages.debian.org/trixie/alsa-utils/arecord.1.en.html).
Geen continue WAV of lokale audio-spool. Alleen geaccepteerde clips worden als
mono PCM16-WAV in RAM opgebouwd en naar de bestaande audio-ingest gestuurd.

### BirdNET werkelijk hergebruiken

Onderzocht in de officiele **birdnet 1.1.1 wheel**:

- `birdnet.load("acoustic", "3.0", "onnx", precision="fp32")` eenmaal.
- `model.predict_session(...)` eenmaal openen.
- Herhaald `session.run_arrays((float32_mono_samples, capture_rate))`.
- Een producer, een inference-worker, batch=1, prefetch=1, CPU, FP32.
- `max_n_files=1` begrenst de interne resultaatscapaciteit.
- PCM16 / 32768 naar float32; de bestaande BirdNET-resampling verzorgt
  onder meer 48 kHz -> 32 kHz. Geen nieuw resampling-algoritme.
- Acoustic v3 preview3.1: 3 seconden / 96000 modelsamples.
- De scheduler bepaalt overlap; binnen elk 3s-array staat overlap=0.
- Alle classes beschikbaar: top_k=None. Backyard past >= threshold expliciet
  toe; geen top-5-afkap, speciesfilter of extra sigmoid.
- Een nulvenster warmt de sessie op VOOR de microfoon opent; die uitkomst
  wordt weggegooid. Daarna blijft dezelfde sessie inclusief modelworker bestaan.

De diagnostische `model.predict()` opent per call een sessie; de monitor
gebruikt die methode niet. Geen private model/backend-methoden nodig.

De sessie draait in een supervisorproces met de library-producer en -worker
daaronder, zodat een vastgelopen native backend begrensd kan worden gestopt.
Dat zijn niet meerdere parallelle inference-workers.

Bronnen: [gepinde officiele release](https://pypi.org/project/birdnet/1.1.1/),
[upstream repository](https://github.com/birdnet-team/birdnet).
Gecontroleerde wheelbestanden: acoustic/models/v3_0/model.py,
acoustic/inference/session.py, core/producer.py, core/worker.py en
core/prediction/prediction_result.py. Bij upgrades opnieuw controleren.

## Sampletijd, overlap en gaps

Bereiken zijn halfopen: [start_sample, end_sample), op de originele capture-rate.
Sample 0 heeft een UTC- en monotonic-anchor bij capturestart:

`estimated_UTC(sample) = UTC_anchor + sample / capture_rate`

NTP-sprongen veranderen bestaande sampletijden niet.
wall_clock_shift_seconds toont afwijking van de huidige wallclock ten opzichte
van de monotone startklok. capture_clock_lag_seconds vergelijkt monotone
verstreken tijd met ontvangen sampleduur: inclusief USB/ALSA-opstart, buffering
en sampleklokdrift. Dit is geen exact gemeten hardwarelatency.

Timestamps zijn SCHATTINGEN, geen hardwaretimestamps. BirdNET classificeert een
venster, niet het exacte begin/einde van een roep. detected_at is de geschatte
start van dat resultaatvenster.

raw_metadata bewaart stream-ID, device, rate/format, anchors, window- en
resultaatsamples, lokale modeloffsets, model/backend/precision, hop en clipbereik.
Een nieuwe start krijgt een nieuwe stream-ID.

Baseline window=3, overlap=0, hop=3. Later overlap=1.5, hop=1.5.
Overlap kan grensgevallen verbeteren, maar garandeert geen herkenning en
verdubbelt het aantal jobs. Geen automatische species-/window-samenvoeging.

De event-ID is deterministisch uit stream/window/resultaat/label/model.
HTTP-retries gebruiken exact dezelfde payload en WAV. Een volgend overlappend
venster heeft een eigen ID, ook bij dezelfde soort. Zo blijft latere expliciete
interpretatie mogelijk; API-retry en overlappende roepen zijn verschillende zaken.

Bij ALSA-overrun/underrun/suspend/error, onverwachte EOF of >3s zonder PCM:
capture_gaps stijgt, foutmelding, monitor stopt met exitcode 1. Geen automatisch
restart of verzonnen aantal verloren samples. Na onderzoek handmatig herstarten.

ALSA-stderr bevat geen betrouwbaar exact gapsample. Late foutmeldingen kunnen
reeds verzonden vensters niet terugtrekken; onbekende hardwaregaps zijn hiermee
niet uitgesloten. Normale ringwrap is geen capturegap. Praktijkvalidatie blijft nodig.

## Begrenzing en verliesbeleid

Default ring: 60 * 48000 * 2 = 5,760,000 bytes.
Inferencequeue: vier gekopieerde 3s-windows, 1,152,000 bytes, plus actieve job.
Kopieen voorkomen dat ringwrap reeds ingeplande inferenceaudio verandert.

Bij volle queues vervalt het oudste wachtende item; actieve taken worden niet
onderbroken. Schedulerachterstand slaat oude jobs direct over zonder ze eerst
allemaal te alloceren. Verlies wordt geteld; last_lost_range toont het laatste
verloren samplebereik. Geen onbeperkte WAV-/job-voorraad.

Pre/post-roll default elk 1s: instelbare TESTWAARDEN, geen definitieve productkeuze.
Clip = resultaatvenster plus context. Bij streamstart wordt pre-roll op sample 0
begrensd; metadata bevat het echte bereik. Post-roll wacht in de clipqueue.
Bij overschreven clipbegin vervallen clip EN nog niet geposte kandidaat,
met clips_expired/ring_overruns. Geen verkeerde/gedeeltelijke audio uploaden.

Clipqueue: 32 metadata-items. Outboundqueue: 16 WAVs, bij defaults circa 7.7 MB
plus actieve upload. Lage thresholds kunnen veel kandidaten per window geven;
daarom zijn ook deze queues begrensd. Queuegroottes max 64, ring max 300s.

Ring moet minstens window + pre/post-roll bevatten, maar praktisch bovendien
maximale queue-/inferentievertraging plus veiligheidsmarge. 60s is een startwaarde.
Model-RAM en queues komen erbij. Meer buffer lost structureel te trage analyse niet op.

HTTP: standaard 2s timeout, maximaal 3 pogingen, onderbreekbare backoff 0.5/1s.
Netwerkfout, 408, 429 en 5xx worden herhaald; o.a. 409/422 zijn terminaal.
Response maximaal 64 KiB, geen redirects. Na POST-succes alleen PUT herhalen.
Na verloren POST-response exact dezelfde POST herhalen: server-idempotency
voorkomt dubbele records.

Bij offline API blijven capture/inferentie werken. Na retries vervalt het item;
bij volle outboundqueue vervallen oude wachtende clips. GEEN duurzame offline
replay. Als POST slaagde en PUT definitief faalt, blijft een geldige detectie
zonder audio achter. Geen automatische verwijdering of herstel.

BirdNET-logs staan in een eigen tijdelijke directory. Loggerlevel WARNING
voorkomt INFO/DEBUG-per-window-groei; Python-warnings worden niet onderdrukt.
Bij >8 MiB tijdelijke librarydata stopt de monitor bij de volgende statuscontrole
(kleine overschrijding mogelijk). Eigen tempdirectory wordt bij stop verwijderd;
modelcache blijft behouden. Redirect stdout niet onbeperkt naar een logbestand.

## Configuratie

CLI wint van omgeving. Elke optie heeft BACKYARD_MONITOR_<NAAM_MET_UNDERSCORES>
als variabele, bijvoorbeeld BACKYARD_MONITOR_OVERLAP=1.5.
De detector leest GEEN .env; zet deze keys niet in de strikt gevalideerde API-.env.

| Optie | Default / beperking |
| --- | --- |
| --device | plughw:CARD=Device,DEV=0 |
| --rate | 48000; ook 32000, 44100, 96000 |
| --channels | 1; stereo expliciet geweigerd |
| --threshold | 0.60; 0..1 |
| --window | 3; andere lengtes in deze baseline geweigerd |
| --overlap | 0; 0 <= overlap < 3; hop in hele capturesamples |
| --ring-seconds | 60; max 300 |
| --inference-queue | 4; 1..64 |
| --clip-queue | 32; 1..64 |
| --outbound-queue | 16; 1..64 |
| --pre-roll / --post-roll | elk 1; elk 0..10 |
| --api-url | http://127.0.0.1:8010; origin zonder credentials/path |
| --http-timeout | 2; 0.1..5s |
| --attempts | 3; 1..5 |
| --status-seconds | 10; 1..60s |
| --capture-only | alleen RAM-capture, geen model/jobs/HTTP |

Modelcache: .detector-test/model-cache, of bestaande BIRDNET_APP_DATA.
Geen dependencybestanden gewijzigd; geen databasemigratie.

## Exacte Pi-procedure

Vanuit je bestaande Backyard-repository-root. Stop eerst eventuele diagnostische
capture/live met Ctrl+C: slechts een programma mag de microfoon gebruiken.

### 1. Pull en tests

```bash
git switch main
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
source .venv/bin/activate
pytest -v -W error
deactivate
.venv-detector/bin/python -m pip check
.venv-detector/bin/python -m detector.monitor --help
```

Bestaande detectorvenv niet vervangen/upgraden. Alleen indien die ontbreekt:
volg eerst DETECTOR_TEST.md met de bestaande gepinde detector/requirements.txt.

### 2. A: eerst 10 minuten capture-only

```bash
python3 -m detector.monitor --capture-only --device 'plughw:CARD=Device,DEV=0'
```

Iedere 10s status. Ongeveer 480000 extra samples per 10s, gaps=0,
alsa_overruns=0; RAM blijft vast, geen WAVs/API-events. Clock lag mag door
buffering varieren, maar niet aanhoudend sterk groeien. Controleer de
microfooninhoud met de reeds bewezen diagnostische capture; capture-only
classificeert niet. Stop met Ctrl+C: geen traceback, geen eigen arecord meer.

### 3. API in terminal A

Als de API al goed draait: geen tweede exemplaar starten. Anders:

```bash
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --no-access-log
```

### 4. B: baseline in terminal B, minstens 30 minuten

```bash
curl --fail http://127.0.0.1:8010/api/health
.venv-detector/bin/python -m detector.monitor \
  --device 'plughw:CARD=Device,DEV=0' \
  --rate 48000 --channels 1 --window 3 --overlap 0 \
  --threshold 0.60 --ring-seconds 60 \
  --inference-queue 4 --pre-roll 1 --post-roll 1 \
  --api-url http://127.0.0.1:8010
```

Wacht op "Monitor gestart"; model/warmup gebeurt ervoor. Bied bekend
vogelgeluid aan. Dit schrijft echte detecties en clips naar Backyard.
Stop met Ctrl+C. Eerst baseline stabiel, daarna overlap.

### 5. C: overlap, alleen na stabiele baseline, minstens 30 minuten

```bash
.venv-detector/bin/python -m detector.monitor \
  --device 'plughw:CARD=Device,DEV=0' \
  --rate 48000 --channels 1 --window 3 --overlap 1.5 \
  --threshold 0.60 --ring-seconds 60 \
  --inference-queue 4 --pre-roll 1 --post-roll 1 \
  --api-url http://127.0.0.1:8010
```

De oude 6s / 2.8s-meting bewijst niet dat deze configuratie haalbaar is.
Vergelijk de echte mean EN last inference time met hop=1.5s.
Duurzaam: gemiddeld verwerking < hop, met marge. Teststreefwaarde ratio <0.8,
geen garantie. Structureel ratio >=1, oplopende leeftijd/drops of throttling:
terug naar overlap 0. Laat bij succes vervolgens meerdere uren handmatig draaien.

### 6. D-J: metrics, geheugen, CPU en temperatuur

Status bevat:
- uptime, samples_captured, capture_gaps, alsa_overruns, capture_clock_lag_seconds;
- inference_depth/oldest_seconds, windows_processed/dropped, ring_overruns;
- queue_wait_seconds, inference_seconds/mean_seconds, latency_seconds;
- realtime_ratio (mean/hop), realtime_ratio_last, inference_active_seconds;
- detections en laatste soort/confidence/UTC/event-ID;
- clips_expired/dropped, outbound_depth, uploads_ok/failed/dropped, http_failures.

latency_seconds meet resultaatbeschikbaarheid t.o.v. geschat venstereinde.
queue_wait begint bij scheduling. Gewone ringwrap telt niet als overrun:
ring_overruns telt verloren benodigde vensters/clips. Eenzelfde audioverlies
kan meerdere consumenten raken. Verwacht geen groeiende backlog of drops/gaps.

Terminal C:

```bash
ps -eo pid,ppid,comm,rss,pcpu,args --sort=-rss | head -n 20
top
cat /sys/class/thermal/thermal_zone0/temp
```

RSS is KiB; bekijk monitor EN BirdNET-kindprocessen. Som kan gedeelde pagina's
dubbeltellen. top toont CPU, temperatuur is milligraden Celsius.
Indien beschikbaar: vcgencmd get_throttled. Geen harde monitordependency.

### 7. K-L: ingest en werkelijke audio

```bash
curl --fail http://127.0.0.1:8010/api/birds/latest | python3 -m json.tool
```

Controleer source=backyard-birdnet-monitor, confidence>=threshold,
samplebereiken/stream-ID en audio_url. Een net gepost record kan kort op PUT wachten.

Een laatste clip voor controle ophalen (overschrijft alleen het genoemde testbestand):

```bash
python3 - <<'PY'
import json, urllib.request, wave
from pathlib import Path
base = "http://127.0.0.1:8010"
with urllib.request.urlopen(base + "/api/birds/latest", timeout=5) as r:
    item = json.load(r)
assert item["source"] == "backyard-birdnet-monitor", item["source"]
assert item["audio_url"], "Wacht op geslaagde audio-upload"
path = Path(".detector-test/monitor-check.wav")
path.parent.mkdir(exist_ok=True)
with urllib.request.urlopen(base + item["audio_url"], timeout=5) as r:
    path.write_bytes(r.read())
with wave.open(str(path)) as wav:
    meta = item["raw_metadata"]
    expected = meta["clip_end_sample"] - meta["clip_start_sample"]
    assert wav.getnchannels() == 1 and wav.getsampwidth() == 2
    assert wav.getframerate() == meta["sample_rate"]
    assert wav.getnframes() == expected
    print(item["scientific_name"], item["confidence"], expected / wav.getframerate(), "seconden", path)
PY
```

Luister indien mogelijk met aplay .detector-test/monitor-check.wav.
Dit ene controlebestand is geen continue recorder.

Offline-test: stop alleen de API met Ctrl+C terwijl monitor draait. Samples en
windows_processed moeten doorgaan; HTTP-fouten/drops worden zichtbaar. Herstart
API met stap 3. Nieuwe items moeten slagen; vervallen items worden niet gereplayed.

### 8. M: schoon stoppen

Ctrl+C stopt scheduling/capture, annuleert BirdNET en laat HTTP begrensd uitlopen.
Geen drain van alle wachtrijen. Laatste status toont windows/clips/uploads_abandoned,
forced_worker_stop en shutdown_threads_remaining. Normaal enkele seconden;
bij vastlopen circa 5s modelgrace + capturestop + HTTP-timeout/joinmarge.
Exitcode 130 bij Ctrl+C, geen traceback. De API blijft apart draaien.

```bash
pgrep -af 'arecord|detector.monitor'
```

Geen eigen achtergebleven capture/monitor. Controleer zo nodig ook genoteerde
BirdNET-child-PIDs. Een in-flight POST/PUT kan al gecommit zijn: geen
compensatoire databasewrites of verwijderingen.

## Uitgevoerd en nog te bewijzen

Automatische tests: synthetische PCM, threads, echt gespawnde fake-worker,
foutinjectie, bestaande FastAPI-testclient met tijdelijke database/storage.
Geen modeldownload, microfoon of externe API. Dekking: ringwrap, begrenzing,
timing/overlap, IDs, clips/pre-post-roll, retries/uitval, procesopruimen en
onafhankelijkheid bij trage inference/HTTP. Exact resultaat in oplevermelding.

Nog op Pi: echte ALSA-continuiteit, herhaalde native ONNX-inference, RAM/CPU/
temperatuur, langdurige backlog en Ctrl+C met echte BirdNET-processen.
Mocktests bewijzen niet dat de native runtime daar probleemloos blijft draaien.

Open grenzen:
- Geen hardwaretimestamps of automatisch herstel na ALSA-gap.
- Mono-only; stereo/downmix is een latere expliciete keuze.
- Optimale threshold/overlap/buffer/pre-post-roll nog niet vastgesteld.
- Geen duurzame offline queue, retention, speciesmerging of service.
- Wachtende/actieve events kunnen bij stop vervallen; tellers tonen dit.
- Geforceerde native shutdown is een noodpad. Resource-trackerwarnings op de Pi
  vragen onderzoek, geen claim van schone normale afsluiting.
- Defecte DNS-resolutie kan langer duren dan sockettimeout; de daemon-HTTP-thread
  mag daarom procesexit niet blokkeren. Default numerieke localhost vermijdt DNS.
- Geen harde realtime-garantie of bewezen herstel na stroomverlies/OOM.

Lokale verificatie op Windows / Python 3.14.7: pytest -v -W error,
139 tests geslaagd in 7.00s, geen warnings. pip check: geen conflicten.
De bestaande 77 tests zijn behouden; 62 monitortests toegevoegd.
