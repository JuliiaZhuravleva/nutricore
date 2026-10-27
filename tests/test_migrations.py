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
# A migration's parents: one quoted id, None for the base, or — for a MERGE
# revision, which is alembic's own remedy for the multiple-heads condition these
# tests detect — a tuple of ids. Refusing the tuple would fail this whole file at
# exactly the moment someone applies the documented fix.
_DOWN = re.compile(
    r"^down_revision(?::\s*[^=]+)?\s*=\s*(\([^)]*\)|\[[^\]]*\]|[\"'][^\"']+[\"']|None)",
    re.M,
)
_QUOTED = re.compile(r"[\"']([^\"']+)[\"']")


def _parents(raw: str) -> tuple:
    """Every parent revision named by a down_revision assignment."""
    if raw == "None":
        return ()
    return tuple(_QUOTED.findall(raw))


def _chain(versions=None) -> dict:
    """{revision: (parents tuple, filename)} for every migration."""
    chain = {}
    for path in sorted((versions or _VERSIONS).glob("*.py")):
        source = path.read_text()
        rev = _REVISION.search(source)
        assert rev, f"{path.name}: no revision identifier"
        down = _DOWN.search(source)
        assert down, (
            f"{path.name}: no down_revision — expected a quoted id, a tuple of ids "
            "(merge revision), or None (base)"
        )
        chain[rev.group(1)] = (_parents(down.group(1)), path.name)
    return chain


def test_there_is_exactly_one_head():
    chain = _chain()
    parents = {p for parents, _ in chain.values() for p in parents}
    heads = sorted(rev for rev in chain if rev not in parents)
    assert len(heads) == 1, (
        "alembic has multiple heads — `alembic upgrade head` fails at deploy: "
        f"{[(h, chain[h][1]) for h in heads]}"
    )


def test_every_down_revision_exists():
    chain = _chain()
    for parents, filename in chain.values():
        for parent in parents:
            assert parent in chain, (
                f"{filename}: down_revision {parent!r} names a migration that does "
                "not exist — the chain is broken and alembic cannot walk it"
            )


def test_exactly_one_base_revision():
    chain = _chain()
    bases = [name for parents, name in chain.values() if not parents]
    assert len(bases) == 1, f"expected one base migration, found {bases}"


def test_a_merge_revision_parses_to_two_parents_and_one_head(tmp_path):
    """Known-positive control for the parser itself.

    `alembic merge heads` is the prescribed remedy when this file's head check
    fires. If the parser cannot read the merge revision it writes, every check
    here goes red precisely when the problem has just been fixed — so the shape
    is pinned with a synthetic chain rather than trusted.
    """
    (tmp_path / "a.py").write_text('revision = "a"\ndown_revision = None\n')
    (tmp_path / "b.py").write_text('revision = "b"\ndown_revision = "a"\n')
    (tmp_path / "c.py").write_text('revision = "c"\ndown_revision = "a"\n')
    (tmp_path / "m.py").write_text(
        'revision: str = "m"\n'
        'down_revision: Union[str, Sequence[str], None] = ("b", "c")\n'
    )

    chain = _chain(tmp_path)

    assert chain["m"][0] == ("b", "c"), "merge parents were not parsed"
    parents = {p for ps, _ in chain.values() for p in ps}
    heads = [rev for rev in chain if rev not in parents]
    assert heads == ["m"], f"a merged chain must have exactly one head, got {heads}"


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
