# Contributing

nutricore is a personal Telegram food-tracking bot — the thin **spoke** of a hub-and-spoke health
ecosystem (the hub is a separate project, `my-health`). Contributions are welcome; please open an
issue to discuss non-trivial changes before sending a PR.

## Ground rules (non-negotiable)

- **No secrets in git.** The Telegram bot token, OpenAI key, and any export/consult tokens are
  supplied at runtime via environment variables / 1Password — never hardcoded or put in `.env`. A
  gitleaks pre-commit hook and CI enforce this.
- **Keep the spoke thin.** The bot does **no medical reasoning and stores no medical data of
  record** — it captures meals and relays chat text to the my-health hub. Do not add medical logic
  or medical storage here, and do not route mental-health data through the bot's own OpenAI. See
  `docs/consult-relay.md`.

## Dev setup

```bash
poetry install                # Python venv + dependencies
cp .env.example .env          # fill in your own tokens (never commit .env)
pre-commit install            # enable the gitleaks hook
```

Requires Python 3.12 and [Poetry](https://python-poetry.org/). The bot runs via `bot.py` (polling)
or `app/main.py` (webhook).

## Verify before opening a PR

```bash
poetry run pytest
poetry run black --check . && poetry run isort --check-only . && poetry run flake8
```

- Tests run on **SQLite in-memory** (see `tests/conftest.py`), with FastAPI `TestClient` +
  dependency overrides for API tests (see `tests/test_export_meals.py`).
- Run `pre-commit run --all-files` to match the secret-scan CI locally.

## Commit style

Conventional-commit prefixes (`feat`, `fix`, `docs`, `chore`, …) with a short, specific summary.
Keep PRs focused; include tests for behavioral changes.
