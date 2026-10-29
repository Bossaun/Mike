# Scoped group work and manager receipts

The existing worker may load private `/data/group_work.json` beside the existing
`group_daily.json`. This replaces that group's generic daily summary with a
source-bound task registry and addressed replies. No second poller or roster
expansion. Model and shared daily budget are unchanged.

Group config: enabled/verified true, chat_id equal the separately verified daily
group, started_at integer Unix timestamp, participants mapping local key to
name/user_id, tasks, kickoff boolean. Tasks carry id (C plus two digits), owner,
title, ask, criterion, reviewer, source, active/daily booleans. Maximum 30 tasks.
Store mode600 uid10001. Never commit configs, private IDs, transcripts or SQLite.

Owner-authorized registry is reconciled on restart. Assignment/acceptance changes
reset progress; removed definitions never dispatch. The bot cannot invent new
assignments, alter recipients, financial data, prices or sheet cells.

New mentions of the exact bot username, initial 'Валли', and replies to bot
messages are prioritized. Ordinary fresh messages can update existing tasks from
literal evidence. Private capture runs after existing TEAM authentication and
requires a task ID or a reply to a confirmed work message. Private reports are
excluded from group model context. Dates come from explicit literal calendar/time
or today/tomorrow expressions, Moscow timezone. Ambiguous dates need clarification.

Work claim -> in_progress. Owner completion -> awaiting_acceptance. Only the
configured reviewer or Mike can explicitly accept. Inspection, promises and
negative acceptance are not completion. No external artifact verification is
claimed. Images/voice without text are not interpreted by this text-only path.

One reminder per owner/bucket per day at the existing schedule; daily touch has
its own reply anchor. Agreed future due times suppress ordinary reminders until
due. A verified Business route is preferred; otherwise the authorized group is
used. Never change route after an attempt. Staff private replies are Business
only. Work can continue in the group when the private Business window is absent.

Private `/data/delivery_notices.json`: enabled true, manager existing admin key,
started_at positive timestamp, transport business or business_or_bot. Only Mike
has the separately owner-approved bot DM fallback. Private-only transport never
falls back into a group. Receipts require durable Telegram send acknowledgment;
no notice recursion or historical replay. Daily task digest uses observed state,
not invented completion. These queued messages carry configuration seals checked
again by Dispatcher before route and before send. Config removal/change cancels
stale queued items. Unknown group sends require reconciliation; they never retry.

Acceptance: focused unit/integration tests plus exact deployed SHA, fresh runtime,
visible outbound receipt, and a separate real employee reply/result. Passing tests
or deployment is not evidence of employee completion. Existing local group timer
must remain paused while this server workflow is active.
