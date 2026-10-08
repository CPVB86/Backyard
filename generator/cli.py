"""Explicit Generator lookup/ensure; reference file access is local-admin only."""
import argparse
import json
import os
from pathlib import Path
from app.core.config import Settings
from generator.store import AssetStore, public_status
from operations.environment import read_environment


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "ensure", "register"))
    parser.add_argument("--domain", default="bird")
    parser.add_argument("--scientific-name", required=True)
    parser.add_argument("--common-name")
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--anti-reference", type=Path)
    parser.add_argument("--style-reference", type=Path)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--asset-id")
    parser.add_argument("--file")
    parser.add_argument("--pose", choices=("perched", "flight"))
    args = parser.parse_args(argv)
    try:
        if args.environment_file:
            os.environ.update(read_environment(args.environment_file))
        store = AssetStore(Settings().resolved_storage_root / "generator")
        if args.action == "status":
            result = store.lookup(args.domain, args.scientific_name)
        elif args.action == "register":
            if not all((args.asset_id, args.file, args.pose)):
                parser.error("--asset-id, --file and --pose required for register")
            result = store.register_existing(args.domain, args.scientific_name,
                                             args.asset_id, args.file, args.pose)
        else:
            if not args.common_name:
                parser.error("--common-name required for ensure")
            result = store.ensure(args.domain, args.scientific_name, args.common_name,
                                  retry_failed=args.retry_failed, options={
                                      key:getattr(args,key) for key in ("reference","anti_reference","style_reference")})
        print(json.dumps(public_status(result), ensure_ascii=True, indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Generator: {error}\n")


if __name__ == "__main__":
    main()
