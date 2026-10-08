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

Nieuw artwork wordt pas current na bevestiging van juiste ID, matte=none en
Art Mode=on. Alleen de vorige eigen MY_-ID in het tv-identiteitgebonden journal
mag daarna weg; SAM_-art en andere persoonlijke foto's worden nooit verwijderd.
Activatie gebruikt eenmalige setters zonder matching-ack-wachtlus, begrensde
getters/polling en slaat redundant aanzetten van Art Mode over. Logs tonen
stappen, duur, hash en ID; geen tokens of ruwe authenticatieframes.

Bij netwerk-/headerfalen vóór image-bytes mag de volgende run veilig opnieuw
uploaden. Zodra bytes mogelijk verstuurd zijn, blijft een onzekere upload zonder
duurzaam content-ID geblokkeerd om duplicaten/adoptie van andermans foto's te
voorkomen. Bekende pending IDs en mislukte cleanup worden automatisch later
hervat. Wis geen journalvelden om een fout te omzeilen. Oude journals blijven
leesbaar; zonder historische hash kan eenmaal een veilige vervanging nodig zijn
voordat deduplicatie werkt. Na herstel van een oude hashloze pending neemt de
volgende run pas een eventueel nieuwere PNG mee.

Zie [automatische export en upload activeren](AVIAN_SAMSUNG_AUTOMATION.md).
