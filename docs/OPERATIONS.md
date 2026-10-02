> Productiemonitoring vereist geo=1 en geldige latitude/longitude. Zie [geo-configuratie en verificatie](GEO_OPERATIONS.md).

# Backyard 24/7 op Raspberry Pi 5

Doel: Debian 13 ARM64, Python 3.13.5, user `cpvb86`, bestaande repository
`/home/cpvb86/Backyard`, API-venv `.venv`, ML-venv `.venv-detector`.
De observation/policyfase is functioneel gereed. De dag/nacht-duurtest hieronder
is tegelijk de praktijkacceptatie daarvan; er is geen extra voorafgaande
handmatige policy-acceptatiefase nodig.

## Wat wordt geïnstalleerd

Bronnen in `deploy/systemd/`:

- `backyard-api.service`: directe API-Python-executable, één Uvicorn-worker.
- `backyard-detector.service`: directe detector-Python-executable en monitor-CLI.
- `backyard.env.example`: één gedeeld productie-environmentbestand.

Beide services draaien als **cpvb86**, met absolute executablepaden en
WorkingDirectory=/home/cpvb86/Backyard. Ze schrijven stdout/stderr naar journal.
Geen shellwrapper, automatische git pull, pip-install, migratie, cleanup of
database-reset bij start. Bestaande historische data/audio blijven behouden.

### Ordering en herstel

API wil network-online.target en wacht daarop; beide units vereisen de mount
van de repository. Dat is bootordering, geen garantie dat internet beschikbaar
is. Normale monitoring gebruikt lokaal gecachte modellen en localhost-HTTP.

API ExecStartPost wacht maximaal 60s op echte health/database-ready. Detector
heeft Wants + After op API, plus een eigen readinesscontrole vóór de monitor.
Die controle verifieert ook dezelfde policyfingerprint. API-uitval tijdens
capture stopt de detector niet; bestaande begrensde HTTP-retries blijven werken.
Na definitief mislukte upload is er geen duurzame replay.

Beide gebruiken Restart=on-failure. API wacht 10s, detector 30s tussen pogingen.
StartLimitIntervalSec=0 voorkomt een permanente start-limit-dead-state bij
tijdelijk ontbrekende USB/API. Een blijvende configuratiefout blijft met deze
vertraging herhalen en moet worden opgelost; geen snelle onbeperkte fork-loop.

Type=exec controleert dat de executable werkelijk gestart is. Bij de detector
betekent active nog niet dat modelwarmup/capture gereed is: daarvoor moeten
verse metrics en toenemende windows_processed zichtbaar zijn. Bestaande
BirdNET-startupdeadline blijft 180s; de unit-starttimeout van 75s begrenst de
60s ExecStartPre, niet de warmup in het reeds gestarte hoofdproces.

SIGTERM geeft de monitor nu een nette shutdown, ook tijdens modelstartup.
KillMode=mixed laat eerst het hoofdproces opruimen; resterende processen in de
service-cgroup worden bij beëindiging/timeout door systemd opgeruimd. TimeoutStopSec
is 30s en SendSIGKILL blijft aan. `setsid()` in ALSA/BirdNET verandert geen
cgroup-lidmaatschap. Daardoor ontsnappen deze subprocessen niet aan een
service-stop nadat het hoofdproces hard is gecrasht.
Bronnen: [Debian systemd.service](https://manpages.debian.org/trixie/systemd/systemd.service.5.en.html),
[systemd.kill](https://manpages.debian.org/trixie/systemd/systemd.kill.5.en.html),
[start limits](https://manpages.debian.org/trixie/systemd/systemd.unit.5.en.html).

Een vastgelopen inferencecall van meer dan 60s stopt de monitor met foutstatus,
zodat systemd kan herstellen. Dit is ruime marge boven de bewezen circa 0.31s
en verandert geen confidence/observation-policy. Instelbaar 5..300s.

Een Linux flock op `data/monitor.lock` voorkomt twee monitor-entrypoints tegelijk,
ook een handmatige start naast systemd. Een stale PID-tekst na SIGKILL blokkeert
niet: de kernel-lock wordt vrijgegeven. Verwijder/verplaats de lockfile niet
tijdens capture en override het lockpad niet om een tweede proces te starten.
Losse diagnostische capture-tools/andere audio-apps gebruiken die lock niet:
start die niet tegelijk met de productiemonitor.

### USB en permissies

`plughw:CARD=Device,DEV=0` gebruikt de ALSA-card-ID in plaats van een numeriek
cardnummer. Die blijft bruikbaar als nummer 0/1 bij boot wisselt. De ID is
geen USB-serienummergarantie: bij meerdere identieke USB-audioapparaten kunnen
IDs ambigu worden. Controleer dan `arecord -l`, `arecord -L` en
`/proc/asound/cards`, en pas het configureerbare device aan. Geen nieuwe udevregels.

De detector krijgt SupplementaryGroups=audio, dus geen root en geen afhankelijkheid
van een interactieve login-ACL. Er is bewust geen PrivateDevices=yes.
Ontbrekende/losgeraakte microfoon leidt via bestaande ALSA-foutdetectie tot een
zichtbare fout en herstart na de vertraging. Er is geen ConditionPathExists die
een ontbrekend apparaat stil overslaat zonder retry.

PrivateTmp, NoNewPrivileges, ProtectSystem=full en UMask=0027 zijn ingesteld.
De bestaande home/data/cachepaden blijven voor cpvb86 beschrijfbaar.
CPU/RAM/tasks-accounting omvat subprocessen; geen te krappe MemoryMax die het
ML-model willekeurig doodt. Coredumps zijn uitgeschakeld om grote modeldumps
te vermijden. Bestaande audio wordt niet gechmod of verplaatst.

## Productieconfiguratie

`/etc/backyard/backyard.env` is de enige nieuwe runtimeconfig. Het wordt
expliciet uit het voorbeeld geïnstalleerd en niet bij iedere deploy overschreven.

Belangrijkste waarden:

| Instelling | Productiewaarde |
| --- | --- |
| Device | plughw:CARD=Device,DEV=0 |
| Rate/channels | 48000 / mono |
| Window/overlap/hop | 3.0 / 1.5 / 1.5 seconden |
| Base threshold | 0.60 |
| Ring/context | 60s / pre 1s, post 1s |
| Inference/policy/clip/outbound queue | 4 / 4 / 32 / 16 |
| HTTP | localhost:8010, timeout 2s, 3 pogingen |
| Statusinterval | 30s |
| Inference-stallgrens | 60s |
| API | 0.0.0.0:8010, één worker |
| Modelcache | /home/cpvb86/Backyard/.detector-test/model-cache |

Geen nieuwe policy/default-thresholds. Bestaande root `policy.json` wordt
automatisch door beide processen gelezen. Bij een aangepast BACKYARD_POLICY_PATH
in de API-.env moet hetzelfde absolute pad in het gedeelde servicebestand staan,
zodat de detector het ook ziet. Detector leest API-.env niet.

Bestaande database/storageinstellingen in API-.env blijven geldig; het voorbeeld
zet hiervoor bewust geen overrides. Als je overrides toevoegt, gebruik de
**bestaande** absolute paden. Gebruik in serviceconfig geen ~ of shellvariabelen.
Het statuscommando leest dezelfde serviceconfig en API-.env.

Geo blijft de bestaande keuze: zonder echte ingestelde coördinaten is het
signaal unknown. Gebruik je al geo, zet dan GEOGRAPHY=1 plus de bestaande echte
LATITUDE/LONGITUDE in backyard.env. Vul geen fictieve locatie in. Op de eerste
start kunnen modeldownloads nodig zijn; bestaande caching blijft behouden.
Pas UVICORN_PORT en BACKYARD_MONITOR_API_URL samen aan als je bewust van 8010 afwijkt.

EnvironmentFile-notatie: één KEY=value per regel, optioneel quotes voor spaties,
losse commentaarregels. Geen export, backslashes, commandosubstitutie of variabele-
expansie. Deze eenvoudige subset wordt ook door de status-CLI gecontroleerd.
Geen secrets in de unitbestanden; eventuele lokale configuratie blijft buiten Git.

## Installatie: exact op de Pi uitvoeren

Voer de blokken achtereenvolgens uit, en ga alleen verder als de vorige stap
slaagt. De services worden niet vanaf Windows geïnstalleerd of bestuurd.

### 1. Stop handmatige processen, update en test

Stop een bestaande handmatige monitor en API met Ctrl+C in hun eigen terminals.
Als deze services later al geïnstalleerd zijn, stop je beide eerst met:

```bash
sudo systemctl stop backyard-detector backyard-api
```

Bij de eerste installatie bestaat die service-stop nog niet; Ctrl+C is dan
de juiste stap. Controleer dat er geen oude monitor/arecord/API achterblijft:

```bash
cd /home/cpvb86/Backyard
pgrep -af 'detector.monitor|uvicorn app.main:app|arecord'
git status
git switch main
git pull --rebase origin main
source .venv/bin/activate
python --version
python -m pip install -r requirements-dev.txt
python -m pip check
pytest -v -W error
.venv-detector/bin/python -m pip check
```

Geen requirements gewijzigd. De twee echte POSIX-procestests die op Windows
worden overgeslagen moeten op de Pi ook draaien. Los testfouten op vóór deployment.
Bewaar lokale Git-wijzigingen; gebruik geen reset/force.

Controleer bestaande opslag en migreer expliciet, met alle writers gestopt:

```bash
python - <<'PY'
import os
from pathlib import Path
from operations.environment import read_environment
from app.core.config import Settings
from app.core.migrate import main as migrate
service_env = Path("/etc/backyard/backyard.env")
if service_env.exists():
    os.environ.update(read_environment(service_env))
settings = Settings()
print("Bestaande database:", settings.resolved_database_path)
print("Bestaande audio-root:", settings.resolved_storage_root)
print("Afwijkend policy-pad:", settings.policy_path)
migrate()
PY
```

Schema 2 blijft ongewijzigd in deze operationsfase. Als de vorige fase nog niet
op de Pi stond, maakt die migratie eerst een backup. Verwijder geen bestaande
database/audio. Kies bij serviceconfig dezelfde paden als hierboven.

### 2. Controleer ALSA en bekijk te installeren bestanden

```bash
command -v arecord
getent group audio
cat /proc/asound/cards
arecord -l
arecord -L
ls -l /dev/snd
cat deploy/systemd/backyard-api.service
cat deploy/systemd/backyard-detector.service
cat deploy/systemd/backyard.env.example
```

Verwacht de bedoelde USB-card-ID Device en capturedevice DEV=0. Geen nieuwe
testopname of handmatige detector starten naast de komende service.

### 3. Installeer config en units expliciet

```bash
sudo install -v -d -m 0750 -o root -g "$(id -gn cpvb86)" /etc/backyard
if ! sudo test -e /etc/backyard/backyard.env; then
  sudo install -v -m 0640 -o root -g "$(id -gn cpvb86)" \
    deploy/systemd/backyard.env.example /etc/backyard/backyard.env
fi
sudoedit /etc/backyard/backyard.env
sudo install -v -m 0644 -o root -g root \
  deploy/systemd/backyard-api.service /etc/systemd/system/backyard-api.service
sudo install -v -m 0644 -o root -g root \
  deploy/systemd/backyard-detector.service /etc/systemd/system/backyard-detector.service
sudo systemd-analyze verify \
  /etc/systemd/system/backyard-api.service \
  /etc/systemd/system/backyard-detector.service
sudo systemctl daemon-reload
sudo systemctl enable backyard-api.service backyard-detector.service
sudo systemctl start backyard-api.service
sudo systemctl start backyard-detector.service
```

Genereer/configureer ook BACKYARD_API_TOKEN zoals hieronder; een lege waarde blokkeert API-start.
Controleer in sudoedit device, eventuele eerdere geo-instellingen, policy-pad
en eventuele bestaande database/storage-overrides. Houd defaults voor bewezen
48kHz/3s/1.5s/0.60. Zet geen monitorvariabelen in API-.env.

Controleer voortgang; modelwarmup kan enkele minuten duren:

```bash
systemctl is-enabled backyard-api backyard-detector
systemctl status backyard-api backyard-detector --no-pager --full
curl -H "Authorization: Bearer $BACKYARD_API_TOKEN" --fail http://127.0.0.1:8010/api/health
sudo journalctl -u backyard-detector -n 30 --no-pager
sudo /home/cpvb86/Backyard/.venv/bin/python -m operations.status
```

Verwacht enabled, active/running, health ok, cpvb86, verse monitorstatus met
phase=running, samples_captured en windows_processed oplopend. Een ATTENTION
over nog ontbrekende 24u-historie is op de eerste dag normaal.
Andere ATTENTION-regels wel beoordelen. De services draaien als cpvb86;
sudo bij status is uitsluitend voor read-only toegang tot het volledige journal.

## Start, stop, restart en logs

```bash
sudo systemctl start backyard-api
sudo systemctl stop backyard-api
sudo systemctl restart backyard-api

sudo systemctl start backyard-detector
sudo systemctl stop backyard-detector
sudo systemctl restart backyard-detector

systemctl status backyard-api backyard-detector --no-pager --full
sudo journalctl -u backyard-api -f
sudo journalctl -u backyard-detector -f
```

Dit zijn afzonderlijke beheercommando's, niet een blok om achter elkaar als
installatiestap uit te voeren. Ctrl+C bij journalctl stopt alleen het meekijken.
Een bewuste systemctl stop wordt niet automatisch teruggedraaid.
Stop beide voor onderhoud: detector eerst, API daarna; start API eerst.
Als alleen API stopt, blijft capture actief en zullen uploads tijdelijk mislukken.

Journal is de primaire logging, status elke 30s, geen access-log per request.
Bestaande bounded ML-tempdirectory en warninglogging blijven behouden.
Globale journald-config/retention worden niet veranderd:

```bash
sudo journalctl --disk-usage
sudo journalctl --list-boots
sudo systemd-analyze cat-config systemd/journald.conf
ls -ld /var/log/journal /run/log/journal
```

Een ontbrekende directory is op zichzelf geen fout. Storage=auto kan vluchtige
logging gebruiken als /var/log/journal ontbreekt; dan verdwijnt eerdere historie
bij reboot. Rotatie/retention kan eveneens historie wegnemen. Bekijk dat voor
de duurtest. Geen automatische wijziging of vacuum door Backyard.
De status-CLI meldt te korte/stale historie; een groen huidig proces bewijst
geen volledige nacht. Bewaar zo nodig expliciet begin/eindrapporten hieronder.

## Reboot-acceptatie

Zorg dat de twee services eerst werken en enabled zijn. Voer daarna uit:

```bash
sudo reboot
```

Na opnieuw SSH-inloggen **geen applicatie handmatig starten**. Laad voor curl eerst
het token zoals in de auth-sectie hieronder (status doet dit automatisch):

```bash
cd /home/cpvb86/Backyard
systemctl is-enabled backyard-api backyard-detector
systemctl status backyard-api backyard-detector --no-pager --full
curl -H "Authorization: Bearer $BACKYARD_API_TOKEN" --fail http://127.0.0.1:8010/api/health
sudo journalctl -b -u backyard-api -u backyard-detector -n 60 --no-pager
sudo /home/cpvb86/Backyard/.venv/bin/python -m operations.status
sleep 35
sudo /home/cpvb86/Backyard/.venv/bin/python -m operations.status
systemctl show backyard-api backyard-detector -p User -p MainPID -p NRestarts
pgrep -a -u cpvb86 -x arecord
sudo systemd-cgls --unit backyard-detector.service
```

Beide active/enabled als cpvb86, precies één bedoelde arecord, verse status,
samples/windows stijgen tussen de controles. NRestarts hoort bij normale boot
niet steeds te groeien. Tijdens warmup wacht je op Monitor gestart en de eerste
statusmetingen; active alleen is onvoldoende bewijs.

## Gecontroleerde crash-recovery-tests

Voer deze **vóór** de ongestoorde 24u-run uit, één voor één. Een SIGKILL-test
onderbreekt verwerking; pending audio kan vervallen. SQLite-transacties beschermen
records, maar dit is geen stroomverliesgarantie voor de hele DB/filesystem-keten.
Er wordt geen database verwijderd of automatische cleanup uitgevoerd.

Maak vooraf een consistente, unieke SQLite-backup met de echte serviceconfig
(uit te voeren als cpvb86; het configbestand is via de primaire groep leesbaar):

```bash
cd /home/cpvb86/Backyard
.venv/bin/python - <<'PY'
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from uuid import uuid4
from app.core.config import Settings
from operations.environment import read_environment
env = read_environment("/etc/backyard/backyard.env")
settings = Settings(**{key: env["BACKYARD_" + key.upper()] for key in Settings.model_fields
                       if "BACKYARD_" + key.upper() in env})
path = settings.resolved_database_path.resolve()
backup = path.with_name(path.name + ".backup-ops-" +
                        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex)
with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
    with closing(sqlite3.connect(backup)) as target:
        source.backup(target)
print("Consistente DB-backup:", backup)
PY
```

### API crash

```bash
API_PID_BEFORE=$(systemctl show backyard-api -p MainPID --value)
DETECTOR_PID_BEFORE=$(systemctl show backyard-detector -p MainPID --value)
systemctl show backyard-api backyard-detector -p MainPID -p NRestarts
sudo systemctl kill --kill-whom=main --signal=SIGKILL backyard-api.service
sleep 15
.venv/bin/python -m operations.wait_api --timeout 60
systemctl show backyard-api backyard-detector -p ActiveState -p MainPID -p NRestarts
printf 'Vorige API PID=%s; vorige detector PID=%s\n' "$API_PID_BEFORE" "$DETECTOR_PID_BEFORE"
sudo journalctl -u backyard-api -u backyard-detector --since '3 minutes ago' --no-pager
sudo .venv/bin/python -m operations.status --check-db
```

API-PID moet vervangen zijn en API NRestarts toenemen; detector-PID blijft
normaal dezelfde en capture/windows gaan door. HTTP-fouten tijdens deze test
zijn verwacht en blijven zichtbaar in het rapport. quick_check moet ["ok"]
geven. Een observation kan zonder audio zijn gebleven bij definitief falende
upload; geen duurzame offline queue toegevoegd.

### Detector crash

```bash
DETECTOR_PID_BEFORE=$(systemctl show backyard-detector -p MainPID --value)
sudo systemctl kill --kill-whom=main --signal=SIGKILL backyard-detector.service
sleep 35
systemctl status backyard-detector --no-pager --full
sudo journalctl -u backyard-detector --since '3 minutes ago' --no-pager
```

Wacht zo nodig op modelwarmup; herstel moet zonder handmatige monitorstart
volgen. Controleer vervolgens:

```bash
systemctl show backyard-detector -p ActiveState -p MainPID -p NRestarts -p User
printf 'Vorige detector PID=%s\n' "$DETECTOR_PID_BEFORE"
pgrep -a -u cpvb86 -x arecord
sudo systemd-cgls --unit backyard-detector.service
sudo .venv/bin/python -m operations.status --check-db
sleep 35
sudo .venv/bin/python -m operations.status
```

Nieuw hoofd-PID en invocation/stream-ID, NRestarts verhoogd, exact één nieuwe
arecord in de detector-cgroup, geen oude capture/modelprocessen erbuiten,
geen blijvende device-busyfout, opnieuw oplopende samples/windows en DB-check ok.
De lock herstelt automatisch na SIGKILL; verwijder de lockfile niet.
Bij een gezonde USB-microfoon horen er geen verdere herstarts te volgen.
[systemctl kill-opties voor Debian 13](https://manpages.debian.org/trixie/systemd/systemctl.1.en.html).

Optioneel USB-herstel testen vóór de duurtest: trek de bedoelde USB-microfoon
kort los, controleer de duidelijke ALSA-fout/herstart in journal, sluit hem aan
en wacht op automatische capture. Gebruik dezelfde card-ID en geen tweede
handmatige detector. Dit veroorzaakt opzettelijk een meetgat.

## Start de volledige dag/nacht-duurtest

Na reboot- en recovery-tests: laat beide services minstens 24 uur met rust.
Geen handmatige monitor/capture naast systemd. Laat gewone vogelactiviteit
de observation-, review- en evidence-laag in de praktijk vullen.

Leg een beginrapport vast; een ATTENTION door korte historie of de opzettelijke
crashtests is op dat moment verwacht. Het rapport wordt wel geschreven:

```bash
cd /home/cpvb86/Backyard
mkdir -p .detector-test
date -u
sudo .venv/bin/python -m operations.status --json > .detector-test/operations-start.json
```

Na minstens 24 uur is **één commando**, ook direct na SSH-login, voldoende
voor het ochtendoverzicht:

```bash
(cd /home/cpvb86/Backyard && sudo .venv/bin/python -m operations.status)
```

Het toont services/enabled/User/PID/NRestarts, API-health, detectoruptime,
capture/ALSA/ring-gaps, inference/windows/drops/ratio, raw/relevante candidates,
observations/auto/review/unsupported/discard, evidence, HTTP, queues, journal-
herstarts/coverage, databasecounts, WAV-aantallen/bytes en RAM/disk/temperatuur.
CPUUsageNSec is cumulatieve cgroup-CPU-tijd, geen instantaan percentage;
load averages en temperatuur geven aanvullende context.

Exitcode 0 = CURRENTLY_OK zonder gevonden waarschuwingen;
1 = ATTENTION (rapport beschikbaar); 2 = onleesbare/ongeldige configuratie.
De melding is geen automatische wetenschappelijke detectievalidatie.

Verwacht tijdens een ongestoorde gezonde run:

- actieve/enabled services als cpvb86 en verse huidige detector-invocation;
- min of meer 24u uptime, geen onverwacht groeiende NRestarts;
- capture_gaps, alsa_overruns, ring_overruns, windows_dropped en overige drops 0;
- inference gemiddeld ruim sneller dan hop=1.5s, queues niet oplopend;
- geen nieuwe HTTP failures tijdens normale API-beschikbaarheid;
- aantal observations/evidence verklaarbaar uit supporting candidates;
- geen WAVs voor nieuwe uitgesloten niet-doelgroepen;
- reviewaudio beschikbaar, geen onverwachte explosieve opslaggroei.

Counters zijn per monitorinvocation. Journalrapportage telt verschillen per
invocation en bewaart ook foutcounters van vóór herstarts. Begon een proces vóór
het gekozen tijdvak, dan wordt de eerste aanwezige sample als ondergrens gebruikt.
Laatste maximaal 30s vóór een harde crash kunnen onzichtbaar zijn; verdwenen
journalrecords zijn niet terug te reconstrueren. Metadata uit een oude
invocation worden nooit als verse metingen van de nieuwe service behandeld.

Er worden maximaal 20.000 journalrecords binnen het tijdvak gelezen (timeout 20s,
max 64KiB per regel). Een bereikt limiet/ongeldige regel geeft waarschuwing.
`--hours 1` tot `--hours 168` is mogelijk. Standaard 24h. Grote meetgaten,
te korte historie en stale status worden apart gemeld.

## Database- en audiogroei beoordelen

Hetzelfde commando doet alleen SELECT/PRAGMA query_only en filesystem-stat.
Geen migratie, opslag, herstel of cleanup. SQLite wordt met mode=ro geopend;
een ontbrekend DB-pad maakt nooit een lege database.

Bewaar een eindrapport en vergelijk met het begin:

```bash
sudo .venv/bin/python -m operations.status --json --check-db > .detector-test/operations-end.json
.venv/bin/python - <<'PY'
import json
from pathlib import Path
start = json.loads(Path(".detector-test/operations-start.json").read_text())
end = json.loads(Path(".detector-test/operations-end.json").read_text())
print("Van", start["at"], "tot", end["at"])
for key in ("observations_total", "supports_total", "legacy_detections", "legacy_audio"):
    print(key, "groei:", end["database"][key] - start["database"][key])
for name, group in end["audio"]["groups"].items():
    before = start["audio"]["groups"][name]
    print(name, "WAV groei:", group["wav_files"] - before["wav_files"],
          "bytes groei:", group["bytes"] - before["bytes"], "totaal bytes:", group["bytes"])
print("Nieuwe uploads in laatste tijdvak:", end["database"]["audio_uploaded_since"])
print("Observations per status:", end["database"]["by_status"])
print("Evidence per categorie:", end["database"]["by_evidence_kind"])
print("DB integrity:", end["database"].get("quick_check"))
print("Waarschuwingen:", end["warnings"])
PY
df -h /home/cpvb86/Backyard
```

Audio-inventaris verdeelt permanent_bird/permanent_bat/review_bird/review_bat.
Permanente bird-audio bevat ook bestaande historische Birds-audio. Groei uit
begin/eindrapporten onderscheidt die bestaande voorraad. new_or_modified_since
is filesystem-mtime, dus kan ook een promotie/kopie omvatten; audio_uploaded_since
gebruikt opgeslagen observation-uploadmetadata. Counts zijn momentopnames
terwijl ingest doorgaat, geen atomair snapshot van DB plus filesystem.

De filesystemscan is begrensd op 100.000 entries/10s; incomplete resultaten
worden gemarkeerd, niet als nul voorgesteld. Bestandsinhoud wordt niet geladen.
Symlinks worden niet gevolgd. Expliciete quick_check heeft een querybudget
van 10s en repareert niets.

Luister een echte observation terug en inspecteer review wanneer de duurtest
materiaal heeft opgeleverd:

```bash
curl -H "Authorization: Bearer $BACKYARD_API_TOKEN" --fail 'http://127.0.0.1:8010/api/observations?domain=bird&limit=10' | .venv/bin/python -m json.tool
curl -H "Authorization: Bearer $BACKYARD_API_TOKEN" --fail http://127.0.0.1:8010/api/observations/review | .venv/bin/python -m json.tool
```

Haal de getoonde audio_url op met dezelfde Authorization-header; Swagger staat op /docs.
Controleer labels, supports, reasons en daadwerkelijke audio. De policy is
ongewijzigd: sterke normale observations zijn automatisch permanent;
reviewaudio blijft behouden. Geen automatische verwijdering tijdens deze fase.

## Latere bewuste deployment

```bash
cd /home/cpvb86/Backyard
sudo systemctl stop backyard-detector backyard-api
git status
git pull --rebase origin main
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -v -W error
.venv/bin/python -m pip check
.venv/bin/python - <<'PY'
import os
from operations.environment import read_environment
from app.core.migrate import main
os.environ.update(read_environment("/etc/backyard/backyard.env"))
main()
PY
```

Als unitbronnen veranderd zijn: installeer alleen die opnieuw, verify en
daemon-reload zoals boven. Overschrijf backyard.env niet. Start daarna API en
detector, controleer health/status. Er is geen auto-update bij boot.

## Verificatie en grenzen

De huidige ontwikkelmachine is Windows. Unitbestanden worden op structuur/
commando's getest en aan Debian 13-documentatie getoetst; systemd zelf,
USB/cgroups/reboot en de volledige duurtest moeten op de Pi worden uitgevoerd.
Twee echte POSIX-tests verifiëren daar SIGTERM-subprocesscleanup en flock-release
na SIGKILL. Die worden op Windows expliciet overgeslagen.

Een actieve service garandeert geen herkenning. Er is geen harde realtime-,
ononderbroken-power-loss- of permanente-opslaggarantie. Crash/USB/herstarts
verliezen vluchtige buffers. De startup/stall-deadlines leveren herstelpogingen,
geen garantie dat kapotte hardware vanzelf werkt. Geen externe watchdog voor
een volledig vastgelopen OS of geblokkeerde hoofdthread.

Onbeoordeelde reviewaudio kan groeien; diskwaarschuwing vervangt geen beheer.
Retentionbeleid is niet gewijzigd. Alle /api/* routes vereisen Bearer-authenticatie.
Gebruik HTTPS buiten het vertrouwde LAN; een token versleutelt geen HTTP. Historische data worden niet herclassificeerd.
Na deze operationsfase stoppen we; geen frontend of andere modules.

## Lokale oplevercontrole

Windows / Python 3.14.7: **250 passed, 2 skipped in 10.18s**, geen warnings
met `python -m pytest -q -W error`. De 2 skips zijn uitsluitend de echte
POSIX-subprocess/flock-tests; op de Pi moeten ook die slagen (252 tests).
`pip check`: geen conflicten. Geen dependencybestanden of observation-policy gewijzigd.

De exacte API-service-entrypoint/options zijn met UVICORN_HOST/PORT en een
geïsoleerde tijdelijke SQLite/storage uitgevoerd. Echte HTTP-readiness met
policycheck, synthetische permanente/reviewaudio en read-only statusinventaris
zijn geslaagd. Op Windows ontbrekend systemd/journal wordt correct als
ATTENTION weergegeven. Systemd-unitvalidatie op Linux, hardwareherstel,
reboot en de 24u-duurtest zijn nog via bovenstaande Pi-procedure te bevestigen.


## Bearer-authenticatie na de duurtest

Voer deze deployment pas NA de lopende 24-uursduurtest uit. Bewaar eerst het
bestaande eindrapport. Alle `/api/*` routes, inclusief health, policy, audio en
onbekende routes, vereisen het token. Ontbrekende/foute credentials geven 401
met WWW-Authenticate: Bearer. Lege/ongeldige serverconfig weigert startup;
er is geen anonieme fallback. /docs en /openapi.json bevatten alleen schema/UI.
Swagger Authorize gebruikt dezelfde Bearer-header.

```bash
cd /home/cpvb86/Backyard
sudo .venv/bin/python -m operations.status --json --check-db > .detector-test/operations-end.json
sudo systemctl stop backyard-detector
sudo systemctl stop backyard-api
git status
git pull --rebase origin main
source .venv/bin/activate
pytest -v -W error
python -m pip check
```

Geen dependencies of schema gewijzigd; geen pip-install of migratie nodig.
Genereer het token rechtstreeks in het bestaande configbestand, zonder het
in shellhistorie, procesargumenten of uitvoer te plaatsen. Bestaande andere
instellingen en bestandsrechten blijven behouden. Een bestaand token wordt
bij opnieuw uitvoeren behouden:

```bash
sudo /home/cpvb86/Backyard/.venv/bin/python - <<'PY'
from pathlib import Path
import secrets
from operations.environment import read_environment
path = Path('/etc/backyard/backyard.env')
env = read_environment(path)
if not env.get('BACKYARD_API_TOKEN'):
    lines = path.read_text().splitlines()
    lines = [line for line in lines if not line.strip().startswith('BACKYARD_API_TOKEN=')]
    lines.append('BACKYARD_API_TOKEN=' + secrets.token_urlsafe(32))
    path.write_text('\n'.join(lines) + '\n')
print('BACKYARD_API_TOKEN geconfigureerd; waarde niet getoond.')
PY
sudo systemctl start backyard-api
sudo systemctl start backyard-detector
sudo .venv/bin/python -m operations.status
```

Beide bestaande units lezen hetzelfde EnvironmentFile, ook hun ExecStartPre/
ExecStartPost. Unitbestanden veranderen niet: daemon-reload is niet nodig.
Bij latere tokenrotatie: detector stoppen, API herstarten, detector starten.
De status-CLI leest zelf het configbestand, ook onder sudo; een shell-token
overschrijft het productie-token niet. Readiness en HTTPTransport lezen het
geerfde token automatisch. Het token komt niet in status/metrics/logging.

Configureer dezelfde waarde in de aparte WordPress-plugin; gebruik bijvoorbeeld
`sudoedit /etc/backyard/backyard.env` om deze zelf te bekijken/kopieren. Deel hem
niet in logs of Git. Voor handmatige curl-checks na SSH-login:

```bash
export BACKYARD_API_TOKEN="$(.venv/bin/python - <<'PY'
from operations.environment import read_environment
print(read_environment('/etc/backyard/backyard.env')['BACKYARD_API_TOKEN'])
PY
)"
curl --fail -H "Authorization: Bearer $BACKYARD_API_TOKEN" http://127.0.0.1:8010/api/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/api/health
unset BACKYARD_API_TOKEN
```

Verwacht respectievelijk health ok en 401. Oude curl- en debugvoorbeelden in
historische fase-documentatie vereisen nu ook deze header/omgevingsvariabele.
Start geen extra monitor naast systemd.
