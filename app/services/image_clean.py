"""What goes to the model is the picture, not where it was taken.

A photo Telegram sends has no EXIF: Telegram re-encodes it. An image sent as a file comes
as it left the phone, with GPS in it. So the metadata goes before the bytes leave for the
model: pure byte-walking, no image library, the pixels are not decoded or touched. What
cannot be walked (an unknown format, a broken file) goes as it came: half a picture is
worse than none, and a broken file is one the model refuses anyway. GIF is not walked: a
GIF holds no EXIF, only a rare comment block.
"""

from __future__ import annotations

import struct
from typing import Optional

JPEG = b"\xff\xd8"
PNG = b"\x89PNG\r\n\x1a\n"
RIFF, WEBP = b"RIFF", b"WEBP"
GIF = (b"GIF87a", b"GIF89a")

# APP0 holds the format, APP2 the colour profile and APP14 the colour transform.
_JPEG_DROP = frozenset((0xE1, 0xED, 0xFE))  # APP1 (EXIF, XMP), APP13 (IPTC), comment
_JPEG_START_OF_SCAN = 0xDA
_JPEG_ALONE = frozenset((0x01, *range(0xD0, 0xD8)))  # markers with no length after them
_PNG_DROP = frozenset((b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"))
_WEBP_DROP = frozenset((b"EXIF", b"XMP "))
_VP8X_META_FLAGS = 0b00001100  # the "has EXIF" and "has XMP" bits


def _jpeg(data: bytes) -> Optional[bytes]:
    kept = [JPEG]
    at = len(JPEG)
    while True:
        if at + 2 > len(data) or data[at] != 0xFF:
            return None
        marker = data[at + 1]
        if marker == 0xFF:  # a marker may be padded with fill bytes
            kept.append(data[at : at + 1])
            at += 1
            continue
        if marker == _JPEG_START_OF_SCAN:
            kept.append(data[at:])
            return b"".join(kept)
        if marker in _JPEG_ALONE:
            kept.append(data[at : at + 2])
            at += 2
            continue
        if at + 4 > len(data):
            return None
        size = struct.unpack(">H", data[at + 2 : at + 4])[0]
        if size < 2 or at + 2 + size > len(data):
            return None
        if marker not in _JPEG_DROP:
            kept.append(data[at : at + 2 + size])
        at += 2 + size


def _png(data: bytes) -> Optional[bytes]:
    kept = [PNG]
    at = len(PNG)
    while at < len(data):
        if at + 12 > len(data):
            return None
        size = struct.unpack(">I", data[at : at + 4])[0]
        kind = data[at + 4 : at + 8]
        end = at + 12 + size
        if end > len(data):
            return None
        if kind not in _PNG_DROP:
            kept.append(data[at:end])
        at = end
    return b"".join(kept)


def _webp(data: bytes) -> Optional[bytes]:
    if len(data) < 12:
        return None
    kept = []
    at = 12
    while at < len(data):
        if at + 8 > len(data):
            return None
        kind = data[at : at + 4]
        size = struct.unpack("<I", data[at + 4 : at + 8])[0]
        end = at + 8 + size + (size % 2)
        if at + 8 + size > len(data):
            return None
        chunk = data[at : min(end, len(data))]
        if kind == b"VP8X" and size >= 1:
            flags = chunk[8] & ~_VP8X_META_FLAGS
            chunk = chunk[:8] + bytes((flags,)) + chunk[9:]
        if kind not in _WEBP_DROP:
            kept.append(chunk)
        at = end
    body = WEBP + b"".join(kept)
    return RIFF + struct.pack("<I", len(body)) + body


def clean(data: bytes) -> bytes:
    """The image without its metadata; what cannot be walked, as it came."""
    try:
        if data.startswith(JPEG):
            walked = _jpeg(data)
        elif data.startswith(PNG):
            walked = _png(data)
        elif data[:4] == RIFF and data[8:12] == WEBP:
            walked = _webp(data)
        else:
            walked = None
    except (struct.error, IndexError):
        walked = None
    return data if walked is None else walked


def mime_of(data: bytes) -> str:
    """By the bytes, not by the file's label. JPEG when they say nothing."""
    if data.startswith(PNG):
        return "image/png"
    if data[:4] == RIFF and data[8:12] == WEBP:
        return "image/webp"
    if data[:6] in GIF:
        return "image/gif"
    return "image/jpeg"
