# Generator

Species assets belong to `(domain, scientific_name)`, independently of observations.
`generator/store.py` owns lookup, per-species manifests, status, storage containment,
single-writer locking and persistent attempts. The domain adapter declares
`required_assets`, identities, lookup, validation and generation. `bird` owns its
perched/flight illustrations, photo/sketch types, prompts, segmentation and masks.
`bat` is registered but not implemented (`not_configured`, generation HTTP 501).
A future Bat adapter can declare completely different asset types; no bird-specific
poses or image algorithms are embedded in the generic store/API.

Imported files are shipped in `generator/domains/birds/assets`. New output lives in
`BACKYARD_STORAGE_ROOT/generator/<domain>/<sha256(scientific_name)>/` with raw
responses, prompt snapshots, manifest and attempt state. Preserve this directory
in backups alongside existing data. No database migration is needed.

See [source/pipeline inventory](../generator/domains/birds/PROVENANCE.md) for exact
asset roles, reused functions, source revision and licensing.

## Authenticated API

Every endpoint uses existing `Authorization: Bearer <BACKYARD_API_TOKEN>` auth:

* `GET /api/generator`: domains and required asset IDs.
* `GET /api/generator/bird/species?scientific_name=Turdus%20merula`: status,
  missing_assets, attempts and assets. Each asset includes its type, pose where
  applicable, dimensions, mask, pixel_dimensions, SHA-256, MIME type and URL.
* `GET /api/generator/{domain}/assets/{species_key}/{asset_id}`: exact file with
  private cache headers. Always use the URL from the species response.
* `POST /api/generator/bird/generate` with JSON
  `{"scientific_name":"Turdus merula","common_name":"Common Blackbird"}`:
  ensure both illustration poses, generating only those missing. It may take
  up to two provider calls (240-second timeout each); clients/proxies must allow
  for this. Existing assets do not need a provider key. GET never generates.

Missing flight does not silently substitute perched in the completeness check.
Consumers wanting the existing AvianVisitors display fallback can select flight,
then perched, then photo_cutout from the same authenticated response. Sketches
remain separately available; they do not count as finished illustration poses.

Generation is synchronous and explicitly requested, with one writer across API
and CLI. HTTP 409 means a concurrent/prior attempt needs attention. After a timeout
inspect provider usage and preserved raw output before explicitly requesting
`retry_failed: true`; ordinary retries never silently repeat a paid request.
A process crash can leave generation.lock: inspect the PID/process before manual
removal. An already-written final PNG can recover its metadata on explicit retry
without another provider call. No force-overwrite endpoint is provided.

## Pi deployment

After the running duration test, from the existing checkout:

```sh
cd /home/cpvb86/Backyard
git pull --ff-only origin main
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest -v
sudo systemctl restart backyard-api.service
sudo systemctl status backyard-api.service --no-pager
```

The initial asset transfer is approximately 580 MB. Pillow is the only added runtime
dependency. No service-unit edits, daemon-reload or detector restart are required.
Existing illustrations are immediately usable without any generation credentials.

Only for generating missing illustrations, edit the shared environment file:

```sh
sudoedit /etc/backyard/backyard.env
```

Add your real `OPENAI_API_KEY` privately, plus optional `OPENAI_IMAGE_MODEL` and
`OPENAI_IMAGE_QUALITY` overrides. The unchanged AvianVisitors defaults are
`gpt-image-2.5-flare` and `medium`; use a model available to your provider account.
Then restart `backyard-api.service`. Do not put these provider variables in the
Backyard application .env (which only accepts BACKYARD settings). Local development
uses exported process variables instead. No key or paid generation is needed to
run tests. Existing BACKYARD_API_TOKEN and detector configuration stay unchanged.

Read-only CLI example using the existing production environment (run as the service
user with permission to read its environment file):

```sh
python -m generator.cli status --environment-file /etc/backyard/backyard.env \
  --domain bird --scientific-name "Turdus merula"
```

Explicit generation changes `status` to `ensure` and adds
`--common-name "Common Blackbird"`. Optional trusted local reference paths are
`--reference`, `--anti-reference`, `--style-reference`; these are not accepted as
server file paths via the remote API. `--retry-failed` is an explicit recovery action.

Backyard is the central API/store for future AvianVisitors, WordPress and other
consumers. This migration does not alter those consumers or delete the original
fork. Point consumers at this API before retiring their old generators.
