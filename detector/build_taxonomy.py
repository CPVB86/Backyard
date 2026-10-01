"""Regenerate the pinned factual acoustic taxonomy from the upstream CSV."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

SOURCE = "https://zenodo.org/records/20703646/files/BirdNET+_V3.0-preview3.1_Global_11K_Labels.csv"
SHA256 = "8124b0ea2d187104c5e2cd95a0f937165647e20349c8fd34d4d5ef991821f8f0"


def render(document):
    lines = ["{", '  "source": ' + json.dumps(document["source"]) + ",",
             '  "sha256": ' + json.dumps(document["sha256"]) + ",", '  "taxa": {']
    lines += ["    " + json.dumps(name) + ": " + json.dumps(row) + ","
              for name, row in sorted(document["taxa"].items())]
    lines[-1] = lines[-1].removesuffix(",")
    return "\n".join(lines + ["  }", "}", ""])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_csv", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("data")/"acoustic_taxonomy.json")
    args = parser.parse_args(argv)
    raw = args.source_csv.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SHA256:
        parser.error("Unexpected upstream CSV hash; review model/taxonomy compatibility before upgrading")
    taxa = {}
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"):
        name = row["sci_name"].strip()
        taxon = [row["class"].strip(), row["order"].strip()]
        if name in taxa and taxa[name] != taxon:
            raise ValueError("Conflicting taxonomy for " + name)
        taxa[name] = taxon
    args.output.write_text(render({"source": SOURCE, "sha256": SHA256, "taxa": taxa}), encoding="utf-8")
    print(f"{len(taxa)} taxa written to {args.output}")


if __name__ == "__main__":
    main()
