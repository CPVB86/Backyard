"""Read-only Dutch/German names from the taxonomy already cached by BirdNET 1.1.1.

No ML imports, downloads, model-language changes or database writes.
See docs/SPECIES_NAMES.md for the versioned upstream source.
"""
import csv
from functools import lru_cache
import os
from pathlib import Path

from app.core.config import ROOT

TAXONOMY_PATH = Path("taxonomy-v3/98b27fc4a77c/taxonomy_v0.2-Jun2026.csv")


@lru_cache(maxsize=1)
def _read_names(path: Path, modified_ns: int, size: int) -> dict[str, dict[str, str]]:
    # The metadata key refreshes the cache if BirdNET replaces the file.
    try:
        with path.open(encoding="utf-8", newline="") as source:
            # Match BirdNET's scientific-name join and last-row-wins behavior.
            return {row["sci_name"].strip(): {lang: (row.get("common_name_" + lang) or "").strip()
                                           for lang in ("nl", "de")}
                    for row in csv.DictReader(source) if row.get("sci_name", "").strip()}
    except (OSError, UnicodeError, csv.Error):
        return {}


def localized_name(scientific_name: str, english_name: str | None, language: str) -> str | None:
    # Same default cache as Backyard's monitor/diagnostic entrypoints.
    root = Path(os.environ.get("BIRDNET_APP_DATA", str(ROOT / ".detector-test/model-cache")))
    path = root.expanduser().resolve() / TAXONOMY_PATH
    try:
        info = path.stat()
    except OSError:
        # Do not cache absence: the detector may populate it after API startup.
        return english_name
    return _read_names(path, info.st_mtime_ns, info.st_size).get(scientific_name, {}).get(language) or english_name
