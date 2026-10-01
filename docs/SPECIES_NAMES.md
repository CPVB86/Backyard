# Nederlandse en Duitse soortnamen in API-output

Birds-detecties en observations bevatten naast de ongewijzigde `scientific_name`
en `common_name` read-only `common_name_nl` en `common_name_de`. De koppelsleutel is exact de
wetenschappelijke naam, niet de Engelse naam of de positie in een labellijst.
Geen database-migratie, inputveld, detector- of policywijziging. Raw candidates
blijven ongewijzigd als broninformatie.

## Bestaande BirdNET-bron

Onderzocht: het exacte `birdnet==1.1.1` wheel, met name
`birdnet/utils/taxonomy_v3.py` en `birdnet/acoustic/models/v3_0/model.py`.
BirdNET 1.1.1 ondersteunt `nl` en `de` en genereert deze labels uit
`common_name_nl` en `common_name_de` van [taxonomy_v0.2-Jun2026.csv, geomodel v3.0.4](https://github.com/birdnet-team/geomodel/blob/v3.0.4/taxonomy_v0.2-Jun2026.csv).
De wetenschappelijke sleutelkolom is `sci_name`; ook het akoestische model
koppelt op die kolom, met de laatste rij als er meerdere matches zijn.

Het onderzochte bronbestand is 11.078.402 bytes, SHA-256:
`98b27fc4a77c5e321c7bbf96f924fc4b58170de9688e79ebf3ea8263d522580a`.
De download is tegen de checksum uit het 1.1.1-wheel gecontroleerd.
`Gallinago gallinago` heeft daarin `Watersnip` en `Bekassine`.

De API leest de bestaande, door BirdNET beheerde cache:
`$BIRDNET_APP_DATA/taxonomy-v3/98b27fc4a77c/taxonomy_v0.2-Jun2026.csv`.
De bestaande systemd-environment geeft beide services dezelfde BIRDNET_APP_DATA.
Zonder override gebruikt Backyard `.detector-test/model-cache` in de projectroot,
net als de bestaande Backyard-detector. Er wordt geen BirdNET/ML in de API-venv
geinstalleerd of geimporteerd en geen data tijdens een API-request gedownload.
Geen handmatige vertaaltabel of tweede kopie van de taxonomie in Git.

De namen worden in geheugen gecachet; bestandswijzigingen worden via mtime/size
herkend. Ontbrekend/onleesbaar bestand, ontbrekende soort of lege vertaling geeft per taal
exact de bestaande Engelse naam terug. Als ook die ontbreekt (legacy Birds),
blijft de fallback null. Een later aangemaakte modelcache wordt zonder API-restart
zichtbaar. Bij een toekomstige BirdNET-versie met een andere taxonomie moet deze
kleine bronadapter expliciet worden gecontroleerd; er is geen stille bronupgrade.

De Pi-modelcache zelf was vanuit de Windows-ontwikkelomgeving niet toegankelijk.
De bron en cachelocatie zijn geverifieerd aan BirdNET 1.1.1, en de API-verrijking is
ook met het volledige, checksum-gecontroleerde upstreambestand getest.
