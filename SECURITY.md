# Security Policy

nutricore is a personal Telegram food-tracking bot and the **thin spoke** of a hub-and-spoke
health ecosystem (the hub is a separate project, `my-health`). This repository holds **code only** —
no secrets, no user data. Secrets (the Telegram bot token, the OpenAI key, export/consult tokens) are
supplied at runtime via environment variables / 1Password and are never committed.

## Reporting a vulnerability

Please report security issues **privately**:

- Open a [GitHub Security Advisory](../../security/advisories/new) (preferred), or
- Email the maintainer via the address on their GitHub profile.

Do **not** open a public issue for a vulnerability until it has been addressed. Include steps to
reproduce and the impact you observed.

## Scope & design notes

- **Secrets** never live in the repo, `.env`, or config — only in env/1Password at runtime.
  `.env`/keys/certs are gitignored, and commits are scanned by gitleaks (local pre-commit hook + CI,
  see `.github/workflows/secrets-scan.yml`).
- **Spoke role (an invariant):** the bot performs **no medical reasoning and stores no medical data
  of record**. It captures meals and relays chat text to the my-health hub; all medical logic and
  the mental-health guardrails live in the hub, behind one trust boundary.
- **Mental-health data never flows through this bot's own AI.** The bot's OpenAI usage is for food
  parsing only. The consult path is a pure relay to the hub; the hub's deterministic crisis handling
  (item 9) is authoritative and the bot only displays it.

Because this is a single-maintainer personal project, there are no formal SLAs — reports are handled
on a best-effort basis.
