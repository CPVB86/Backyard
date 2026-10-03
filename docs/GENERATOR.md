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
The old `POST /api/generator/{domain}/generate` returns HTTP 405: consumers only
read assets/status and never initiate provider work. Their existing Backyard Bearer
token is sufficient; they never receive or need OPENAI_API_KEY.

The species response retains `status` (`ready`/`partial`/`missing`/`not_configured`)
and adds `generation: {status, reason}`. Status is `complete`, `incomplete`,
`generation_pending`, `generation_running`, `generation_failed` or
`generation_not_configured`. Reasons are fixed public codes, never exception text
or provider responses. Complete assets always take precedence over historical job
state, including after administrator recovery. GET never generates.

Missing flight does not silently substitute perched in the completeness check.
Consumers wanting the existing AvianVisitors display fallback can select flight,
then perched, then photo_cutout from the same authenticated response. Sketches
remain separately available; they do not count as finished illustration poses.

## Automatic accepted-observation scheduling

After ingest/review has committed, `auto_accepted` and `human_confirmed` results
notify the Generator scheduler. Pending/recommended review, rejection and raw
candidates never schedule. Idempotent ingest/confirm retries may safely repeat the
check. Existing complete species do not create a job. No historical observation
backfill is performed automatically; explicit reconciliation is available below.

One daemon thread in the existing single-worker API process processes a durable
SQLite queue in `BACKYARD_STORAGE_ROOT/generator/jobs.sqlite3`. This is Generator
storage, not an observation database migration. A unique `(domain, scientific_name)`
key suppresses duplicate jobs. Queue claiming is transactional; only one job is
loaded at a time. No extra service, broker or dependency is introduced. Continue
using the existing systemd `--workers 1` configuration.

The worker uses the same AssetStore.ensure, domain required_assets and generation
lock as the local CLI. It never forces a retry or overwrites images. A failed
provider call remains failed despite new observations of the same species. After
an interrupted process, formerly running jobs become failed with
`interrupted_check_provider_usage`; queued jobs survive restart. Inspect provider
usage/raw output before local administrator recovery with `--retry-failed`.
A hard kill can leave generation.lock; inspect the owning PID before removing a
stale lock. Shutdown stops new claims and waits at most one second for the worker;
unfinished network requests may be interrupted by process exit and are never
blindly retried.

Missing/malformed OPENAI_API_KEY leaves assets incomplete with
`generation_not_configured`. Configure the key and restart the API to resume these
unattempted jobs. Provider HTTP 401/403 also reports not configured, but an actual
attempt requires explicit administrator recovery after credentials are fixed.
Neither status nor logs include keys or provider error bodies. Bats can implement
its own configuration check and asset requirements without OpenAI or Bird poses.

Scheduling and provider errors never roll back accepted observations. The enqueue
is deliberately after the observation commit, not an atomic outbox: a process/disk
failure in that small interval can miss a job and produces a scheduling warning
when observable. An idempotent observation retry or later accepted observation of
that species repeats the check. Local `ensure` is also available for recovery.
Back up the whole Generator directory, including the queue and attempt files.

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

For automatic generation of missing illustrations, edit the shared environment file:

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

Local administrator recovery changes `status` to `ensure` and adds
`--common-name "Common Blackbird"`. Optional trusted local reference paths are
`--reference`, `--anti-reference`, `--style-reference`; these are not accepted as
server file paths via the remote API. `--retry-failed` is an explicit recovery action.

Backyard is the central API/store for future AvianVisitors, WordPress and other
consumers. This migration does not alter those consumers or delete the original
fork. Point consumers at this API before retiring their old generators.


## Reconcile existing accepted species

For accepted observations predating automatic scheduling (or a missed enqueue),
use the explicit management command. It reads unique `(domain, scientific_name)`
from `auto_accepted`/`human_confirmed` observations and checks the existing store.
Ready species are skipped; others pass through Scheduler.accepted and the same
queue. No observations, assets or attempt state are changed by reconciliation.
The existing API worker polls the queue; this command never starts a second worker,
calls the provider, resets running jobs, or enables retry_failed. Failed/attempted
jobs retain their safeguards. A non-accepted or nonexistent requested species fails
without creating a job. There is no GET side effect or automatic all-species scan.

After deploying this change, to schedule **only Houtduif / Columba palumbus**, run
as the existing service user (cpvb86, with read access to the shared env file):

```sh
cd /home/cpvb86/Backyard
.venv/bin/python -m generator.reconcile \
  --environment-file /etc/backyard/backyard.env \
  --domain bird --scientific-name "Columba palumbus"
```

The common name is read from the existing accepted observation; it is not supplied
as a new observation or a translation override. Output reports the current
`generation.status` (normally generation_pending, or running if already claimed).
Repeating the command is safe. The API service must be running to process jobs and
must have loaded OPENAI_API_KEY from the shared environment. The CLI opens the
observation SQLite database in enforced read-only mode; only Generator queue
storage is writable. No observation migration or new dependencies are needed.

Only when intentionally reconciling **all** accepted species:

```sh
.venv/bin/python -m generator.reconcile \
  --environment-file /etc/backyard/backyard.env --all
```

Add `--domain bird` to restrict that scan to Birds. Jobs already pending, running,
failed or rejected by provider credentials are not reset. Existing unconfigured
jobs follow the existing API-restart recovery rules; this command does not invoke
recover on a live worker. For genuine failed paid attempts, inspect provider usage
and raw responses before the existing local CLI --retry-failed recovery.
