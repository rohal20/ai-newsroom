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

from . import __version__, pipeline
from .agents import editor
from .briefing import render_markdown, save_edition, select_candidates
from .config import Config, ModelSpec, load_config
from .dedupe import dedupe
from .llm import LLM, FallbackLLM, LLMError, OpenAICompatClient
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
    s = config.llm
    clients = []
    for spec in [ModelSpec(provider=s.provider, model=s.model), *s.fallbacks]:
        try:
            clients.append(OpenAICompatClient(
                spec.provider, spec.model,
                temperature=s.temperature, timeout=s.timeout_seconds, max_retries=s.max_retries,
                reasoning_effort=s.reasoning_effort if spec.provider == "gemini" else None,
                min_interval=s.min_interval_seconds,
            ))
        except LLMError as exc:
            if not clients:      # the main model must be usable; a fallback without a key is just skipped
                raise
            print(f"Skipping fallback {spec.provider}/{spec.model}: {str(exc).splitlines()[0]}")
    return FallbackLLM(clients)


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

    if args.dry_run:
        b = config.briefing
        candidates = select_candidates(stories, max_total=b.max_candidates, max_per_source=b.max_per_source)
        print(f"\n--- what the editor would see (dry run, nothing sent) ---\n")
        print(editor.build_prompt(candidates))
        return 0

    try:
        llm = make_llm(config)
        run = pipeline.run(llm, stories, config, read=make_reader())
    except LLMError as exc:
        print(f"Could not write the briefing: {exc}")
        return 1

    today = date.today()
    md = render_markdown(run.briefing, run.sheets, config.topic, today, candidates=run.candidates)
    path = save_edition(md, today, args.out)
    log_path = Path(args.data) / f"run-{today.isoformat()}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")

    facts = sum(len(s.facts) for s in run.sheets)
    dropped = sum(len(s.dropped) for s in run.sheets)
    print(f"\n{run.briefing.one_line}\n")
    for i, item in enumerate(run.briefing.items, 1):
        print(f"  {i}. {item.title}")
    print(f"\n{facts} facts verified, {dropped} unsupported dropped · "
          + " · ".join(f"{k} {v}s" for k, v in run.seconds.items())
          + f" · model {getattr(llm, 'name', config.llm.model)}")
    print(f"Saved → {path}\nRun log → {log_path}")
    return 0


def make_reader():
    """Separate function so tests can swap in fake article pages."""
    import requests

    from .article import read_article

    session = requests.Session()
    return lambda story: read_article(story, session)


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
