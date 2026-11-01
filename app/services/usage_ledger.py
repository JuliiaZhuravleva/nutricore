"""The lasting record of what the models cost, per person per day.

Every paid call reaches ``charge`` through ``usage_meter.record``, whether or not anyone
has a meter open to show a price on screen. A write that fails loses that one call's
record, says so in the log, and leaves the person's flow alone.
"""

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.crud.crud_usage_ledger import crud_usage_ledger
from app.db.session import SessionLocal
from app.services.attribution import current_person

logger = logging.getLogger(__name__)


def today():
    return datetime.now(ZoneInfo(settings.DEFAULT_TIMEZONE)).date()


def charge(usd: Optional[float]) -> None:
    """One model call for the current person. None: no known price, counted and not
    guessed. Never raises."""
    try:
        with SessionLocal() as db:
            crud_usage_ledger.add(
                db, telegram_id=current_person(), day=today(), cost_usd=usd
            )
    except Exception as e:
        logger.warning("A model call not in the ledger: %s", type(e).__name__)
