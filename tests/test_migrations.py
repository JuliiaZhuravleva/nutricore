"""Structural checks on the alembic revision chain.

The deploy runs ``alembic upgrade head`` before the app starts
(``claw-deploy release`` → migrate-before-start, docs/RELEASE.md). A second head
makes that command fail at release time, on the mini, after the merge to main —
the most expensive place to find it. These checks are pure file parsing: no DB,
no alembic import, no network.

What they do NOT cover: whether a migration actually applies. SQLite-backed tests
build their schema from the models via ``Base.metadata.create_all``, so the
migration files themselves are never executed here. That remains the openclaw-side
migration gate's job.
"""

from __future__ import annotations

import pathlib
import re

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"

_REVISION = re.compile(r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)[\"']", re.M)
_DOWN = re.compile(
    r"^down_revision(?::\s*[^=]+)?\s*=\s*(?:[\"']([^\"']+)[\"']|None)", re.M
)


def _chain() -> dict:
    """{revision: (down_revision or None, filename)} for every migration."""
    chain = {}
    for path in sorted(_VERSIONS.glob("*.py")):
        source = path.read_text()
        rev = _REVISION.search(source)
        assert rev, f"{path.name}: no revision identifier"
        down = _DOWN.search(source)
        assert down, f"{path.name}: no down_revision (use None for the base)"
        chain[rev.group(1)] = (down.group(1), path.name)
    return chain


def test_there_is_exactly_one_head():
    chain = _chain()
    parents = {down for down, _ in chain.values()}
    heads = sorted(rev for rev in chain if rev not in parents)
    assert len(heads) == 1, (
        "alembic has multiple heads — `alembic upgrade head` fails at deploy: "
        f"{[(h, chain[h][1]) for h in heads]}"
    )


def test_every_down_revision_exists():
    chain = _chain()
    for rev, (down, filename) in chain.items():
        if down is None:
            continue
        assert down in chain, (
            f"{filename}: down_revision {down!r} names a migration that does not "
            "exist — the chain is broken and alembic cannot walk it"
        )


def test_exactly_one_base_revision():
    chain = _chain()
    bases = [name for _, (down, name) in chain.items() if down is None]
    assert len(bases) == 1, f"expected one base migration, found {bases}"


def test_revision_ids_are_unique():
    """A copy-pasted revision id silently shadows another migration."""
    ids = []
    for path in sorted(_VERSIONS.glob("*.py")):
        found = _REVISION.search(path.read_text())
        if found:
            ids.append((found.group(1), path.name))
    seen = {}
    for rev, name in ids:
        assert rev not in seen, f"duplicate revision {rev!r}: {seen[rev]} and {name}"
        seen[rev] = name
