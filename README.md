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
| v0.3 | Editor, researcher and writer agents; quote-checked facts | ✅ done |
| v0.4 | Critic agent + revise loop | next |
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

Example output:

```
Fetched 238 stories from 5/6 feeds → 236 unique → data/stories-2026-10-19.json
Editor: choosing 5 of 40 candidates from 5 sources
  • Replit switches its default model provider to OpenAI  (TechCrunch AI)
  • ...
Researcher: 5 facts from the article  [techcrunch.com]
Researcher: 4 facts from the article, 1 dropped  [huggingface.co]
...
Writer: 5 stories written
23 facts verified, 2 unsupported dropped · editor 6.1s · researcher 31.0s · writer 9.4s
Saved → editions/2026-10-19.md
Run log → data/run-2026-10-19.json
```

Other commands:

```bash
python -m newsroom fetch -v          # only fetch and deduplicate, no AI
python -m newsroom brief --dry-run   # show what the editor would see (no key needed)
pytest -q                            # run the tests (no key or internet needed)
```

## How it works

1. **Fetch:** downloads each RSS or Atom feed. A feed that is down is logged and skipped. Each feed can set its own age limit, since blogs post less often than news sites.
2. **Filter and deduplicate:** keeps on-topic stories, then merges the same story arriving from several feeds.
3. **Select:** picks up to 40 candidates, taken in turn from each source so one busy feed (arXiv) cannot crowd out the rest.
4. **Editor agent:** ranks the candidates by importance. Code then applies the mix rule (at most 2 research papers), because hard rules are more reliable in code than in a prompt.
5. **Researcher agent:** downloads each chosen article, extracts the readable text, and pulls out 3 to 6 facts. **Each fact must come with a quote, and code checks that the quote really appears in the article.** Facts that fail the check are dropped before anyone writes a word.
6. **Writer agent:** writes the headline, summary and "why it matters" for each story using only the verified facts.
7. **Edition:** saved to `editions/YYYY-MM-DD.md`, with how many facts were checked per story. A full run log (editor reasons, every fact and quote, timings) goes to `data/run-YYYY-MM-DD.json`.

### Why the quote check matters

Language models sometimes add details that sound right but are not in the source. Asking the model to quote its evidence, and verifying that quote in code, catches most of that cheaply and deterministically. In testing, a model that invented one extra fact per story had every one of them removed. The critic agent in v0.4 builds on this by checking the *written* sentences, not just the facts.

The model also never writes URLs: it refers to stories by id, and every link comes from the fetched data.

## Configuration

Everything lives in [`config.yaml`](config.yaml):

- **topic, feeds, keywords:** what the newsroom follows. Point it at security, finance or your own industry by changing these. No code changes needed.
- **llm:** any OpenAI-compatible provider: `gemini` (free tier), `groq`, or `ollama` to run a model on your own machine. Set `fallbacks` to keep the run going when the main model is overloaded or out of free quota: the run switches once and stays on the backup. `reasoning_effort: low` keeps Gemini 3 fast, and `min_interval_seconds` spaces out calls to stay under per-minute limits.
- **briefing:** stories per edition, candidates, per-source cap, and the paper limit.
- **research:** how much of each article is read, and the minimum number of verified facts.

## Project layout

```
newsroom/
  config.py         load and validate config.yaml
  models.py         the Story model shared by every agent
  sources.py        fetch feeds, filter by keyword and age
  dedupe.py         remove duplicates, clean tracking parameters from links
  article.py        download a page and extract the article text
  llm.py            client for any OpenAI-compatible API, plus JSON helpers
  agents/
    editor.py       ranks stories; code enforces the mix rule
    researcher.py   extracts facts and verifies each quote against the article
    writer.py       writes the briefing from verified facts only
  pipeline.py       runs editor → researcher → writer and records a run log
  briefing.py       candidate selection and the Markdown edition
  __main__.py       command line: fetch, brief
tests/              unit and pipeline tests with fake feeds, pages and a fake model
editions/           one Markdown file per day
```

## License

MIT
