# Nutricore docs

Nutricore is a **personal** Telegram nutrition bot (public repo, single-user tool). This
folder is the map — start with the row that matches what you want.

## Understanding the project
- [../README.md](../README.md) — what it is + quickstart
- [../ROADMAP.md](../ROADMAP.md) — direction, current status, and what's deliberately *not* built
- [product-philosophy.md](product-philosophy.md) — the "why", and how product decisions get made

## The build plan (source of truth for order + status)
- [stages/README.md](stages/README.md) — stage index + live status table
- [stages/00-user-scenarios.md](stages/00-user-scenarios.md) … `01`–`04`, `90`, [access-control.md](stages/access-control.md)

## Features & design records
- [photo-product-lookup.md](photo-product-lookup.md) — packaged-food КБЖУ lookup (round-1 shipped, round-2 active)
- [decisions/ADR-0001-pluggable-nutrition-resolution-pipeline.md](decisions/ADR-0001-pluggable-nutrition-resolution-pipeline.md) — the resolution pipeline
- [consult-relay.md](consult-relay.md) — `/consult` → my-health hub (shipped)

## Operating the project
- [RELEASE.md](RELEASE.md) — how a change ships (nutricore ⇄ openclaw-setup seam)
- [../CONTRIBUTING.md](../CONTRIBUTING.md) · [../SECURITY.md](../SECURITY.md)

## Assistant / coordination files (AI-facing, not product docs)
- [../CLAUDE.md](../CLAUDE.md) — assistant guide & conventions
- [../HANDOFF.md](../HANDOFF.md) — latest session state
- [../_registry.md](../_registry.md) — work log · [../_tech-debt.md](../_tech-debt.md) — debt registry

## History
- [archive/origin-brief.md](archive/origin-brief.md) — the original 2024 concept brief (Russian; superseded)
