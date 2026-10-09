# Samsung Frame — handmatige commando's en automatische sync

Voor QE50LS03FAUXXN / 50LS03F (2025), Art API 5.0.1.0. De bestaande
`samsungtvws==3.0.6` uploadt een 3840 × 2160 PNG met `matte="none"` en
`portrait_matte="none"`. Geen automatische passe-partoutfallback.

Projectroot `/home/cpvb86/Backyard`, Python `.venv/bin/python`, configuratie
`/home/cpvb86/Backyard/.env`. Het standaardpad is
`data/samsung_frame/samsung-frame.png`. Root-.env en shellenvironment worden
door de bestaande Settings gelezen. Geen `/etc`-bestand nodig voor deze module.
De optionele --environment-file bestaat alleen voor expliciete overrides;
laat die bij normale handmatige commando's en timers weg.

Vanuit de projectroot:

```bash
.venv/bin/python -m app.modules.samsung_frame connect
.venv/bin/python -m app.modules.samsung_frame status
.venv/bin/python -m app.modules.samsung_frame sync
.venv/bin/python -m app.modules.samsung_frame recover
```

`connect` test pairing en tv-identiteit. Accepteer eenmalig Backyard Samsung
Frame op de tv. Token wordt atomisch opgeslagen in `data/samsung_frame/token`
(0600, opslagmap 0700). `BACKYARD_SAMSUNG_FRAME_TOKEN` blijft normaal leeg.
Pi en tv moeten op hetzelfde vertrouwde subnet staan. De library gebruikt
WSS/HTTPS op 8002 en de door de tv aangewezen uploadsocket; haar Samsung-
zelfondertekende TLS-certificaat wordt niet gevalideerd.

`sync` is de normale automatische/handmatige route: alleen gewijzigde PNG-bytes
uploaden, of eerst uitsluitend een bestaande pending/cleanup-transactie hervatten.
De SHA-256 wordt persistent aan de beheerde ID gekoppeld. Status blijft beschikbaar
zonder artworkwijziging. `recover`/`upload --resume` lezen of genereren geen PNG.
De oudere `upload` blijft beschikbaar voor een expliciete handmatige upload;
timers gebruiken uitsluitend `sync`. `generate` maakt een geometrische test-PNG;
gebruik dit niet naast de collage-timers, omdat het hetzelfde outputpad overschrijft.

Nieuw artwork wordt pas current na bevestiging van het gekozen content-ID en
`matte=none`. In Art Mode wordt de collage geselecteerd voor weergave; tijdens
tv-kijken wordt de selectie voor de volgende Art Mode-sessie opgeslagen.
Upload, sync en recover activeren nooit Art Mode en versturen geen powercommando.
De selectie gebruikt altijd `show=False`, ook als Art Mode nu aan staat: hierdoor
kan een gelijktijdige gebruikerswissel naar tv-kijken niet door `show=True`
worden onderbroken. Alleen de vorige eigen MY_-ID in het tv-identiteitgebonden
journal mag daarna weg; SAM_-art en andere persoonlijke foto's blijven behouden.
Cleanup vereist de bevestigde nieuwe selectie en matte, niet Art Mode=on.
Getters/polling blijven begrensd. Sync-output meldt `presentation=art` of
`background` bij bevestigde selectie; bij `unchanged` is dit null (geen selectie).
Dit is de gemeten Art Mode-status, geen bewijs van zichtbare pixels.
Logs tonen stappen, modus, duur, hash en ID; geen tokens of authenticatieframes.

## Onderzocht gedrag op de Frame 2025

De gepinde samsungtvws 3.0.6-bron gebruikt voor Art API 5.0.1.0 `send_image`,
een D2D-socket en `image_added` met content-ID. Upload bevat matte-instellingen,
maar geen `show`, power- of Art Mode-setter. `select_image` verstuurt een boolean
`show`; de bestaande BoundedArt transporteert expliciet `False` zonder op een
matching setter-ack te blijven wachten. Bevestiging loopt via getters.
De [librarydocumentatie](https://pypi.org/project/samsungtvws/2.7.2/) beschrijft
`show=False` als selecteren zonder meteen tonen als de tv niet in Art Mode is;
[Art API-documentatie van openHAB](https://www.openhab.org/addons/bindings/samsungtv/)
beschrijft eveneens selecteren zonder display. Dit is het protocolcontract,
geen fysieke bevestiging voor jouw QE50LS03FAUXXN-firmware.

33 gerichte Samsung-tests geslaagd, inclusief het werkelijke library-uploadpad
met gemockte socket/Art API 5.0.1.0, on/off-selectie, moduswissel tijdens selectie,
meerdere achtergronduploads en uitsluitend eigen cleanup. Geen tv- of Pi-test.

Praktijktest op jouw tv, vanuit `/home/cpvb86/Backyard`:

```bash
# Laat een uitzending spelen en maak ��n nieuwe collage.
.venv/bin/python -m app.modules.avian_collage_exporter
.venv/bin/python -m app.modules.samsung_frame sync
.venv/bin/python -m app.modules.samsung_frame status
```

Controleer dat de uitzending blijft spelen, Art Mode=off blijft en de nieuwe ID
`managed.current` is met `matte_id=none`. Ga daarna zelf met de afstandsbediening
naar Art Mode: de nieuwe collage moet direct zichtbaar worden, zonder extra
sync. Herhaal export/sync terwijl Art Mode aan staat en controleer direct zichtbare
verversing. Doe ook twee exports tijdens tv-kijken om te controleren dat alleen
de nieuwste Backyard-collage overblijft en persoonlijke foto's behouden blijven.
Als de firmware `show=False` niet correct bevestigt/ververst, meldt de module de
exacte fout en bewaart pending/oud artwork; er is geen fallback naar `show=True`
of het activeren van Art Mode. Deel dan status en servicejournal voor onderzoek.

Bij netwerk-/headerfalen vóór image-bytes mag de volgende run veilig opnieuw
uploaden. Zodra bytes mogelijk verstuurd zijn, blijft een onzekere upload zonder
duurzaam content-ID geblokkeerd om duplicaten/adoptie van andermans foto's te
voorkomen. Bekende pending IDs en mislukte cleanup worden automatisch later
hervat. Wis geen journalvelden om een fout te omzeilen. Oude journals blijven
leesbaar; zonder historische hash kan eenmaal een veilige vervanging nodig zijn
voordat deduplicatie werkt. Na herstel van een oude hashloze pending neemt de
volgende run pas een eventueel nieuwere PNG mee.

Zie [automatische export en upload activeren](AVIAN_SAMSUNG_AUTOMATION.md).
