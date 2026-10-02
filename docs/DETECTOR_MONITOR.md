# Continue Backyard Birds-monitor

Start handmatig met `python -m detector.monitor`. Dit is de nieuwe continue
productiearchitectuur. Capture en 50% overlap zijn op de echte Pi bewezen.
De observation-policyfase voegt schema 2 en review toe, zonder nieuwe dependencies.
Zie [OBSERVATION_POLICY.md](OBSERVATION_POLICY.md) voor de volledige actuele
Pi-acceptatie, policy, migratie en review. De actuele 24/7-installatie en duurtest
staan in [OPERATIONS.md](OPERATIONS.md).

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
                                bounded policyqueue
                                          |
                         domain + plausibility + aggregatie
                                          |
                          permanent / review / discard
                                          |
                                bounded clipqueue
                                          |
                         extractor leest ring, wacht post-roll
                                          |
                               bounded outboundqueue
                                          |
                              eigen HTTP-uploadthread
                                          |
                             POST observation -> PUT WAV
```

Capture doet korte raw PCM16-reads en RAM-writes. De ringlock beschermt alleen
RAM-kopieen; geen inferentie, HTTP, modeldownload of clipbestand onder die lock.
Scheduler, inferentie, policy, clipextractie en HTTP hebben afzonderlijke threads.
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
venster, niet het exacte begin/einde van een roep. Observation start/end zijn
de geschatte grenzen van de samenhangende resultaatvensters.

Candidate-metadata bewaart stream-ID, device, rate/format, anchors, window- en
resultaatsamples, lokale modeloffsets, model/backend/precision, hop en clipbereik.
Een nieuwe start krijgt een nieuwe stream-ID.

CLI-default blijft window=3, overlap=0, hop=3 voor compatibiliteit.
De bewezen productieconfiguratie gebruikt expliciet overlap=1.5, hop=1.5.
Overlap kan grensgevallen verbeteren maar garandeert geen herkenning. De
nieuwe aggregatie combineert werkelijk overlappende supports, met begrensde
eventduur en behoud van individuele IDs; zie OBSERVATION_POLICY.md.

De event-ID is deterministisch uit stream/window/resultaat/label/model.
HTTP-retries gebruiken exact dezelfde payload en WAV. Een volgend overlappend
venster heeft een eigen candidate-ID, ook als het dezelfde observation steunt; API-retry en overlappende roepen zijn verschillende zaken.

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
Clip = geaggregeerd eventbereik plus context. Bij streamstart wordt pre-roll op sample 0
begrensd; metadata bevat het echte bereik. Post-roll wacht in de clipqueue.
Bij overschreven clipbegin vervallen clip EN nog niet geposte kandidaat,
met clips_expired/ring_overruns. Geen verkeerde/gedeeltelijke audio uploaden.

Clipqueue: 32 metadata-items. Outboundqueue: 16 WAVs, bij 9s event + 2s context
op 48kHz maximaal circa 16.9 MB
plus actieve upload. Lage thresholds kunnen veel kandidaten per window geven;
daarom zijn ook deze queues begrensd. Queuegroottes max 64, ring max 300s.

Ring moet minstens max_event + idle + pre/post-roll bevatten (default 16s),
maar praktisch bovendien
maximale queue-/inferentievertraging plus veiligheidsmarge. 60s is een startwaarde.
Model-RAM en queues komen erbij. Meer buffer lost structureel te trage analyse niet op.

HTTP: standaard 2s timeout, maximaal 3 pogingen, onderbreekbare backoff 0.5/1s.
Netwerkfout, 408, 429 en 5xx worden herhaald; o.a. 409/422 zijn terminaal.
Observation-response maximaal 512 KiB, geen redirects. Na POST-succes alleen PUT herhalen.
Na verloren POST-response exact dezelfde POST herhalen: server-idempotency
voorkomt dubbele records.

Bij offline API blijven capture/inferentie werken. Na retries vervalt het item;
bij volle outboundqueue vervallen oude wachtende clips. GEEN duurzame offline
replay. Als POST slaagde en PUT definitief faalt, blijft een observation
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
| --threshold | 0.60; mag policy review_lower niet overschrijden |
| --window | 3; andere lengtes in deze baseline geweigerd |
| --overlap | 0; 0 <= overlap < 3; hop in hele capturesamples |
| --ring-seconds | 60; max 300 |
| --inference-queue | 4; 1..64 |
| --policy-queue | 4; 1..64 |
| --geography | verplicht bij inference; latitude/longitude vereist |
| --latitude / --longitude | leeg; echte coordinaten verplicht bij geography |
| --clip-queue | 32; 1..64 |
| --outbound-queue | 16; 1..64 |
| --pre-roll / --post-roll | elk 1; elk 0..10 |
| --api-url | http://127.0.0.1:8010; origin zonder credentials/path |
| --http-timeout | 2; 0.1..5s |
| --attempts | 3; 1..5 |
| --status-seconds | CLI 10; production environment 30; 1..60s |
| --inference-timeout | 60; 5..300s; vastgelopen call stopt voor serviceherstel |
| --lock-file | projectroot/data/monitor.lock; Linux singleton, niet verwijderen tijdens run |
| --capture-only | alleen RAM-capture, geen model/jobs/HTTP |

Modelcache: .detector-test/model-cache, of bestaande BIRDNET_APP_DATA.
Geen dependencybestanden gewijzigd. Expliciete migratie naar schema 2 vereist.

## Actuele Pi-procedure en status

Volg [de volledige observation-acceptatie](OBSERVATION_POLICY.md#volledige-raspberry-pi-acceptatie):
stop monitor/API, pull main, installeer API-devrequirements, pip check,
expliciete migratie, pytest, API, geo-controle, monitor met 3s/1.5s/0.60,
werkelijke audio en synthetische review/confirm/reject/bat/chimpansee-cases.
De oude /api/birds/latest toont alleen het historische contract; gebruik
voor nieuwe monitorresultaten /api/observations.

Capture-only blijft beschikbaar zonder ML of API:

```bash
python3 -m detector.monitor --capture-only --device 'plughw:CARD=Device,DEV=0'
```

Status bevat de bestaande capture/inference/HTTP-metingen plus de policy-,
review- en aggregatietellers uit OBSERVATION_POLICY.md. Normale ringwrap is
geen overrun; ring_overruns telt verloren benodigde windows/clips.
realtime_ratio = gemiddelde inference / hop. Streef ruim onder 1 met geen
oplopende backlog, capturegaps, overruns of drops.

Ctrl+C annuleert capture/inference en stopt begrensd. Geen volledige drain:
windows, policybatches, actieve aggregaties, clips en uploads kunnen worden
verlaten en worden geteld. Exitcode 130 zonder traceback is normaal.
Een in-flight HTTP-write kan al geslaagd zijn; geen compensatoire deletes.

## Verificatie en grenzen

Vorige fase door gebruiker bewezen op Pi5/Python 3.13.5: 139 tests, continue
48kHz mono-capture, 3s/1.5s overlap, circa 0.31s inference en geen gaps/drops.
De nieuwe policyfase is lokaal getest met synthetische PCM, threads,
FastAPI/SQLite/storage, procesopruiming en foutinjectie. De nieuwe native
geo-runtime en langdurige totale belasting moeten opnieuw op Pi worden getest.

Geen hardwaretimestamps, automatische ALSA-gaprecovery, stereo/downmix,
duurzame offline queue of power-loss-garantie. Systemd/autostart staat in OPERATIONS.md.
Backpressure verliest expliciet oud werk; begrenzing maakt verlies niet onmogelijk.
Onbeoordeelde reviewaudio wordt niet stil verwijderd; bewaak reviewvoorraad en
schijfruimte. Native forced-stop is een noodpad, geen bewijs van normale cleanup.
Defecte DNS-resolutie kan sockettimeout overschrijden; daemon-HTTP-thread mag
exit niet blokkeren. Numerieke localhost vermijdt DNS in de standaardopstelling.

Productie vereist nu expliciete geo-config; zie [geo-herstel](GEO_OPERATIONS.md).
