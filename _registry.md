# Work Registry

Recent significant work. Check before starting new tasks.

## Recent

<!-- Format: - YYYY-MM-DD | topic | one-line summary -->
- 2026-07-06 | food-image + meal-logging robustness | `fix/td-004-food-image` (9 commits, **PUSHED**, not merged): fixed **TD-004** (food-image analysis — deprecated model, nested `image_url`, JSON parse, portion key) + deleted dead `openai.py`; hardened image path (base64 data URL to OpenAI, persist photo `file_id`); DRY parse/reply helpers; OpenAI SDK retries (`OPENAI_MAX_RETRIES`); atomic meal draft + reset on new-entry/reject (fixed a stale-`photos` carryover bug); new **`ai_call_logs`** debug table (model/schema/CRUD/migration `c2d3e4f5a6b7` + `app/services/ai_call_log_service.py`) with `LOG_LEVEL` config and a daily Celery-beat purge (`DEBUG_LOG_RETENTION_DAYS=60`); fixed pre-existing `grant_subscription` session leak + `crud_subscription` silent-commit. Reviewed via /review-deep (4 agents) + /review — clean. 76 tests green. New debt: TD-006 (Low). PR not opened.
- 2026-07-06 | review-note fixes | `feat/unfreeze` (+2 commits, on top of the earlier merge to main): fixed TD-002 (bot token leaked into logs — clamped httpx/httpcore to WARNING in `create_bot_application`) and TD-003 (narrowed `ALLOWED_HOSTS` default `['*']`→loopback); re-ran /review-security + /review-deep (clean; the `docker-compose.prod.yml` "deletion" was a diff artifact — file lives on main, merge preserves it). Logged new debt from first live mini run: TD-004 (Critical — food-image analysis broken, deprecated `gpt-4-vision-preview` hardcoded) + TD-005 (High — self-heal on model_not_found). 45 tests green. NOT pushed.
- 2026-07-02 | bot unfreeze | `feat/unfreeze` (9 commits): committed the `/consult` relay to the my-health hub; fixed schema/test drift (Meal photos/ai_analysis + migration, User.settings, pytz→datetime.UTC); added access-control modes (open/whitelist/closed, default whitelist) with a silent global gate; secured the REST API (`X-API-Token`, fail-closed) + Telegram webhook secret; extracted `app/services/access_control.py`; staged roadmap in `docs/stages/`. Reviewed via /review, /review-deep ×2, /review-security — all findings fixed. Not pushed/merged.

## Archive

Entries older than 3 months moved here.
