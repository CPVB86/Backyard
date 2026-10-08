# Automatische Avian → Samsung Frame-koppeling

Gebruik uitsluitend de bestaande installatie:

- Projectroot: `/home/cpvb86/Backyard`
- Python: `/home/cpvb86/Backyard/.venv/bin/python`
- Configuratie: `/home/cpvb86/Backyard/.env`
- PNG: `/home/cpvb86/Backyard/data/samsung_frame/samsung-frame.png`

De exporter maakt op :00/:15/:30/:45 een atomische 3840 × 2160 PNG.
Een onafhankelijke Samsung-sync start op :02/:17/:32/:47, vergelijkt de SHA-256
met de laatst succesvol geactiveerde afbeelding en uploadt uitsluitend nieuw
beeld. De bestaande collage-data, renderer en matte=none-upload worden hergebruikt.
De timestamp verandert per export; ook bij hetzelfde vogelbestand is dat nieuw
beeld. Bij exportfalen blijft de vorige PNG staan en wordt die niet opnieuw
geüpload als haar hash al current is.

## Installeren en activeren op de Pi

Vervang PI_HOST door je Pi-hostnaam/IP. Je hoeft API of detector niet te stoppen
en geen database te migreren. Werk in de bestaande venv, niet .venv-detector:

```bash
ssh cpvb86@PI_HOST
cd /home/cpvb86/Backyard
git pull --ff-only origin main
sudo apt-get update
sudo apt-get install -y chromium tzdata
.venv/bin/python -m pip install -r requirements-collage-export.txt -r requirements-samsung-frame.txt
.venv/bin/python -m pip check
nano /home/cpvb86/Backyard/.env
chmod 600 /home/cpvb86/Backyard/.env
```

Behoud bestaande API-, database-, storage- en overige projectwaarden. Voeg alleen
ontbrekende onderstaande instellingen toe; vervang het tv-IP door je echte adres:

```dotenv
BACKYARD_SAMSUNG_FRAME_HOST=192.168.1.50
BACKYARD_SAMSUNG_FRAME_TOKEN=
BACKYARD_SAMSUNG_FRAME_TOKEN_PATH=data/samsung_frame/token
BACKYARD_SAMSUNG_FRAME_STATE_PATH=data/samsung_frame/artwork.json
BACKYARD_SAMSUNG_FRAME_IMAGE_PATH=data/samsung_frame/samsung-frame.png
BACKYARD_SAMSUNG_FRAME_TIMEOUT=60
BACKYARD_AVIAN_EXPORT_HOURS=24
BACKYARD_AVIAN_EXPORT_MAX_UPSCALE=1.5
BACKYARD_AVIAN_EXPORT_CHROMIUM_PATH=/usr/bin/chromium
BACKYARD_AVIAN_EXPORT_TIMEOUT=180
```

Kopieer .env.example niet over een bestaand .env. Als deze instellingen eerder
elders stonden, neem alleen hun echte huidige waarden over naar de root-.env.
De Samsung-tokenfile en het ownershipjournal blijven op hun bestaande pad;
er wordt geen nieuwe pairing of eigen-artworkadministratie afgedwongen.
Laat het initieel Samsung-token normaal leeg; bevestig pairing alleen indien nodig.

Controleer eerst handmatig (identieke configuratie als de services):

```bash
.venv/bin/python -m app.modules.samsung_frame connect
.venv/bin/python -m app.modules.avian_collage_exporter
.venv/bin/python -m app.modules.samsung_frame sync
.venv/bin/python -m app.modules.samsung_frame status
```

Als er pending werk is, doet sync uitsluitend herstel; de volgende run verwerkt
pas de nieuwste PNG. `recover` blijft beschikbaar voor expliciet handmatig herstel.

Installeer en enable beide timers (ook bestaande exportunit vervangen):

```bash
sudo install -m 0644 deploy/systemd/backyard-collage-export.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/backyard-collage-export.timer /etc/systemd/system/
sudo install -m 0644 deploy/systemd/backyard-samsung-frame.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/backyard-samsung-frame.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/backyard-collage-export.service /etc/systemd/system/backyard-collage-export.timer /etc/systemd/system/backyard-samsung-frame.service /etc/systemd/system/backyard-samsung-frame.timer
systemd-analyze calendar '*-*-* *:00,15,30,45:00 Europe/Amsterdam'
systemd-analyze calendar '*-*-* *:02,17,32,47:00 Europe/Amsterdam'
sudo systemctl daemon-reload
sudo systemctl enable --now backyard-collage-export.timer backyard-samsung-frame.timer
```

De uren zijn expliciet Europe/Amsterdam, onafhankelijk van de Pi-hosttimezone.
AccuracySec=1s, geen random vertraging. Persistent=true zorgt bij inschakelen/boot
voor één inhaalrun als een tijdstip is gemist, daarna het normale kwartierschema.
Daardoor kunnen de eerste runs direct na activatie plaatsvinden.

## Monitoring en reboot

```bash
systemctl list-timers backyard-collage-export.timer backyard-samsung-frame.timer
systemctl is-enabled backyard-collage-export.timer backyard-samsung-frame.timer
systemctl status backyard-collage-export.timer backyard-samsung-frame.timer --no-pager
journalctl -u backyard-collage-export.service -u backyard-samsung-frame.service -n 80 --no-pager
.venv/bin/python -m app.modules.samsung_frame status
```

Live volgen: `journalctl -f -u backyard-collage-export.service -u backyard-samsung-frame.service`.
Voor een directe praktische ketentest zonder op een timer te wachten:

```bash
sudo systemctl start backyard-collage-export.service
sudo systemctl start backyard-samsung-frame.service
```

Na een door jou uitgevoerde reboot gebruik je dezelfde list-timers/is-enabled/
journalctl-commando's. De timers zijn enabled voor timers.target; token, hashes,
PNG en ownershipjournal blijven persistent. Een oneshot-service die na succes
inactive is, is normaal: de timer blijft actief.

Stoppen: `sudo systemctl disable --now backyard-collage-export.timer backyard-samsung-frame.timer`.
Dit verwijdert geen PNG, hashes, token of tv-artworks.

## Fouten en veiligheid

- Het gedeelde OS-slot verhindert overlappende renders/uploads, inclusief
  handmatige CLI's. Timerdiensten zijn geen templates en starten geen tweede
  eigen instantie. De uploader is geordend na een eventueel lopende export,
  maar start niet zelf nogmaals een export. Bij drukte/fout volgt de volgende
  geplande poging; er is geen snelle retry-loop.
- Alleen een succesvol geactiveerde PNG krijgt current_sha256. Pending_sha256
  volgt de werkelijk geüploade bytes, ook als Avian ondertussen een nieuwere
  PNG maakt. Herstel leest/uploadt geen nieuw bestand in dezelfde transactie.
- Upload/selectie/matte/opslagfalen verwijderen de vorige afbeelding niet.
  Cleanup verwijdert alleen de vorige beheerde MY_-ID, na activatiebevestiging
  en extra active/matte=none/Art Mode-controle. Andere persoonlijke foto's en
  Samsung-artworks blijven buiten de administratie en delete-calls.
- Offline/verbinding/pairingproblemen worden bij de volgende timerrun opnieuw
  geprobeerd. Een upload die aantoonbaar vóór image-bytes faalt, wist veilig
  haar intent-marker en mag later opnieuw proberen. Bekende pending/previous
  IDs worden automatisch hervat, zonder dubbele upload.
- Bij een onzekere upload nadat bytes mogelijk verstuurd zijn maar vóór een
  duurzaam ontvangen content-ID wordt niet blind opnieuw geüpload. De timer
  probeert het herstelpad later opnieuw, maar deze specifieke ambiguïteit
  vereist journal/log/tv-controle door de eigenaar: zonder ID kan de module
  geen eigendom veilig bewijzen. Oude hashloze upload_attempt-markers blijven
  eveneens geblokkeerd. Dit voorkomt dubbele upload en vreemde deletes.
- Logs tonen render-/commandoduur, foutstap en sync-resultaat uploaded,
  recovered of unchanged; tokens worden niet gelogd.

De twee nieuwe diensten lezen de root-.env via dezelfde Python Settings als
handmatige commando's. Zij hebben bewust geen EnvironmentFile met afwijkende
systemd-parsing of oude /etc-configuratie. API-/detectorservices en hun eventuele
historische operationele configuratie zijn buiten deze wijziging gebleven.

## Technische controles

Gerichte fake-tv-tests: SHA-deduplicatie na restart, gewijzigde PNG-vervanging,
pending-hash-herstel, veilige retry vóór transfer, behoud bij mogelijke partial
transfer en timer/configuratiecontract. Bestaande Samsung- en exporterchecks
blijven behouden. Geen tv-, Pi-, boot- of systemd-uitvoering lokaal geclaimd;
de eigenaar voert de praktijktest en timeractivatie op de Pi uit.
