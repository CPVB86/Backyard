# Audiobewaring activeren

Dit is centrale Backyard-functionaliteit. BYRemote blijft alleen opnemen en
doorgeven. Het beleid staat standaard UIT, zodat een code-update op zichzelf
geen bestaande audio verwijdert. Geen nieuwe dependency of database-reset nodig.

## Bewaarregels bij activering

- Eén bruikbaar bevestigd voorbeeld en één reviewvoorbeeld per domein/soort.
  Groepeer op de effectieve wetenschappelijke naam na eventuele soortcorrectie.
- Handmatig bevestigd gaat boven automatisch geaccepteerd; binnen dezelfde
  prioriteit wint de nieuwste observationtijd. Importtijd is geen selectiecriterium.
- Reviewaudio verloopt 14 dagen na de opnametijd. Waarnemingen, aantallen en
  reviewstatus blijven staan; verlopen audio betekent geen automatische afwijzing.
- Afwijzen verbergt de audio direct. Trash blijft maximaal 24 uur en maximaal
  256 MiB. Bij ruimtegebrek kan trash dus eerder definitief verdwijnen.
- Een expliciete correctie kan trash binnen die grenzen herstellen. Vervangen
  voorbeelden worden direct na publicatie van hun opvolger opgeruimd.
- Bij onvoldoende ruimte blijft het bestaande voorbeeld staan. De nieuwe
  waarneming krijgt een terminale storage_pressure-uitkomst, zonder eindeloze
  herupload. Ook tijdelijke promotiekopieën moeten binnen het budget passen.
- Legacy Birds-audio telt mee en wordt in dezelfde soortselectie opgenomen.
  Catalogus, Generator-assets, modelbestanden en Samsung-configuratie blijven staan.

Deze selectie is bewijsbeheer, geen soortbrede goedkeuring van alle events.
Een oude waarneming krijgt niet stil de originele audio van een ander event.

## Instellingen

In `/etc/backyard/backyard.env`, gedeeld door API en beide bridges:

```dotenv
BACKYARD_AUDIO_RETENTION_ENABLED=true
BACKYARD_AUDIO_REVIEW_DAYS=14
BACKYARD_AUDIO_TRASH_HOURS=24
BACKYARD_AUDIO_RETENTION_MAX_BYTES=3221225472
BACKYARD_AUDIO_TRASH_MAX_BYTES=268435456
BACKYARD_AUDIO_MIN_FREE_BYTES=8589934592
```

Het 3-GiB-budget telt de vier Backyard-audiomappen samen, inclusief fysieke
restbestanden en tijdelijke kopieën. Onder 8 GiB vrije ruimte wordt geen extra
audio gepubliceerd. Deze limiet bevat NIET de aparte BirdNET-Go-bronclips,
logbestanden, modellen, database of DB-back-ups.

## Gecontroleerde uitrol op de centrale Pi

Werk de code bij naar deze branch/commit zonder lokale wijzigingen te overschrijven.
Gebruik de bestaande `.venv`; requirements zijn ongewijzigd. Voer eerst uit:

```bash
cd /home/cpvb86/Backyard
sudo .venv/bin/python -m operations.activate_audio_retention
```

Dit opent SQLite read-only, controleert de schema/integriteit en toont de eerste
batch. Het schrijft geen instellingen, audio of database. Controleer de getoonde
termijnen/budgetten en behoud/verwijderredenen. Daarna:

```bash
sudo .venv/bin/python -m operations.activate_audio_retention --apply
```

De activering pauzeert de API en beide bridges, maakt een consistente DB- en
environmentback-up in `retention-backups/<UTC-timestamp>/`, stelt het gedeelde
environmentbestand in, voert de eerste cleanup uit en installeert de vijfminuten-
timer. Alleen eerder actieve API/bridges worden daarna gestart. BirdNET-Go en
BYRemote blijven tijdens deze stap draaien. Er wordt geen checkpoint gereset.
De eerste uitvoering is begrensd op 20 batches van 100 acties; de timer vervolgt
eventuele achterstand. Mislukte unlinks blijven als tombstones retrybaar.

De back-up bevat metadata/configuratie, GEEN audiobestanden. Verwijderde audio
kan niet uit die DB-back-up worden teruggehaald. Bij een activeringsfout: lees de
fout en controleer servicestatus voordat je verdergaat; geen nieuwe reset uitvoeren.

```bash
systemctl is-active backyard-api backyard-birds backyard-bats
systemctl list-timers --all --no-pager backyard-audio-cleanup.timer
sudo journalctl --no-pager -u backyard-audio-cleanup.service -n 30
sudo .venv/bin/python -m app.core.cleanup_evidence --env-file /etc/backyard/backyard.env
```

De laatste opdracht is opnieuw read-only. Voor een losse begrensde apply:

```bash
sudo .venv/bin/python -m app.core.cleanup_evidence --env-file /etc/backyard/backyard.env --apply --limit 100
```

## API/WP-contract

Een observation heeft bestaande `audio_available` en `audio_url` voor het EIGEN
bewijs. Verwijderde, vervangen of trashed audio krijgt false/null. Nieuw:
`audio_retention.state`, `.reason`, `.deleted_at` en `.terminal`. Een producer
moet bij terminal=true geen automatische audio-restauratie proberen.

Het aparte endpoint
`GET /api/observations/species-audio?domain=bird&scientific_name=Parus%20major`
geeft `accepted` en `review`, elk null of een verwijzing met observation_id,
timestamp, status, source_kind en audio_url. source_kind onderscheidt observation
van legacy. Gebruik dit als een duidelijk gelabeld soortvoorbeeld, nooit als
het originele bewijs van alle historische events. Authenticatie blijft vereist.
De actieve reviewlijst sluit bewust vervallen/vervangen audio uit; de volledige
waarnemingshistorie blijft beschikbaar.

## Bronfouten en grenzen

Met hetzelfde opt-in environment houden beide Go-bridges ontbrekende bronaudio
(404/410) en specifiek ongeldige WAV/timing apart bij. Na minimaal drie pogingen
en vijf minuten gaat een NIEUW, nog niet gepost event naar een duurzame
`source-quarantine/<instance>-<id>.json`, met imported=false. Daarna gaat de
scan verder. Ops-status toont quarantineaantallen apart van succesvolle imports.
Tijdelijke netwerkfouten en onbekende beleids/configuratiefouten worden niet
stil overgeslagen. Een al bevroren pending-upload wordt NOOIT automatisch
opgegeven; als daarvan het bronbewijs verloren gaat is operatoringrijpen nodig.

BirdNET-Go blijft eigenaar van zijn eigen bronbuffer en native retentie. Deze
wijziging verwijdert daar geen bestanden en wijzigt geen Go-database. De eerder
voorgestelde aparte grens van 2 GiB/48 uur voor die buffer is NIET geïmplementeerd:
deze release biedt geen losse DELETE-audio-API. Losse filesystemdeleties zouden
kapotte Go-verwijzingen opleveren. Behoud voorlopig de bestaande native retentie
en controleer bronvolume/vrije ruimte via de beheerstatus. Dit is dus geen
gecombineerde quota voor alle opslag op de Pi.

## Verificatie

Lokaal getest met echte tijdelijke SQLite-databases en API-verzoeken, synthetische
WAVs en nagebootste model/netwerk/TV-aanroepen. Tests dekken vervanging, tijd en
prioriteit, reviewverloop, trash/herstel, soortcorrecties, legacyselectie,
opslagdruk, tombstone-herstel, read-only dry-run en bridgequarantaine.
Echte Linux-systemd-activering, ARM-audio en post-activatie-import moeten nog op
de Pi worden geverifieerd. De tests bewijzen geen langdurige hardwarebetrouwbaarheid.

Lokale uitvoering op Windows/Python 3.14.7: laatste volledige suite 583 passed,
4 skipped, één PermissionError bij atomair vervangen van een bestand in een
ongewijzigde Samsung-test. Die test slaagde daarna bij afzonderlijke hercontrole,
samen met 19 andere retentie/configuratietests. Laatste relevante gecombineerde
run: 74 passed, 2 skipped (retentie, activeringsconfiguratie, beheer en bridge).
Een bestaande CLI-test liet database/storage-environment lekken naar een latere
Samsung-configuratietest; de test registreert die twee waarden nu bij monkeypatch
zodat ze worden hersteld. Productiecode van Generator/Samsung is niet aangepast.
