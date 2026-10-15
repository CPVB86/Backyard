# Optionele BirdNET-Go vogelbron, met Python-terugval

De bestaande Python-detector blijft geinstalleerd. Alleen expliciet omschakelen
activeert Go. Geen database-/policy-/Avian-migratie en geen nieuwe dependencies.
Gebruik de bestaande `.venv-detector`: de bridge laadt alleen de bestaande geo
3.0-provider uit birdnet 1.1.1, niet het akoestische Python-model.

## Contract en bewuste verschillen

- Go verzorgt continue capture, inference, tijdelijke clips en aggregatie.
- De bestaande HTTP-ingest, Bearer-auth, geo/seizoensprovider, reviewpolicy,
  Backyard-audioretentie, Generator en AvianVisitors blijven verantwoordelijk.
- Met toestemming voor deze bouwfase: **een Go-event = een support**. Geen
  reconstructie van inferencevensters uit een hitcount of overlappende Go-events.
  Hierdoor zullen sommige events minder snel automatisch worden geaccepteerd.
- Het voorbeeld gebruikt Go's ingebouwde **BirdNET V2.4**, niet Python acoustic
  3.0 preview. Confidence en soortuitkomsten kunnen verschillen. De provenance
  vermeldt het daadwerkelijke model; presenteer deze proef niet als een identieke
  modelvergelijking. Bij latere modelwisseling ook MODEL_VERSION aanpassen.
- Go export vereist minimaal 10s; hier WAV PCM16 mono 48kHz, length=10 en
  precapture=4. BeginTime is het startpunt van de clip; EndTime-BeginTime is
  6s in deze configuratie. Dit is een upstream eventspan, geen nauwkeurig gemeten
  vogelroep. De volledige onbewerkte clip gaat door bestaande audio-ingest.
- Geo-filtering blijft in Backyard. Go range threshold=0 laat ook exoten door;
  dynamische drempels, daglicht-/honden- en minimum-hitfilters staan uit.
  Go privacyfilter blijft aan: daarmee kan Go detecties onderdrukken.
- Geen herimport van oude Go-demo-events: eerste bridge-start bewaart een UTC
  startgrens. Start pas nadat de Python-detector gestopt is. De korte opstartgap
  is bewust toegestaan. Checkpoint en instance-ID nooit wissen bij terugval.
- Go bewaart zijn eigen operationele DB en tijdelijk geexporteerde clips. Dit is
  geen tweede Backyard-archief; gebruik de begrensde Go-retentie. Monitor schijf/RAM.

Een aparte `backyard-birds-go` container (poort 8082) voorkomt dat terugval de
werkende bat-container/config hoeft te wijzigen. Beide containers gebruiken de
reeds gekozen release 20261014. Op 4GB RAM daadwerkelijk belastbaarheid meten.
Tijdelijk bats stoppen mag, maar is geen automatische stap in de switchtool.

## Pi: voorbereiden zonder bestaande opname te stoppen

```bash
cd /home/cpvb86/Backyard
git pull --ff-only
mkdir -p "$HOME/backyard-birds-go/config" "$HOME/backyard-birds-go/data"
# Alleen bij eerste installatie; bestaande configuratie nooit overschrijven.
if [ ! -e "$HOME/backyard-birds-go/config/config.yaml" ]; then
  cp deploy/birdnet-go-birds.yaml.example "$HOME/backyard-birds-go/config/config.yaml"
fi
```

Vergelijk in het voorbeeld `device`, samplerate en overlap met je bestaande
`BACKYARD_MONITOR_DEVICE/RATE/OVERLAP`. Voor deze Pi is de vogelmicrofoon
`plughw:CARD=Device,DEV=0`, 48000 Hz. VleermicUSB384k blijft uitsluitend voor bats.
Controleer ook Go threshold tegen policy review_lower (huidig 0.60), en de locatie.

Voeg met `sudoedit /etc/backyard/backyard.env` deze regels toe. Behoud de bestaande
token-, geo-, microfoon- en opslagregels:

```ini
BACKYARD_GO_BIRD_URL=http://127.0.0.1:8082
BACKYARD_GO_BIRD_INSTANCE=pi-birds-go-1
BACKYARD_GO_BIRD_STATE=/home/cpvb86/Backyard/data/bird-go-bridge.json
BACKYARD_GO_BIRD_MODEL_VERSION=BirdNET-Go-20261014-BirdNET_V2.4
```

```bash
sudo docker pull ghcr.io/tphakala/birdnet-go:20261014
# CREATE, niet RUN: de vogelmicrofoon blijft nu nog bij Python.
# Slechts eenmaal; een bestaande container/database niet vervangen.
sudo docker create --name backyard-birds-go --restart=no \
  --device /dev/snd:/dev/snd \
  -p 127.0.0.1:8082:8080 \
  -e TZ=Europe/Amsterdam \
  -e BIRDNET_UID="$(id -u)" -e BIRDNET_GID="$(id -g)" \
  -v "$HOME/backyard-birds-go/config:/config" \
  -v "$HOME/backyard-birds-go/data:/data" \
  --log-opt max-size=10m --log-opt max-file=3 \
  ghcr.io/tphakala/birdnet-go:20261014

sudo install -m 644 deploy/systemd/backyard-birdnet-go.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/backyard-birds.service /etc/systemd/system/
sudo systemctl daemon-reload
.venv/bin/python -m operations.bird_source go
```

De laatste opdracht is alleen dry-run. Geen pip-upgrade, API-herstart of migratie
nodig. De nieuwe vogelbridge gebruikt bestaande `.venv-detector` dependencies.

## Omschakelen en controleren

```bash
sudo .venv/bin/python -m operations.bird_source go --apply
sudo systemctl status backyard-birdnet-go backyard-birds backyard-detector --no-pager -l
sudo journalctl -u backyard-birds -n 50 --no-pager
sudo docker logs --since 10m backyard-birds-go 2>&1 | grep -Ei 'pipeline stats|error|failed|capture'
```

De tool stopt en disablet Python, start Go, wacht op Go API en een verse succesvolle
bridge-poll met actieve geo en enablet dan de Go-units voor boot. Bij een opstartfout
stopt/disablet hij beide Go-units en enablet/start hij Python. De terugval controleert eerst dat de Go-container echt gestopt is. Als de terugval zelf
faalt, eindigt het commando met een fout; het meldt dan geen succesvolle terugval.
De switch kan enkele minuten duren wegens geo-initialisatie.

Go draait onder systemd met `--restart=no` in Docker. Conflicts/ordering sluiten
Python en beide Go-units uit; de bridge gebruikt ook dezelfde Linux-monitorlock.
Start deze container daarom niet handmatig met `docker start` naast Python.

Controleer na vijf minuten:
- Python inactive/disabled; Go capture en bridge active/enabled.
- Go pipeline stats met de vogelbron, juiste model en inferences > 0.
- `bird_bridge_status`, phase=running, geo.active=true; geen retrying.
- Een echte `bird_imported`, correcte status/audio in Backyard en, indien accepted,
  dezelfde soort in Avian. Geen synthetische productiewaarnemingen nodig.
- RAM/schijf en beide microphones: `free -h`, `df -h`, `sudo docker stats --no-stream`.

```bash
sudo .venv/bin/python -m operations.status --hours 1
```

Operations herkent de actieve bron en blijft expliciet waarschuwen dat alleen
bridge-connectiviteit geen capture/inference/24h-bewijs levert. Het oude
Python-journal blijft beschikbaar als historische informatie.

Op het Pi-bureaublad: http://127.0.0.1:8082/ui/dashboard voor vogels,
8081 voor bats. Vanaf Windows zo nodig een aparte SSH-tunnel voor 8082.

## Verplichte terugvalproef

```bash
cd /home/cpvb86/Backyard
sudo .venv/bin/python -m operations.bird_source python --apply
sudo systemctl status backyard-detector backyard-birds backyard-birdnet-go --no-pager -l
sudo journalctl -u backyard-detector --since '3 minutes ago' --no-pager
```

Controleer echte nieuwe `monitor_status` met oplopende samples_captured en
windows_processed en geo.active=true. Alleen active/running bewijst dat nog niet.
Go-vogelcapture/bridge moeten inactive/disabled zijn; bats blijven actief.
Daarna desgewenst opnieuw `... operations.bird_source go --apply`.

Noodroute, als de CLI niet beschikbaar is:

```bash
sudo systemctl disable --now backyard-birds.service
sudo systemctl disable --now backyard-birdnet-go.service
sudo docker stop backyard-birds-go
sudo systemctl enable --now backyard-detector.service
```

Geen DB, audio, Python-venv, Go-volume of checkpoint verwijderen. Events die Go voor
terugval al had vastgelegd kunnen bij hervatting nog idempotent worden verwerkt;
Python heranalyseert die oude audio niet. Er kunnen korte opnamegaten bij de
omschakeling optreden. Opstartrollback is geen permanente automatische failover
bij latere hardwarestoringen; gebruik dan de expliciete terugvalroute.

## Verificatie en bron

Softwaretests gebruiken de echte Backyard API en fake Go-records/WAVs en toetsen
geo/policy, retry/restart/idempotentie, audio en switch-/rollback-commandovolgorde.
Gerichte regressieset: 243 passed, 2 skipped (Linux-specifiek) op Windows.
Windows kan de Linux systemd/ALSA-terugval niet uitvoeren; bovenstaande Pi-proef
blijft verplicht voordat de overstap bewezen is.

Releasecontracten:
- https://github.com/tphakala/birdnet-go/blob/20261014/internal/api/v2/detections/detections.go
- https://github.com/tphakala/birdnet-go/blob/20261014/internal/analysis/processor/processor.go
- https://github.com/tphakala/birdnet-go/blob/20261014/internal/conf/config.yaml
