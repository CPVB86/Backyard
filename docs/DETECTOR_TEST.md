# Geisoleerde BirdNET-diagnostiek (fase 3)

Dit is een **handmatige hardware-/integratietest**, geen 24/7 detector.
Geen Backyard-ingest, audio-upload, SQLAlchemy, systemd of bootconfiguratie.
Capture, WAV-inspectie en modelanalyse zijn afzonderlijke functies/modules.

## Compatibiliteitsonderzoek: 30 september 2026

Doel: Pi 5 / 4 GB, Debian 13 Trixie, Linux aarch64, CPython 3.13.5.

Gecontroleerd via PyPI-metadata, gepubliceerd birdnet-1.1.1-wheel en een
pip wheel-only dry-run voor CPython 3.13 / Linux ARM64:

| Component | Gekozen versie / bewijs |
| --- | --- |
| birdnet | 1.1.1; Python >=3.11; universeel py3-none-any-wheel |
| onnxruntime | 1.30.0; cp313-cp313-manylinux_2_28_aarch64 |
| ai-edge-litert | 2.2.0; cp313-cp313-manylinux_2_27_aarch64 |
| numpy / scipy | 2.5.3 / 1.18.1; cp313 ARM64 wheels |
| pandas / pyarrow | 3.0.6 / 25.0.1; cp313 ARM64 wheels |
| soundfile / psutil | 0.14.0 / 7.2.2; ARM64 wheel / ABI3 ARM64 wheel |

Ook de verdere dependency-resolutie slaagde zonder source builds. Het resultaat
is vastgelegd als constraints-py313-linux.txt. pip evalueert bij cross-platform
resolutie sommige markers op de host: LiteRT is daarom expliciet meegenomen.
De dry-run op de **echte Pi** hieronder is de laatste controle van haar omgeving.
Een wheel is geen bewijs dat modelinference werkt of realtime is op 4 GB.

BirdNET 1.1.1 trekt op Linux/Python 3.13 ook LiteRT mee, hoewel onze adapter ONNX
kiest. We onderdrukken die dependency niet met --no-deps. Geen TensorFlow-,
PyTorch-, CUDA- of repro-extra installeren. Geen downgrade van systeem-Python
en geen compiler/source-build als fallback. Als de Pi-dry-run faalt: stop en
deel de fout; eerst een geisoleerde alternatiefversie onderzoeken.

Bronnen:
[BirdNET 1.1.1](https://pypi.org/project/birdnet/1.1.1/),
[ONNX Runtime 1.30.0](https://pypi.org/project/onnxruntime/1.30.0/),
[LiteRT 2.2.0](https://pypi.org/project/ai-edge-litert/2.2.0/),
[BirdNET modellen](https://github.com/birdnet-team/birdnet/blob/main/docs/models.rst).

## Omgevingen strikt scheiden

- .venv: bestaande Backyard API en pytest. Geen ML-installatie hierin.
- .venv-detector: uitsluitend BirdNET-diagnostiek.
- detector/requirements.txt: eigen dependencyset met constraints.
- .detector-test/: een bewaarde capture en modelcache, genegeerd door Git.
- Live-audio: maximaal een tijdelijk WAV in een eigen systeem-tempdirectory;
  verwijderd na analyse en bij normale exit/Ctrl+C. Een hard kill kan een
  achtergebleven tempdirectory nalaten, maar er wordt geen reeks WAVs opgebouwd.

De hoofdomgeving, requirements.txt, requirements-dev.txt en .env veranderen
niet. Geen databasemigratie nodig. Instellingen zijn expliciete CLI-opties.
BIRDNET_APP_DATA wordt, tenzij zelf ingesteld, voor modelimport naar
.detector-test/model-cache gezet. Geen gebruik van definitieve Birds-storage.

## 1. Update en bestaande tests (Backyard .venv)

Alle volgende shellcommando's horen **op de Pi via SSH**, vanuit de repository-root:

```bash
git switch main
git pull --ff-only origin main
.venv/bin/python -m pytest -v
```

Verwacht 77 tests. Deze fase voegt 21 tests toe zonder echte hardware/model.
De bestaande 56 tests blijven intact; die zijn eerder door de gebruiker op de Pi
geverifieerd. De 77 tests zijn door Codex op Windows/Python 3.14 uitgevoerd.

## 2. OS-tools en microfoon vinden (buiten beide venvs)

Onderstaande apt-opdracht installeert alleen ontbrekende hulpmiddelen.
Codex heeft niets op de Pi geinstalleerd of aan audio-/netwerkconfiguratie veranderd.

```bash
sudo apt-get install alsa-utils libsndfile1 python3-venv
uname -m
python3 --version
getconf GNU_LIBC_VERSION
arecord -l
arecord -L
cat /proc/asound/cards
```

Kies een ALSA-naam uit de huidige uitvoer. Voor de eerder gemelde
USB PnP Sound Device kan die bijvoorbeeld zijn:

```bash
export BIRDNET_DEVICE='plughw:CARD=Device,DEV=0'
```

**Bevestig CARD=Device eerst met arecord -L.** Geen permanent hw:2,0.
Een ALSA-card-ID is minder afhankelijk van cardnummers, maar niet uniek bij twee
identieke USB-devices. USB-serienummer/poortmapping zo nodig later vaststellen;
nu geen udev/ALSA-systeemconfiguratie veranderen. plughw mag het native formaat
naar de aangevraagde rate/kanalen converteren; 48 kHz output bewijst geen native
48-kHz-microfoon.

## 3. Eerste capture-test (geen BirdNET-installatie nodig)

Gebruik systeem-Python; dit commando gebruikt alleen de standaardbibliotheek:

```bash
python3 -m detector.cli capture --device "$BIRDNET_DEVICE"
```

Default: 6 seconden, 48000 Hz, mono, PCM S16_LE WAV.
Uitvoer: .detector-test/capture.wav, grootte, werkelijke duur/rate/kanalen,
peak en RMS in dBFS. 0 dBFS is de digitale maximumamplitude; -inf betekent
exacte stilte. Peak/RMS zijn signaaldiagnostiek, geen soortherkenning of
universele kwaliteitsgrens. Veel samples dicht bij full scale kunnen clipping
betekenen; zeer laag niveau eerst onderzoeken met plaatsing/native mixerinfo.
Het script past geen gain/mixerinstellingen aan.

Luister desgewenst met `aplay .detector-test/capture.wav` als de Pi een
werkende audio-uitgang heeft. Headless playback is geen voorwaarde.

Een bestaande capture wordt standaard beschermd. Bewust opnieuw proberen:

```bash
python3 -m detector.cli capture --device "$BIRDNET_DEVICE" --replace
```

Alleen die vaste diagnostische capture wordt vervangen. Andere opties:
--rate 48000, --channels 1, --duration 6 (1-30 hele seconden).
Capture heeft een timeout en ruimt mislukte/afgebroken eigen opnamen op.

## 4. BirdNET installeren (uitsluitend .venv-detector)

Gebruik expliciete interpreterpaden; activeren/deactiveren is niet nodig.
Controleer eerst dat python3 de verwachte 3.13.5 is.

```bash
python3 -m venv .venv-detector
.venv-detector/bin/python -m pip install --upgrade pip
.venv-detector/bin/python -m pip install --dry-run --only-binary=:all: -r detector/requirements.txt
```

**Alleen als deze dry-run slaagt:**

```bash
.venv-detector/bin/python -m pip install --only-binary=:all: -r detector/requirements.txt
.venv-detector/bin/python -m pip check
.venv-detector/bin/python -c 'import birdnet, onnxruntime; print("ONNX", onnxruntime.__version__, onnxruntime.get_available_providers())'
```

Dit voorkomt ongemerkte ML-compilatie. Een mislukte installatie raakt de
Backyard-venv niet. De modeldownload komt pas bij analyze/live, niet bij capture.

## 5. Eerst single-file analyseren

```bash
.venv-detector/bin/python -m detector.cli analyze .detector-test/capture.wav
```

Gebruikte API: birdnet.load("acoustic", "3.0", "onnx", precision="fp32").
Het v3-model is preview3.1, geen definitieve release. FP32 is gekozen als
CPU-baseline; het ONNX-bestand is 541598502 bytes (ongeveer 517 MiB).
BirdNET controleert de modelhash. Eerste start downloadt model en labels:
internet, vrije opslag en geduld nodig. De werkelijke RAM-behoefte is groter
dan de bestandsgrootte en moet op deze Pi worden gemeten.

Eerst meten, niet automatisch meer workers inzetten: n_workers=1,
n_producers=1, batch_size=1, prefetch_ratio=1, device=CPU.
De bibliotheek documenteert dat een door OOM gekillde worker de inference kan
laten vastlopen. Bij vastlopen/geheugendruk stoppen en onderzoeken; geen
RAM-, swap- of bootaanpassingen door deze tools.

Per venster worden top-5 resultaten getoond, inclusief scores onder 0.60.
Een ster markeert score >= threshold, alle scores blijven 0..1.
Geen extra sigmoid/softmax en geen lokale soortfilter. De uitkomst is een
modelscore, geen onafhankelijk bevestigde waarneming. Controleer bekende audio.

```bash
.venv-detector/bin/python -m detector.cli analyze .detector-test/capture.wav --threshold 0.60 --top-k 5 --overlap 1.5
```

Dit experimenteert met 1.5s overlap **binnen het WAV-bestand**, niet met
doorlopende capture. De default overlap is 0 voor de eenvoudige baseline.

Optioneel --started-at '2026-09-30T10:00:00+02:00' als je de opname-start werkelijk
kent. Geen tijd afleiden uit bestands-mtime. Zonder starttijd toont analyze alleen
de venster-offsets. Capture/live melden de processtart als **geschatte** UTC-start;
USB-/ALSA-opstartlatentie maakt dit nog geen sample-nauwkeurige registratie.

## 6. Pas daarna: diagnostische live-test

Alleen na werkende capture en single-file analyse:

```bash
.venv-detector/bin/python -m detector.cli live --device "$BIRDNET_DEVICE" --duration 6 --rate 48000 --channels 1 --threshold 0.60
```

Ctrl+C stopt de handmatige test. De eigen CLI geeft geen traceback bij normale
KeyboardInterrupt; het gedrag van de echte modelprocessen wordt op de Pi getest.

**Deze loop neemt op, stopt capture, analyseert en wacht 0.5s. Er zijn opnamegaten.**
Hij is uitsluitend diagnostiek en mag geen productieservice worden.
--pause 0 verwijdert alleen de extra pauze, niet de analyse-/opstartgaten.
Er is maximaal een tijdelijke WAV en geen onbeperkte queue. Elk predict()-call
maakt volgens BirdNET een inference-session: de gemeten analysetijd bevat dus
ook sessie-overhead. Die verhouding analyse/audio is geen bewijs van 24/7-capaciteit.

## Vensters en latere continue productiearchitectuur (nog niet gebouwd)

Onderzocht in birdnet 1.1.1:
- Acoustic v3 standaardvenster W=3s, 96000 samples op 32 kHz.
- 48-kHz-input wordt door het pakket geresampled; 6s is geen modelvereiste.
- overlap_duration_s is instelbaar: hop H = W - overlap; 0 <= overlap < 3.
- Resultaatvelden start_time/end_time zijn seconden relatief aan de input;
  end_time wordt begrensd op de bestandsduur. Een classificatie lokaliseert de
  vogelroep niet exact binnen dat venster.
- Production-optimum is niet vastgesteld: bv. 1.5s overlap is een te meten
  hypothese die grensproblemen kan verminderen, geen gegarandeerde oplossing.
  Meer overlap verhoogt rekenlast; een roep op een bestandsgrens blijft in deze
  diagnostische loop kwetsbaar, ook met overlap binnen de bestanden.

Doelarchitectuur voor een volgende fase:

```text
continue capture -> begrensde PCM-ringbuffer (sample-indexen)
                       |                         |
                       v                         +-> clip-extractie na resultaat
              begrensde window-queue                (pre/post-roll later kiezen)
                       |
                  inference-worker
                       |
            getimede kandidaatwaarneming
                       |
           later bestaande Backyard HTTP-ingest
```

Capture mag nooit wachten op inference, HTTP, file-upload of modeldownload.
Model vooraf laden. Queue-items bevatten verwijzingen naar beschermde sample-
ranges, niet onbeperkt nieuwe WAVs. Houd benodigde samples tijdelijk vast of
kopieer ze naar een eveneens begrensd window-/clipbuffer voordat de ring
overschrijft. Post-roll kan vereisen dat clip-extractie op toekomstige samples
wacht; blokkeer daarvoor niet de capture-thread.

Timing die de volgende fase moet vastleggen:
stream/session-ID, device-identiteit, originele sample rate, monotone sample-
index van window-start/einde, UTC-anchor met monotone klok, model-/adapterversie,
window/hop-instellingen en expliciete gaps. UTC = anchor + sample-index/rate;
corrigeer/registreer klokdrift en NTP-sprongen zonder de samplevolgorde te verliezen.
Na resampling offsets terugkoppelen naar de oorspronkelijke capture-tijdlijn.
Bewaar venster-offsets, niet een verzonnen exact roepmoment.

**Retry is niet hetzelfde als overlap.** Een stabiele event-ID identificeert
een producer-event (bv. session/window/species/model), ook na retries.
Twee overlappende windows behouden ieder hun eigen identiteit en timing.
Niet alle waarnemingen van dezelfde soort binnen X seconden samenvoegen.
Latere interpretatie kan overlappende kandidaten relateren, maar moet de
oorspronkelijke vensters en audio kunnen terugvinden. Het bestaande contract
heeft event_id, source/source_version/model_version en raw_metadata hiervoor;
er wordt nu niets gepost of geupload.

Backpressure moet meetbaar en begrensd zijn:
- Meet analysetijd S, hop H, queue-diepte, leeftijd oudste job, drops/gaps,
  ring-overruns, RAM en clip-/uploadachterstand.
- Voor een worker is duurzaam gemiddeld S < H nodig (met marge). W=3 en
  H=1.5 verdubbelt het aantal windows versus H=3. S/audiofileduur uit deze
  diagnostische test vervangt die productiecapaciteitsmeting niet.
- Ringcapaciteit moet ten minste het analysevenster + maximale resultaatlatentie
  + gewenste pre-roll/post-roll + veiligheidsmarge dekken; resultaatlatentie
  omvat queue-wachten. Nog geen definitieve pre/post-roll of buffergrootte.
- Rekenvoorbeeld, geen instelling: 60s mono PCM16 op 48 kHz is 5.76 MB;
  float32 verdubbelt dit. Model-RAM, queues en beschermde clips komen erbij.
- Bij structureel tekort helpt een grotere buffer slechts tijdelijk. Kies later
  een expliciet beleid (bv. oudste nog niet gestarte jobs laten vallen om recent
  te blijven), registreer de verloren sample-intervallen en behoud capture.
  Geen stille drops en geen onbegrensde backlog. Reeds benodigde clipdata mag
  niet onopgemerkt worden overschreven.

Er wordt geen volledige 24/7-stream permanent bewaard. Pas later worden voor
geaccepteerde kandidaten korte clips uit de tijdelijke buffer gehaald en via
de bestaande JSON POST + audio PUT opgeslagen. Daarvoor hoeft geen tweede
ingestarchitectuur te worden ontworpen.

## Uitgevoerde versus nog uit te voeren controles

Uitgevoerd: metadata/wheelonderzoek inclusief volledige cross-target
dependency-resolutie; 77 repositorytests zonder ML-installatie, inclusief
PCM-niveaus, resultaatcontract, overlapbehoud, CLI-validatie en fout-/Ctrl+C-
opruiming. Help/capture importeren BirdNET niet; geen core/API-code gewijzigd.

Nog niet uitgevoerd: Linux-installatie van deze detectorvenv, echte ALSA-capture,
modeldownload, echte BirdNET-inference, RAM/CPU/temperatuur/realtime-meting,
Ctrl+C tijdens echte inference en continu monitoren. De hardwaretest volgt
samen op de Pi. Succesvolle wheel-resolutie is geen claim dat deze checks slagen.
