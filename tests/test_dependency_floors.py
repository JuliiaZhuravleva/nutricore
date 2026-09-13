"""Dependency floors that runtime code silently depends on.

These are not style checks. Each floor exists because a source file calls an API
that does not exist below it, while every seam that would surface the mismatch is
mocked elsewhere in the suite.

The incident: ``OpenAIService.web_search_nutrition`` was written against the
Responses API (ADR-0002) and shipped while ``poetry.lock`` pinned openai 1.61.1,
which has no ``client.responses`` at all. Every single call raised
``AttributeError``; ``NameWebSearchStrategy`` swallowed it to a WARNING, so the
strategy was 100% dead in production for weeks and the suite stayed green — the
pipeline tests patch ``web_search_nutrition`` wholesale, so its body never ran.

Two independent floors are asserted, because they can disagree:
  * what is INSTALLED — scripts/test.sh runs a pre-built Poetry cache venv;
  * what the LOCK ships — the Docker image installs strictly from poetry.lock.
An upgraded dev venv alone must not be able to turn this file green.

Floors verified by inspecting the published wheels (2026-09-13):
  openai 1.65.5  — no openai/resources/responses/  → no client.responses
  openai 1.66.0  — responses present; web_search tool typed "web_search_preview"
  openai 1.108.0 — still "web_search_preview" only
  openai 1.109.1 — GA literal Required[Literal["web_search", ...]]
"""

from __future__ import annotations

import pathlib
import tomllib
from importlib.metadata import PackageNotFoundError, version

import pytest

# client.responses exists from here (used by web_search_nutrition, ADR-0002).
OPENAI_RESPONSES_FLOOR = (1, 66, 0)
# The GA tool literal the call actually sends: tools=[{"type": "web_search"}].
# Below this the SDK's own type for that tool is "web_search_preview".
OPENAI_WEB_SEARCH_FLOOR = (1, 109, 1)

_REPO = pathlib.Path(__file__).resolve().parents[1]

# Packages the runtime imports that the pre-built gate venv has silently lacked
# before: pgvector is a main dependency, but it was missing from the cache venv,
# so every test run exercised the SQLite fallback column type instead of the real
# Vector(1536) the deployed image uses.
_RUNTIME_PACKAGES = ("openai", "pgvector", "sqlalchemy", "psycopg2-binary")


def _v(raw: str) -> tuple:
    return tuple(int(part) for part in raw.split(".")[:3] if part.isdigit())


def _locked_version(name: str) -> str:
    data = tomllib.loads((_REPO / "poetry.lock").read_text())
    for package in data["package"]:
        if package["name"] == name:
            return package["version"]
    raise AssertionError(f"{name} is not in poetry.lock at all")


def test_installed_openai_has_the_responses_api():
    installed = version("openai")
    assert _v(installed) >= OPENAI_RESPONSES_FLOOR, (
        f"openai {installed} has no client.responses — web_search_nutrition "
        "raises AttributeError on every call. Bump pyproject.toml AND run "
        "`poetry update openai`, then re-run the suite."
    )


def test_installed_openai_types_the_ga_web_search_tool():
    installed = version("openai")
    assert _v(installed) >= OPENAI_WEB_SEARCH_FLOOR, (
        f"openai {installed} predates the GA web_search tool; the call sends "
        'tools=[{"type": "web_search"}], which this SDK generation typed as '
        '"web_search_preview".'
    )


def test_the_installed_client_really_exposes_responses_create():
    """The floor above is a number; this is the artefact it stands for."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key="test-key-not-used")
    assert hasattr(client, "responses"), (
        "installed openai SDK has no .responses namespace — the version floor "
        "passed but the surface is missing; the floor constant is wrong"
    )
    assert callable(client.responses.create)


def test_poetry_lock_pins_openai_above_both_floors():
    """The gate venv is not what ships — Docker installs from poetry.lock."""
    locked = _locked_version("openai")
    assert _v(locked) >= OPENAI_WEB_SEARCH_FLOOR, (
        f"poetry.lock pins openai {locked}: the deployed image would lack the "
        "Responses API even though this dev venv has it."
    )


def test_locked_pyproject_constraint_cannot_resolve_below_the_floor():
    """A lock can be regenerated; the constraint is what guards the next resolve."""
    data = tomllib.loads((_REPO / "pyproject.toml").read_text())
    constraint = data["tool"]["poetry"]["dependencies"]["openai"]
    # Poetry caret: "^2.54.0" == >=2.54.0,<3.0.0. Any constraint whose floor is
    # below OPENAI_WEB_SEARCH_FLOOR could resolve to a Responses-less release.
    assert constraint.startswith("^"), (
        f"unexpected openai constraint {constraint!r} — this control only "
        "understands caret constraints; update it deliberately"
    )
    assert _v(constraint.lstrip("^")) >= OPENAI_WEB_SEARCH_FLOOR, (
        f"pyproject allows openai {constraint}, which can resolve below the "
        "Responses API floor"
    )


@pytest.mark.parametrize("package", _RUNTIME_PACKAGES)
def test_locked_runtime_packages_are_installed_in_the_gate_venv(package):
    """Known-positive for a stale gate venv.

    pgvector sat in poetry.lock's main group while being absent from the cache
    venv, so `Vector(1536)` silently degraded to the SQLite fallback type in
    every test run — the suite was testing a column type production never uses.
    """
    try:
        version(package)
    except PackageNotFoundError:  # pragma: no cover - only on a stale venv
        pytest.fail(
            f"{package} is in poetry.lock but not installed in the test venv — "
            "run `poetry install`; the suite is exercising different code than "
            "the deployed image."
        )
