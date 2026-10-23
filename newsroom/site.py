"""The website: `python -m newsroom site` turns editions/ into a static site in site/.

Pages:
  index.html               today's run (dashboard) and today's briefing
  editions/YYYY-MM-DD.html the same for every past day
  archive.html             every edition, newest first
  sources.html             the feeds the newsroom reads
  how-it-works.html        the agents, and the critic's measured results
  feed.xml                 RSS, so readers can subscribe

Every number comes from a real run: editions/YYYY-MM-DD.json (written since v0.6) or,
for older editions, what can be read back from the Markdown file. Nothing is estimated.
No JavaScript and no build tools, so GitHub Pages can serve it as is.
"""

from __future__ import annotations

import math
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from email.utils import format_datetime
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .agents.critic import Issue
from .config import Config
from .record import EditionRecord, FactRecord, load_record

_MD_NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.(md|json)$")
_MARKER = ".newsroom-site"


# ---------------------------------------------------------------- data


@dataclass
class SiteStory:
    title: str
    summary: str
    why: str
    url: str
    source: str
    is_paper: bool = False
    status: str = ""  # approved / rewritten / facts-only / numbers-only / unchecked, "" if not recorded
    rewrites: int = 0
    facts: list[FactRecord] = field(default_factory=list)
    facts_count: int = 0
    checked_against: str = ""
    issues: list[Issue] = field(default_factory=list)
    dropped: int = 0
    sentences: int = 0


@dataclass
class SiteEdition:
    day: date
    one_line: str
    stories: list[SiteStory]
    candidates: int | None = None
    sentences: int | None = None
    dropped: int = 0
    record: EditionRecord | None = None
    number: int = 0

    @property
    def facts_verified(self) -> int:
        return sum(s.facts_count for s in self.stories)

    @property
    def rewritten(self) -> int:
        return sum(1 for s in self.stories if s.status == "rewritten")

    @property
    def facts_only(self) -> int:
        return sum(1 for s in self.stories if s.status == "facts-only")

    @property
    def issues_caught(self) -> int:
        return sum(len(s.issues) for s in self.stories)

    @property
    def has_critic(self) -> bool:
        return any(s.status for s in self.stories)

    @property
    def total_seconds(self) -> float | None:
        return self.record.total_seconds if self.record else None

    @property
    def words(self) -> int:
        return sum(len(f"{s.title} {s.summary} {s.why}".split()) for s in self.stories) + len(self.one_line.split())

    @property
    def read_minutes(self) -> int:
        return max(1, math.ceil(self.words / 200))

    @property
    def page(self) -> str:
        return f"editions/{self.day.isoformat()}.html"


def from_record(rec: EditionRecord) -> SiteEdition:
    stories = [
        SiteStory(
            title=s.title, summary=s.summary, why=s.why_it_matters, url=s.url, source=s.source, is_paper=s.is_paper,
            status=s.status, rewrites=s.rewrites, facts=s.facts, facts_count=len(s.facts),
            checked_against="abstract" if "abstract" in s.text_source else s.text_source,
            issues=s.first_issues, dropped=s.dropped_facts, sentences=s.sentences_checked,
        )
        for s in rec.stories
    ]
    has_critic = rec.critic_rounds > 0 or any(s.status not in ("", "unchecked") for s in rec.stories)
    return SiteEdition(
        day=rec.day, one_line=rec.one_line, stories=stories, candidates=rec.candidates,
        sentences=sum(s.sentences for s in stories) if has_critic else None,
        dropped=sum(s.dropped for s in stories), record=rec,
    )


_STORY_RE = re.compile(r"^## \d+\. (.+)$", re.MULTILINE)
_SOURCE_RE = re.compile(r"^Source: \[(?P<name>[^\]]+)\]\((?P<url>[^)\s]+)\)(?P<rest>.*)$")
_FACTS_RE = re.compile(r"(\d+) facts checked against the ([\w ]+?)(?: ·|</sub>|$)")


def _status_from_text(text: str) -> tuple[str, int]:
    m = re.search(r"approved after (\d+) rewrite", text)
    if m:
        return "rewritten", int(m[1])
    if "critic approved" in text:
        return "approved", 0
    if "failed fact-check" in text:
        return "facts-only", 0
    if "numbers checked" in text:
        return "numbers-only", 0
    if "not reviewed" in text:
        return "unchecked", 0
    return "", 0


def from_markdown(path: Path, day: date) -> SiteEdition:
    """Read an edition written before v0.6, when only the Markdown file was saved."""
    text = path.read_text(encoding="utf-8")
    one_line = next((line[2:].strip() for line in text.splitlines() if line.startswith("> ")), "")
    parts = _STORY_RE.split(text)
    stories = []
    for title, body in zip(parts[1::2], parts[2::2]):
        summary, why, url, source, rest = [], "", "", "", ""
        for para in (p.strip() for p in body.split("\n\n")):
            if not para or para.startswith("<sub>Chosen"):
                continue
            if para.startswith("**Why it matters:**"):
                why = para.removeprefix("**Why it matters:**").strip()
            elif m := _SOURCE_RE.match(para):
                source, url, rest = m["name"], m["url"], m["rest"]
            else:
                summary.append(para)
        status, rewrites = _status_from_text(rest)
        fm = _FACTS_RE.search(rest)
        stories.append(SiteStory(
            title=title.strip(), summary=" ".join(summary), why=why, url=url, source=source,
            is_paper="arxiv.org" in url, status=status, rewrites=rewrites,
            facts_count=int(fm[1]) if fm else 0, checked_against=fm[2].strip() if fm else "",
        ))
    footer = text[text.rfind("<sub>Chosen"):] if "<sub>Chosen" in text else ""
    num = lambda pattern: int(m[1]) if (m := re.search(pattern, footer)) else None  # noqa: E731
    return SiteEdition(
        day=day, one_line=one_line, stories=stories, candidates=num(r"Chosen from (\d+) stories"),
        sentences=num(r"critic checked (\d+) sentences"), dropped=num(r"(\d+) unsupported facts removed") or 0,
    )


def load_editions(editions_dir: str | Path) -> list[SiteEdition]:
    """Every edition, oldest first, numbered from 1. A JSON record wins over the Markdown file."""
    found: dict[date, Path] = {}
    for p in Path(editions_dir).glob("*"):
        m = _MD_NAME.match(p.name)
        if not m:
            continue
        day = date(int(m[1]), int(m[2]), int(m[3]))
        if m[4] == "json" or day not in found:
            found[day] = p
    editions = []
    for day in sorted(found):
        p = found[day]
        editions.append(from_record(load_record(p)) if p.suffix == ".json" else from_markdown(p, day))
    for i, e in enumerate(editions, 1):
        e.number = i
    return editions


@dataclass
class EvalSummary:
    day: str
    model: str
    caught: int
    planted: int
    by_numbers: int
    by_ai: int
    false_alarms: int
    clean: int
    file: str
    rows: list[list[str]] = field(default_factory=list)


def load_eval(evals_dir: str | Path) -> EvalSummary | None:
    """The newest evals/results-YYYY-MM-DD.md written by `python -m newsroom eval-critic`."""
    files = sorted(Path(evals_dir).glob("results-*.md"))
    if not files:
        return None
    text = files[-1].read_text(encoding="utf-8")

    def grab(pattern: str) -> re.Match | None:
        return re.search(pattern, text)

    caught, alarms = grab(r"Planted errors caught: (\d+) of (\d+)"), grab(r"False alarms on clean drafts: (\d+) of (\d+)")
    if not caught or not alarms:
        return None
    nums, ai = grab(r"by the number check \(code\): (\d+)"), grab(r"by the AI critic: (\d+)")
    model = grab(r"Model: `([^`]+)`")
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 5 and cells[0] not in ("Case",) and not set(cells[0]) <= set("-"):
            rows.append(cells)
    return EvalSummary(
        day=files[-1].stem.removeprefix("results-"), model=model[1] if model else "", caught=int(caught[1]),
        planted=int(caught[2]), by_numbers=int(nums[1]) if nums else 0, by_ai=int(ai[1]) if ai else 0,
        false_alarms=int(alarms[1]), clean=int(alarms[2]), file=files[-1].name, rows=rows,
    )


# ---------------------------------------------------------------- small helpers


def e(value: object) -> str:
    return escape(str(value), quote=True)


def safe_url(url: str) -> str:
    return url if urlparse(url).scheme in ("http", "https") else "#"


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    s = round(seconds)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def clock(seconds: float) -> str:
    s = round(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def get_tz(name: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # unknown name, or no time zone data (Windows without tzdata)
        return timezone.utc


STATUS_PILL = {
    "approved": ("ok", "Critic approved"),
    "rewritten": ("ok", "Approved after {n}"),
    "facts-only": ("warn", "Verified facts only"),
    "numbers-only": ("warn", "Numbers checked"),
    "unchecked": ("mute", "Not reviewed"),
}


def status_pill(story: SiteStory) -> str:
    if story.status not in STATUS_PILL:
        return ""
    cls, label = STATUS_PILL[story.status]
    return f'<span class="pill {cls}">{e(label.format(n=plural(story.rewrites, "rewrite")))}</span>'


# ---------------------------------------------------------------- page shell

FONTS = ("https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800"
         "&family=Hanken+Grotesk:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500"
         "&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&display=swap")
ICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' "
        "height='16' rx='4' fill='%230B1230'/%3E%3Crect x='4' y='4' width='8' height='8' rx='2' fill='%237F92FF'/%3E%3C/svg%3E")
NAV = [("run", "Today's run", "index.html"), ("editions", "Editions", "archive.html"),
       ("sources", "Sources", "sources.html"), ("how", "How it works", "how-it-works.html")]


def page(title: str, body: str, *, active: str, root: str, config: Config, model: str, description: str) -> str:
    s = config.site
    nav = "".join(
        f'<a class="nav{" on" if key == active else ""}" href="{root}{href}"'
        f'{" aria-current=page" if key == active else ""}>{e(label)}</a>'
        for key, label, href in NAV
    )
    foot = [("Topic", config.topic), ("Model", model or config.llm.model)]
    if s.schedule:
        foot.append(("Schedule", s.schedule))
    side_foot = "<br>".join(f"{e(k)} <b>{e(v)}</b>" for k, v in foot)
    rss = f'<link rel="alternate" type="application/rss+xml" title="{e(s.title)}" href="{root}feed.xml">' if s.base_url else ""
    links = [f"ai-newsroom v{__version__}", "every number on this site comes from a real run"]
    if s.repo_url:
        links.append(f'<a href="{e(safe_url(s.repo_url))}">source code</a>')
    if s.base_url:
        links.append(f'<a href="{root}feed.xml">RSS</a>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}">
<link rel="icon" href="{ICON}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="{root}style.css">
{rss}
</head>
<body>
<div class="shell">
<nav class="side" aria-label="Site">
<a class="brand" href="{root}index.html">ai-newsroom</a>
{nav}
<div class="side-foot">{side_foot}</div>
</nav>
<main class="main">
{body}
<footer class="site-foot">{" · ".join(links)}</footer>
</main>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------- dashboard


def run_log(ed: SiteEdition, tz: tzinfo) -> list[tuple[str, str, str, str]]:
    """(time, agent, message, kind) lines rebuilt from the record. Times are real stage end times."""
    rec = ed.record
    n = len(ed.stories)
    lines: list[tuple[str, str, str, str]] = []
    if rec is None:
        stamp = lambda _offset: ""  # noqa: E731
    elif rec.started:
        start = rec.started if rec.started.tzinfo else rec.started.replace(tzinfo=timezone.utc)
        stamp = lambda offset: (start + timedelta(seconds=offset)).astimezone(tz).strftime("%H:%M:%S")  # noqa: E731
    else:
        stamp = lambda offset: "+" + clock(offset)  # noqa: E731

    t = 0.0
    if rec and rec.fetch:
        f = rec.fetch
        t += f.seconds
        lines.append((stamp(t), "sources", f"Fetched {f.feeds_ok} of {f.feeds_total} feeds · {f.fetched} stories", ""))
        lines.append(("", "sources", f"Removed duplicates → {f.unique} unique", ""))
    secs = rec.seconds if rec else {}
    t += secs.get("editor", 0)
    picked = f"picked {n}"
    lines.append((stamp(t), "editor", f"Ranked {ed.candidates} candidates, {picked}" if ed.candidates else f"Picked {n} stories", ""))
    t += secs.get("researcher", 0)
    msg = f"Verified {plural(ed.facts_verified, 'fact')} from {plural(n, 'article')}"
    if ed.dropped:
        msg += f" · {ed.dropped} unsupported dropped"
    lines.append((stamp(t), "research", msg, ""))
    t += secs.get("writer", 0)
    lines.append((stamp(t), "writer", f"Draft v1 ready · {plural(n, 'story', 'stories')}", ""))

    if ed.has_critic:
        for i, s in enumerate(ed.stories, 1):
            for issue in s.issues[:2]:
                problem = issue.problem or "not found in the facts"
                lines.append(("", "critic", f"Story {i}: “{issue.text}” {problem}", "warn"))
            if len(s.issues) > 2:
                lines.append(("", "critic", f"Story {i}: {len(s.issues) - 2} more", "warn"))
        sent_back = sum(1 for s in ed.stories if s.status in ("rewritten", "facts-only"))
        if sent_back:
            lines.append(("", "critic", f"Returned {plural(sent_back, 'story', 'stories')} to the writer", ""))
            if ed.rewritten:
                lines.append(("", "writer", f"Rewrote {plural(ed.rewritten, 'story', 'stories')}", ""))
        t += secs.get("critic", 0)
        checked = f" · {ed.sentences} sentences checked" if ed.sentences else ""
        if ed.facts_only:
            lines.append((stamp(t), "critic", f"{plural(ed.facts_only, 'story', 'stories')} shown as verified facts only", "warn"))
        if any(s.status == "numbers-only" for s in ed.stories):
            lines.append((stamp(t), "critic", "AI critic unavailable; numbers checked in code", "warn"))
        else:
            ok = n - ed.facts_only
            lines.append((stamp(t), "critic", (f"All {n} stories approved" if ok == n else f"{ok} of {n} approved") + checked, "good"))
    lines.append((stamp(t), "publish", f"Committed editions/{ed.day.isoformat()}.md", "good"))
    return lines


def lanes(ed: SiteEdition) -> list[tuple[str, str, str, str]]:
    """(agent, state, pill label, detail) for the six agents."""
    rec, n = ed.record, len(ed.stories)
    secs = rec.seconds if rec else {}

    def took(key: str) -> str:
        return f" · {duration(secs[key])}" if key in secs else ""

    if rec and rec.fetch:
        src = (f"{rec.fetch.unique} unique stories" + (f" · {duration(rec.fetch.seconds)}" if rec.fetch.seconds else ""))
    else:
        src = f"{ed.candidates} candidates" if ed.candidates else "Feed counts not recorded"
    research = plural(ed.facts_verified, "fact") + " verified" + (f", {ed.dropped} dropped" if ed.dropped else "")
    writer = plural(n, "story", "stories") + (f" · {plural(ed.rewritten, 'rewrite')}" if ed.rewritten else "")
    out = [
        ("Sources", "done", "Done", src),
        ("Editor", "done", "Done", f"{n} stories picked" + took("editor")),
        ("Researcher", "done", "Done", research + took("researcher")),
        ("Writer", "done", "Done", writer + took("writer")),
    ]
    if not ed.has_critic:
        out.append(("Critic", "mute", "Not yet", "Added in v0.4"))
    else:
        caught = ed.issues_caught
        detail = f"{ed.sentences} sentences checked" if ed.sentences else "Every sentence checked"
        if caught:
            detail += f" · {plural(caught, 'catch', 'catches')}"
        elif ed.rewritten and not rec:
            detail += f" · {plural(ed.rewritten, 'story', 'stories')} sent back"
        if ed.facts_only:
            out.append(("Critic", "flag", "Flagged", f"{ed.facts_only} shown as facts only" + took("critic")))
        else:
            out.append(("Critic", "caught" if (caught or ed.rewritten) else "done", "Approved", detail + took("critic")))
    out.append(("Publish", "done", "Done", f"editions/{ed.day.isoformat()}.md"))
    return out


def chart(ed: SiteEdition, editions: list[SiteEdition]) -> str:
    by_day = {x.day: x for x in editions}
    days = [ed.day - timedelta(days=13 - i) for i in range(14)]
    timed = [by_day[d].total_seconds for d in days if d in by_day and by_day[d].total_seconds]
    top = max(timed, default=0)
    bars = []
    for d in days:
        x = by_day.get(d)
        label = f"{d:%d %b}"
        if x is None:
            bars.append(f'<span class="none" title="{label}: no edition"></span>')
        elif not x.total_seconds:
            bars.append(f'<span class="nodata" title="{label}: run time not recorded"></span>')
        else:
            h = max(6, round(100 * x.total_seconds / top))
            cls = ' class="today"' if d == ed.day else ""
            bars.append(f'<span{cls} style="height:{h}%" title="{label}: {duration(x.total_seconds)}"></span>')
    avg = f"avg {duration(sum(timed) / len(timed))}" if timed else "timings start with v0.6"
    return f"""<div class="panel-t" style="margin-top:6px"><span>Run time, last 14 days</span><span>{e(avg)}</span></div>
<div class="chart" role="img" aria-label="Run time per day for the 14 days up to {ed.day:%d %B}">{"".join(bars)}</div>
<div class="chart-x"><span>{days[0]:%d %b}</span><span>{days[-1]:%d %b}</span></div>"""


def dashboard(ed: SiteEdition, editions: list[SiteEdition], config: Config, root: str) -> str:
    rec = ed.record
    tz = get_tz(config.site.timezone)
    sched = f'<span class="sched">{e(config.site.schedule)}</span>' if config.site.schedule else ""
    total = ed.total_seconds
    run_clock = f'<span class="clock" title="Total run time">{clock(total)}</span>' if total else ""
    pill = '<span class="pill ok">Published</span>'

    if rec and rec.fetch:
        scanned = ("Stories scanned", str(rec.fetch.fetched), f"from {rec.fetch.feeds_ok} of {rec.fetch.feeds_total} feeds")
    else:
        scanned = ("Candidates", str(ed.candidates) if ed.candidates else "–", "shown to the editor")
    if not ed.has_critic:
        critic = ("Critic catches", "–", "critic added in v0.4")
    elif rec:
        critic = ("Critic catches", str(ed.issues_caught),
                  "claim sent back" if ed.issues_caught == 1 else ("claims sent back" if ed.issues_caught else "nothing to fix"))
    else:
        sent = ed.rewritten + ed.facts_only
        critic = ("Critic catches", str(sent), "story sent back" if sent == 1 else "stories sent back")
    kpis = [
        scanned,
        ("Selected", str(len(ed.stories)), "by the editor"),
        ("Facts verified", str(ed.facts_verified), "quotes checked in code"),
        critic,
        ("Run time", duration(total), e(rec.model) if rec else "not recorded before v0.6"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><span class="l">{e(l)}</span><span class="v">{e(v)}</span><span class="s">{s}</span></div>'
        for l, v, s in kpis  # noqa: E741
    )
    lane_html = "".join(
        f'<div class="lane" data-s="{st}"><span class="nm">{e(nm)}</span><div class="bar"><div></div></div>'
        f'<span class="pill {dict(done="ok", caught="ok", flag="warn", mute="mute")[st]}">{e(label)}</span>'
        f'<span class="dt">{e(dt)}</span></div>'
        for nm, st, label, dt in lanes(ed)
    )
    log_lines = run_log(ed, tz)
    zone = datetime.now(tz).strftime("%Z") if rec and rec.started else ""
    log_html = "".join(
        f'<li class="{kind}"><span class="ts">{e(ts)}</span><span class="ag">{e(ag)}</span><span class="msg">{e(msg)}</span></li>'
        for ts, ag, msg, kind in log_lines
    )
    note = "" if rec else '<p class="note">This edition was written before v0.6, so its timings and critic notes were not recorded. The counts come from the edition file.</p>'
    return f"""<section class="run" id="run" aria-labelledby="run-title">
<div class="run-head">
<h1 id="run-title">Run #{ed.number}</h1><span class="meta">{ed.day:%A %d %b} · topic: {e(config.topic)}</span>{sched}
<div class="right">{run_clock}{pill}</div>
</div>
<div class="kpis">{kpi_html}</div>
<div class="cols">
<div class="panel"><div class="panel-t"><span>Agents</span><span>6 steps</span></div>
<div class="lanes">{lane_html}</div>
{chart(ed, editions)}
</div>
<div class="panel"><div class="panel-t"><span>Run log{(" · " + e(zone)) if zone else ""}</span><span class="live">Finished</span></div>
<ol class="log">{log_html}</ol>
{note}
</div>
</div>
</section>"""


# ---------------------------------------------------------------- the reader


def kicker(story: SiteStory, i: int) -> str:
    kind = "Research paper" if story.is_paper else (urlparse(story.url).hostname or "").removeprefix("www.")
    return f"{i:02d} · {kind}" if kind else f"{i:02d}"


def story_html(story: SiteStory, i: int) -> str:
    why = f'<p class="why"><b>Why it matters:</b> {e(story.why)}</p>' if story.why else ""
    against = f" against the {e(story.checked_against)}" if story.checked_against else ""
    if story.facts:
        items = "".join(f"<li>{e(f.claim)}<q>{e(f.quote)}</q></li>" for f in story.facts)
        caught = ""
        if story.issues:
            caught = "".join(
                f'<li class="caught">“{e(x.text)}”: {e(x.problem or "not found in the facts")} '
                f'<small>({"number check" if x.by == "numbers" else "AI critic"})</small></li>'
                for x in story.issues
            )
            caught = f'<p class="caught-t">Caught in the first draft, then fixed:</p><ul>{caught}</ul>'
        facts = (f'<details class="facts"><summary>{plural(story.facts_count, "verified fact")}{against}</summary>'
                 f"<ul>{items}</ul>{caught}</details>")
    elif story.facts_count:
        facts = f'<span class="facts-n">{plural(story.facts_count, "fact")} checked{against}</span>'
    else:
        facts = ""
    return f"""<article class="story">
<span class="k">{e(kicker(story, i))}</span>
<h3>{e(story.title)}</h3>
<p>{e(story.summary)}</p>
{why}
<div class="foot"><a class="src" href="{e(safe_url(story.url))}" rel="noopener">Source: {e(story.source)} ↗</a>{status_pill(story)}</div>
{facts}
</article>"""


def reader(ed: SiteEdition, config: Config) -> str:
    stories = "".join(story_html(s, i) for i, s in enumerate(ed.stories, 1))
    bits = [f"{ed.facts_verified} facts verified against their sources"]
    if ed.candidates:
        bits.insert(0, f"Chosen from {ed.candidates} stories")
    if ed.sentences:
        bits.append(f"critic checked {ed.sentences} sentences")
    raw = ""
    if config.site.repo_url:
        base = e(config.site.repo_url.rstrip("/") + "/blob/main/editions/" + ed.day.isoformat())
        raw = f' · raw: <a href="{base}.md">Markdown</a>' + (f' · <a href="{base}.json">JSON</a>' if ed.record else "")
    return f"""<section class="edition" id="briefing" aria-labelledby="briefing-title">
<header class="mast">
<h2 id="briefing-title">{e(config.site.title)}</h2>
<span class="im">Issue {ed.number} · {plural(len(ed.stories), "story", "stories")} · {ed.read_minutes} min read · {ed.day:%A %d %B %Y}</span>
</header>
<p class="oneline">{e(ed.one_line)}</p>
{stories}
<p class="edition-foot">{e(" · ".join(bits))}{raw}</p>
</section>"""


def edition_page(ed: SiteEdition, editions: list[SiteEdition], config: Config, *, latest: bool) -> str:
    root = "" if latest else "../"
    i = editions.index(ed)
    prev_ = editions[i - 1] if i > 0 else None
    next_ = editions[i + 1] if i + 1 < len(editions) else None
    link = lambda x, text: f'<a href="{root}{x.page}">{text}</a>' if x else "<span></span>"  # noqa: E731
    pager = f'<nav class="pager" aria-label="Editions">{link(prev_, f"← {prev_.day:%a %d %b}" if prev_ else "")}' \
            f'<a href="{root}archive.html">All editions</a>{link(next_, f"{next_.day:%a %d %b} →" if next_ else "")}</nav>'
    body = dashboard(ed, editions, config, root) + reader(ed, config) + pager
    model = ed.record.model if ed.record else ""
    title = f"{config.site.title} · {ed.day:%d %B %Y}"
    return page(title, body, active="run" if latest else "editions", root=root, config=config, model=model,
                description=ed.one_line or f"The {config.topic} briefing for {ed.day:%d %B %Y}")


# ---------------------------------------------------------------- other pages


def archive_page(editions: list[SiteEdition], config: Config, model: str) -> str:
    rows = []
    for ed in reversed(editions):
        pills = [f'<span class="pill mute">{plural(len(ed.stories), "story", "stories")}</span>']
        if ed.rewritten:
            pills.append(f'<span class="pill ok">{ed.rewritten} rewritten</span>')
        if ed.facts_only:
            pills.append(f'<span class="pill warn">{ed.facts_only} facts only</span>')
        rows.append(f'<a class="row" href="{ed.page}"><span class="d">#{ed.number} · {ed.day:%a %d %b %Y}</span>'
                    f'<span class="t">{e(ed.one_line)}</span><span class="ps">{"".join(pills)}</span></a>')
    listing = "".join(rows) or '<p class="lede">No editions yet. Run <code>python -m newsroom brief</code> to write the first one.</p>'
    body = f"""<header><p class="eyebrow">Archive</p><h1 class="page-title">Every edition, newest first</h1>
<p class="lede">{plural(len(editions), "edition")}, each written by the agents and checked by the critic before it was published.</p></header>
<div class="arch">{listing}</div>"""
    return page(f"Editions · {config.site.title}", body, active="editions", root="", config=config, model=model,
                description=f"Every {config.topic} briefing written by ai-newsroom")


def sources_page(editions: list[SiteEdition], config: Config, model: str) -> str:
    latest = next((x.record for x in reversed(editions) if x.record and x.record.fetch), None)
    counts = latest.fetch.per_feed if latest else {}
    rows = []
    for f in config.feeds:
        kind = "aggregator" if f.aggregator else "feed"
        if f.filter:
            kind += " · keyword filter"
        n = counts.get(f.name)
        rows.append(f"<tr><td><b>{e(f.name)}</b><br><span class=\"mono\">{e(urlparse(str(f.url)).hostname or '')}</span></td>"
                    f"<td>{e(kind)}</td><td class=\"num\">{f.max_age_hours or config.max_age_hours}h</td>"
                    f"<td class=\"num\">{'–' if n is None else n}</td></tr>")
    when = f"in the run on {latest.day:%d %B}" if latest else "(recorded from v0.6)"
    body = f"""<header><p class="eyebrow">Sources</p><h1 class="page-title">What the newsroom reads</h1>
<p class="lede">{plural(len(config.feeds), "feed")}, fetched every morning. Stories older than the age limit are skipped, and the same story from several feeds is kept once.</p></header>
<div class="panel table-wrap"><table>
<thead><tr><th>Feed</th><th>Type</th><th class="num">Age limit</th><th class="num">Stories {e(when)}</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<p class="note">Keywords for filtered feeds: {e(", ".join(config.keywords)) or "none"}. Change the feeds in <code>config.yaml</code>; no code changes needed.</p>"""
    return page(f"Sources · {config.site.title}", body, active="sources", root="", config=config, model=model,
                description=f"The feeds ai-newsroom reads for its {config.topic} briefing")


def how_page(editions: list[SiteEdition], ev: EvalSummary | None, config: Config, model: str) -> str:
    last = editions[-1] if editions else None
    rec = last.record if last else None
    stat = lambda text: f'<span class="st">{e(text)}</span>' if last else ""  # noqa: E731
    nodes = [
        ("Sources", "RSS and Atom feeds: arXiv, blogs, news, Hacker News. Duplicates merged.",
         f"{rec.fetch.fetched} stories" if rec and rec.fetch else (f"{last.candidates} candidates" if last and last.candidates else "")),
        ("Editor", f"Ranks the candidates. Code enforces the mix: at most {config.briefing.max_papers} research papers.", f"Top {len(last.stories)} picked" if last else ""),
        ("Researcher", "Reads each article and pulls out facts. Every quote is checked in code.", f"{last.facts_verified} facts" if last else ""),
        ("Writer", "Writes headline, summary and why it matters from verified facts only.", "Draft ready" if last else ""),
        ("Critic", "Checks every sentence: numbers in code, then an AI critic. Fails go back.",
         plural(last.issues_caught, "catch", "catches") if rec else ("checked" if last and last.has_critic else "")),
        ("Publish", "Commits the edition, rebuilds this site. Every morning, on GitHub Actions.", "Published" if last else ""),
    ]
    node_html = "".join(
        f'<div class="node{" critic" if t == "Critic" else ""}"><span class="n">{i:02d}</span><span class="t">{t}</span>'
        f'<span class="d">{e(d)}</span>{stat(s) if s else ""}</div>'
        for i, (t, d, s) in enumerate(nodes, 1)
    )
    results = ""
    if ev:
        rows = "".join(f"<tr><td class=\"mono\">{e(r[0])}</td><td>{e(r[1])}</td><td>{e(r[3])}</td><td>{e(r[4])}</td></tr>" for r in ev.rows)
        results = f"""<section class="sec"><p class="eyebrow">Measured, not promised</p><h2 class="h2">How often the critic catches a planted error</h2>
<p class="lede">{ev.planted + ev.clean} test drafts, {ev.planted} with one error planted on purpose. Model: <code>{e(ev.model)}</code>, {e(ev.day)}.</p>
<div class="results">
<div class="why-c"><span class="n">Caught</span><span class="big">{ev.caught}/{ev.planted}</span><h3>planted errors caught</h3><p>A changed number, an added company, "plans to" turned into "has launched", an invented "first".</p></div>
<div class="why-c"><span class="n">False alarms</span><span class="big">{ev.false_alarms}/{ev.clean}</span><h3>clean drafts flagged</h3><p>A critic that flags everything is useless, so clean drafts are part of the test.</p></div>
<div class="why-c"><span class="n">Two layers</span><span class="big">{ev.by_numbers} + {ev.by_ai}</span><h3>by code + by the AI critic</h3><p>The number check is plain code and never gets tired. The AI critic catches the rest. One error can be caught by both.</p></div>
</div>
<div class="panel table-wrap"><table><thead><tr><th>Case</th><th>Kind</th><th>Result</th><th>What the critic flagged</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="note">A small test set, and the same model family writes and checks, so treat this as a sanity check, not a benchmark. Run <code>python -m newsroom eval-critic</code> to measure your own model.</p></section>"""
    totals = ""
    if editions:
        recorded = [x for x in editions if x.record]
        caught = sum(x.issues_caught for x in recorded)
        totals = f"""<section class="sec"><p class="eyebrow">So far</p><div class="kpis">
<div class="kpi"><span class="l">Editions</span><span class="v">{len(editions)}</span><span class="s">since {editions[0].day:%d %b %Y}</span></div>
<div class="kpi"><span class="l">Stories published</span><span class="v">{sum(len(x.stories) for x in editions)}</span><span class="s">each linked to its source</span></div>
<div class="kpi"><span class="l">Facts verified</span><span class="v">{sum(x.facts_verified for x in editions)}</span><span class="s">quote found in the article</span></div>
<div class="kpi"><span class="l">Stories rewritten</span><span class="v">{sum(x.rewritten for x in editions)}</span><span class="s">after a critic catch</span></div>
<div class="kpi"><span class="l">Claims caught</span><span class="v">{caught if recorded else "–"}</span><span class="s">{"in editions since v0.6" if recorded else "recorded from v0.6"}</span></div>
</div></section>"""
    body = f"""<header><p class="eyebrow">How it works</p><h1 class="page-title">Five agents. One editorial desk.</h1>
<p class="lede">Each agent does one job. The critic checks every sentence against its source, and if a claim can't be verified, it goes back for a rewrite. After two failed rewrites, only the verified facts are published.</p></header>
<div class="pipe">{node_html}</div>
{totals}{results}"""
    return page(f"How it works · {config.site.title}", body, active="how", root="", config=config, model=model,
                description="How ai-newsroom's agents read, fact-check and write the briefing")


def empty_page(config: Config) -> str:
    body = """<header><p class="eyebrow">Today</p><h1 class="page-title">No editions yet</h1>
<p class="lede">Run <code>python -m newsroom brief</code>, or start the daily-edition workflow on GitHub, and the first briefing will appear here.</p></header>"""
    return page(config.site.title, body, active="run", root="", config=config, model="", description="ai-newsroom")


def rss(editions: list[SiteEdition], config: Config) -> str:
    base = config.site.base_url.rstrip("/") + "/"
    items = []
    for ed in reversed(editions[-30:]):
        when = datetime.combine(ed.day, datetime.min.time(), tzinfo=timezone.utc)
        desc = "".join(f"<h3>{e(s.title)}</h3><p>{e(s.summary)}</p>" for s in ed.stories)
        items.append(f"<item><title>{e(f'{config.site.title} · {ed.day:%A %d %B %Y}')}</title>"
                     f"<link>{e(base + ed.page)}</link><guid>{e(base + ed.page)}</guid>"
                     f"<pubDate>{format_datetime(when)}</pubDate><description>{e(ed.one_line + desc)}</description></item>")
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0"><channel><title>{e(config.site.title)}</title>'
            f"<link>{e(base)}</link><description>{e(f'A daily {config.topic} briefing, fact-checked by AI agents')}</description>"
            f"{''.join(items)}</channel></rss>\n")


# ---------------------------------------------------------------- build


def build_site(config: Config, editions_dir: str | Path = "editions", evals_dir: str | Path = "evals",
               out_dir: str | Path = "site") -> list[Path]:
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        if not (out / _MARKER).exists():
            raise FileExistsError(f"{out} is not empty and was not made by `newsroom site`; choose another --out")
        shutil.rmtree(out)
    (out / "editions").mkdir(parents=True, exist_ok=True)

    editions = load_editions(editions_dir)
    model = next((x.record.model for x in reversed(editions) if x.record), "")
    pages: dict[str, str] = {}
    if editions:
        for ed in editions:
            pages[ed.page] = edition_page(ed, editions, config, latest=False)
        pages["index.html"] = edition_page(editions[-1], editions, config, latest=True)
    else:
        pages["index.html"] = empty_page(config)
    pages["archive.html"] = archive_page(editions, config, model)
    pages["sources.html"] = sources_page(editions, config, model)
    pages["how-it-works.html"] = how_page(editions, load_eval(evals_dir), config, model)
    if config.site.base_url:
        pages["feed.xml"] = rss(editions, config)

    written = []
    for name, html in pages.items():
        path = out / name
        path.write_text(html, encoding="utf-8")
        written.append(path)
    shutil.copyfile(Path(__file__).with_name("site_style.css"), out / "style.css")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / _MARKER).write_text("built by python -m newsroom site\n", encoding="utf-8")
    return written
