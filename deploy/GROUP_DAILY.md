# One authorized group, daily on the existing worker

This is a separate opt-in discussion review, not an employee/admin role grant.
It does not enable generic group routes, change the closed TEAM roster, or start
another Telegram poller. It uses the existing model and shared usage budget.

Provision `/data/group_daily.json` privately, mode 600, owner UID 10001, before
restarting/deploying the single worker. Fields: enabled/verified (booleans true),
chat_id (confirmed negative integer), title, timezone (IANA), hour/minute,
started_at (UTC Unix timestamp), seed_context (at most 4000 characters of
owner-approved, dated, group-only facts; never private chats or credentials).
The file and group identifiers must not be committed to source control.
Absent/disabled/invalid configuration leaves the private worker running.
Changes or removal revoke an active group instance until a controlled restart.

The bot must belong to the specific group, be allowed to send, and have access to
ordinary messages: administrator in this group OR privacy mode already disabled.
Prefer the narrow group grant rather than changing privacy for all groups.
Fresh rights are checked before review and again immediately before send.
Until then the health state is blocked_group_read_permission and no daily post
is generated. Permission restoration is checked again within five minutes.

Only newly delivered text/captions in the configured group after started_at are
stored (last 200 messages, at most 3000 characters per message). Media is not
fetched/transcribed. Edits replace their corresponding saved text. Old history
is not obtained through Bot API. Seed is explicitly historical and new replies
can supersede it. Arbitrary messages never enter employee task/admin commands.

A review runs once per local date after hour:minute; downtime doesn't replay a
queue of missed days. At most one message is attempted each day. Empty or exact
repeated output remains quiet. The model has no tools, receives only this group,
and returns a message with source IDs. Staff links and purchase-selection
questions are filtered before send. A recorded message ID proves API delivery,
not that a human read it or completed the work.

Tables group_daily_messages and group_daily_runs are in the existing persistent
SQLite database. Runtime key GOLDWAY:group_daily_health holds only state/time.
Review errors are stored without provider payloads. Interrupted sends become
unknown and suspend later group sends until a human reconciles delivery. Never
clear unknown to retry without proof. Existing private workflows remain active.

Before enabling, pause any local sender for the same daily group task. Validate
one actual incoming group update and one bot delivery when permissions exist;
synthetic tests and successful deployment alone do not establish live reception.
