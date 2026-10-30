"""The picture renderer (stage 5, slice D): turns jobs into PNGs with chrome-headless-shell. Standard library only.

Contract with the bot, through a directory both containers mount (``RENDER_DIR``):
- the bot writes ``in/<sha256>.json`` atomically (a temporary name, then a rename): the ``window.REPORT`` object for
  ``templates/card.html``;
- the renderer writes ``out/<sha256>.png`` (atomically, PNG signature checked) or ``out/<sha256>.err`` (one line of
  text), then removes the job. The newest job is drawn first, with a fresh look at ``in/`` after every card: the card
  asked for last is the one the user looks at;
- ``heartbeat`` holds the time of the last loop, so the bot can tell a live renderer from a dead one;
- ``engine`` holds the version of the templates and of this file, written at start: the bot puts it into every job,
  so changed templates never reuse a card drawn by the old ones.

One Chrome is kept warm between jobs and driven over its debugging pipe: per job it opens a tab, reads the page state
(ready flag, height, the template's own error), waits for the pictures to decode and takes the screenshot. It is
started with the sidecar, right after the first heartbeat, and closes after ``WARM_IDLE`` without a job. Any failure
of the warm browser closes it and the job is drawn the one-shot way: Chrome runs per job and exits, twice per job (once
to read the page, once for the screenshot). ``RENDER_WARM=0`` turns the warm browser off.
``RENDER_KEEP_DAYS`` (default 2) is how long ``out/`` keeps its files: a drawn day names what was eaten, so it does not
stay long. The data never reaches a network: the container has none, and Chrome also maps every host name to nothing.

``python watcher.py serve`` runs the loop; ``python watcher.py health`` exits 1 when the heartbeat is stale;
``python watcher.py once JOB.json OUT.png`` renders one job (previews and the samples).
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import html
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"
CHROME = os.environ.get("CHROME", "/opt/chrome-headless-shell-linux-arm64/chrome-headless-shell")
RENDER_DIR = Path(os.environ.get("RENDER_DIR", "/render"))
WIDTH = 540
MIN_HEIGHT, MAX_HEIGHT = 120, 1080
CHROME_TIMEOUT = 20.0  # seconds per Chrome run; the whole process group is killed after it
WARM = os.environ.get("RENDER_WARM", "1") != "0"
WARM_IDLE = 120.0  # seconds without a job before the warm browser is closed
WARM_TIMEOUT = 8.0  # seconds for one job in the warm browser; then it is closed and the job drawn the one-shot way
WARM_START_TIMEOUT = CHROME_TIMEOUT  # seconds for the browser started with the sidecar, when no card waits on it yet
WARM_STRIKES = 3  # failures in a row that turn the warm browser off until a restart
POLL = 0.05
KEEP_IN = 3600  # a job nobody could render in an hour is dropped
HEARTBEAT_MAX_AGE = 30  # seconds; an older heartbeat means the loop is stuck or gone
PNG_SIG = b"\x89PNG\r\n\x1a\n"
NAME = re.compile(r"[0-9a-f]{64}")
MARKS = ("<!--BASE-->", "<!--REPORT-->")


class RenderError(Exception):
    pass


def engine_version() -> str:
    """The first 16 hex digits of a sha256 over this file and every template file, by path and content."""
    digest = hashlib.sha256(Path(__file__).read_bytes())
    for path in sorted(p for p in TEMPLATES.rglob("*") if p.is_file()):
        digest.update(b"\0" + path.relative_to(TEMPLATES).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()[:16]


def report_json(report: object) -> str:
    """JSON safe inside a <script>: no '<', '>' or '&' (so no '</script>'), no U+2028/U+2029."""
    text = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    for a, b in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        text = text.replace(a, b)
    return text


def build_page(report: dict[str, object]) -> str:
    page = (TEMPLATES / "card.html").read_text(encoding="utf-8")
    for mark in MARKS:
        if page.count(mark) != 1:
            raise RenderError(f"card.html has {page.count(mark)} {mark} marks, needs one")
    parts = {
        "<!--BASE-->": f'<base href="{TEMPLATES.parent.as_uri()}/">',
        "<!--REPORT-->": f"<script>window.REPORT = {report_json(report)};</script>",
    }
    for mark, value in parts.items():
        page = page.replace(mark, value, 1)
    return page


def base_args(height: int, profile: Path) -> list[str]:
    return [
        CHROME,
        "--no-sandbox",  # the container drops every capability; Chrome's own sandbox needs some of them
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-extensions",
        "--host-resolver-rules=MAP * ~NOTFOUND",
        f"--user-data-dir={profile}",
        "--force-device-scale-factor=2",
        f"--window-size={WIDTH},{height}",
    ]


def chrome_args(page: Path, png: Path | None, height: int, profile: Path) -> list[str]:
    return [
        *base_args(height, profile),
        "--virtual-time-budget=5000",
        "--dump-dom" if png is None else f"--screenshot={png}",
        page.as_uri(),
    ]


def run_chrome(args: list[str]) -> tuple[int, str, str]:
    """Run Chrome in its own process group and kill the whole group on a timeout (its helpers would outlive it)."""
    proc = subprocess.Popen(  # noqa: S603 - the pinned Chrome binary, with arguments built here
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )
    try:
        out, err = proc.communicate(timeout=CHROME_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise RenderError(f"Chrome took over {CHROME_TIMEOUT:.0f} s") from None
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def page_state(dom: str) -> tuple[bool, int | None, str | None]:
    """(ready, height, the template's error text) from the dumped DOM. Inline style and script text is cut first:
    their comments may mention <body> or the error div."""
    dom = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", "", dom, flags=re.S | re.I)
    body = re.search(r"<body\b[^>]*>", dom)
    attrs = body.group(0) if body else ""
    height = re.search(r'\bdata-height="(\d+)"', attrs)
    error = re.search(r'<div class="error">(.*?)</div>', dom, re.S)
    return (
        'data-ready="1"' in attrs,
        int(height.group(1)) if height else None,
        html.unescape(error.group(1)).strip() if error else None,
    )


class BrowserError(Exception):
    """The warm browser failed: closed, slow, or an answer that makes no sense. Never the template's own error."""


class BrowserRefused(BrowserError):
    """The browser answered a call with an error, e.g. a page still loading has no context to evaluate in yet."""


# Read in the page: the same three facts page_state reads from a dumped DOM.
STATE_JS = """(() => {
  const b = document.body, e = document.querySelector("div.error");
  return [!!b && b.dataset.ready === "1", b && /^\\d+$/.test(b.dataset.height || "") ? +b.dataset.height : null,
          e ? e.textContent.trim() : null];
})()"""
# Every picture decoded, then two frames, so the screenshot never catches an image half drawn.
SETTLE_JS = """Promise.all(Array.from(document.images, (i) => i.decode().catch(() => null)))
  .then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))"""


# Chrome reads the pipe on fds 3 and 4 only. This small Python, started in Chrome's place, moves the two ends there and
# becomes Chrome. A shell cannot: dash, Debian's /bin/sh, reads one digit only in a redirection.
PIPE_EXEC = (
    "import os, sys; r, w = int(sys.argv[1]), int(sys.argv[2]); os.dup2(r, 3); os.dup2(w, 4); os.close(r);"
    " os.close(w); os.execv(sys.argv[3], sys.argv[3:])"
)


def _above_stdio(fd: int) -> int:
    """The same pipe end numbered 5 or above, clear of the child's stdio and of 3 and 4: an end that happened to be 0
    (a parent run with stdin closed) would be overwritten by the child's stdin before Chrome read it."""
    moved = fcntl.fcntl(fd, fcntl.F_DUPFD, 5)
    os.close(fd)
    return moved


class Browser:
    """One Chrome driven over --remote-debugging-pipe: commands go in on its fd 3 and answers come out on its fd 4,
    each a JSON object ended by a NUL byte. Events come on the same pipe and are skipped."""

    def __init__(self) -> None:
        self._profile = Path(tempfile.mkdtemp(prefix="chrome-"))
        cmd_r, self._cmd = os.pipe()
        self._out, out_w = os.pipe()
        try:
            cmd_r, out_w = _above_stdio(cmd_r), _above_stdio(out_w)
            self._proc = subprocess.Popen(  # noqa: S603 - this Python, then the pinned Chrome
                [
                    sys.executable,
                    "-c",
                    PIPE_EXEC,
                    str(cmd_r),
                    str(out_w),
                    *base_args(MAX_HEIGHT, self._profile),
                    "--remote-debugging-pipe",
                    "about:blank",
                ],
                pass_fds=(cmd_r, out_w),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            for fd in (self._cmd, self._out):
                os.close(fd)
            shutil.rmtree(self._profile, ignore_errors=True)
            raise
        finally:
            os.close(cmd_r)
            os.close(out_w)
        self._buf = b""
        self._next = 0

    def call(
        self, method: str, params: dict[str, object] | None = None, *, session: str | None = None, deadline: float
    ) -> dict[str, object]:
        self._next += 1
        msg: dict[str, object] = {"id": self._next, "method": method, "params": params or {}}
        if session is not None:
            msg["sessionId"] = session
        data = json.dumps(msg).encode() + b"\0"
        try:
            while data:
                data = data[os.write(self._cmd, data) :]
        except OSError as exc:
            raise BrowserError(f"{method}: cannot write: {exc}") from None
        while True:
            answer = self._read(deadline, method)
            if answer.get("id") != self._next:
                continue  # an event, or a late answer to a call given up on
            if "error" in answer:
                raise BrowserRefused(f"{method}: {answer['error']}")
            result = answer.get("result")
            if not isinstance(result, dict):
                raise BrowserError(f"{method}: no result")
            return result

    def _read(self, deadline: float, method: str) -> dict[str, object]:
        while b"\0" not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self._out], [], [], left)[0]:
                raise BrowserError(f"{method}: no answer in time")
            chunk = os.read(self._out, 65536)
            if not chunk:
                raise BrowserError(f"{method}: the browser closed its pipe")
            self._buf += chunk
        raw, self._buf = self._buf.split(b"\0", 1)
        try:
            answer = json.loads(raw)
        except ValueError:
            raise BrowserError(f"{method}: an answer that is not JSON") from None
        if not isinstance(answer, dict):
            raise BrowserError(f"{method}: an answer that is not an object")
        return answer

    def evaluate(self, expression: str, session: str, deadline: float, *, wait: bool = False) -> object:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": wait},
            session=session,
            deadline=deadline,
        )
        if "exceptionDetails" in result:
            raise BrowserRefused(f"Runtime.evaluate: {result['exceptionDetails']}")
        value = result.get("result")
        return value.get("value") if isinstance(value, dict) else None

    def draw(self, page: Path) -> bytes:
        """One card in a fresh tab. The template's own error is a RenderError; anything else is a BrowserError."""
        deadline = time.monotonic() + WARM_TIMEOUT
        target = self.call("Target.createTarget", {"url": "about:blank"}, deadline=deadline).get("targetId")
        if not isinstance(target, str):
            raise BrowserError("Target.createTarget: no target id")
        try:
            session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True}, deadline=deadline)
            sid = session.get("sessionId")
            if not isinstance(sid, str):
                raise BrowserError("Target.attachToTarget: no session id")
            return self._draw(page, sid, deadline)
        finally:
            self.call("Target.closeTarget", {"targetId": target}, deadline=max(deadline, time.monotonic() + 1.0))

    def _draw(self, page: Path, sid: str, deadline: float) -> bytes:
        metrics = {"width": WIDTH, "height": MAX_HEIGHT, "deviceScaleFactor": 2, "mobile": False}
        self.call("Emulation.setDeviceMetricsOverride", metrics, session=sid, deadline=deadline)
        self.call("Page.navigate", {"url": page.as_uri()}, session=sid, deadline=deadline)
        while True:
            try:
                state = self.evaluate(STATE_JS, sid, deadline)
            except BrowserRefused:
                state = [False, None, None]  # the page is still loading: no context to read yet
            if not (isinstance(state, list) and len(state) == 3):
                raise BrowserError(f"page state: {state!r}")
            ready, height, error = state
            if error:
                raise RenderError(f"template: {str(error)[:200]}")
            if ready:
                break
            if time.monotonic() >= deadline:
                raise BrowserError("page not ready in time")
            time.sleep(0.01)
        if not isinstance(height, int) or not height:
            raise RenderError("page ready without a height")
        height = max(MIN_HEIGHT, min(height, MAX_HEIGHT))
        if height != MAX_HEIGHT:
            metrics["height"] = height
            self.call("Emulation.setDeviceMetricsOverride", metrics, session=sid, deadline=deadline)
        self.evaluate(SETTLE_JS, sid, deadline, wait=True)
        shot = self.call("Page.captureScreenshot", {"format": "png"}, session=sid, deadline=deadline).get("data")
        try:
            data = base64.b64decode(shot if isinstance(shot, str) else "", validate=True)
        except ValueError:
            raise BrowserError("Page.captureScreenshot: data that is not base64") from None
        if not data.startswith(PNG_SIG):
            raise BrowserError("Page.captureScreenshot: no PNG")
        return data

    def close(self) -> None:
        try:
            self.call("Browser.close", deadline=time.monotonic() + 1.0)
        except BrowserError:
            pass
        try:
            self._proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self._proc.pid, signal.SIGKILL)  # its helpers too, whatever Chrome did
        except OSError:
            pass
        self._proc.wait()
        for fd in (self._cmd, self._out):
            try:
                os.close(fd)
            except OSError:
                pass
        shutil.rmtree(self._profile, ignore_errors=True)


class Warm:
    """The warm browser's life: started by the first job, reused by the next, closed after ``WARM_IDLE`` without one.
    A failure closes it and ``draw`` answers None, so the job falls back to the one-shot way; ``WARM_STRIKES`` in a
    row turn it off until a restart, so a browser that cannot work costs a few slow cards, not every card."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._browser: Browser | None = None
        self._used = 0.0
        self._strikes = 0
        self.on = True

    def start(self) -> None:
        """Start the browser before any job, with the longer ``WARM_START_TIMEOUT``: nobody waits on it yet. A failure
        counts like a job's, and the first job then tries again the usual way."""
        if not self.on or self._browser is not None:
            return
        try:
            self._launch(WARM_START_TIMEOUT, why="start")
        except (BrowserError, OSError) as exc:
            self._failed(exc)
        finally:
            self._used = self._clock()

    def draw(self, page: Path) -> bytes | None:
        if not self.on:
            return None
        try:
            browser = self._browser or self._launch(WARM_TIMEOUT, why="job")
            data = browser.draw(page)
        except (BrowserError, OSError) as exc:
            self._failed(exc)
            return None
        finally:
            self._used = self._clock()
        self._strikes = 0
        return data

    def _launch(self, timeout: float, *, why: str) -> Browser:
        started = time.monotonic()
        self._browser = browser = Browser()
        browser.call("Browser.getVersion", deadline=started + timeout)  # up and answering
        log("browser_started", ms=round((time.monotonic() - started) * 1000), why=why)
        return browser

    def _failed(self, exc: Exception) -> None:
        self._strikes += 1
        log("browser_failed", error=str(exc)[:200], strikes=self._strikes)
        self.close("failed")
        if self._strikes >= WARM_STRIKES:
            self.on = False
            log("browser_off", reason=f"{WARM_STRIKES} failures in a row")

    def idle(self) -> None:
        if self._browser is not None and self._clock() - self._used >= WARM_IDLE:
            self.close("idle")

    def close(self, reason: str) -> None:
        if self._browser is not None:
            browser, self._browser = self._browser, None
            browser.close()
            log("browser_closed", reason=reason)


def render(report: dict[str, object], warm: Warm | None = None) -> bytes:
    with tempfile.TemporaryDirectory(prefix="job-") as tmp:
        page, png, profile = Path(tmp, "page.html"), Path(tmp, "out.png"), Path(tmp, "profile")
        page.write_text(build_page(report), encoding="utf-8")
        if warm is not None:
            drawn = warm.draw(page)
            if drawn is not None:
                return drawn
        rc, dom, log = run_chrome(chrome_args(page, None, MAX_HEIGHT, profile))
        ready, height, error = page_state(dom)
        if error:
            raise RenderError(f"template: {error[:200]}")
        if not ready or not height:
            raise RenderError(f"page not ready (rc={rc}): {log.strip()[-200:]}")
        height = max(MIN_HEIGHT, min(height, MAX_HEIGHT))
        rc, _, log = run_chrome(chrome_args(page, png, height, profile))
        data = png.read_bytes() if png.exists() else b""
    if not data.startswith(PNG_SIG):
        raise RenderError(f"no PNG (rc={rc}): {log.strip()[-200:]}")
    return data


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def keep_days(value: str | None) -> float:
    """``RENDER_KEEP_DAYS`` in seconds: a positive number of days, else the default (logged)."""
    try:
        days = float(value) if value is not None else 2.0
    except ValueError:
        days = 0.0
    if not 0 < days <= 365:
        log("keep_days_ignored", value=value, used=2)
        days = 2.0
    return days * 86400


def process(job: Path, out_dir: Path, warm: Warm | None = None) -> None:
    name = job.stem
    try:
        if NAME.fullmatch(name) is None:
            return
        if (out_dir / f"{name}.png").exists():
            return
        try:
            text = job.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        try:
            report = json.loads(text)
            if not isinstance(report, dict):
                raise RenderError("the job is not an object")
            started = time.monotonic()
            png = render(report, warm)
        except (RenderError, OSError, ValueError) as exc:
            _write_atomic(out_dir / f"{name}.err", f"{type(exc).__name__}: {exc}".encode()[:1000])
            log("render_failed", job=name[:12], error=str(exc)[:200])
            return
        _write_atomic(out_dir / f"{name}.png", png)
        ms = round((time.monotonic() - started) * 1000)
        log("rendered", job=name[:12], view=report.get("view"), ms=ms)
    finally:
        job.unlink(missing_ok=True)


def newest_job(in_dir: Path, taken: set[Path]) -> Path | None:
    """The card asked for last, of those not taken in this round: the newest request is the one the user looks at,
    and an older card nobody withdrew is drawn after it. ``taken`` keeps a job that could not be removed from
    coming back in the same round."""
    newest: tuple[float, Path] | None = None
    for path in in_dir.glob("*.json"):
        if path in taken:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:  # withdrawn between the listing and the look
            continue
        if newest is None or mtime > newest[0]:
            newest = (mtime, path)
    return newest[1] if newest is not None else None


def prune(directory: Path, older_than: float, now: float) -> None:
    for path in directory.iterdir():
        try:
            if now - path.stat().st_mtime > older_than:
                path.unlink()
        except OSError:
            continue


def log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def serve(root: Path) -> None:
    in_dir, out_dir = root / "in", root / "out"
    for directory in (in_dir, out_dir):
        directory.mkdir(parents=True, exist_ok=True)
    engine = engine_version()
    _write_atomic(root / "engine", engine.encode())
    keep_out = keep_days(os.environ.get("RENDER_KEEP_DAYS"))
    log("renderer_started", chrome=CHROME, engine=engine, warm=WARM, keep_days=keep_out / 86400)
    warm = Warm() if WARM else None
    if warm is not None:
        _write_atomic(root / "heartbeat", str(int(time.time())).encode())  # alive first: a card asked for waits
        warm.start()
    last_prune = 0.0
    while True:
        now = time.time()
        _write_atomic(root / "heartbeat", str(int(now)).encode())
        taken: set[Path] = set()
        while (job := newest_job(in_dir, taken)) is not None:  # a fresh look at in/ after every card
            taken.add(job)
            process(job, out_dir, warm)
        if warm is not None:
            warm.idle()
        if now - last_prune > 600:
            prune(out_dir, keep_out, now)
            prune(in_dir, KEEP_IN, now)
            last_prune = now
        time.sleep(POLL)


def healthy(root: Path, now: float) -> bool:
    try:
        return now - int((root / "heartbeat").read_text().strip()) <= HEARTBEAT_MAX_AGE
    except (OSError, ValueError):
        return False


def main(argv: list[str]) -> int:
    if argv[1:2] == ["serve"]:
        serve(RENDER_DIR)
        return 0
    if argv[1:2] == ["health"]:
        return 0 if healthy(RENDER_DIR, time.time()) else 1
    if len(argv) == 4 and argv[1] == "once":
        report = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        started = time.monotonic()
        warm = Warm() if WARM else None
        try:
            Path(argv[3]).write_bytes(render(report, warm))
        except RenderError as exc:
            print(f"render failed: {exc}", file=sys.stderr)
            return 1
        finally:
            if warm is not None:
                warm.close("done")
        print(f"rendered in {time.monotonic() - started:.2f} s")
        return 0
    print("usage: watcher.py serve | health | once JOB.json OUT.png", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
