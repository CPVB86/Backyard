# Geo/seizoensplausibility herstellen

## Diagnose en bron

De eerder geleverde backyard.env.example zette BACKYARD_MONITOR_GEOGRAPHY=0.
De monitor maakte dan geen GeoPlausibility-provider; predictions zonder signaal
werden terecht unknown maar daardoor ging de unusual-discardtak nooit lopen.
Latitude/longitude alleen invullen activeerde geo niet. Er was geen zichtbare
provider/configstatus in operations.status. Dit verklaart unknown bij deployment
met die defaults. De daadwerkelijke /etc-config op de Pi is vanuit deze
ontwikkelmachine niet gelezen; controleer hem met de nieuwe status en journal.

De al bestaande BirdNET 1.1.1-aanroep is aan het exacte wheel gecontroleerd:
`birdnet.load("geo", "3.0", "onnx", precision="fp32")`, geomodel v3.0.4;
`predict_session(min_confidence=0.0, device="CPU", half_precision=False)`;
`session.run(latitude, longitude, week=1..48).to_structured_array()`.
0.0 blijft een bekende onwaarschijnlijke score, geen unknown. Lege, onvolledige
of niet-eindige modeluitvoer stopt voortaan startup. Alleen werkelijk niet
in het geo-model gevonden wetenschappelijke namen krijgen unknown met reden.
Acoustic inference blijft alle soorten scoren; geen geographic allowlist.

De bestaande formule is correct volgens
[geomodel v3.0.4 date_to_week](https://github.com/birdnet-team/geomodel/blob/v3.0.4/utils/gbifutils.py):
`(month-1)*4 + min((day-1)//7+1, 4)`.
Datum = UTC-starttijd van de candidate, uit de stream/samplepositie.
1-7, 8-14, 15-21 en 22-maandeinde zijn vier perioden per maand; geen ISO-weken.
Alle 48 scores worden vooraf geladen, dus een periodewissel vereist geen restart.
2 oktober 2026 is periode 37. Naive timestamps worden afgewezen.

[Publieke BirdNET geo-API](https://birdnet-team.github.io/birdnet/birdnet.geo.models.v3_0.html).
Deze scores zijn modelmatige occurrence, geen bewijs van feitelijke aanwezigheid.

## Bestaande beslisregels blijven intact

Bij standaardpolicy: occurrence >= 0.03 normal, daaronder unusual.
Normal volgt bestaande confidence/aggregatie. Unusual < 0.90 valt af,
behalve >= 2 verschillende supporting windows met best confidence >= 0.85:
dat blijft pending_review. Unusual >= 0.90 blijft review_recommended/permanent.
Onvoldoende unusual gaat niet naar HTTP/observation of clipextractie/WAV.
Bestaande policy.json blijft leidend; thresholds/fingerprint niet gewijzigd.

## Configuratie en bewuste deployment

Stop alleen de detector, behoud alle data en je API-token:

```bash
cd /home/cpvb86/Backyard
sudo systemctl stop backyard-detector
git status
git pull --rebase origin main
source .venv/bin/activate
pytest -v -W error
python -m pip check
sudoedit /etc/backyard/backyard.env
```

Zet BACKYARD_MONITOR_GEOGRAPHY=1 en de twee coordinaatvariabelen op de werkelijke
locatie (decimale graden, punt als decimaalteken). Bewaar de bestaande
BIRDNET_APP_DATA, API-token en alle overige instellingen. Kopieer de lege
voorbeeldconfig niet over je productieconfig. Geen requirements/migratie,
unitwijziging of daemon-reload nodig.

Verifieer vervolgens de echte geo-provider in de detectorvenv, zonder capture:

```bash
.venv-detector/bin/python - <<'PY'
import os
from datetime import datetime, timezone
from operations.environment import read_environment
os.environ.update(read_environment('/etc/backyard/backyard.env'))
from detector.providers import GeoPlausibility, plausibility_at
from detector.birdnet_adapter import Prediction
from observations.policy import Policy
geo = GeoPlausibility(os.environ['BACKYARD_MONITOR_LATITUDE'], os.environ['BACKYARD_MONITOR_LONGITUDE'])
print(geo.status)
now = datetime.now(timezone.utc).isoformat()
for name in ('Parus major', 'Ara macao', 'Hirundo rustica'):
    prediction = geo.annotate([Prediction(name, name, .7, 0, 3)])[0]
    print(name, plausibility_at(prediction.plausibility, now, Policy.load()))
PY
```

De eerste run kan geo-modeldata downloaden. Verwacht ready/active, 48 perioden,
locatie en finite scores. Beoordeel een plaatselijke soort tegenover een exoot;
een trekvogel kan afhankelijk van de actuele periode nog plausibel zijn.
Dit is modeldiagnostiek, geen handmatige soort-override.
Start pas na succesvolle verificatie:

```bash
sudo systemctl start backyard-detector
sudo journalctl -u backyard-detector -n 50 --no-pager
sudo .venv/bin/python -m operations.status
```

Status toont de feitelijk geladen locatie/provider/periode via de actuele
service-invocation, plus configured_latitude/longitude. enabled is config,
active/ready bewijst dat de provider in de worker geladen is. Config/runtime-
verschillen, ontbrekende/oude runtime-informatie of providerfouten geven ATTENTION.
Ook zonder candidates is de geo-status zichtbaar. Startupfouten staan in journal;
systemd blijft met de bestaande vertraging proberen. API hoeft hiervoor niet te
herstarten. Wacht op verse oplopende windows en plausibility normal/unusual;
bestaande 24u-counters bevatten ook de oude unknowns.

## Oude reviewqueue: niets automatisch verwijderen

Er is geen herclassificatie/migratie of cleanupwijziging uitgevoerd.
De bestaande reject-route kan pending_review naar human_rejected zetten en
plant evidence op delete_pending; cleanup_evidence verwijdert alleen zulke
afgewezen audio na de bewaartermijn, in batches van maximaal 100. Observation-
records blijven behouden en de bestaande cleanup verwijdert nooit pending_review.
Dat kan de reviewqueue leegmaken, maar voldoet niet aan fysiek wissen van records.

Voor de gevraagde eenmalige fysieke verwijdering ontbreekt dus een kleine,
expliciete onderhoudsactie: dry-run met vaste ID-selectie/snapshot voor
`domain=bird AND status=pending_review AND evidence_kind=review`, aantallen/paden,
SQLite- en reviewaudio-backup; daarna expliciete apply, status opnieuw controleren,
alleen die review-WAVs en bijbehorende supports/observation-rijen verwijderen.
Laat API/detector tijdens die onderhoudsactie gestopt. Geen permanent/historisch
Birds-audio, review_recommended of inmiddels bevestigde records meenemen.
Deze optie is conform opdracht alleen beschreven, niet gebouwd of uitgevoerd.
Geen brede SQL DELETE of directoryverwijdering gebruiken.

## Verificatiegrens

Gerichte tests gebruiken voorspelbare geo-scores voor lokaal/exoot/buiten-seizoen,
controleren de publieke BirdNET-call, 48 perioden, nul/invalid scores, config en
status, plus dat unusual-discard geen clipjob maakt. De gehele suite wordt op
Windows uitgevoerd. Echte Pi/ONNX/providerresultaten voor de tuin moeten met het
bovenstaande commando worden bevestigd; die zijn niet vanaf Windows gemeten.
