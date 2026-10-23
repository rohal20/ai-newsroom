# ai-newsroom

![tests](https://github.com/rohal20/ai-newsroom/actions/workflows/tests.yml/badge.svg)
![daily edition](https://github.com/rohal20/ai-newsroom/actions/workflows/daily.yml/badge.svg)

A team of AI agents that reads the news, checks every claim against its source, and delivers one short, trusted briefing every morning.

**[Read the latest briefing →](editions/README.md)** · a new edition is published automatically every day at 07:07 (Pakistan time).

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
| v0.4 | Critic agent, revise loop, and a measured evaluation | ✅ done |
| v0.5 | Publishes itself every morning with GitHub Actions | ✅ done |
| v0.6 | Website with an archive of editions | next |

## Results

The critic was measured on [13 test drafts](evals/critic_cases.json), 8 of them with a planted error, using `gemini-3.5-flash-lite` ([full report](evals/results-2026-10-22.md)):

| | Result |
|---|---|
| Planted errors caught | **8 of 8**, each pointed at the exact phrase |
| Caught by the number check (code) | 2 |
| Caught by the AI critic | 7 (one error was caught by both) |
| False alarms on clean drafts | **0 of 5** |

The errors cover a changed number, an invented amount, an added company, "plans to" turned into "has launched", a contradiction, a wrong month, an invented "world's first" and an overclaimed result. This is a small test set, and the same model family writes and checks the drafts, so treat it as a sanity check rather than a benchmark. Run `python -m newsroom eval-critic` to measure your own model.

## Quick start

```bash
git clone https://github.com/rohal20/ai-newsroom.git
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
Critic (round 1): 4 of 5 approved, 1 problem(s) in 1 stor(ies)
    ✗ "$60 billion": number not found in the verified facts
Writer: rewrote 1 stor(ies)
Critic (round 2): 1 of 1 approved

Critic: 4 approved, 1 rewritten
23 facts verified, 2 unsupported dropped · editor 6.1s · researcher 31.0s · writer 9.4s · critic 12.0s
Saved → editions/2026-10-19.md
Run log → data/run-2026-10-19.json
```

Other commands:

```bash
python -m newsroom fetch -v          # only fetch and deduplicate, no AI
python -m newsroom brief --dry-run   # show what the editor would see (no key needed)
python -m newsroom eval-critic       # measure the critic on drafts with planted errors
python -m newsroom index             # rebuild the editions archive page
pytest -q                            # run the tests (no key or internet needed)
```

## Run it every morning on GitHub (free)

The [`daily-edition`](.github/workflows/daily.yml) workflow runs the whole newsroom on GitHub's servers, commits the new edition and updates the [archive](editions/README.md). Your computer can be off.

1. In your repository, go to **Settings → Secrets and variables → Actions → New repository secret**. Name it `GEMINI_API_KEY` and paste your key. (Optional: add `GROQ_API_KEY` too, as a second fallback.)
2. Go to **Actions → daily-edition → Run workflow** to run it once now and check it works (about 2–3 minutes).
3. That's it. From now on it runs every day at 02:07 UTC (07:07 in Pakistan). To change the time, edit the `cron` line in the workflow; it is always in UTC.

If a run fails (a missing key, or every model out of quota), GitHub emails you and the run log is kept for 14 days under the run's **Artifacts**. Editions are committed by `github-actions[bot]`.

## How it works

1. **Fetch:** downloads each RSS or Atom feed. A feed that is down is logged and skipped. Each feed can set its own age limit, since blogs post less often than news sites.
2. **Filter and deduplicate:** keeps on-topic stories, then merges the same story arriving from several feeds.
3. **Select:** picks up to 40 candidates, taken in turn from each source so one busy feed (arXiv) cannot crowd out the rest.
4. **Editor agent:** ranks the candidates by importance. Code then applies the mix rule (at most 2 research papers), because hard rules are more reliable in code than in a prompt.
5. **Researcher agent:** downloads each chosen article, extracts the readable text, and pulls out 3 to 6 facts. **Each fact must come with a quote, and code checks that the quote really appears in the article.** Facts that fail the check are dropped before anyone writes a word.
6. **Writer agent:** writes the headline, summary and "why it matters" for each story using only the verified facts.
7. **Critic agent:** checks every written sentence against that story's facts, in two layers:
   - **Number check (code):** every number in the draft ($45B, 89%, 17 of 211) must appear in the facts. "$45 billion", "45B" and "45bn" are treated as the same number.
   - **AI critic:** flags added names, dates, causes, "firsts" and over-certainty ("plans to" becoming "has launched"), quoting the exact phrase.

   Flagged stories go back to the writer with the critic's notes, up to 2 rewrites. A story that still fails is published as its verified facts only, so nothing unverified reaches the reader. If the AI critic is unavailable, the number check still runs and the edition says so.
8. **Edition:** saved to `editions/YYYY-MM-DD.md`, with how many facts were checked per story, and added to the archive page `editions/README.md`. A full run log (editor reasons, every fact and quote, every critic round, timings) goes to `data/run-YYYY-MM-DD.json`.

### Measuring the critic

`python -m newsroom eval-critic` runs the critic on [13 drafts](evals/critic_cases.json): 8 with a planted error (a changed number, an invented amount, an added company, a "plans to" turned into "has launched", a contradiction, a wrong month, an invented "world's first", an overclaimed result) and 5 clean drafts. It reports how many errors were caught, by which layer, and how many clean drafts were wrongly flagged, and writes a report to `evals/results-YYYY-MM-DD.md`.

With the AI critic switched off, the number check alone catches **2 of 8** planted errors with **0 false alarms**. That is the baseline the AI layer has to beat. (This exact result is checked by the test suite.)

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
    writer.py       writes the briefing from verified facts only, and rewrites flagged stories
    critic.py       number check in code + AI critic on every written sentence
  pipeline.py       runs editor → researcher → writer ⇄ critic and records a run log
  evaluate.py       scores the critic on drafts with planted errors
  briefing.py       candidate selection and the Markdown edition
  archive.py        the editions archive page (editions/README.md)
  __main__.py       command line: fetch, brief
tests/              unit and pipeline tests with fake feeds, pages and a fake model
editions/           one Markdown file per day, plus README.md, the archive
.github/workflows/  tests on every push; daily-edition every morning
evals/              critic test cases and evaluation reports
```

## License

MIT
