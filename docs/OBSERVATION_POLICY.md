# Observations, policy en evidence

Voor de actuele gecombineerde productie-/policy-duurtest: zie
[OPERATIONS.md](OPERATIONS.md). De onderstaande losse handmatige procedure
blijft beschikbaar als diagnostiek, maar is geen verplichte voorfase.

Deze fase bouwt voort op de door de gebruiker op de Pi bewezen continue monitor
(139 tests, 3s windows, 1.5s overlap, circa 0.31s inference). De nieuwe policy,
migratie en review zijn lokaal automatisch getest. Herhaal de onderstaande
acceptatie op de echte Pi; lokale mocks bewijzen geen microfoon/ONNX-prestaties.

## Architectuur en contract

```text
continue ALSA -> vaste PCM-ring -> scheduler -> bounded inferencequeue
                                                |
                                      blijvende BirdNET-sessie
                                                |
                                       bounded policyqueue
                                                |
                          taxonomie -> plausibility/confidence -> aggregatie
                                                |
                                  permanent / review / discard
                                                |
                     bounded clipqueue -> ringextractie met pre/post-roll
                                                |
                         bounded outboundqueue -> POST observation + PUT WAV
```

Capture, inference, policy, clipextractie en HTTP hebben eigen threads. Geo
wordt vooraf berekend, nooit onder de capturelock. Geen nieuwe queue-server,
database in de detector, losse WAV-spool of opslag van de volledige stream.

Een **raw candidate** is een modelresultaat op een samplevenster. Een bestaande
**BirdDetection** is het ongewijzigde historische ingestcontract. Een nieuwe
**observation** groepeert een samenhangende reeks overlappende candidates en
heeft een verklaarbare beslissing en evidence-lifecycle. De monitor schrijft
voortaan naar `/api/observations`; oude Birds-routes blijven beschikbaar en
tonen hun oude records, inclusief de historische chimpansee. Er is geen
automatische kopie naar beide tabellen.

Generieke candidates bevatten domain (`bird`, `bat`, `unsupported`, `unknown`),
soort, confidence, source/candidate/window/stream IDs, modelversie, sample-rate,
halfopen start/eindsamples, UTC-anchor, plausibility en bronmetadata. De
BirdNET-adapter bepaalt het domein met klasse/orde uit de oorspronkelijke
acoustic-labels. Aves mag door; niet-vogels en onbekende namen worden vroeg
uitgesloten. Zelfs een bat-label uit BirdNET activeert geen batpipeline.
Een latere geschikte bat-adapter levert `bat` aan hetzelfde generieke contract.

De API vertrouwt taxonomy/plausibility van de producer, maar berekent zelf de
policybeslissing en valideert samenhang/timing; de client kan geen status of
opslagpad kiezen. Dit is een vertrouwde lokale API zonder authenticatie, geen
publieke taxonomische validatieservice. De oude handmatige Birds-ingest heeft
bewust geen nieuwe policy opgedrongen gekregen.

## Transparante initiële policy

`policy.example.json` bevat alle defaults. Kopieer het alleen voor aanpassingen
naar `policy.json` in de projectroot. API en monitor lezen hetzelfde bestand.
Alternatief: exporteer `BACKYARD_POLICY_PATH` in beide processen. Relatieve
paden zijn relatief aan de projectroot. De detector leest geen API-.env:
een afwijkend policy-pad in .env moet dus ook als environment worden
doorgegeven aan de detector. Herstart beide na een wijziging. De fingerprint
in de ingestpayload voorkomt stille verschillen (409 bij mismatch).

| Instelling | Default | Betekenis |
| --- | --- | --- |
| target_domains | bird, bat | Generieke doelgroepen |
| review_lower | 0.60 | Ondergrens voor review/ondersteuning |
| auto_single | 0.90 | Eén sterke, normale candidate automatisch |
| auto_supported | 0.85 | Beste score bij voldoende overlapondersteuning |
| unknown_single | 0.97 | Strengere enkele candidate zonder geosignaal |
| strong_unusual | 0.90 | Ongebruikelijk maar sterk: permanent + review aanbevolen |
| required_windows | 2 | Minimaal twee verschillende, werkelijk overlappende vensters |
| max_event_seconds | 9 | Geen eindeloze keten van dezelfde soort |
| max_supports | 12 | Bovengrens candidates per observation |
| max_active | 64 | Bovengrens actieve aggregaties |
| idle_seconds | 5 | Afronden als er geen nieuwe resultaten komen |
| geo_normal | 0.03 | Geo-score vanaf deze waarde: normal |
| review_days | 14 | Reviewherinnering; na afwijzing cleanup-grace |

Het maximum, de individuele scores en de ondersteuning blijven zichtbaar;
scores worden niet opgeteld of als onafhankelijke waarschijnlijkheden
vermenigvuldigd. Overlappende resultaten zijn immers gecorreleerd.
Deze waarden zijn uitlegbare startwaarden, geen op Backyard-data
gekalibreerde zekerheden.

| Uitkomst | Voorwaarde | Evidence |
| --- | --- | --- |
| auto_accepted | normal en >=0.90; of normal/unknown met >=2 supports en max >=0.85; of unknown enkele >=0.97 | permanent |
| review_recommended | unusual met max >=0.90 | permanent, geen mens nodig voor bewaring |
| pending_review | normal/unknown >=0.60 die niet automatisch voldoet | review |
| pending_review | unusual, >=2 supports en max >=0.85 maar <0.90 | review |
| discarded | buiten doelgroep, <0.60 of unusual met onvoldoende bewijs | geen rij/WAV |

Bij gemengde signalen prevaleert unusual, vervolgens unknown. Een extra
candidate onder 0.60 telt niet als confidence-ondersteuning. Reason codes
zoals `strong_unusual_preserve`, `supported_confidence`,
`gray_zone_preserve_for_review`, `below_review_lower` en
`outside_target_domain` maken de keuze controleerbaar. De oorspronkelijke
beslissing, policywaarden/fingerprint en raw supports veranderen niet bij
menselijke review; die heeft een afzonderlijke auditnotitie.

## BirdNET locatie, datum en taxonomie

Onderzocht: de officiële [birdnet 1.1.1 wheel](https://pypi.org/project/birdnet/1.1.1/),
met name geo/models/v3_0/model.py en geo/inference/session.py.
De publieke API ondersteunt:

```python
model = birdnet.load("geo", "3.0", "onnx", precision="fp32")
with model.predict_session(min_confidence=0.0, device="CPU", half_precision=False) as session:
    result = session.run(latitude, longitude, week=week)
    rows = result.to_structured_array()
```

De gepinde library haalt geomodel **3.0.4** op. Alle 48 weken worden eenmaal
voor capture berekend; inference krijgt daarna alleen een lookup. De
week wordt per candidate berekend uit zijn geschatte UTC-startdatum:
`(month - 1) * 4 + min((day - 1) // 7 + 1, 4)`. Dit zijn vier delen per maand,
**geen ISO-weeknummers**. Daarmee blijft een langlopende monitor werken over
weekgrenzen. Bron:
[geomodel date_to_week](https://github.com/birdnet-team/geomodel/blob/v3.0.4/utils/gbifutils.py).
De onderliggende gegevens zijn seizoensverwachtingen, geen actueel weer of
bewijs dat een soort aanwezig/afwezig is.

Geo is verplicht voor de productiemonitor: stel BACKYARD_MONITOR_GEOGRAPHY=1
plus geldige BACKYARD_MONITOR_LATITUDE/LONGITUDE in backyard.env in.
Ontbrekende/uitgeschakelde config stopt voor model/capture; alleen capture-only
hardwarediagnostiek mag zonder geo draaien. Zie [GEO_OPERATIONS.md](GEO_OPERATIONS.md).
De eerste keer worden geo-model/labels in dezelfde BirdNET-cache gedownload.
Een ingeschakelde maar defecte provider stopt de startup vóór capture met
een fout; geen stille degradatie. Zonder overeenkomende geo-soortnaam wordt
de individuele candidate unknown. Coördinaten/score/week/provider worden
bij geaccepteerde/review-supports bewaard.

Acoustic krijgt **geen geografisch speciesfilter**. Locale vertaalt labels,
maar vormt geen betrouwbare geografische allowlist. Wetenschappelijke namen
zijn de koppelsleutel, niet vertaalde namen. Een sterke onverwachte vogel
wordt daardoor nog steeds geanalyseerd en permanent bewaard.
De oorspronkelijke acoustic-taxonomie verschilt van de bredere geo-taxonomie:
daarom gebruikt hard domain filtering de bij het acoustic-model behorende
labels. Zie [catalogus, hash en herbouwinstructies](../detector/data/README.md).

De [acoustic preview-release](https://zenodo.org/records/20703646) is
experimenteel en heeft onder meer nog geen noise fallback class. De
productiemonitor behoudt de bewezen keuze van 3s; dat is onze
windowconfiguratie, geen bewering dat de v3-netwerkarchitectuur uitsluitend
3s kan verwerken. Upgrade library/model/taxonomie niet los van elkaar.

## Aggregatie en timing

Alleen dezelfde source, stream, domain, soort, model, rate en anchor kunnen
samenkomen. Candidates moeten op volgorde staan en echt sample-overlap hebben.
Alleen rakende bereiken worden gesplitst. Dubbele candidate/window IDs of
identieke samplebereiken tellen niet als extra ondersteuning. Bij span- of
supportlimiet begint een nieuw event; bij volle active-set sluit het oudste.

Voorbeeld bij 48kHz: windows [0,144000), [72000,216000), [144000,288000)
met scores .71/.91/.84 leveren één observation [0,288000), drie supports,
beste confidence .91. Met 1s context wordt de clip [0,336000): 7 seconden.
Pre-roll wordt bij streamstart afgeknipt op sample 0.

Een latere window-watermark zonder aansluiting rondt de observation af.
De idle-timeout vangt uitblijvende resultaten op. Nabije maar niet
overlappende roepen worden niet samengevoegd alleen omdat de soort gelijk is.
Een observation is wel een **akoestisch eventvenster**, niet exact één roep
of één individueel dier: BirdNET geeft geen nauwkeurige roepgrenzen.

API-retries houden exact event-ID/payload/WAV. Die idempotency staat los van
akoestische overlap. De candidate-ID mag niet in twee observations terugkomen.
Bij de maximale eventspan kan resterende audio-overlap tussen twee events
bestaan; samplebereiken en supports houden dit zichtbaar. Geen algemene
regel "zelfde soort binnen X seconden = duplicaat".

## Evidence en review

- Permanent: `birds/audio/YYYY/MM/DD/<id>.wav` of `bats/audio/...`.
- Review: `review/bird/audio/YYYY/MM/DD/<id>.wav` of `review/bat/audio/...`.
- Discard: alleen geaggregeerde monitortellers/laatste reason, geen DB-rij/WAV.
- Datum is de UTC-observationstart. Clipcontext blijft configureerbaar;
  pre/post elk 1s is een voorlopige waarde.
- Eén observation krijgt één WAV. PUT-retries maken geen extra kopieën.
  Verschillende soorten op dezelfde audio kunnen afzonderlijke clips houden;
  globale content-deduplicatie is niet ingevoerd.

`pending_review`-audio wordt vóór ringoverschrijving veiliggesteld.
Na 14 dagen wordt `review_due_at` overdue, maar **onbeoordeelde audio wordt
nooit automatisch verwijderd**. Zonder review kan deze voorraad dus groeien;
controleer de reviewlijst en vrije schijfruimte. Geen onbeperkte RAM-queue,
maar ook geen stil verlies van nog niet beluisterd bewijs.

Confirm controleert het WAV-bestand en de hash, promoveert naar permanent
en zet `human_confirmed`. De tijdelijke tweede kopie tijdens promotie wordt
na DB-commit opgeruimd; een achtergebleven kopie blijft als stale-key
geregistreerd voor cleanup. Bij fouten vóór commit blijft de reviewkopie
bruikbaar. Reject zet `human_rejected` + `delete_pending`; audio blijft nog
14 dagen beschikbaar, daarna is expliciete cleanup toegestaan. Beide
menselijke eindbeslissingen zijn in deze fase definitief. Een herhaling met
dezelfde actie/notitie is idempotent; een verouderde expected_status geeft 409.

Cleanup is standaard dry-run, max 100 observations per uitvoering:

```bash
python -m app.core.cleanup_evidence
python -m app.core.cleanup_evidence --apply
```

Alleen afgewezen audio na de deadline en stale promotiekopieën worden gewist.
Permanente, onbeoordeelde en historische Birds-audio blijven staan.
De rapportage noemt observation IDs, delete_rejected en stale_copy.
Na verwijderen blijven observation/supports/hash en status `deleted` behouden.
Bij onderbreking tussen unlink en commit kan dezelfde cleanup veilig opnieuw.
Dit is een handmatig commando, geen verborgen periodieke job.

## API

| Methode en pad | Gebruik |
| --- | --- |
| GET /api/observations/policy | Actieve waarden en fingerprint |
| POST /api/observations | Candidates + clipbereik; API beslist |
| PUT /api/observations/{id}/audio | Raw PCM16 mono-WAV, exact passend bij clip |
| GET /api/observations | Nieuwste 50; domain/status/limit filters, max 100 |
| GET /api/observations/{id} | Status, reasons, supports, policy en audio_url |
| GET /api/observations/{id}/audio | WAV, inclusief HTTP Range |
| GET /api/observations/review | pending_review en review_recommended |
| POST /api/observations/{id}/confirm | expected_status, optionele note |
| POST /api/observations/{id}/reject | expected_status, optionele note |

Swagger: `http://<PI-IP>:8010/docs`. Alleen in een vertrouwd LAN gebruiken.
Bij nieuwe ingest: 201; exacte retry of discard: 200; conflict: 409;
ongeldige metadata/audio: 422; te groot: 413. JSON max 256KiB en max 32
supports (actieve policy kan strenger zijn). WAV gebruikt de bestaande
BACKYARD_MAX_AUDIO_BYTES (8MiB default). Verhoog die bewust als aangepaste
eventlengte/rate/context grotere clips oplevert. POST kan al geslaagd zijn
terwijl audio nog onderweg is: `audio_url=null` betekent geen beschikbaar WAV.

## Schema en bestaande data

Schema-versie **2** voegt alleen `observations` en `observation_candidates`
toe met source/event- en candidate-uniciteit, status/domain-constraints en
tijd/review-indexen. Bestaande bird_detections en bird_audio worden niet
herschreven. Versie 0 doorloopt eerst de bestaande bevroren 0->1 migratie.

Stop API en monitor vóór `python -m app.core.migrate`. Het commando maakt
met SQLite backup een unieke `.backup-...` naast de DB, inclusief committed
WAL-data. DDL + user_version veranderen in één transactie. Onbekende schema's
worden geweigerd. Een herhaling op versie 2 verandert niets. Audio wordt niet
verplaatst of verwijderd. Bewaar bestaande .env en datamappen.

## Begrenzing en metrics

Nieuwe policyqueue: 4 batches, max 64 instelbaar met `--policy-queue`.
Aggregatie max 64 actieve events x 12 supports. Outbound blijft 16 WAVs:
bij default maximum 9s event + 2s context is dat circa 16.9MB bij 48kHz,
plus één actieve upload. Ring blijft 60s / 5.76MB.
Minimaal ringbudget is max_event + idle + pre/post (default 16s), plus
praktisch voldoende marge voor inference/queueachterstand. Structureel
langzamere verwerking wordt niet opgelost met steeds meer buffer.

| Teller | Interpretatie |
| --- | --- |
| raw_candidates / detections | Alle resultaten boven detector-base-threshold |
| policy_candidates | Resultaten daadwerkelijk door policy verwerkt |
| unsupported_domain_candidates | Vroeg uitgesloten taxa |
| discarded_candidates | Taxon/confidence-policy discard |
| aggregation_count | Extra supports samengevoegd in afgeronde events |
| observations_planned | Policy heeft observation/clip ingepland |
| observations_created | POST succesvol ontvangen, eenmaal per uploadtaak |
| auto_accepted / review_observations | Policy-uitkomsten vóór HTTP |
| permanent_clips / review_clips | Geslaagde WAV-uploads per evidencecategorie |
| discarded_clips | API wees ingeplande observation alsnog af |
| policy_batches_dropped | Oudste policybatch vervallen bij volle queue |
| plausibility_normal/unusual/unknown | Afgeronde policy-events per signaal |
| *_depth / *_oldest_seconds | Inference-, policy-, clip- en outboundbacklog |

Deze tellers zijn proceslokaal en resetten bij restart. Planned is niet
hetzelfde als opgeslagen. Raw = discarded + supports van observations is
alleen vergelijkbaar als active/pending en drop-tellers worden meegenomen.
Een kwijtgeraakte succesvolle POST-response kan buiten het zicht van de
monitor vallen. Cleanup-deleties staan in het cleanup-rapport, niet in
monitortellers van een ander proces. Geen raw database voor irrelevante taxa.

Bestaande capture/inference/HTTP-metrics blijven bestaan. Bij volle queue
vervalt oudste wachtende werk met teller; actieve capture wacht niet.
Audio die niet meer in de ring staat wordt niet deels/verkeerd geüpload.
HTTP-uitval kent begrensde retries en verlies; er is geen duurzame replay.
Ctrl+C stopt begrensd en rapporteert ook policy_batches_abandoned en
aggregation_abandoned_candidates. Wachtend werk wordt niet volledig gedraind.

## Volledige Raspberry Pi-acceptatie

Voer alles uit vanuit de bestaande **Backyard-repository-root**.
Gebruik de bewezen ALSA-devicewaarde als deze afwijkt van het onderstaande.
Deze test voegt herkenbare synthetische records met stille WAVs toe;
dat zijn geen echte vogel/vleermuiswaarnemingen.

### 1. Stop, update, migreer en test

Stop eerst monitor én API in hun eigen terminals met Ctrl+C.
Voer onderstaande blokken achtereenvolgens uit; ga alleen verder als het
vorige commando slaagt. Bij lokale Git-wijzigingen: bewaar die en los ze
eerst op; geen reset/force.

```bash
git status
git switch main
git pull --rebase origin main
git log -1 --format='%H %s'
source .venv/bin/activate
python --version
python -m pip install -r requirements-dev.txt
python -m pip check
python -m app.core.migrate
pytest -v -W error
```

Controleer migratieoutput: backup bij oude schema's, schema 2, geen reset.
Op de doel-Pi hoort Python 3.13.5 te staan. Alle tests moeten slagen zonder
warnings. Bestaande historische detecties/audio blijven via de oude routes
bereikbaar; open desgewenst vóór en na migratie dezelfde bekende audio-URL.

Geen nieuwe dependencies. Controleer de bestaande afzonderlijke detectorvenv:

```bash
.venv-detector/bin/python -m pip check
.venv-detector/bin/python -c 'from importlib.metadata import version; print("birdnet", version("birdnet"), "onnxruntime", version("onnxruntime"))'
```

Verwacht birdnet 1.1.1 en onnxruntime 1.30.0. Installeer ML niet in de API-venv.

### 2. API starten (terminal A)

```bash
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --no-access-log
```

Terminal C (ook repository-root):

```bash
source .venv/bin/activate
curl --fail http://127.0.0.1:8010/api/health
curl --fail http://127.0.0.1:8010/api/observations/policy
```

Verwacht health status/database ok. Swagger op `http://<PI-IP>:8010/docs`.

### 3. Geosignaal en continue monitor (terminal B)

Gebruik de echte tuincoördinaten (decimale graden, punt als decimaalteken).
De eerste providercontrole laadt/cachet geo vóór de monitor start:

```bash
read -r -p 'Latitude van de tuin: ' LATITUDE
read -r -p 'Longitude van de tuin: ' LONGITUDE
export BACKYARD_MONITOR_LATITUDE="$LATITUDE"
export BACKYARD_MONITOR_LONGITUDE="$LONGITUDE"
export BIRDNET_APP_DATA="$PWD/.detector-test/model-cache"
.venv-detector/bin/python - <<'PY'
import os
from detector.providers import GeoPlausibility
provider = GeoPlausibility(os.environ["BACKYARD_MONITOR_LATITUDE"], os.environ["BACKYARD_MONITOR_LONGITUDE"])
print("Geo geladen:", len(provider.scores), "soorten,", 48, "weekbins")
PY
.venv-detector/bin/python -m detector.monitor \
  --device 'plughw:CARD=Device,DEV=0' \
  --rate 48000 --channels 1 --window 3 --overlap 1.5 \
  --threshold 0.60 --ring-seconds 60 --pre-roll 1 --post-roll 1 \
  --geography --api-url http://127.0.0.1:8010
```

Voor bewust testen zonder geo: laat `--geography` weg en zorg dat
BACKYARD_MONITOR_GEOGRAPHY niet op true staat. Dan is unknown verwacht.
De locatie is aanvullend bewijs, geen harde speciesfilter.

Laat minimaal 10 minuten lopen, daarna langer onder normale belasting.
Gebruik herkenbare echte vogelgeluiden bij de microfoon. Een stille tuin
garandeert geen detectie; beoordeel de daadwerkelijke WAV, niet alleen het label.
Een sterke normale vogel hoort auto_accepted te worden. Een strong unusual
blijft permanent met review_recommended. Zonder geo gelden unknown-thresholds.

### 4. Werkelijke observations en audio (terminal C)

```bash
curl --fail 'http://127.0.0.1:8010/api/observations?domain=bird&limit=10' | python -m json.tool
curl --fail http://127.0.0.1:8010/api/observations/review | python -m json.tool
python - <<'PY'
import json, urllib.request, wave
from pathlib import Path
base = "http://127.0.0.1:8010"
with urllib.request.urlopen(base + "/api/observations?domain=bird&limit=100", timeout=5) as r:
    items = json.load(r)
item = next((i for i in items if i["source"] == "backyard-birdnet-monitor" and i["audio_url"]), None)
assert item, "Nog geen echte monitor-observation met audio; laat bekende vogelgeluiden horen en probeer opnieuw."
path = Path(".detector-test/observation-check.wav")
path.parent.mkdir(exist_ok=True)
with urllib.request.urlopen(base + item["audio_url"], timeout=5) as r:
    path.write_bytes(r.read())
with wave.open(str(path)) as wav:
    assert wav.getnframes() == item["clip"]["end_sample"] - item["clip"]["start_sample"]
    assert wav.getframerate() == item["clip"]["sample_rate"]
    assert wav.getnchannels() == 1 and wav.getsampwidth() == 2
print(item["id"], item["scientific_name"], item["status"], item["decision"])
print("Audio:", base + item["audio_url"])
print("Lokaal:", path)
PY
aplay .detector-test/observation-check.wav
```

Open de getoonde audio-URL vanaf een andere computer door 127.0.0.1 te
vervangen door het Pi-IP. Controleer supports, plausibility-score/week, reasons,
clipbereik en audio. Meerdere overlappende supports kunnen één observation
ondersteunen. Een echte call hoeft niet in alle windows gedetecteerd te worden.

### 5. Alle policy/reviewpaden expliciet testen (terminal C)

De CLI is standaard dry-run en stuurt uitsluitend met `--send`.
Iedere uitvoering gebruikt nieuwe, herkenbaar synthetische IDs.

```bash
python -m detector.debug_observation --case bird-review
mkdir -p .detector-test
python -m detector.debug_observation --case bird-auto --send > .detector-test/auto.json
python -m detector.debug_observation --case bird-overlap --send > .detector-test/overlap.json
python -m detector.debug_observation --case bird-unusual --send > .detector-test/unusual.json
python -m detector.debug_observation --case bird-review --send > .detector-test/confirm.json
python -m detector.debug_observation --case bird-review --send > .detector-test/reject.json
python -m detector.debug_observation --case bat --send > .detector-test/bat.json
python -m detector.debug_observation --case chimpanzee --send > .detector-test/chimpanzee.json
```

Met ongewijzigde defaults controleert dit blok alle uitkomsten, retrieval en
confirm/reject. De twee review-WAVs worden vóór review gedownload. Het zijn
stiltebestanden, uitsluitend voor opslag/API-controle:

```bash
python - <<'PY'
import json, urllib.request
from pathlib import Path
base = "http://127.0.0.1:8010"
def get(path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return json.load(r)
def post(path, payload):
    req = urllib.request.Request(base + path, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)
cases = {name: json.loads(Path(".detector-test/" + name + ".json").read_text())
         for name in ("auto", "overlap", "unusual", "confirm", "reject", "bat", "chimpanzee")}
for name, status, evidence in (("auto","auto_accepted","permanent"),
                              ("overlap","auto_accepted","permanent"),
                              ("unusual","review_recommended","permanent"),
                              ("confirm","pending_review","review"),
                              ("reject","pending_review","review"),
                              ("bat","auto_accepted","permanent")):
    item = cases[name]
    assert item["status"] == status and item["evidence_kind"] == evidence, item
    assert all(c["metadata"]["synthetic"] for c in item["candidates"])
    with urllib.request.urlopen(base + item["audio_url"], timeout=5) as r:
        audio = r.read()
    assert audio[:4] == b"RIFF"
    Path(".detector-test/" + name + ".wav").write_bytes(audio)
    print(name, item["id"], status, base + item["audio_url"])
assert cases["overlap"]["supporting_candidate_count"] == 3
assert cases["bat"]["domain"] == "bat"
chimp = cases["chimpanzee"]
assert chimp["status"] == "discarded" and chimp["id"] is None and chimp["audio_url"] is None
assert chimp["decision"]["reasons"] == ["outside_target_domain"]
review_ids = {i["id"] for i in get("/api/observations/review?limit=100")}
assert {cases[n]["id"] for n in ("confirm","reject","unusual")} <= review_ids
for name, action, status, evidence in (("confirm","confirm","human_confirmed","permanent"),
                                      ("reject","reject","human_rejected","delete_pending")):
    identity = cases[name]["id"]
    result = post("/api/observations/" + identity + "/" + action,
                  {"expected_status":"pending_review", "note":"Synthetic Pi acceptance test"})
    assert result["status"] == status and result["evidence_kind"] == evidence, result
    assert get("/api/observations/" + identity)["status"] == status
    print(action, status, evidence, "cleanup_after:", result["cleanup_after"])
print("PASS: auto, overlap, unusual, review/audio, confirm, reject, bat en chimpansee-exclusion")
PY
python -m app.core.cleanup_evidence
```

Direct na reject mag de WAV nog bestaan: cleanup_after is 14 dagen later.
De dry-run verwijdert niets. De aparte geautomatiseerde test simuleert de
latere datum en bewijst echte cleanup; wijzig de Pi-klok/database niet.
Synthetische records blijven duidelijk gemarkeerd. Er is geen automatische
productie-injectie en er wordt geen bat-detector geladen.

### 6. Continuïteit, uitval en stoppen

Controleer gedurende de echte monitorrun:

- capture_gaps, alsa_overruns, ring_overruns = 0;
- windows_dropped, policy_batches_dropped, clips_expired/dropped,
  uploads_dropped/failed = 0 bij normale belasting;
- inference/policy/clips/outbound_depth en oldest_seconds groeien niet structureel;
- realtime_ratio blijft ruim onder 1 (de eerdere Pi-baseline was circa 0.21);
- raw/support/observation/evidence-tellers zijn verklaarbaar;
- normale hoge matches verschijnen zonder menselijke actie;
- reviewaudio blijft via de API bereikbaar.

Controleer RAM, CPU en temperatuur indien gewenst:

```bash
ps -eo pid,ppid,comm,rss,pcpu,args --sort=-rss | head -n 20
cat /sys/class/thermal/thermal_zone0/temp
df -h .
```

Uitvaltest: stop alleen API (Ctrl+C in terminal A), laat monitor kort doorlopen.
Capture/windows_processed moeten doorgaan; HTTP-failures zijn nu verwacht.
Herstart API met stap 2: nieuwe observations moeten weer aankomen.
Niet-verzonden clips worden niet duurzaam gereplayed.

Stop vervolgens monitor met Ctrl+C in terminal B. Verwacht exitcode 130,
geen traceback en normaal shutdown_threads_remaining=0. Abandoned-tellers
mogen wachtend werk tonen. Geen automatische delete van al verzonden records.

```bash
pgrep -af 'arecord|detector.monitor'
```

Geen eigen achtergebleven monitor/capture. Stop desgewenst API met Ctrl+C.
Daarna eindigt deze fase: geen systemd, frontend of bat-hardware installeren.

## Troubleshooting en grenzen

- Migration required: stop writers, voer expliciete migratie uit; nooit DB wissen.
- 409 Policy mismatch: dezelfde policyfile/environment in API en monitor,
  beide herstarten. Exacte oude ingest-retries blijven idempotent na een policywijziging.
- Geen auto_accept bij .94 met geo uit: unknown vraagt .97 single of .85
  met twee echte overlappende supports. Het bewijs gaat wel naar review.
- Geen geo-match: unknown; geen verzonnen geografische afwijzing.
- Geen audio_url: PUT nog onderweg of mislukt; controleer HTTP-fouten/storage.
- Ring overrun/drops: achterstand onderzoeken; geen onbegrensde wachtrij toevoegen.
- Review groeit: beluisteren en beoordelen. Bewaring zonder review en
  onbeperkt weinig schijfruimte kunnen niet allebei worden gegarandeerd.
- Speciesrename/cataloogupgrade vereist expliciete compatibiliteitscontrole.
- Geen exacte roepgrenzen, individuele dieren, hardwaretimestamps of
  gegarandeerde herkenning. Overlap is aanvullende, gecorreleerde ondersteuning.
- Geen duurzame offline replay, automatisch gapherstel, authentication,
  power-loss-garantie of verplicht menselijke review.
- Echte nieuwe geo/ONNX-runtime en langdurige Pi-belasting moeten met deze
  procedure worden geverifieerd; unit/integratietests vervangen dat niet.

## Uitgevoerde oplevercontrole (1 oktober 2026)

- Windows, Python 3.14.7, pytest 9.1.1: **218 passed in 5.79s**, geen warnings
  met `.venv/Scripts/python -m pytest -v -W error` (139 bestaande + 79 nieuwe tests).
- `python -m pip check`: geen dependencyconflicten. Geen requirements gewijzigd.
- Echte lokale Uvicorn-API met aparte tijdelijke SQLite/storage: alle zeven
  synthetische CLI-cases, WAV-retrieval, het exacte bovenstaande
  confirm/reject-acceptatieblok en cleanup dry-run geslaagd.
- Gepinde bron-CSV opnieuw gedownload, SHA256 gecontroleerd en catalogus
  byte-exact geregenereerd: 11.560 taxa.
- Migratie vanaf schema 1 bewaart historische Pan troglodytes .68 en WAV-bytes;
  rollback en bestaande versie-0-migratietests slagen.
- Nieuwe native BirdNET-geo/ALSA-uitvoering op ARM64 is hier niet uitgevoerd.
  Gebruik de volledige Pi-procedure hierboven voor die platformacceptatie.


## Authenticated Birds-reviewflow voor API-consumers

Gebruik server-side `Authorization: Bearer <BACKYARD_API_TOKEN>` voor alle
onderstaande requests, inclusief audio. Geen publieke API of WordPress-code.

- Lijst: `GET /api/observations?domain=bird&status=pending_review&limit=50`.
  Bestaande nieuwste-eerst sortering, limit 1..100 (standaard 50).
  `/api/observations/review?domain=bird` blijft de bredere bestaande lijst van
  zowel pending_review als review_recommended; gebruik voor deze flow het
  expliciete statusfilter hierboven.
- Totaal: `GET /api/observations/count?domain=bird&status=pending_review` geeft
  `{"count": 12}` (of 0). Dit is een enkele SQL COUNT met dezelfde filters,
  zonder observations, candidates, labels of audio te laden. Geen lijstlimiet.
  Domain/status zijn optioneel zoals bij de lijst; ongeldige waarden geven 422.
  Het totaal kan tussen requests veranderen doordat ingest/review doorgaat.
- Detail: `GET /api/observations/{id}`.
- Audio: `GET /api/observations/{id}/audio`, via het bestaande `audio_url`.
  Ondersteunt de bestaande WAV/Range-response. Stuur de header ook bij audio;
  een kale browser/audio-tag-URL draagt het token niet vanzelf mee.
- Bevestigen: `POST /api/observations/{id}/confirm`.
- Afwijzen: `POST /api/observations/{id}/reject`.

De laatste twee gebruiken ongewijzigd de bestaande reviewbody, bijvoorbeeld:

```json
{"expected_status": "pending_review", "note": "Beluisterd"}
```

Een gewijzigde observation geeft 409: ververs dan de lijst/detail. Confirm
vereist intacte audio en maakt/behoudt die permanent; status human_confirmed.
Reject geeft human_rejected/delete_pending en laat de bestaande expliciete
cleanup na de bewaartermijn intact. Reject wist dus niet direct de WAV.

De bestaande observation-responses (lijst/detail/ingest/audio-upload/review)
behouden alle velden en krijgen alleen deze aanvullende aliases:

| Veld | Betekenis |
| --- | --- |
| timestamp | start_at: begin van de observation, ISO 8601 UTC |
| common_name_en | ongewijzigde opgeslagen common_name |
| confidence | best_confidence, getal 0..1 |
| supports | supporting_candidate_count, integer; details blijven in candidates |
| evidence | evidence_kind: permanent, review, delete_pending of deleted |
| audio_available | boolean: audio-metadata heeft status available en evidence is niet deleted |

`id`, `scientific_name`, `common_name_nl`, `common_name_de`, `status`, `audio`
en `audio_url` bestonden al en blijven behouden. NL/DE gebruiken de bestaande
BirdNET-bron en Engelse fallback. Audio-availability betreft de geregistreerde
uploadstatus: de lijst doet geen filesystemscan; de audio-GET controleert ook
het bestand en kan 404 geven bij ontbrekende/verwijderde evidence.
Geen schema-, policy-, confidence-, detector-, auth- of systemd-wijzigingen.
