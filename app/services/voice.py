"""A voice note: its bytes kept, and heard (M4, section 3.2).

The shared local service first, the paid door when it is not there, cannot or runs out
of time. Silence is an answer, and costs nothing more. No transcript and no message body
in a log, and no Telegram file path: that URL carries the bot token.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

LOCAL = "local"
PAID = "openai"
LOCAL_DOOR = "/v1/audio/transcriptions"
LOCAL_TIMEOUT_S = 120.0
LOCAL_NO_SPEECH = 422

# For both services: the words of the meal, heard as food.
FOOD_PROMPT = "Дневник питания: блюда, продукты, порции и граммы."
# For a message to the owner: the words about the bot, not about a meal.
GENERAL_PROMPT = "Отзыв о боте дневника питания: что было непонятно и чего не хватает."

LAST_CORRECTION_RULE = (
    "This entry is a voice note, spoken as it came. The last correction in a sentence "
    "wins: from «один… нет, два банана» record two bananas, and leave out what was "
    "taken back."
)


class NoSpeech(Exception):
    """Silence or noise: nothing to read, and nobody is asked again."""


class NotHeard(Exception):
    """A read nobody asked for, and the local service did not answer. It passes."""


@dataclass(frozen=True)
class Heard:
    text: str
    backend: str
    model: Optional[str] = None


def extension(audio: bytes) -> str:
    """Of the bytes, not of a name: both services read the upload by its extension.
    What Telegram calls a voice is OGG, so that is what the unknown is taken for."""
    if audio[4:8] == b"ftyp":
        return "mp4"
    if audio[:4] == b"RIFF":
        return "wav"
    if audio[:4] == b"fLaC":
        return "flac"
    if audio[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if audio[:3] == b"ID3" or audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    return "ogg"


def _write(name: str, audio: bytes) -> None:
    folder = Path(settings.AUDIO_DIR)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(dir=folder, prefix=".voice-")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(audio)
        os.replace(temporary, folder / name)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


async def keep(source_id: int, audio: bytes) -> Optional[str]:
    """``meal_sources.audio_path``: the name inside ``AUDIO_DIR``. None if the disk
    refused: the read goes on, and Telegram still has the file."""
    name = f"{source_id}.{extension(audio)}"
    try:
        await asyncio.to_thread(_write, name, audio)
    except OSError as e:
        logger.warning("Voice not saved: %s", type(e).__name__)
        return None
    return name


async def kept(name: Optional[str]) -> Optional[bytes]:
    if not name:
        return None
    try:
        return await asyncio.to_thread((Path(settings.AUDIO_DIR) / name).read_bytes)
    except OSError as e:
        logger.warning("Voice not found: %s", type(e).__name__)
        return None


async def _local(audio: bytes, filename: str, prompt: str) -> Optional[str]:
    """The service's words; None if it was no answer. Silence raises."""
    url = settings.WHISPER_URL.rstrip("/") + LOCAL_DOOR
    try:
        # Its own address on the host: never through a proxy from the environment.
        async with httpx.AsyncClient(
            timeout=LOCAL_TIMEOUT_S, trust_env=False
        ) as client:
            response = await client.post(
                url,
                files={"file": (filename, audio)},
                data={
                    "language": "ru",
                    "prompt": prompt,
                    "response_format": "json",
                },
            )
    except httpx.HTTPError as e:
        logger.warning("Local whisper not reached: %s", type(e).__name__)
        return None
    if response.status_code == LOCAL_NO_SPEECH:
        raise NoSpeech()
    if response.status_code != 200:
        logger.warning("Local whisper refused: %s", response.status_code)
        return None
    try:
        text = response.json().get("text")
    except (ValueError, AttributeError) as e:
        logger.warning("Local whisper unreadable: %s", type(e).__name__)
        return None
    if not isinstance(text, str) or not text.strip():
        # The service says silence by a 422, so this is not that one, and may be heard.
        logger.warning("Local whisper answered nothing")
        return None
    return text.strip()


async def hear(
    audio: bytes,
    *,
    paid: Optional[Callable[[bytes, str], Awaitable[Heard]]] = None,
    local_only: bool = False,
    prompt: str = FOOD_PROMPT,
) -> Heard:
    """``local_only``: nobody asked for this read, so nothing is paid for it (5.2). With
    no ``paid`` there is no door to pay at, whatever else is asked."""
    filename = f"voice.{extension(audio)}"
    if settings.WHISPER_URL:
        text = await _local(audio, filename, prompt)
        if text is not None:
            return Heard(text, LOCAL)
    if local_only or paid is None:
        raise NotHeard()
    return await paid(audio, filename)
