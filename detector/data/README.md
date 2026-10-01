# Acoustic taxonomy provenance

Powered by BirdNET. The catalog contains scientific-name/class/order facts
extracted from the official BirdNET+ V3.0 Developer Preview 3.1 labels, by
Mario Lasseck, Maximilian Eibl, Holger Klinck and Stefan Kahl; Chemnitz
University of Technology, Museum fuer Naturkunde Berlin and Cornell Lab of
Ornithology.

- [Source release and attribution](https://zenodo.org/records/20703646)
- [Original CSV](https://zenodo.org/records/20703646/files/BirdNET+_V3.0-preview3.1_Global_11K_Labels.csv)
- [Upstream terms](https://zenodo.org/records/20703646/files/TERMS_OF_USE.txt)
- [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)

Changes: extract only scientific name, class and order; deduplicate identical
names and sort into JSON (11,560 taxa). This derived catalog is provided under
CC BY-SA 4.0 with the upstream attribution/terms above; it is not a model or
a hand-maintained national species list. No model weights are redistributed.
Unknown names are excluded conservatively by the BirdNET adapter.

Source SHA256:
`8124b0ea2d187104c5e2cd95a0f937165647e20349c8fd34d4d5ef991821f8f0`.

Regenerate explicitly, from the project root:

```bash
mkdir -p .detector-test
curl --fail --location 'https://zenodo.org/records/20703646/files/BirdNET+_V3.0-preview3.1_Global_11K_Labels.csv' -o .detector-test/acoustic-labels.csv
python -m detector.build_taxonomy .detector-test/acoustic-labels.csv
```

The generator refuses an unrecognized source hash. This is an offline runtime
catalog tied to acoustic preview3.1: reassess it when upgrading BirdNET/model
labels. Broader geographical-model taxonomy has different species coverage
and is deliberately not used as the acoustic hard-domain source.
