"""🍲 ``dish_tidy_batch``: what ✅ and ↩️ do to the dishes, called directly with a mask
and answered in plain data (the message screen and the coming widget share it).

On SQLite with a fake bot, the tidy-up's own fixtures: Sunday 15 November 2026 in
Tbilisi. Nothing here reaches a model or the network.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest

from app.crud.crud_dish_tidy import crud_dish_tidy
from app.models import PersonalFood
from app.services import dish_tidy, dish_tidy_batch

# The tidy-up's own fixtures and helpers (a fixture imported is a fixture).
from tests.test_dish_tidy import (  # noqa: F401
    AT_12,
    _live,
    _sent,
    _starred,
    _user,
    factory,
    no_model,
)


@pytest.fixture
def db(request):
    with request.getfixturevalue("factory")() as session:
        yield session


LATER = AT_12 + timedelta(minutes=5)
ROOT = Path(__file__).resolve().parents[1]


def _idx(db, batch) -> dict:
    """kind → row idx of the batch (the kitchen makes one row of each kind)."""
    return {i.kind: i.idx for i in crud_dish_tidy.items(db, batch.id)}


def _mask(*idxs) -> int:
    return sum(1 << i for i in idxs)


def test_apply_with_the_default_ticks_does_what_was_ticked(db):
    k, _markup, batch = _sent(db)
    idx = _idx(db, batch)
    ticked = [
        i.idx for i in crud_dish_tidy.items(db, batch.id) if i.ticked
    ]  # as the screen's default mask
    result = dish_tidy_batch.apply(db, _user(db), batch, _mask(*ticked), LATER)
    assert result.already is False
    assert result.kept and result.changed
    assert idx[dish_tidy.MERGE] in result.applied
    assert result.failed == []
    assert not _live(db, k.boiled) or not _live(db, k.buckwheat)  # the pair merged
    assert batch.state == "applied"


def test_an_unticked_row_is_declined_and_an_unticked_same_pair_comes_apart(db):
    k, _markup, batch = _sent(db)
    idx = _idx(db, batch)
    assert not _live(db, k.sauce) or not _live(db, k.sauce_too)  # merged by itself
    result = dish_tidy_batch.apply(db, _user(db), batch, 0, LATER)
    assert sorted(result.declined) == sorted(idx.values())
    assert result.applied == [] and result.failed == []
    assert result.changed  # the near-identical pair came apart
    assert result.kept is False
    assert _live(db, k.sauce) and _live(db, k.sauce_too)
    assert _live(db, k.buckwheat) and _live(db, k.boiled)
    assert not _starred(db, k.oats)
    states = {i.kind: i.state for i in crud_dish_tidy.items(db, batch.id)}
    assert set(states.values()) == {"declined"}


def test_a_second_apply_finds_the_batch_closed_and_does_nothing(db):
    _k, _markup, batch = _sent(db)
    first = dish_tidy_batch.apply(db, _user(db), batch, 0, LATER)
    assert first.already is False
    again = dish_tidy_batch.apply(db, _user(db), batch, 0xFF, LATER)
    assert again.already is True
    assert (again.applied, again.declined, again.failed) == ([], [], [])
    assert again.changed is False


def test_a_row_whose_dishes_moved_on_is_failed_not_applied(db):
    k, _markup, batch = _sent(db)
    idx = _idx(db, batch)
    star = db.get(PersonalFood, k.oats.id)
    star.canonical_name = "овсянка на воде"  # renamed after the batch was made
    db.commit()
    result = dish_tidy_batch.apply(
        db, _user(db), batch, _mask(idx[dish_tidy.STAR]), LATER
    )
    assert idx[dish_tidy.STAR] in result.failed
    assert idx[dish_tidy.STAR] not in result.applied
    assert not _starred(db, k.oats)


def test_undo_takes_back_what_the_batch_did_and_only_once(db):
    k, _markup, batch = _sent(db)
    idx = _idx(db, batch)
    everything = _mask(*idx.values())
    done = dish_tidy_batch.apply(db, _user(db), batch, everything, LATER)
    assert set(done.applied) == {idx[dish_tidy.MERGE], idx[dish_tidy.STAR]}
    assert _starred(db, k.oats)
    back = dish_tidy_batch.undo(db, _user(db), batch, LATER)
    assert back.already is False
    assert back.undone >= 3  # the near-identical pair, the merge and the ⭐
    assert back.not_back == []
    assert batch.state == "undone"
    assert not _starred(db, k.oats)
    assert _live(db, k.boiled) and _live(db, k.buckwheat)
    assert _live(db, k.sauce) and _live(db, k.sauce_too)
    again = dish_tidy_batch.undo(db, _user(db), batch, LATER)
    assert again.already is True and again.undone == 0


def test_undo_names_what_could_not_be_taken_back(db):
    k, _markup, batch = _sent(db)
    idx = _idx(db, batch)
    dish_tidy_batch.apply(db, _user(db), batch, _mask(*idx.values()), LATER)
    source = db.get(PersonalFood, k.boiled.id)
    if source.merged_into_id is None:  # the pair merged the other way round
        source = db.get(PersonalFood, k.buckwheat.id)
    source.merged_into_id = k.oats.id  # moved on since: the merge no longer fits
    db.commit()
    back = dish_tidy_batch.undo(db, _user(db), batch, LATER)
    assert back.already is False
    assert len(back.not_back) == 1
    assert back.undone >= 1  # the rest still came back
    assert batch.state == "undone"


def test_the_batch_module_has_no_telegram_in_it():
    tree = ast.parse((ROOT / "app/services/dish_tidy_batch.py").read_text())
    names = [
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    ]
    assert names and not [n for n in names if n and n.split(".")[0] == "telegram"]
    assert not [n for n in names if n and "dish_tidy_screen" in n]
