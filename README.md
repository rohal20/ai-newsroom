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
| v0.2 | First AI briefing (one agent, Gemini Flash) | ✅ done |
| v0.3 | Editor, researcher and writer agents | next |
| v0.4 | Critic agent + revise loop | planned |
| v0.5 | Daily run on GitHub Actions | planned |
| v0.6 | Website with an archive of editions | planned |

## Quick start

```bash
git clone https://github.com/your-username/ai-newsroom.git
cd ai-newsroom
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

Get a free Gemini API key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey), then:

```bash
cp .env.example .env          # Windows: copy .env.example .env
# open .env and paste your key after GEMINI_API_KEY=

python -m newsroom brief
```

This fetches today's stories, asks the model to pick the five that matter, and saves the edition to `editions/YYYY-MM-DD.md`.

Other commands:

```bash
python -m newsroom fetch -v          # only fetch and deduplicate, no AI
python -m newsroom brief --dry-run   # show exactly what would be sent to the model (no key needed)
pytest -q                            # run the tests (no key or internet needed)
```

## Configuration

Everything lives in [`config.yaml`](config.yaml):

- **topic, feeds and keywords:** what the newsroom follows. To cover security, finance or your own industry, change these. No code changes needed.
- **llm:** which model writes the briefing. Any OpenAI-compatible provider works: `gemini` (free tier), `groq`, or `ollama` to run a model on your own machine with no key.
- **briefing:** how many stories go in each edition, and how many candidates the model chooses from.

## How it works

1. **Fetch:** downloads each RSS or Atom feed. A feed that is down is logged and skipped, so one broken source never stops the run.
2. **Filter:** drops old stories, and for general feeds such as Hacker News keeps only stories that match a keyword as a whole word (so "said" does not match "AI").
3. **Deduplicate:** normalizes links and merges near-identical headlines, so a story shared by several feeds appears once.
4. **Select:** picks up to 40 candidates, newest first, taken in turn from each source so one busy feed (arXiv) cannot crowd out the rest.
5. **Brief:** the model picks the top stories and writes a summary and a "why it matters" line for each.

### No invented links

The model never writes URLs. It refers to stories by id, and every link in the edition comes from the fetched data. If the model returns an id that does not exist, that item is dropped. This is the first step toward the critic agent, which will check every claim against its source.

The client also retries rate limits and server errors with backoff, accepts JSON wrapped in code fences, and asks the model once more (with the error) if its reply cannot be parsed.

## Project layout

```
newsroom/
  config.py     load and validate config.yaml
  models.py     the Story model shared by every agent
  sources.py    fetch feeds, filter by keyword and age
  dedupe.py     remove duplicates, clean tracking parameters from links
  llm.py        small client for any OpenAI-compatible API (Gemini, Groq, Ollama)
  briefing.py   select candidates, write the briefing, render the edition
  __main__.py   command line: fetch, brief
tests/          unit tests with fixture feeds and a fake model (no network needed)
editions/       one Markdown file per day
```

## License

MIT
