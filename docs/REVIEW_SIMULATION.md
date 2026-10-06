# Read-only review simulation

This tool is diagnostic only. It does not change production policy, API filtering,
WordPress, database statuses, evidence retention or existing observations.
There is no apply mode. SQLite is opened with mode=ro and PRAGMA query_only,
in a single read transaction. No provider or generation calls are made.

Run from the repository root on the Pi:

```bash
.venv/bin/python -m operations.review_analysis --environment-file /etc/backyard/backyard.env > /tmp/backyard-review-v3.json
```

The input is the current pending_review and review_recommended backlog, not all
historical detections. Daily counts use Europe/Amsterdam; edge days may be partial.
Windows development requires the tzdata dependency in requirements-dev.txt.

## Shared classification model

Evidence passes at confidence >= .85, or >= .75 with 2 qualifying windows,
or >= .65 with 3 qualifying windows. Supports are overlapping windows, not
independent calls. Each recorded policy supplies the support confidence floor.

- Unknown plausibility stays unknown regardless of confidence; strong_unknown
  means evidence would pass if local plausibility were normal.
- Normal plus passing evidence becomes simulated accepted; otherwise low_evidence.
- Unusual plus the recorded policy's existing review thresholds becomes human_review.
  Existing unusual review_recommended records remain human_review.
- An unusual record below those thresholds aborts the diagnostic rather than
  silently discarding it or assigning an invented fifth outcome.
- Rarity never affects classification. No production status is created.

Human review is clustered by domain/species within 300 seconds of the first
start. A cluster is assigned to its first local day. Raw records remain distinct.
Provider labels identify missing-result context, not a proven root cause.
All records and raw plausibility metadata are exported for further diagnosis;
environment values, API tokens and audio content are not exported.

## Production integration and migration

Production, simulator, API legacy filtering and migration call observations.policy.classify.
Evidence thresholds are defined once in EVIDENCE_THRESHOLDS. Policy version 2 is
included in the fingerprint: update and restart API and detector together.
Old auto_single/unknown_single fields remain readable for historical policy data;
they no longer control normal/unknown acceptance. Unusual thresholds are unchanged.

No schema migration is needed. The existing status lifecycle is preserved:
accepted uses auto_accepted; low_evidence and unknown remain stored as pending_review
with distinct decision.classification/reasons; human_review uses pending_review or
review_recommended. The API exposes classification separately from status.
Human-confirmed/rejected records retain their final human state.
Consumers must use /api/observations/review and /api/observations/count?review_only=true
for human work, not status=pending_review. Both filter before applying the list limit.
The WordPress plugin changes are in the separate AvianVisitors repository.

The read-only migration plan and explicit apply use the same stored candidate
metadata and policy parameters, with the shared new classifier. Only legacy
pending_review/review_recommended records are eligible. Existing auto_accepted and
human decisions are untouched. A single BEGIN IMMEDIATE transaction prevents
concurrent review races during apply; any failure rolls everything back. A rerun
skips policy-version-2 decisions. The previous decision is retained in metadata.
No records or audio are deleted. Accepted audio becomes permanent in metadata and
keeps its existing storage key, avoiding nontransactional filesystem moves.
Migration does not call Generator or spend provider credits. Use the existing
Generator reconciliation command separately if desired after inspecting the result.

Deploy after review:

```bash
cd /home/cpvb86/Backyard
sudo systemctl stop backyard-detector.service backyard-api.service
git pull --ff-only
.venv/bin/python -m operations.review_migration --environment-file /etc/backyard/backyard.env
# Only after reviewing this dry-run, explicitly apply:
.venv/bin/python -m operations.review_migration --environment-file /etc/backyard/backyard.env --apply
sudo systemctl start backyard-api.service backyard-detector.service
```

No --apply has been run on a real database during development. Tests exercise it
only on isolated temporary databases. Make a normal SQLite backup before applying.
Full simulation: python -m operations.review_analysis with the same environment flag.
