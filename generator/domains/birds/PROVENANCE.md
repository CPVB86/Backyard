# AvianVisitors provenance and asset pipeline

Source: CPVB86/AvianVisitors, commit `f70d84bc7fb8cba30a1368e666479788325f85ba`.
Original project: Twarner491/AvianVisitors. Source license is retained in
`LICENSE.avian.txt` (CC BY-NC-SA 4.0). These imported works retain that license.
The source checkout/fork has not been modified or removed.

## Reused implementation

| AvianVisitors source | Backyard Birds component |
| --- | --- |
| `demo/generate.py`: prepare_prompt, save_cutout | `render.py`; only resource paths adapted |
| `avian/scripts/openai_images.py` | `openai_images.py`, unchanged HTTP/image protocol and defaults |
| `avian/scripts/pregen.py`: slugify, load_prompt, load_species_notes, POSES | `helpers.py` |
| `avian/scripts/prompt.template.md`, species-notes.json | Same files, byte-for-byte |
| `avian/scripts/build_masks.py`: build_tables, dump_perkey and constants | `masks.py`, unchanged algorithms |
| `demo/server.py`: local_catalog and AutoImageGenerator | Same dimension/mask completeness constraints, local override priority, persistent attempt-before-call and no automatic retry in adapter/store |
| `avian/scripts/generate_one.py`: chroma_cut | `processing.py`, imports made lazy |
| `avian/scripts/upgrade_cutouts.py`: birefnet_cut | `processing.py`, unchanged function and thresholds |
| `avian/scripts/cutout.py` | Optional offline `cutout.py`, explicit --dir required |
| `avian/scripts/verify.py` | Optional offline `verify.py`, explicit --dir and environment-only key |

The working OpenAI path already present in the fork is the runtime generator.
Its complete prompt, species notes, role-based optional anatomy/negative references,
per-pose robin style references, transparent-background override, alpha validation,
crop/padding, atomic PNG publication, preserved raw response and mask generation
are retained. Generation remains explicit. There is no second automatic detector
or observation-triggered generator. The older Gemini orchestration and Pi SSH
upgrade transport are not new Backyard services; their reusable image processing
and optional species/anatomy verification are retained here.

## Exact roles traced in the source

* `illustrations`: final RGBA cutouts. The legacy cream-ground route uses
  pregen -> chroma/BiRefNet segmentation -> alpha crop -> illustrations.
  The fork's OpenAI route directly requests RGBA, checks/crops it, then builds masks.
* `cutouts`: background-removed **photographs**, served as a fallback by
  `avian/api/cutout.php`, not a required intermediate for every illustration.
  Original display order is requested illustration, perched illustration for
  missing flight, photo cutout, then optional dynamic Wikipedia/rembg cache.
  Existing photo fallbacks are available as `photo_cutout`; they do not make an
  absent illustration pose complete. No network generation occurs on an API GET.
* `sketches`: existing legacy PNG assets. No active producer or consumer of
  `avian/assets/sketches` was found in the current checkout. Frontend `sketchSrc`
  instead addresses cutout.php. The files are preserved as `legacy_sketch`,
  with IDs `sketch_perched`/`sketch_flight`; no invented preprocessing stage.
* `dimensions`: scaled to a 560-pixel longest side, used for composition sizing.
* `masks`: alpha threshold >127, longest side 93, row-major MSB-first packed bits
  encoded as base64. `avian/frontend/apt.js` consumes these for silhouette packing
  and collision detection. They are **outputs** of the final cutout, not the
  segmentation model's input masks. Original illustration tables are unchanged.

## Imported assets

* 666 bundled illustrations and 24 local illustrations (344 bird species and one archived non-bird demo species).
* 157 photographic cutouts and 76 legacy sketches.
* 24 local raw responses, reference images and local generation-history metadata.
* Exact bundled/local dims.json and masks.json; supplementary masks/dimensions for
  cutouts/sketches are derived with the original build_tables algorithm.
* `assets/imported-files.json` records SHA-256 for each of the 923 display PNGs;
  `assets/catalogue.json` associates domain-specific types/poses with scientific names.

The importer uses source model labels and local generation history. A legacy
illustration absent from labels retains the binomial encoded by its existing slug;
no translation is invented. Local illustrations override bundled ones. There was
no dynamic BirdSongs/Extracted/cutouts cache in the source checkout to import.

Reproducible offline import from the source checkout:

```sh
python -m generator.domains.birds.import_avian /path/to/AvianVisitors
```

Existing imported PNGs with different bytes cause an error rather than overwrite.
This command is for maintainers, not required on the Pi after cloning/pulling.

Optional cream-background tools retain the original algorithms. `chroma_cut`
requires numpy/Pillow; BiRefNet requires rembg/onnxruntime/scipy and its large model.
These dependencies are intentionally not loaded/installed for normal API service
or OpenAI generation. Use a separate workstation environment for such maintenance;
regenerate mask/dimension metadata after changing a cutout. `verify.py` uses
GEMINI_API_KEY only when explicitly invoked; it is not an automatic acceptance
check in the fork's OpenAI path or in Backyard. No claim of anatomical correctness
is made by the structural PNG/alpha validation.

The source local demo also generated two Phoca vitulina (harbour seal) images.
Their original PNGs/raw images and tables are retained; their catalogue entry is
archived in legacy-nonbirds.json and is not served or generated as a bird.
