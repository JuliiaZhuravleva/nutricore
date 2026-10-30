"""The bot's side of the picture renderer (``render/watcher.py``): a job directory.

The bot writes ``in/<sha256>.json`` and watches for ``out/<sha256>.png`` or
``.err``. The name is the sha256 of the job, the sidecar's template version
inside it, so one card is drawn once and a changed template draws anew.

The sidecar is used only while its heartbeat is fresh: a stopped renderer costs
no waiting and the bot answers in text. A job that failed is not tried again for
``ERR_HOLD``: a template error would only come back.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# The sidecar beats between cards; its slowest one takes 48 s (render/watcher.py).
# Older than this means stuck or gone.
HEARTBEAT_MAX_AGE = 60.0
ERR_HOLD = 600.0
POLL = 0.1
PNG_SIG = b"\x89PNG\r\n\x1a\n"
_ENGINE = re.compile(r"[0-9a-f]{16}")


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Ticket:
    """One card asked for: wait for it as long as the caller can afford."""

    def __init__(self, renderer: "PictureRenderer", name: str):
        self._renderer = renderer
        self.name = name
        self.failed = False

    async def wait(
        self, seconds: float, superseded: Optional[Callable[[], bool]] = None
    ) -> Optional[bytes]:
        """The PNG, or None: not drawn in time, ``failed``, or ``superseded``."""
        out = self._renderer.root / "out"
        png, err = out / f"{self.name}.png", out / f"{self.name}.err"
        limit = time.monotonic() + seconds
        while True:
            drawn = _read_png(png)
            if drawn is not None:
                return drawn
            if err.exists():
                logger.warning("render: %s failed: %s", self.name[:12], _tail(err))
                self.failed = True
                return None
            if not self._renderer.alive():
                logger.warning("render: the sidecar stopped during %s", self.name[:12])
                self.failed = True
                return None
            if superseded is not None and superseded():
                return None
            if time.monotonic() >= limit:
                return None
            await asyncio.sleep(self._renderer.poll)


class PictureRenderer:
    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], float] = time.time,
        poll: float = POLL,
    ):
        self.root = root
        self.poll = poll
        self._clock = clock
        self._problem: Optional[str] = None

    def alive(self) -> bool:
        problem = self._find_problem()
        if problem != self._problem:
            # Once per change: this is asked on every tap and every poll.
            if problem:
                logger.warning("render: pictures are off, text only: %s", problem)
            else:
                logger.info("render: pictures are on again")
            self._problem = problem
        return problem is None

    def _find_problem(self) -> Optional[str]:
        try:
            beat = int((self.root / "heartbeat").read_text(encoding="ascii").strip())
        except (OSError, ValueError) as e:
            return f"no readable heartbeat ({type(e).__name__})"
        if self._clock() - beat > HEARTBEAT_MAX_AGE:
            return f"heartbeat older than {HEARTBEAT_MAX_AGE:.0f} s"
        return None

    def request(self, report: dict) -> Optional[Ticket]:
        """Queue the card unless it is drawn; None when no picture can come."""
        if not self.alive():
            return None
        data = self._job(report)
        name = hashlib.sha256(data).hexdigest()
        out = self.root / "out"
        png, err = out / f"{name}.png", out / f"{name}.err"
        if png.is_file():
            return Ticket(self, name)
        try:
            if self._clock() - err.stat().st_mtime < ERR_HOLD:
                return None
        except OSError:
            pass  # no earlier failure
        try:
            err.unlink(missing_ok=True)  # it may have been passing: try again
            _write_atomic(self.root / "in" / f"{name}.json", data)
        except OSError as e:
            logger.warning("render: cannot queue %s: %s", name[:12], e)
            return None
        return Ticket(self, name)

    def _job(self, report: dict) -> bytes:
        job = dict(report)
        engine = self._engine()
        if engine:
            job["engine"] = engine
        return json.dumps(
            job, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()

    def _engine(self) -> Optional[str]:
        try:
            text = (self.root / "engine").read_text(encoding="ascii").strip()
        except (OSError, ValueError):
            return None
        return text if _ENGINE.fullmatch(text) else None


def _read_png(path: Path) -> Optional[bytes]:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if data.startswith(PNG_SIG) else None


def _tail(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()[:200]
    except OSError as e:
        return str(e)
