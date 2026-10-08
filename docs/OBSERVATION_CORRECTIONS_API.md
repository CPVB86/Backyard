# Observation-correcties voor WordPress

## Bestaande flow en compatibiliteit

De bestaande Bearer-authenticatie blijft verplicht. De API heeft geen individuele
WordPress-gebruikerssessies: WordPress controleert zelf beheerrechten en nonce en
roept de API server-side aan. `actor` is een door die vertrouwde client opgegeven
beheerder-ID, geen zelfstandig bewijs van authenticatie of autorisatie.

Normale `/confirm` en `/reject` blijven compatibel en gebruiken expected_status.
Een eerdere menselijke beslissing wijzigen gebeurt expliciet via `/correct`.
Geen bulkcorrectie, historische herclassificatie of nieuwe observation wordt uitgevoerd.

## Detail ophalen, daarna corrigeren

GET `/api/observations/{id}` levert naast de ongewijzigde oorspronkelijke
scientific_name/common_name/confidence/timestamps/decision/policy/candidates:

- `status`: actuele bestaande lifecycle-status;
- `effective_identity`: domain, biologische scientific_name, en identity_override;
- `review_version`: integer, aanvankelijk 0 voor legacy of onbeoordeelde records;
- `review_modified_at`: laatste reviewtijd of null;
- `review`: actuele menselijke beslissing en auditgeschiedenis;
- `correction_capabilities`: ondersteunde acties en mogelijke identiteitsoverrides.

Alle observation-lijsten gebruiken dezelfde serializer. Een capability vervangt
geen servervalidatie: bevestigen vereist nog steeds beschikbare, intacte audio.
Bestaande review_capabilities voor de review-inbox blijven ongewijzigd.

POST `/api/observations/{id}/correct`, Content-Type application/json:

```json
{
  "action": "confirm",
  "expected_status": "human_confirmed",
  "expected_version": 1,
  "request_id": "4e40d886-6c02-4a82-9bca-b095ea25b3d2",
  "identity_override": "otje",
  "note": "Na beluisteren herkend als onze kip",
  "actor": "wordpress:42"
}
```

Verkorte response (het werkelijke antwoord bevat ook alle oorspronkelijke velden):

```json
{
  "status": "human_confirmed",
  "scientific_name": "Gallus gallus",
  "review_version": 2,
  "effective_identity": {
    "domain": "bird",
    "scientific_name": "Gallus gallus",
    "identity_override": "otje"
  },
  "review": {
    "action": "confirm",
    "identity_override": "otje",
    "note": "Na beluisteren herkend als onze kip",
    "reason": "human_confirmation",
    "revision": 2,
    "at": "2026-10-08T12:00:00+00:00",
    "history": [
      {
        "observation_id": "<id>",
        "revision": 2,
        "old_status": "human_confirmed",
        "old_identity": null,
        "new_status": "human_confirmed",
        "new_identity": "otje",
        "at": "2026-10-08T12:00:00+00:00",
        "actor": "wordpress:42",
        "note": "Na beluisteren herkend als onze kip",
        "request_id": "4e40d886-6c02-4a82-9bca-b095ea25b3d2"
      }
    ]
  }
}
```

De volledige history behoudt eerdere gebeurtenissen en bevat ook een request_hash.
Een bestaande legacy-review zonder history blijft bij de eerste correctie als
previous_review in het audit-event bewaard. Audit en status worden samen in de
bestaande review-JSON opgeslagen, binnen één BEGIN IMMEDIATE-transactie.

Voor Otje verwijderen: `action: confirm`, `identity_override: null`. Ook weglaten
van identity_override betekent geen individuele identiteit; het is geen PATCH.
Voor afwijzen: `action: reject`, `identity_override: null`.
Voor opnieuw bevestigen na afwijzen: `action: confirm`, de gewenste override of null.
Gebruik telkens de laatst opgehaalde status/versie en een nieuwe request_id.
Alleen bird en de bestaande Gallus-allowlist ondersteunen de Otje-keuze.

## Retry, fouten en audit

- Zelfde verzoek-ID en dezelfde genormaliseerde payload, nog de laatste wijziging:
  200 met actuele response, zonder extra audit-event of versie.
- Verouderde versie/status, hergebruikte ID met andere payload, of herhalen van
  een oud verzoek nadat een latere correctie plaatsvond: 409. Eerst opnieuw ophalen;
  nooit automatisch een nieuwe versie invullen en stilzwijgend opnieuw indienen.
- Ongeldige identiteit, niet-toegestane soort/domein, reject met override: 422.
- Ontbrekende observation/audio of reeds gewiste audio: 404.
- Beschadigde/ontbrekende fysieke evidence terwijl metadata bestaat: 409.
- Ontbrekende/ongeldige Bearer: bestaande 401.

## Audio, Generator en aggregaties

Correcties bewaren audio, annuleren een eerdere cleanup-deadline en verwijderen
geen Generator-assets. Opnieuw bevestigen is onmogelijk als eerdere cleanup de
audio al gewist heeft. Oude gewone reject behoudt zijn bestaande bewaartermijn;
de nieuwe correctieroute plant geen audiodeletie. Bestaande confirm-promotie naar
permanente evidence blijft behouden. Originele BirdNET-data worden nooit gewijzigd.

De correctieroute start geen Generator-job. Atlas kiest bij expliciete Otje de
bestaande otje_perched/otje_flight-assets, met de bestaande fallback als ze ontbreken.

Statistieken zijn live SQL op auto_accepted/human_confirmed. Afwijzen verdwijnt
meteen uit geldige aantallen, eerste/laatste waarneming, tijdvakken en Atlas.
Biologische species-detailtotalen omvatten alle individuen van die soort; Atlas
splitst zijn weergavegroepen op identiteit. Elke observation telt eenmaal.
`species_count` telt nu unieke biologische soorten; `display_identity_count`
telt weergavegroepen. Dit corrigeert de eerdere telling van Gallus plus Otje als
twee soorten. Algemene `/observations/count` zonder status blijft het totale
recordaantal inclusief afgewezen records; dat is geen geldige-waarnemingenstatistiek.

AvianVisitors ververst overzichten elke 60 seconden of bij handmatig verversen.
Detail wordt bij iedere opening opnieuw opgehaald, zonder langdurige detailcache.
Een al open detailvenster moet opnieuw worden geopend om een externe correctie te zien.
WordPress moet na 200 eigen lijst/detail/statistieken verversen en eventuele eigen
caches invalideren. Deze stap voegt geen WordPress-beheerknoppen toe.

## Deployment

Geen schema- of datamigratie nodig: versie en audit leven in Observation.review.
Geen historische records automatisch aanpassen. Updates zijn additief voor API-
consumenten, behalve de beschreven correctie van species_count.

```bash
cd /home/cpvb86/Backyard
git pull --ff-only
sudo systemctl restart backyard-api.service
```

Detector hoeft niet te herstarten: ingestpolicy/fingerprint zijn niet gewijzigd.
Herlaad de AvianVisitors-pagina voor de nieuwe JavaScript-code. Voer echte correcties
alleen uit via een geautoriseerde beheeractie volgens dit contract.


## Definitief contract: soortcorrectie

Bestaand endpoint: `POST /api/observations/{id}/correct`, Bearer-auth verplicht.
De doelsoort wordt gekozen uit de bestaande bird-catalogus. De bestaande
`GET /api/avian-visitors/search?q=Koolmees` geeft catalogusresultaten met
scientific_name; stuur die exacte naam terug. Niet-bestaande soorten en namen die
alleen onder domain=bat bestaan worden geweigerd met 422.

| Veld | Type | Betekenis |
|---|---|---|
| action | confirm / reject | Gewenste menselijke beslissing; verplicht |
| expected_status | bestaande observation-status | Laatst gelezen status; verplicht |
| expected_version | integer >= 0 | Laatst gelezen review_version; verplicht |
| request_id | UUID | Uniek per handmatige actie, identiek bij retry; verplicht |
| scientific_name_override | string of null | Weggelaten: huidige effectieve soort behouden. String: soort uit bird-catalogus. Null: oorspronkelijke BirdNET-soort herstellen. |
| identity_override | otje of null | Weggelaten of null: individuele identiteit verwijderen. Otje alleen geldig voor de effectieve Gallus-soort. |
| note | string, maximaal 1000 tekens | Optionele reden; standaard leeg |
| actor | string of null, maximaal 255 tekens | Optionele beheerder-ID van de vertrouwde consumer |

Voorbeeld: eerder bevestigde Gallus-detectie corrigeren naar Koolmees:

```json
{
  "action": "confirm",
  "expected_status": "human_confirmed",
  "expected_version": 2,
  "request_id": "5a71e10e-3de9-4a37-9d36-82d87e325bf9",
  "scientific_name_override": "Parus major",
  "identity_override": null,
  "note": "Bij beluisteren vastgesteld: Koolmees",
  "actor": "wordpress:42"
}
```

Verkorte response:

```json
{
  "status": "human_confirmed",
  "scientific_name": "Gallus gallus",
  "review_version": 3,
  "effective_identity": {
    "domain": "bird",
    "scientific_name": "Parus major",
    "identity_override": null
  },
  "review": {
    "action": "confirm",
    "scientific_name_override": "Parus major",
    "revision": 3
  }
}
```

De volledige response bevat de bestaande auditvelden, history, review_modified_at
en oorspronkelijke detectie/evidence. Top-level scientific_name/common_name zijn
bewust nog de originele BirdNET-waarden; consumers tonen de actuele soort via
`effective_identity.scientific_name`. Soortlabels/assets komen uit de centrale
catalogus/Generator voor deze effectieve soort. Bestaande assets worden niet gewist
en correctie start geen nieuwe generatie.

Soortcorrectie en eventuele verwijdering van Otje gebeuren atomair. Om een bestaande
Otje-waarneming naar een andere soort te corrigeren: identity_override weglaten of
null meesturen. Een expliciete combinatie van otje met een niet-Gallus-doelsoort
geeft 422 zonder mutatie. Weglaten van scientific_name_override bij een volgende
statuscorrectie behoudt de gekozen soort, ook bij afwijzen en opnieuw bevestigen.

History bevat per nieuwe gebeurtenis old_scientific_name en new_scientific_name,
naast de bestaande oude/nieuwe status en individuele identiteit. Oude history
blijft intact. Retry-hashes voor eerdere verzoeken zonder soortveld blijven geldig;
weglaten en expliciet null zijn voor het nieuwe soortveld verschillende opdrachten.

Alle bestaande soort-, identiteit-, tijdvak-, Atlas-, zoek- en laatste-waarneming-
queries gebruiken dezelfde effectieve-soortprojectie. A naar B betekent A -1,
B +1, totaal gelijk. Afwijzen betekent geldig totaal -1; herstellen +1. Kip/Otje
verandert alleen de individuele groep. Biologische soorttotalen omvatten alle
individuen van die soort; individuele groepen worden nooit bij die totalen opgeteld.

Geen schema- of datamigratie. Opslag blijft in Observation.review; event-ID, audio,
originele velden en raw candidates veranderen niet. Deployment blijft git pull
--ff-only en herstart van uitsluitend backyard-api.service. Geen automatische
historische correcties; alleen expliciete beheeracties via dit endpoint.
