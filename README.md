# ai-newsroom

![tests](https://github.com/your-username/ai-newsroom/actions/workflows/tests.yml/badge.svg)

A team of AI agents that reads the news, checks every claim against its source, and delivers one short, trusted briefing every morning.

```
Sources → Editor → Researcher → Writer → Critic → Publish
                                   ↑________|  (unverified claims go back for a rewrite)
```

## Status

| Version | Milestone | Status |
|---|---|---|
| v0.1 | Fetch feeds, filter, remove duplicates | ✅ done |
| v0.2 | First LLM summary (one agent) | next |
| v0.3 | Editor, researcher and writer agents | planned |
| v0.4 | Critic agent + revise loop | planned |
| v0.5 | Daily run on GitHub Actions | planned |
| v0.6 | Website with an archive of editions | planned |

## Quick start

```bash
git clone https://github.com/your-username/ai-newsroom.git
cd ai-newsroom
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

python -m newsroom fetch -v
# example output:
# Fetched 214 stories from 6/6 feeds → 163 unique → data/stories-2026-10-18.json
```

Run the tests:

```bash
pytest -q
```

## Configuration

Everything lives in [`config.yaml`](config.yaml): the topic, the feeds, how old a story can be, and the keywords used to filter general feeds such as Hacker News. To follow a different topic (security, finance, your industry), change the feeds and keywords. No code changes needed.

## How v0.1 works

1. **Fetch:** downloads each RSS or Atom feed with a timeout. A feed that is down is logged and skipped, so one broken source never stops the run.
2. **Filter:** drops stories older than `max_age_hours`, and for feeds marked `filter: true`, keeps only stories that match a keyword as a whole word (so "said" does not match "AI").
3. **Deduplicate:** the same story often appears in several feeds. Links are normalized (tracking parameters, `www`, trailing slashes removed) and near-identical headlines are merged, keeping the first copy.
4. **Save:** writes the unique stories to `data/stories-YYYY-MM-DD.json`.

## Project layout

```
newsroom/
  config.py     load and validate config.yaml
  models.py     the Story model shared by every agent
  sources.py    fetch feeds, filter by keyword and age
  dedupe.py     remove duplicate stories
  __main__.py   command line: python -m newsroom fetch
tests/          unit tests with fixture feeds (no network needed)
```

## License

MIT
