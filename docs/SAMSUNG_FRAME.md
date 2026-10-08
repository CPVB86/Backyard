# Samsung Frame — handmatige test

Doelapparaat: Samsung The Frame 50LS03F (2025), vanaf Raspberry Pi 5 via Wi-Fi.
Deze module staat binnen het bestaande zelfstandige Backyard-project.
De transportlibrary is [samsungtvws 3.0.6](https://github.com/xchwarze/samsung-tv-ws-api),
met de actuele Art Mode-socketupload en protocolversiedetectie voor moderne
Frame-tv's. Geen cloudaccount, Gemini of OpenAI nodig. Compatibiliteit met de
specifieke LS03F-firmware en daadwerkelijke weergave zijn nog niet op hardware
geverifieerd; de eigenaar voert die acceptatietest uit.

## Installatie op de bestaande productie-Pi

Bron van de module: CPVB86/AvianVisitors commit `ed01fc9`, onder `backyard/`.
Deze overdracht staat uitsluitend in CPVB86/Backyard. De configuratie gebruikt
de bestaande `app.core.config.Settings`; de API-authenticatie blijft ongewijzigd.
Geen tweede clone, venv, database, detector, service of installatie.

Vervang `PI_HOST` door je bestaande Pi-hostnaam of IP-adres:

```bash
ssh cpvb86@PI_HOST
cd /home/cpvb86/Backyard
git switch main
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements-samsung-frame.txt
.venv/bin/python -m pip check
sudoedit /etc/backyard/backyard.env
```

Voeg alleen de Samsung-instellingen hieronder toe aan het bestaande bestand;
vervang `192.168.1.50` door je echte tv-IP. Vervang geen API-token, databasepad,
storage root, detector-, geo- of WordPress-instellingen. Kopieer het voorbeeld
niet over je bestaande productieconfiguratie. Geen migratie of serviceherstart
nodig voor de handmatige Samsung-CLI.

```dotenv
BACKYARD_SAMSUNG_FRAME_HOST=192.168.1.50
BACKYARD_SAMSUNG_FRAME_TOKEN=
BACKYARD_SAMSUNG_FRAME_TOKEN_PATH=data/samsung_frame/token
BACKYARD_SAMSUNG_FRAME_STATE_PATH=data/samsung_frame/artwork.json
BACKYARD_SAMSUNG_FRAME_IMAGE_PATH=data/samsung_frame/samsung-frame.png
BACKYARD_SAMSUNG_FRAME_TIMEOUT=60
```

De CLI leest het productiebestand alleen bij de expliciete optie
`--environment-file /etc/backyard/backyard.env`, met de bestaande veilige
`operations.environment.read_environment` parser (geen shelluitvoering).
Voorrang: shellenvironment > expliciet productiebestand > bestaande root
`.env` > defaults. Andere bekende Backyard-instellingen worden gelezen maar
door de module niet gewijzigd/gebruikt voor database of ingest. Detectorvelden
en UVICORN-velden in het productiebestand blijven buiten de CLI-settings.
Relatieve bestandspaden zijn altijd vanaf de Backyard-projectroot, ook bij een
andere werkmap. De .env.example en deploy/systemd/backyard.env.example bevatten
de nieuwe velden; uitsluitend voorbeelden zijn gewijzigd.

Voor lokale ontwikkeling kun je dezelfde Samsung-velden in de bestaande root
`.env` zetten en de `--environment-file` optie weglaten. Samsung-pairing heeft
geen Backyard-API-token nodig; de API behoudt haar bestaande tokenvereiste.
Laat `BACKYARD_SAMSUNG_FRAME_TOKEN` normaal leeg: tv-goedkeuring levert het
private tokenbestand op. Een bestaand Samsung-token mag via dat environmentveld,
maar nooit via een commandoregel of ingecheckt bestand.
De productie-env moet leesbaar zijn voor `cpvb86`, zoals bij bestaande
operationele checks. Draai de Samsung-CLI als `cpvb86`, niet als root.

## Eerste handmatige test

Zet de tv aan, verbind Pi en tv met hetzelfde lokale subnet, en houd de
afstandsbediening gereed om **Backyard Samsung Frame** toe te staan. Controleer
op de tv de externe apparaatverbindingen/IP-afstandsbediening. Menunamen hangen
van taal/firmware af. De library gebruikt lokaal WSS/HTTPS op poort 8002 en het
door de tv aangewezen uploadsocket; netwerkisolatie kan dit blokkeren.
Samsung gebruikt een zelfondertekend certificaat; de library valideert dat niet.
Gebruik dit op je vertrouwde lokale netwerk.

Vanuit `/home/cpvb86/Backyard`:

```bash
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env connect
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env generate
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env upload
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env status
```

`connect` test werkelijk de pairing, Art API en tv-identiteit; het uploadt niets.
`generate` maakt `data/samsung_frame/samsung-frame.png`: exact 3840 × 2160,
gekleurde geometrische compositie, rand en kruislijnen, kleine lokale timestamp
rechtsonder. `upload` schakelt Art Mode in en selecteert het nieuwe artwork.
Succes geeft `activated` met het Samsung-content-ID en `matte: none` terug.
Controleer op de tv de compositie, timestamp, schermvulling en afwezigheid van
passe-partout. `status` leest de live verbinding, actieve afbeelding en het
eigendomjournal; bij verbindingsfalen toont het het lokale journal en exitcode 1.
Connect/status veranderen geen artworks; pairing kan wel een token opslaan.

Een tweede handmatige proef met nieuwe timestamp:

```bash
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env generate
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env upload
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env status
```

## Veiligheid, fouten en herstel

- Upload gebruikt altijd `matte="none"` én `portrait_matte="none"`. Geen
  automatische fallback of tweede upload met passe-partout.
- De module vereist terugmelding `matte_id="none"` in de contentlijst én bij
  het actieve artwork, het juiste actieve ID en Art Mode `on`. Ontbrekende of
  afwijkende terugmelding is een fout; er wordt dan niets oud verwijderd.
- `data/samsung_frame/artwork.json` bewaart alleen eigen `MY_`-IDs, gekoppeld
  aan de werkelijke `device.id`. Samsung-IDs (`SAM_`) worden geweigerd. De enige
  delete-call richt zich op `previous` uit dit journal; nooit bulkverwijdering.
- Upload-ID wordt vóór selectie opgeslagen; actieve ID vóór verwijdering.
  Atomisch vervangen en fsync beschermen tegen gedeeltelijke lokale writes;
  een OS-lock verhindert overlappende modulecommando's. Na procescrash wordt
  die lock vanzelf vrijgegeven.
- Token en journal krijgen op Linux bestandsmodus 0600, hun opslagmappen 0700.
  Gebruik hiervoor een aparte map die eigendom is van de uitvoerende Pi-user.
  Windows heeft andere ACL-semantiek; bescherm daar de userdirectory met ACLs.
  Tokens worden niet gelogd. Bestanden en afbeelding vallen onder genegeerde
  `data/`; een aangepast pad moet je zelf buiten Git houden.
- Tv-/libraryfouten verschijnen met exceptiontype en oorspronkelijke melding
  (eventueel onderliggende fout), met authenticatietokens geredigeerd. Exitcode
  is 1. Een mislukte opruiming kan betekenen dat het nieuwe beeld al actief is;
  het journal bewaart dan de vorige ID voor herstel.

Bij een onderbroken upload eerst `status` lezen. Staat er `pending` of
`previous`, hervat expliciet zonder opnieuw uploaden:

```bash
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env upload --resume
```

Dit verifieert/selecteert opnieuw de al opgeslagen nieuwe afbeelding en
verwijdert pas daarna de vorige. Een gewone upload handelt nu eerst alleen de bestaande pending/cleanup-transactie af;
in diezelfde aanroep wordt geen nieuw bestand geüpload. Blijvende passe-partout/selectionfouten blijven fouten: bewaar journal en
foutmelding voor diagnose, wijzig geen IDs handmatig. Bij timeout vóór ontvangst
van een content-ID kan op de tv een onbekend uploadrestant staan. Dat wordt
nooit automatisch verwijderd; controleer het zelf in Mijn foto's op de tv.
Als het journal ontbreekt, neemt de module geen bestaande artworks in beheer.
Maak een veilige backup van token/journal; gebruik een apart opslagpad voor een
andere tv. Handmatige externe artworkverwijdering kan herstel blokkeren.

## Voorbereiding op Avian

`FrameService.upload(image_path)` is onafhankelijk van de testgenerator en CLI.
Later kan Avian dezelfde geconfigureerde `samsung-frame.png` atomisch vervangen;
upload leest één volledige snapshot en valideert PNG en resolutie. De producer
moet een tijdelijk bestand naar de doelnaam hernoemen om halve bestanden te
vermijden. De upload-/selectie-/ownershiplogica hoeft daarvoor niet te wijzigen.
Deze oplevering bevat geen Avian-code, polling, 15-minutenschema of systemd-unit.

## Minimale technische controles — overdracht 8 oktober 2026

Uitgevoerd op Windows in de bestaande Backyard-venv: 34 gerichte tests
geslaagd (12 Samsung-cases plus bestaande foundation/auth-cases), CLI-help,
PNG-generatie van exact 3840 × 2160 en pip check zonder dependencyconflicten.
De veiligheidslogica gebruikt fake-tv-tests; geen echte Samsung-netwerkcalls.

De hardwaretest blijft uitsluitend voor de eigenaar: geen verbinding met de tv,
netwerkupload, serviceherstart of wijzigingen op de Pi zijn uitgevoerd.
Gerichte fake-tv-tests controleren strikt matte=none, upload/selectiefalen,
ownership, hervatten, en behoud van productieconfiguratie. CLI-help en PNG
generatie zijn lokaal gecontroleerd, plus bestaande foundation/auth-tests.
De uploadlogica is niet gekoppeld aan Avian, database, detector of WordPress.

```bash
.venv/bin/python -m pytest tests/test_samsung_frame.py tests/test_foundation.py tests/test_auth.py -q
```

## Herstel van pending MY_F0038 — Art API 5.0.1.0

De eigenaar rapporteert QE50LS03FAUXXN (2025), Art API 5.0.1.0:
eerste upload MY_F0037 actief met matte=none; tweede upload MY_F0038 persistent
als pending, terwijl MY_F0037 actief blijft. Het uploadantwoord is dus ontvangen;
de oude transactie stokt daarna in mattecontrole of activatie. Zonder uitvoerlog
is de exacte blokkerende aanroep op die tv nog niet bewezen.

Code-inspectie van de geïnstalleerde samsungtvws 3.0.6 toont dat zowel
set_artmode als select_image wachten op een D2D-antwoord met matching request-ID.
De oude code roept bovendien set_artmode ook aan als Art Mode al aan staat,
en controleert het actieve beeld maar eenmaal onmiddellijk na selectie. Een
ontbrekende/correlatieloze setterbevestiging of vertraagde weergave verhindert
dan afronding. Onverwante events kunnen de oude per-recv-timeout blijven resetten.
Dit zijn aangetoonde zwakke plekken in de code, geen vastgestelde firmwarediagnose.

De transportadapter stuurt setters eenmaal zonder op een matching ack te wachten;
werkelijke succesbevestiging komt uitsluitend uit get_current (juiste ID en
matte=none) plus get_artmode=on. Redundante Art Mode-setters worden overgeslagen.
Activatie wordt tot BACKYARD_SAMSUNG_FRAME_TIMEOUT seconden gepolld, met begrensde
getters en vaste request-deadlines ook bij voortdurende tv-events. Expliciete
setterfouten met hun request-ID/error_code worden tijdens uitlezen gemeld.
Logs tonen stappen zoals verify-matte, read-artmode, select, verify-active en
delete-previous, zonder tokens of ruwe WebSocket-authenticatieframes.

### Exact herstel op de bestaande Pi

Stop eerst een nog hangend handmatig Samsung-commando met Ctrl+C in zijn terminal.
Laat het ownershipjournal intact. Geen generate, geen nieuwe PNG, geen handmatige
ID-wijziging en geen losse Samsung-delete. Vervang PI_HOST door je Pi-host/IP:

```bash
ssh cpvb86@PI_HOST
cd /home/cpvb86/Backyard
git pull --ff-only origin main
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env status
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env recover
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env status
```

Verwacht: active=MY_F0038, matte_id=none, managed.current=MY_F0038,
managed.pending=null en managed.previous=null. Alleen MY_F0037 wordt verwijderd,
na bevestigde activatie en een extra controle vlak vóór verwijderen.
recover leest geen PNG en uploadt niets. upload --resume blijft een alias voor
dit herstelpad. Herhaald recover is veilig en verifieert de huidige eigen afbeelding.
Bij een fout blijft het journal herstelbaar; de stap en fout worden gemeld.
Geen serviceherstart, pip-update, databasewijziging of detectorstop nodig.

### Hertest uitsluitend nadat herstel is gelukt

Upload de bestaande samsung-frame.png handmatig opnieuw (geen generate nodig):

```bash
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env upload
.venv/bin/python -m app.modules.samsung_frame --environment-file /etc/backyard/backyard.env status
```

Hier begint bewust een nieuwe transactie. Bij een overgebleven pending of previous
doet upload alleen herstel en stopt daarna: nooit herstel en nog een upload in
dezelfde aanroep. Alleen de vorige eigen ID uit het journal mag worden opgeruimd.
Na delete wordt de contentlijst gecontroleerd voordat previous wordt gewist.

Vóór een nieuwe upload wordt een upload_attempt met SHA-256 opgeslagen. Bij een
timeout/procescrash vóór een duurzaam content-ID is de uitkomst onzeker; herstart
weigert dan automatisch opnieuw uploaden. Dit voorkomt dubbele blinde retries.
Zonder betrouwbare content-ID kan een onbekend tv-restant niet veilig aan deze
module worden toegeschreven: geen automatische adoptie of delete van andere
persoonlijke foto's. Bewaar journal/log en onderzoek dat expliciet; verwijder
het upload_attempt-veld niet om blind opnieuw te proberen. Oude version-1 journals
zonder dit veld blijven leesbaar, inclusief de bestaande MY_F0038-pending.

### Technische verificatie van deze fix

Uitgevoerd: 20 gerichte Samsung-tests en CLI-help in de bestaande Windows-venv.
Tests simuleren pending-herstel zonder bestand, vertraagde activatie, setter
zonder ack, deadline bij event-flood, expliciete setter-errorcodes, onzekere
uploaduitkomst, mislukte duurzame activatiecommit en wijziging vóór cleanup.
Geen nieuwe testafbeelding gegenereerd tijdens deze herstelronde. Geen tv- of
Pi-acties uitgevoerd. De fysieke activatie op Art API 5.0.1.0 blijft de
acceptatietest van de eigenaar; logging dient om een eventuele vervolgblokkade
per concrete stap te onderzoeken.
