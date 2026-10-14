# Local publication validation — 2026-10-14

Source: GOLDWAY_Telegram_group_pilot_2026-10-14.zip (new group pilot archive).
Python 3.12.14 on macOS arm64. Separate environments and processes:
- Main: `python -m pytest -q` — 98 passed.
- reference_pilot: `python -m pytest -q -o addopts=''` — 69 passed.
- `pip check` passed in both environments.
- Main wheel built with `--no-deps --no-build-isolation`.
- README synthetic CLI ingest repeated twice: 1 proposed task, 1 done event,
  1 pending outbox message. No external API calls or message delivery.

Packaging fixes:
- Removed Dockerfile COPY wheelhouse and local find-links; pinned runtime/build
  requirements are installed from the package index during Railway image build.
- Added ignore rules for private runtime data and build/download artifacts.
- Explicitly retained public examples/config.json, which was accidentally ignored.

Docker CLI/daemon is unavailable on this Mac. Docker image build and Linux runtime
have NOT been validated locally. Previous cloud image validation in VALIDATION.md
is historical evidence for the previous Dockerfile, not this edited image.
No Railway service was started; no live Telegram messages were sent; no working
SQLite database or Google Sheet was changed. Demo SQLite is outside the repository.
Secret-pattern scan found no key/token matches in the publication source.
Only empty env templates and synthetic examples are included.

Railway setup still required (main service only):
- Secrets TELEGRAM_BOT_TOKEN and MODEL_API_KEY for the configured provider.
- Private /data/config.json based on examples/telegram-only.json: verified roles,
  Telegram IDs, allowed groups/recipients, group destinations and meeting_recipient.
- Keep review_mode=true, live_send=false, recipients_verified=false and
  sheets_write=false until the controlled integration checks are approved.
- Persistent volume /data, readable config and writable database for UID 10001;
  Railway-mounted volume permissions must be checked explicitly.
- Start override: goldway --config /data/config.json --db /data/goldway.sqlite run
- Exactly one background worker replica; reference_pilot is reference code,
  not a second process. No simultaneous poller/webhook for the same bot.
- Build from repository root Dockerfile. No public domain / HTTP healthcheck needed.
- Backups stored separately from the persistent volume; restore tested separately.

Archive SHA-256: 92374561a4323a2dba017e116dbf89f12f643893ce242900f08b03cd43e8b571

## Railway preparation follow-up

Added goldway.railway bootstrap and four behavior checks. Updated main suite:
102 passed on macOS Python 3.12. Reference code unchanged (69 previously passed).
Wheel packaging passed again. Private configuration validated offline and kept
outside the repository. GOLDWAY_WORKER_ENABLED defaults false; disabled worker
performs no polling/model calls/database writes and exits normally on SIGTERM.
Tests verify atomic private config writing (mode 600), invalid-config rollback,
preservation of existing SQLite and sanitized failure without private config.
Root-owned mounted-volume recovery/UID drop and Docker image/runtime still require
Linux/Railway verification. See deploy/RAILWAY.md for the updated start command.

## Combined corrections

207 combined main tests passed; 7 fake-process supervisor checks passed separately.
Reply receipt and direct conversation reply_text corrections were merged with
controlled_intake validation. Six exact operational-snippet checks passed locally.
Docker copies operations for the finite authorized test supervisor. All prior
server state stays disabled until an explicitly authorized isolated test.
