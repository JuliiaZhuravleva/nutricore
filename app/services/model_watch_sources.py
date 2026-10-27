"""Where the weekly model check looks (ADR-0004): each company's own word.

- OpenAI: ``models.retrieve`` — ``shutdown_date`` (an undeclared field, so read
  from ``model_extra``) for the alias and its dated snapshot, the earlier wins;
  404 = the model is gone. The replacement name comes from the deprecations page
  and is only text for the message.
- Anthropic (no key here): the "Model status" table of the deprecations page.
- Google: the tables of the Gemini deprecations page (HTML).
- OpenRouter: presence in its public model list; its ``expiration_date`` is one
  more date, never the only word (it is null for gpt-4o-mini, which OpenAI
  shuts down on 2027-01-14).

A page that loads but no longer has the table we read is ``source_broken`` —
"could not check" is reported, never read as "nothing announced".
"""

import asyncio
import datetime
import json
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx
import openai

from app.core.config import settings
from app.services.model_registry import ModelRef, parse_model_ref, spec_for

logger = logging.getLogger(__name__)

ANTHROPIC_DEPRECATIONS_URL = (
    "https://platform.claude.com/docs/en/about-claude/model-deprecations.md"
)
GOOGLE_DEPRECATIONS_URL = "https://ai.google.dev/gemini-api/docs/deprecations"
OPENAI_DEPRECATIONS_URL = "https://developers.openai.com/api/docs/deprecations.md"

# Per source: one slow page must not hold up the rest of the check.
SOURCE_TIMEOUT = 20.0

SOURCE_LABELS = {
    "openai": "OpenAI API",
    "anthropic": "страница сроков Anthropic",
    "google": "страница сроков Google",
    "openrouter": "список моделей OpenRouter",
}

# Worst first: a ref's state is the worst of what its sources say.
_SEVERITY = (
    "not_found",
    "past_date",
    "missing_from_list",
    "soon",
    "announced",
    "source_broken",
    "call_failed",
    "ok",
)


class SourceBroken(Exception):
    """The page loaded, but the table this module reads is not there."""


@dataclass(frozen=True)
class Signal:
    """One source's word on one model.

    kind: ``ok`` | ``deprecated`` (a date may follow) | ``gone`` |
    ``missing_from_list`` | ``call_failed`` | ``source_broken``.
    """

    kind: str
    source: str
    date: datetime.date | None = None
    replacement: str | None = None
    detail: str = ""


@dataclass
class Finding:
    """What the check concluded about one model ref."""

    state: str
    date: datetime.date | None = None
    replacement: str | None = None
    sources: list[str] = field(default_factory=list)
    detail: str = ""


# --- parsing --------------------------------------------------------------------


def _parse_date(text: str) -> datetime.date | None:
    """``"November 30, 2026"``, ``"Nov 30, 2026"`` or ``"2026-11-30"``; None for
    anything else ("Not sooner than …", "To be announced", "N/A")."""
    text = text.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """Every pipe table in ``text`` as (headers, rows). ``\\|`` inside a cell is
    an escaped pipe (OpenAI's "`gpt-4o-mini-tts` \\| `…`")."""
    tables, current = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            current = None
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip("|"))]
        if current is None:
            current = (cells, [])
            tables.append(current)
        elif all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        else:
            current[1].append(cells)
    return tables


def _find_table(tables, *needles: str):
    for headers, rows in tables:
        lowered = [h.lower() for h in headers]
        if all(any(n in h for h in lowered) for n in needles):
            return headers, rows
    return None


def _backticked(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def parse_anthropic(text: str) -> dict[str, Signal]:
    """{model id: Signal} from the Anthropic deprecations page (markdown)."""
    source = SOURCE_LABELS["anthropic"]
    tables = _markdown_tables(text)
    status = _find_table(tables, "api model name", "current state", "retirement")
    if status is None or not status[1]:
        raise SourceBroken("no 'Model status' table")
    headers = [h.lower() for h in status[0]]
    col_retire = next(i for i, h in enumerate(headers) if "retirement" in h)
    col_state = headers.index("current state")

    replacements = {}
    for headers_h, rows in tables:
        lowered = [h.lower() for h in headers_h]
        if "deprecated model" in lowered and "recommended replacement" in lowered:
            for row in rows:
                if len(row) != len(lowered):
                    continue
                old = _backticked(row[lowered.index("deprecated model")])
                new = _backticked(row[lowered.index("recommended replacement")])
                if old and new:
                    replacements[old[0]] = new[0]

    out = {}
    for row in status[1]:
        if len(row) <= max(col_retire, col_state):
            continue
        model, state = row[0].strip("` "), row[col_state].lower()
        date = _parse_date(row[col_retire])
        kind = {"retired": "gone", "deprecated": "deprecated"}.get(state, "ok")
        out[model] = Signal(
            kind,
            source,
            date=date if kind != "ok" else None,
            replacement=replacements.get(model),
        )
    return out


class _TableCollector(HTMLParser):
    """Collects every <table> as rows of cells; a cell is (text, [code texts])."""

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[tuple[str, list[str]]]]] = []
        self._row = self._cell = self._code = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
            self.tables[-1].append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._cell = ([], [])
        elif tag == "code" and self._cell is not None:
            self._code = []

    def handle_endtag(self, tag):
        if tag == "code" and self._code is not None:
            self._cell[1].append("".join(self._code).strip())
            self._code = None
        elif tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell[0]).split())
            self._row.append((text, self._cell[1]))
            self._cell = None
        elif tag == "tr":
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[0].append(data)
        if self._code is not None:
            self._code.append(data)


def parse_google(html: str) -> dict[str, Signal]:
    """{model id: Signal} from the Gemini deprecations page (HTML tables:
    Model | Release date | Shutdown date | Recommended replacement)."""
    source = SOURCE_LABELS["google"]
    collector = _TableCollector()
    collector.feed(html)
    out = {}
    for table in collector.tables:
        if not table:
            continue
        headers = [text.lower() for text, _ in table[0]]
        if "model" not in headers or "shutdown date" not in headers:
            continue
        col_date = headers.index("shutdown date")
        col_repl = next((i for i, h in enumerate(headers) if "replacement" in h), None)
        for row in table[1:]:
            if len(row) <= col_date:
                continue
            text, codes = row[0]
            model = codes[0] if codes else text
            date = _parse_date(row[col_date][0])
            replacement = None
            if col_repl is not None and col_repl < len(row):
                repl_codes = row[col_repl][1]
                replacement = repl_codes[0] if repl_codes else None
            out[model] = Signal(
                "deprecated" if date else "ok",
                source,
                date=date,
                replacement=replacement if date else None,
            )
    if not out:
        raise SourceBroken("no table with Model / Shutdown date")
    return out


def parse_openai_replacements(text: str) -> dict[str, str]:
    """{model id or snapshot: recommended replacement} from OpenAI's
    deprecations page. Text for the message only: an empty result costs nothing."""
    out = {}
    for headers, rows in _markdown_tables(text):
        lowered = [h.lower() for h in headers]
        if "shutdown date" not in lowered or "recommended replacement" not in lowered:
            continue
        col_repl = lowered.index("recommended replacement")
        for row in rows:
            if len(row) != len(lowered):
                continue
            new = _backticked(row[col_repl])
            for cell in row[:col_repl]:
                for old in _backticked(cell):
                    if new and old not in out:
                        out[old] = new[0]
    return out


def replacement_for(replacements: dict[str, str], *ids: str | None) -> str | None:
    """The replacement for a model id, its snapshot, or a dated snapshot of it
    (the OpenAI page names ``gpt-4o-mini-2024-07-18``, not ``gpt-4o-mini``)."""
    for mid in filter(None, ids):
        if mid in replacements:
            return replacements[mid]
        dated = re.compile(re.escape(mid) + r"-\d{4}-\d{2}-\d{2}")
        for key, value in replacements.items():
            if dated.fullmatch(key):
                return value
    return None


def parse_openrouter(text: str) -> dict[str, datetime.date | None]:
    """{model id: expiration date or None} from OpenRouter's /models JSON."""
    try:
        data = json.loads(text).get("data")
    except (ValueError, AttributeError):
        data = None
    if not isinstance(data, list) or not data:
        raise SourceBroken("no model list in the response")
    out = {}
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            out[item["id"]] = _parse_date(str(item.get("expiration_date") or ""))
    return out


def _shutdown_date(model) -> datetime.date | None:
    value = (getattr(model, "model_extra", None) or {}).get("shutdown_date")
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).date()
    return _parse_date(str(value)[:10])


# --- the check ------------------------------------------------------------------


def first_party_for(ref: ModelRef) -> tuple[str, str, str | None] | None:
    """(company, id at that company, snapshot) to ask about ``ref``, or None when
    only the route's own list can tell."""
    spec = spec_for(ref)
    if spec.first_party:
        company, model = spec.first_party
        return company, model, spec.snapshot
    if ref.route == "openai":
        return "openai", ref.model, None
    if ref.model.startswith("openai/"):
        return "openai", ref.model.split("/", 1)[1], None
    return None


def classify(signals: list[Signal], today: datetime.date, warn_days: int) -> Finding:
    """Fold the sources' words into one state (the worst one)."""
    finding = Finding("ok", sources=sorted({s.source for s in signals}))
    dates = [s.date for s in signals if s.kind in ("deprecated", "gone") and s.date]
    finding.date = min(dates) if dates else None
    finding.replacement = next((s.replacement for s in signals if s.replacement), None)

    candidates = []
    for s in signals:
        if s.kind == "gone":
            candidates.append("not_found")
        elif s.kind in ("missing_from_list", "call_failed", "source_broken"):
            candidates.append(s.kind)
        elif s.kind == "deprecated":
            candidates.append("announced")
    if finding.date is not None:
        days = (finding.date - today).days
        if days <= 0:
            candidates.append("past_date")
        elif days <= warn_days:
            candidates.append("soon")
    finding.state = min(candidates or ["ok"], key=_SEVERITY.index)
    finding.detail = "; ".join(s.detail for s in signals if s.detail and s.kind != "ok")
    return finding


async def _fetch(http: httpx.AsyncClient, url: str) -> str:
    response = await http.get(url, follow_redirects=True)
    response.raise_for_status()
    return response.text


async def _page(http, url: str, parse, company: str):
    """A parsed page, or a failure Signal that stands in for it."""
    source = SOURCE_LABELS[company]
    try:
        text = await asyncio.wait_for(_fetch(http, url), SOURCE_TIMEOUT)
    except Exception as exc:
        logger.warning("model watch: %s failed: %s", url, exc)
        return Signal("call_failed", source, detail=f"{source}: не загрузилась ({exc})")
    try:
        return parse(text)
    except SourceBroken as exc:
        logger.warning("model watch: %s changed shape: %s", url, exc)
        return Signal(
            "source_broken",
            source,
            detail=f"{source} изменилась, проверь вручную: {url}",
        )


async def _openai_signal(client, model: str, snapshot: str | None) -> Signal:
    source = SOURCE_LABELS["openai"]
    if client is None:
        return Signal("call_failed", source, detail="нет ключа OpenAI")
    dates = []
    for mid in filter(None, (model, snapshot)):
        try:
            found = await asyncio.wait_for(client.models.retrieve(mid), SOURCE_TIMEOUT)
        except openai.NotFoundError:
            if mid == model:
                return Signal("gone", source, detail=f"OpenAI: модели {mid} нет")
            # The alias moved on to a newer snapshot: that is not news.
            continue
        except Exception as exc:
            logger.warning("model watch: retrieve %s failed: %s", mid, exc)
            return Signal("call_failed", source, detail=f"OpenAI не ответил ({exc})")
        if (date := _shutdown_date(found)) is not None:
            dates.append(date)
    if dates:
        return Signal("deprecated", source, date=min(dates))
    return Signal("ok", source)


async def check_lifecycles(
    refs,
    *,
    openai_client,
    http: httpx.AsyncClient,
    today: datetime.date,
    warn_days: int,
) -> dict[str, Finding]:
    """{ref: Finding} for each model ref. Each source is fetched once per run;
    a failing source turns into ``call_failed`` / ``source_broken`` for the
    models that depend on it and never stops the rest."""
    parsed = {str(parse_model_ref(r)): parse_model_ref(r) for r in refs}
    parties = {ref: first_party_for(mref) for ref, mref in parsed.items()}
    companies = {p[0] for p in parties.values() if p}
    need_openrouter = any(m.route == "openrouter" for m in parsed.values())

    jobs = {}
    if "anthropic" in companies:
        jobs["anthropic"] = _page(
            http, ANTHROPIC_DEPRECATIONS_URL, parse_anthropic, "anthropic"
        )
    if "google" in companies:
        jobs["google"] = _page(http, GOOGLE_DEPRECATIONS_URL, parse_google, "google")
    if need_openrouter:
        jobs["openrouter"] = _page(
            http,
            f"{settings.OPENROUTER_BASE_URL.rstrip('/')}/models",
            parse_openrouter,
            "openrouter",
        )
    if "openai" in companies:
        jobs["openai_page"] = _page(
            http, OPENAI_DEPRECATIONS_URL, parse_openai_replacements, "openai"
        )
    openai_keys = sorted(
        {(p[1], p[2]) for p in parties.values() if p and p[0] == "openai"},
        key=lambda key: (key[0], key[1] or ""),
    )
    results = await asyncio.gather(
        *jobs.values(),
        *(_openai_signal(openai_client, m, s) for m, s in openai_keys),
    )
    pages = dict(zip(jobs, results[: len(jobs)]))
    openai_signals = dict(zip(openai_keys, results[len(jobs) :]))
    # The replacement name is decoration; a failed page just leaves it out.
    openai_repl = pages.get("openai_page")
    if not isinstance(openai_repl, dict):
        openai_repl = {}

    findings = {}
    for ref, mref in parsed.items():
        signals = []
        party = parties[ref]
        if party:
            company, model, snapshot = party
            if company == "openai":
                sig = openai_signals[(model, snapshot)]
                repl = replacement_for(openai_repl, model, snapshot)
                if repl and sig.kind in ("deprecated", "gone"):
                    sig = Signal(sig.kind, sig.source, sig.date, repl, sig.detail)
                signals.append(sig)
            else:
                page = pages[company]
                if isinstance(page, Signal):
                    signals.append(page)
                else:
                    signals.append(
                        page.get(model, Signal("ok", SOURCE_LABELS[company]))
                    )
        if mref.route == "openrouter":
            listing = pages["openrouter"]
            source = SOURCE_LABELS["openrouter"]
            if isinstance(listing, Signal):
                signals.append(listing)
            elif mref.model not in listing:
                signals.append(
                    Signal(
                        "missing_from_list",
                        source,
                        detail=f"OpenRouter больше не предлагает {mref.model}",
                    )
                )
            elif listing[mref.model] is not None:
                signals.append(Signal("deprecated", source, date=listing[mref.model]))
            else:
                signals.append(Signal("ok", source))
        findings[ref] = classify(signals, today, warn_days)
    return findings
