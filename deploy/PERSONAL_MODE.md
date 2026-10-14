# Permanent personal mode on Railway

Set `GOLDWAY_PERSONAL_ONLY=true` and `GOLDWAY_WORKER_ENABLED=true` only after the operator has authorized this scope. Use exactly one worker. Existing `GOLDWAY_CONFIG_JSON`, `TELEGRAM_BOT_TOKEN`, and the model credential remain private Railway variables.

The bootstrap derives a profile with only the existing `test_operator`. The complete model configuration and budget are preserved. Staff, group, Business, onboarding, daily summaries, and Sheets routes are disabled. Live sends target only the verified operator. Review mode stays enabled.

The first cutoff and operator identity are stored privately in `/data/personal_start.json`, mode 600, and reused on restart. A changed identity fails closed. The private worker uses `/data/personal.sqlite`; the general `/data/goldway.sqlite` is not created or migrated by this mode. Config is validated before replacement, under UID/GID 10001.

Intake accepts only fresh ordinary standalone private text from this operator after the cutoff, before event storage. Edits, forwards, attachments, Business/group events, other senders, and Reply messages are rejected. For this initial mode, send new standalone text instead of using Telegram Reply. No historical database/transcript import is performed.

The cutoff filters model/storage input. Telegram polling still acknowledges the shared bot queue, including rejected updates. This mode is not exactly-once delivery. Ambiguous sends remain for manual reconciliation, without blind retry.

Validation on 2026-10-14: combined main suite `python -m pytest -q` in the separate preparation directory using Python 3.12 main-venv: **297 passed in 2.03s**. Includes the prior 207 cases plus 74 personal intake and 16 Railway personal bootstrap cases; do not count those targeted runs again. No external API was used in these tests. Deployment, runtime intake, real generation, and Telegram delivery require separate observed evidence.
