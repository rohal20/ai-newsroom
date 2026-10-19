"""Command line entry point.

    python -m newsroom fetch                 # fetch, dedupe, save to data/
    python -m newsroom brief                 # fetch + write today's briefing to editions/
    python -m newsroom brief --dry-run       # show what would be sent to the model, no API call
    python -m newsroom brief --input data/stories-2026-10-18.json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

from . import __version__
from .briefing import build_user_prompt, render_markdown, save_edition, select_candidates, write_briefing
from .config import Config, load_config
from .dedupe import dedupe
from .llm import LLM, LLMError, OpenAICompatClient
from .models import Story
from .sources import collect


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # optional: keys can also be set as normal environment variables
        return
    load_dotenv()


def _config_or_exit(path: str) -> Config | None:
    try:
        return load_config(path)
    except FileNotFoundError:
        print(f"Config file not found: {path}. Run from the project folder or pass --config.")
        return None


def _fetch(config: Config, out_dir: str) -> tuple[list[Story], Path]:
    stories, counts = collect(config)
    unique = dedupe(stories)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_file = out / f"stories-{date.today().isoformat()}.json"
    out_file.write_text(json.dumps([s.model_dump(mode="json") for s in unique], indent=2, ensure_ascii=False), encoding="utf-8")
    ok_feeds = sum(1 for n in counts.values() if n)
    print(f"Fetched {len(stories)} stories from {ok_feeds}/{len(counts)} feeds → {len(unique)} unique → {out_file}")
    return unique, out_file


def cmd_fetch(args: argparse.Namespace) -> int:
    config = _config_or_exit(args.config)
    if config is None:
        return 2
    _fetch(config, args.out)
    return 0


def make_llm(config: Config) -> LLM:
    """Separate function so tests can swap in a fake model."""
    return OpenAICompatClient(config.llm.provider, config.llm.model, temperature=config.llm.temperature)


def cmd_brief(args: argparse.Namespace) -> int:
    config = _config_or_exit(args.config)
    if config is None:
        return 2

    if args.input:
        stories = [Story.model_validate(s) for s in json.loads(Path(args.input).read_text(encoding="utf-8"))]
        print(f"Loaded {len(stories)} stories from {args.input}")
    else:
        stories, _ = _fetch(config, args.data)
    if not stories:
        print("No stories to write about. Check your feeds with: python -m newsroom fetch -v")
        return 1

    b = config.briefing
    candidates = select_candidates(stories, max_total=b.max_candidates, max_per_source=b.max_per_source)
    print(f"Giving the model {len(candidates)} candidates from {len({s.source for s in candidates})} sources")

    if args.dry_run:
        print("\n--- prompt (dry run, nothing sent) ---\n")
        print(build_user_prompt(candidates))
        return 0

    try:
        llm = make_llm(config)
        briefing = write_briefing(llm, candidates, topic=config.topic, n=b.stories)
    except LLMError as exc:
        print(f"Could not write the briefing: {exc}")
        return 1

    today = date.today()
    path = save_edition(render_markdown(briefing, candidates, config.topic, today), today, args.out)
    print(f"\n{briefing.one_line}\n")
    for i, item in enumerate(briefing.items, 1):
        print(f"  {i}. {item.title}")
    print(f"\nSaved → {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()

    # -v works both before and after the command: `newsroom -v fetch` and `newsroom fetch -v`
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="show detailed logs")
    common.add_argument("--config", default="config.yaml", help="path to config.yaml")

    parser = argparse.ArgumentParser(prog="newsroom", description="ai-newsroom: your AI news desk")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="show detailed logs")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="fetch and deduplicate today's stories", parents=[common])
    fetch.add_argument("--out", default="data")
    fetch.set_defaults(func=cmd_fetch)

    brief = sub.add_parser("brief", help="write today's briefing with an LLM", parents=[common])
    brief.add_argument("--input", help="use a saved stories JSON instead of fetching")
    brief.add_argument("--data", default="data", help="where fetched stories are saved")
    brief.add_argument("--out", default="editions", help="where editions are written")
    brief.add_argument("--dry-run", action="store_true", help="print the prompt without calling the model")
    brief.set_defaults(func=cmd_brief)

    args = parser.parse_args(argv)
    verbose = getattr(args, "verbose", False)
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
