# Avian Collage Exporter

Zelfstandige eenmalige export binnen CPVB86/Backyard, voor Samsung The Frame
50LS03F. Uitvoer exact 3840 × 2160 PNG (16:9), standaard
`/home/cpvb86/Backyard/data/samsung_frame/samsung-frame.png`.
Geen nieuwe Backyard-installatie, API-endpoint, uploadtimer of TV-aanroep.

## Hergebruik en compositie

- Dezelfde `avian_visitors.service.recent` als de bestaande browsercollage:
  geaccepteerde bird-observations, effectieve gecorrigeerde soortidentiteit,
  Nederlandse namen, observationaantallen en bestaande Otje-presentatie.
- Dezelfde centrale AssetStore/Generator-afbeeldingen, transparante cutouts,
  silhouetmaskers en deterministische zittend/vliegend-posekeuze. Geen nieuwe
  beeldgeneratie, OpenAI-call, kopie van illustraties of dataset.
- De bestaande maskPack/contour-label/gewichtcode is verplaatst naar gedeelde
  `avian_visitors/static/collage-layout.js`. De browser gebruikt dezelfde
  oorspronkelijke parameters; alleen export kiest een dichter packingbudget,
  canvasaspect en kleinere buitenmarges. Aantallen blijven de vogelgrootte
  beïnvloeden. Vogelsoortnamen volgen de bestaande contour-labels.
- De exporterpagina bevat uitsluitend de compositie: vogelcollage-hoofdlaag
  en timestamp-informatielaag. Geen header, knoppen, atlas, navigatie of
  WordPress. Toekomstige informatielagen kunnen in deze aparte exporttemplate
  naast de twee huidige lagen worden toegevoegd; nog geen sensoren/vleermuizen.
- Headless Chromium rendert rechtstreeks op 3840 × 2160, deviceScaleFactor=1,
  met originele PNG's. Verhoudingen blijven behouden. Geen opschaling van
  een kleine dashboard-screenshot. Kleine bronnen (<800 pixels aan de korte
  zijde) worden niet vergroot; grotere bronnen maximaal 1.5×, instelbaar tot 2×.
  Zeer weinig soorten/kleine bronbeelden kunnen daardoor niet letterlijk
  elke pixel vullen zonder kwaliteitsverlies. Er wordt niets bij verzonnen,
  uitgerekt of afgesneden om die natuurlijke lege ruimte te verbergen.
- Het collagevlak heeft 16 pixels buitenruimte. Onderaan is 88 pixels voor
  informatie gereserveerd; de timestamp staat rechtsonder op 36 pixels, in de
  meegeleverde Caveat-font. Geen passe-partout, decoratieve rand of extra
  achtergrondpaneel. De effen ondergrond is de bestaande Avian-papierkleur.
- Timestamp: `Vogelbezoeken in de tuin op 08-10-2026 - 11:45u`, met werkelijke
  exporttijd en IANA-zone Europe/Amsterdam, inclusief zomer-/wintertijd.

## Installatie en één handmatige export op de bestaande Pi

Vervang PI_HOST door je bestaande Pi-hostnaam/IP. Gebruik de API-venv,
niet `.venv-detector`. API en detector kunnen blijven draaien.

```bash
ssh cpvb86@PI_HOST
cd /home/cpvb86/Backyard
git pull --ff-only origin main
sudo apt-get update
sudo apt-get install -y chromium tzdata
.venv/bin/python -m pip install -r requirements-collage-export.txt
.venv/bin/python -m pip check
sudoedit /etc/backyard/backyard.env
```

Voeg alleen onderstaande velden toe; behoud alle bestaande API-, database-,
storage-, detector-, geo- en Samsung-instellingen:

```dotenv
BACKYARD_AVIAN_EXPORT_HOURS=24
BACKYARD_AVIAN_EXPORT_MAX_UPSCALE=1.5
BACKYARD_AVIAN_EXPORT_CHROMIUM_PATH=/usr/bin/chromium
BACKYARD_AVIAN_EXPORT_TIMEOUT=180
```

Het uitgangspad komt uit de bestaande `BACKYARD_SAMSUNG_FRAME_IMAGE_PATH`.
Relatieve paden blijven relatief aan de Backyard-root. Dezelfde configuratielader
als Samsung wordt gebruikt: shellomgeving > expliciet productie-environmentbestand
> root `.env` > defaults. Export gebruikt de bestaande database in SQLite read-only
modus; maakt/migreert geen database en heeft geen API-token voor HTTP nodig.

```bash
.venv/bin/python -m app.modules.avian_collage_exporter --environment-file /etc/backyard/backyard.env
.venv/bin/python -c 'from PIL import Image; im=Image.open("data/samsung_frame/samsung-frame.png"); print(im.format, im.size)'
```

De Pi gebruikt distro-Chromium. Er hoeft daar geen tweede browserdownload met
`playwright install` plaats te vinden. De optionele dependency is een Python
browserdriver; geen Node-project, npm-installatie of extra webserver.
Voor Windows/macOS kun je het executablepad van een aanwezige Chromium-browser
instellen, of `.venv/.../python -m playwright install chromium` gebruiken en
het optionele executablepad weglaten. Windows krijgt tzdata via requirements.
Het actuele browserprofiel van de gebruiker wordt niet gebruikt.

### PNG bekijken op je Windows-computer

Voer in een **lokale PowerShell**, buiten de SSH-sessie, uit:

```powershell
scp cpvb86@PI_HOST:/home/cpvb86/Backyard/data/samsung_frame/samsung-frame.png "$HOME/Downloads/samsung-frame.png"
Invoke-Item "$HOME/Downloads/samsung-frame.png"
```

Bij een aangepast outputpad gebruik je dat pad. Er is geen onbeveiligde
webserver toegevoegd die token/artworkjournal of andere data zou publiceren.
Bekijk de vogels, namen, dichtheid, kwaliteit en timestamp eerst zelf.
Na jouw visuele beoordeling kan de bestaande Samsung-CLI deze PNG direct
handmatig uploaden; de uploadlogica en `matte="none"` blijven ongewijzigd.

## Veilig vervangen, fouten en logging

PNG wordt eerst volledig in geheugen gerenderd en op PNG-formaat/resolutie
gecontroleerd, dan in een tijdelijk bestand in dezelfde map geschreven, geflusht
en met `os.replace` atomisch gepubliceerd. Geen historische PNG's. Alleen eigen
ongepubliceerde `.collage-*.png`-restanten van een afgebroken write worden onder
de lock opgeruimd. Fouten wijzigen de laatst succesvolle PNG niet.
De log meldt renderduur, soortenaantal en fout; CLI exitcode is 0/1.

Geen aanvaarde waarnemingen in de ingestelde periode, ontbrekende/ongeldige
illustraties, een soort die niet geplaatst kan worden, browserfalen of een
schrijffout geven expliciet een fout. De vorige afbeelding blijft dan staan.
De timestamp in die afbeelding blijft dus die van de vorige geslaagde export.
Er wordt nooit stilzwijgend een gedeeltelijke collage gepubliceerd of betaald
beeldmateriaal gegenereerd. Het browserproces ontvangt uitsluitend een
snapshot en whitelisted lokale bestanden; alle echte netwerkrequests worden
geblokkeerd. API/database en Samsung-token komen niet in de PNG of pagina.

Dezelfde OS-lock als de Samsung-CLI verhindert overlappende exports en
gelijktijdig renderen/uploaden. Bij een bezette lock faalt de nieuwe export
zonder het bestaande bestand te wijzigen. De service heeft een harde
10-minutengrens; een gecrashte/gestopte renderer wordt door systemd opgeruimd.

## Service en timer — pas installeren/activeren na afbeeldinggoedkeuring

Bestanden worden alleen meegeleverd; in deze oplevering is **niets gestart,
geïnstalleerd of enabled op de Pi**. Timer iedere kwartiergrens in
Europe/Amsterdam (:00, :15, :30, :45), zonder random delay, nauwkeurigheid 1s.
systemd start geen tweede instantie als de service nog loopt. `Persistent=false`
voorkomt extra inhaalexports bij boot. Geen afhankelijkheid van een Samsung-upload.

Voer onderstaande commando's pas uit nadat je de afbeelding hebt goedgekeurd:

```bash
cd /home/cpvb86/Backyard
sudo install -m 0644 deploy/systemd/backyard-collage-export.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/backyard-collage-export.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/backyard-collage-export.service /etc/systemd/system/backyard-collage-export.timer
systemd-analyze calendar '*-*-* *:00,15,30,45:00 Europe/Amsterdam'
sudo systemctl daemon-reload
sudo systemctl enable --now backyard-collage-export.timer
systemctl list-timers backyard-collage-export.timer
journalctl -u backyard-collage-export.service -n 30 --no-pager
```

Stoppen later: `sudo systemctl disable --now backyard-collage-export.timer`.
De timer activeert uitsluitend de exporter; nooit Samsung-upload.
Zie [systemd timer-semantiek](https://manpages.debian.org/trixie/systemd/systemd.timer.5.en.html)
en [Playwright browserinstallatie](https://playwright.dev/python/docs/browsers).

## Technische verificatie

40 gerichte exporter-, Avian- en Samsung-tests geslaagd. Gerichte tests op Windows met bestaande Chrome in headless mode: echte
3840 × 2160 PNG uit geïsoleerde geaccepteerde observations en bestaande assets;
Amsterdam zomer-/wintertijd; read-only brondata; ontbrekende database wordt niet
aangemaakt; render-/formaat-/vervangingsfouten behouden vorig bestand;
tempcleanup en lock voorkomen gedeeltelijke/overlappende writes; 1 en 7 soorten
blijven binnen het canvas zonder kleine bronnen op te schalen of te vervormen.
De bestaande Avian-tests controleren ook de gedeelde browserplaatsingscode.
Geen visuele goedkeuring, tv-test, Pi/ARM- of systemd-uitvoering geclaimd.

```bash
# Optioneel: kies je bestaande browser om de echte rendercase mee te draaien.
BACKYARD_EXPORT_TEST_CHROMIUM=/usr/bin/chromium .venv/bin/python -m pytest tests/test_collage_exporter.py tests/test_avian_visitors.py -q
```
