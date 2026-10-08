"""One-shot exporter; service/timer scheduling is independent of Samsung upload."""
import argparse
import logging
from pathlib import Path
import sys
import time
from .renderer import export
from app.modules.samsung_frame.config import load_settings


def main():
    parser = argparse.ArgumentParser(description="Export existing Avian bird collage to a 3840 x 2160 PNG")
    parser.add_argument("--environment-file", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    started = time.monotonic()
    try:
        result = export(load_settings(args.environment_file))
        logging.info("Collage export complete duration=%.2fs output=%s", time.monotonic()-started, result)
        return 0
    except Exception as error:
        logging.error("Collage export failed duration=%.2fs error=%s: %s; previous PNG retained",
                      time.monotonic()-started, type(error).__name__, error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
