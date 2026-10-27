"""The weekly model check (ADR-0004): which models the bot runs on, what their
companies say about them, and one message to the admins when that changed.

Run by Celery beat (``celery_app.tasks.model_watch.check_models``) and on
demand from the admin's ``/models``. What was already told lives in
``model_watch_state``; a state is saved only after the message went out, so an
undelivered one is sent again next run.
"""

import datetime
import logging
from dataclasses import dataclass, field

import httpx

from app.core.config import settings
from app.services.admin_notify import notify_admins
from app.services.model_registry import TASKS
from app.services.model_watch_sources import SOURCE_TIMEOUT, Finding, check_lifecycles
from app.services.model_watch_state import (
    TASK_NAMES_RU,
    clear_state,
    load_states,
    normalize_ref,
    save_state,
)

logger = logging.getLogger(__name__)

_ICONS = {
    "announced": "📅",
    "soon": "⚠️",
    "past_date": "⛔",
    "not_found": "⛔",
    "missing_from_list": "⛔",
    "source_broken": "❓",
    "call_failed": "❓",
}


@dataclass
class WatchReport:
    uses: dict[str, list[str]]
    findings: dict[str, Finding]
    # Refs whose news went into this run's message.
    news: list[str] = field(default_factory=list)
    delivered: bool = False
    message: str | None = None


def models_in_use(svc) -> dict[str, list[str]]:
    """{model ref: [tasks]} for everything the bot calls, embeddings included."""
    uses: dict[str, list[str]] = {}
    for task in TASKS:
        uses.setdefault(normalize_ref(svc.model_for(task)), []).append(task)
    uses.setdefault(normalize_ref(svc.web_search_model), []).append("web_search")
    uses.setdefault(normalize_ref(settings.OPENAI_EMBEDDING_MODEL), []).append(
        "embeddings"
    )
    return uses


def _stored_date(record: dict) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(record.get("date") or "")
    except ValueError:
        return None


def is_news(old: dict | None, finding: Finding) -> bool:
    """Tell the admins when the state changed, or the date moved EARLIER.
    ``ok`` is never news; a later date is not news either."""
    if finding.state == "ok":
        return False
    if not old or old.get("state") != finding.state:
        return True
    old_date = _stored_date(old)
    return finding.date is not None and (old_date is None or finding.date < old_date)


def _fmt(date: datetime.date) -> str:
    return date.strftime("%d.%m.%Y")


def _headline(finding: Finding, today: datetime.date) -> str:
    date = finding.date
    days = (date - today).days if date else None
    if finding.state == "announced":
        if date is None:
            return (
                "Компания объявила модель устаревшей; дату отключения пока не назвала"
            )
        return f"Объявлена дата отключения: {_fmt(date)} (через {days} дн.)"
    if finding.state == "soon":
        return f"Отключат {_fmt(date)} — осталось {days} дн."
    if finding.state == "past_date":
        return (
            f"Дата отключения прошла ({_fmt(date)}) — модель может перестать "
            "отвечать в любой момент"
        )
    if finding.state == "not_found":
        return "Модели больше нет: компания её отключила или переименовала"
    if finding.state == "missing_from_list":
        return "OpenRouter больше не предлагает эту модель"
    if finding.state == "source_broken":
        return "Не смог проверить: страница сроков изменилась"
    return "Не смог проверить: источник не ответил"


def render_message(
    news: list[str],
    findings: dict[str, Finding],
    uses: dict[str, list[str]],
    today: datetime.date,
) -> str:
    blocks = ["🗓 Проверка моделей: есть новости"]
    for ref in news:
        finding = findings[ref]
        lines = [
            f"{_ICONS.get(finding.state, '❓')} {ref}",
            _headline(finding, today),
            "Где используется: "
            + ", ".join(TASK_NAMES_RU.get(t, t) for t in uses.get(ref, [])),
        ]
        if finding.replacement:
            lines.append(f"Компания советует замену: {finding.replacement}")
        if finding.sources:
            lines.append("Источник: " + ", ".join(finding.sources))
        if finding.detail:
            lines.append(finding.detail)
        blocks.append("\n".join(lines))
    blocks.append("Подробности и проверка заново — /models.")
    return "\n\n".join(blocks)


def render_status(report: WatchReport, today: datetime.date) -> str:
    """Every model in use with this run's state — the admin's /models answer."""
    blocks = ["🧠 Модели бота"]
    for ref, tasks in report.uses.items():
        finding = report.findings.get(ref)
        if finding is None or finding.state == "ok":
            icon, headline = "✅", "Дата отключения не объявлена"
        else:
            icon, headline = _ICONS.get(finding.state, "❓"), _headline(finding, today)
        lines = [
            f"{icon} {ref}",
            headline,
            "Задачи: " + ", ".join(TASK_NAMES_RU.get(t, t) for t in tasks),
        ]
        if finding and finding.state != "ok" and finding.replacement:
            lines.append(f"Компания советует замену: {finding.replacement}")
        blocks.append("\n".join(lines))
    if report.news:
        blocks.append(
            "Новости отправлены отдельным сообщением."
            if report.delivered
            else "⚠️ Сообщение с новостями не ушло — повторю в следующий раз."
        )
    return "\n\n".join(blocks)


async def run_model_watch(
    svc,
    *,
    today: datetime.date | None = None,
    http: httpx.AsyncClient | None = None,
) -> WatchReport:
    """Check every model in use; message the admins about what is new."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    uses = models_in_use(svc)
    openai_client = svc.client if settings.OPENAI_API_KEY else None

    async def _check(client: httpx.AsyncClient):
        return await check_lifecycles(
            uses,
            openai_client=openai_client,
            http=client,
            today=today,
            warn_days=settings.MODEL_WATCH_WARN_DAYS,
        )

    if http is None:
        async with httpx.AsyncClient(timeout=SOURCE_TIMEOUT) as client:
            findings = await _check(client)
    else:
        findings = await _check(http)

    stored = load_states()
    report = WatchReport(uses=uses, findings=findings)
    for ref, record in stored.items():
        # Back to ok, or no longer used: forget it, so a new problem alerts again.
        if ref not in findings or findings[ref].state == "ok":
            clear_state(ref)
    report.news = [ref for ref, f in findings.items() if is_news(stored.get(ref), f)]
    logger.info(
        "model watch: %s",
        ", ".join(f"{ref}={f.state}" for ref, f in findings.items()),
    )
    if not report.news:
        return report

    report.message = render_message(report.news, findings, uses, today)
    report.delivered = await notify_admins(report.message)
    if report.delivered:
        for ref in report.news:
            finding = findings[ref]
            save_state(
                ref,
                {
                    "state": finding.state,
                    "date": finding.date.isoformat() if finding.date else None,
                    "detail": finding.detail[:300],
                    "replacement": finding.replacement,
                },
            )
    else:
        logger.warning("model watch: alert not delivered; will retry next run")
    return report
