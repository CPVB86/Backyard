"""Read the documented single-line subset of systemd EnvironmentFile syntax."""
from pathlib import Path
import re
import shlex


def read_environment(path):
    with Path(path).open(encoding="utf-8") as source:
        text = source.read(65537)
    if len(text) > 65536:
        raise ValueError("Environment file exceeds 64 KiB")
    result = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
            raise ValueError(f"Invalid environment assignment at line {number}")
        # Do not execute shell syntax or interpret environment substitutions.
        if "\\" in value or "$" in value or "`" in value:
            raise ValueError(f"Use literal, single-line values at line {number}")
        values = shlex.split(value, comments=False)
        if len(values) > 1:
            raise ValueError(f"Quote values containing spaces at line {number}")
        result[key] = values[0] if values else ""
    return result
