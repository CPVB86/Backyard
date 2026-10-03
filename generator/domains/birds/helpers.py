"""Unchanged AvianVisitors pregen helpers; see PROVENANCE.md."""
import re
import json
from pathlib import Path
POSES = {1: "perched", 2: "in flight with wings spread"}

def slugify(sci: str) -> str:
    """Match avian/frontend/apt.js slugify() exactly."""
    return re.sub(r"[^a-z0-9]+", "-", sci.lower()).strip("-")

def load_prompt(path: Path) -> str:
    """Return everything after the `## Prompt` heading, stripped to the
    next `##` heading (so doc preamble or trailing sections don't bleed
    into the API call)."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"##\s*Prompt\s*\n(.+?)(?=\n##\s|\Z)", text, flags=re.DOTALL)
    return (m.group(1) if m else text).strip()

def load_species_notes(notes_path: Path) -> dict[str, str]:
    """Load per-species prompt addenda. Keys are scientific names; values
    are 1-2 sentence clarifications to inject when generating that
    species. Returns {} if the notes file doesn't exist."""
    if not notes_path.exists():
        return {}
    raw = json.loads(notes_path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, str)}
