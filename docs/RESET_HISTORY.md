# Date-bounded registration reset

On the CENTRAL Backyard Pi only. Never run the old all-history reset for a
date-limited request. This tool selects observations by original start_at and
legacy bird_detections by timestamp, strictly before local midnight of --before
in Europe/Amsterdam. 2026-11-09 means 2026-11-08T23:00:00Z.
A registration starting before midnight belongs to the old period even if it
ends after midnight. Confirmed, rejected and open records are all included.

Preview, with existing production env:

```sh
cd /home/cpvb86/Backyard
sudo .venv/bin/python -m operations.reset_history --before 2026-11-09
```

Preview reads only, reports old observations by domain/status, legacy records
and existing linked audio size. It refuses pending imports, unknown incoming
foreign keys and unsafe audio paths. It does not fetch audio or change policy.
Today's records and their candidate/audio links remain.

Before apply, record which central Backyard services/timers are active:
```sh
systemctl list-units --state=active --no-legend --plain 'backyard-*.service' 'backyard-*.timer'
```
Stop those writers and timers, then:
```sh
sudo systemctl stop 'backyard-*.timer' 'backyard-*.service'
sudo .venv/bin/python -m operations.reset_history --before 2026-11-09 --apply
```
Only restart after successful completion; use the previously active units.
Do not blindly enable optional timers or start the Python detector alongside Go.
BYRemote remains on; stopping the central Go service temporarily disconnects
the audio receiver. Go should reconnect once restarted.

Apply creates reset-backups/<UTC timestamp> beside data/, permissions 0700:
SQLite backup, checkpoint copies, manifest and old linked audio. Audio is MOVED
out of active storage, not irreversibly erased. It therefore still takes disk
space. Do not delete this backup until the new data and counts are verified.
No full data-tree deletion, no generator/catalog/model changes, no upstream
Go database deletion and no system journal deletion. Orphan/unlinked files
are deliberately not guessed or deleted.

The SQLite removals are one transaction; file moves are rolled back on a
caught exception. Filesystem/SQLite/checkpoints are not one atomic transaction
against power loss. On abrupt interruption keep services stopped, inspect the
backup manifest, and recover deliberately. No automatic destructive rollback.
Checkpoint not_before is advanced to at least the boundary; cursor, instance,
done IDs and any stricter existing cutoff are preserved. Historical upstream
events therefore cannot be reimported by the existing Go bridges.
Never reset their instance IDs or clear checkpoint files afterwards.

monitoring-start.json is written under BACKYARD_STORAGE_ROOT. Its started_at
is returned as top-level monitoring_started_at by:
GET /api/presentation/birds and GET /api/presentation/bats.
This is deployment start, not a fake first observation. active_days remains the
number of days with accepted activity. No database schema migration.

WP must use monitoring_started_at for 'Actief sinds' and format it in
Europe/Amsterdam; for this reset show 9 november 2026. Invalidate presentation,
review/count caches after reset. Older WP consumers ignore this additive field.
The backend cannot update Elementor hardcoded dates or WordPress caches itself.

Hardware apply has NOT been run from the development workstation. Test coverage:
exclusive local-day boundary, today's rows and audio preserved, images/catalog
unchanged, preview read-only, repeat apply, pending-import refusal, unknown FK
refusal and transaction rollback on file-move failure.
