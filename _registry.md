# Work Registry

Recent significant work. Check before starting new tasks.

## Recent

<!-- Format: - YYYY-MM-DD | topic | one-line summary -->
- 2026-07-06 | review-note fixes | `feat/unfreeze` (+2 commits, on top of the earlier merge to main): fixed TD-002 (bot token leaked into logs — clamped httpx/httpcore to WARNING in `create_bot_application`) and TD-003 (narrowed `ALLOWED_HOSTS` default `['*']`→loopback); re-ran /review-security + /review-deep (clean; the `docker-compose.prod.yml` "deletion" was a diff artifact — file lives on main, merge preserves it). Logged new debt from first live mini run: TD-004 (Critical — food-image analysis broken, deprecated `gpt-4-vision-preview` hardcoded) + TD-005 (High — self-heal on model_not_found). 45 tests green. NOT pushed.
- 2026-07-02 | bot unfreeze | `feat/unfreeze` (9 commits): committed the `/consult` relay to the my-health hub; fixed schema/test drift (Meal photos/ai_analysis + migration, User.settings, pytz→datetime.UTC); added access-control modes (open/whitelist/closed, default whitelist) with a silent global gate; secured the REST API (`X-API-Token`, fail-closed) + Telegram webhook secret; extracted `app/services/access_control.py`; staged roadmap in `docs/stages/`. Reviewed via /review, /review-deep ×2, /review-security — all findings fixed. Not pushed/merged.

## Archive

Entries older than 3 months moved here.
