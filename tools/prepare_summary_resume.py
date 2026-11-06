"""Clone registered movie/transcript evidence without touching its old project."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.production.summary_intake import clone_summary_source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-id", required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    production = clone_summary_source(args.production_id, args.asset_id, args.title)
    print(json.dumps({"production_id": production.production_id, "title": production.title,
                      "source_asset_id": args.asset_id, "transcript_reused": True,
                      "origin_unchanged": True}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())