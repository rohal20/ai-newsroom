"""Command line entry point.

    python -m newsroom fetch                 # fetch, dedupe, save to data/
    python -m newsroom fetch --config my.yaml --out data/
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

from . import __version__
from .config import load_config
from .dedupe import dedupe
from .sources import collect


def cmd_fetch(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"Config file not found: {args.config}. Run from the project folder or pass --config.")
        return 2
    stories, counts = collect(config)
    unique = dedupe(stories)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"stories-{date.today().isoformat()}.json"
    out_file.write_text(
        json.dumps([s.model_dump(mode="json") for s in unique], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    ok_feeds = sum(1 for n in counts.values() if n)
    print(f"Fetched {len(stories)} stories from {ok_feeds}/{len(counts)} feeds "
          f"→ {len(unique)} unique → {out_file}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # -v works both before and after the command: `newsroom -v fetch` and `newsroom fetch -v`
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="show per-feed logs")

    parser = argparse.ArgumentParser(prog="newsroom", description="ai-newsroom: your AI news desk", parents=[common])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="fetch and deduplicate today's stories", parents=[common])
    fetch.add_argument("--config", default="config.yaml")
    fetch.add_argument("--out", default="data")
    fetch.set_defaults(func=cmd_fetch)

    args = parser.parse_args(argv)
    verbose = getattr(args, "verbose", False)
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
