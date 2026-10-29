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
reset progress; removed definitions never dispatch. Literal direct Mike instructions
can create durable tasks for existing configured employees. Questions, quotes,
third-party narratives and examples cannot create assignments. New tasks are deduped
and private-origin tasks never enter group context. No new recipients, financial
operations, price/stock changes or sheet mutations.

New mentions of the exact bot username, initial 'Валли', and replies to bot
messages are prioritized. Ordinary fresh messages can update existing tasks from
literal evidence. Private capture runs after existing TEAM authentication and
accepts ordinary language; IDs are optional. Confirmed sent messages provide context
and focus for short replies such as 'yes'; queued messages do not. Private reports
and deadline text are excluded from group model context. Natural dates use the
incoming timestamp and Moscow timezone; uncertain dates need one short clarification.

Work claim -> in_progress. Owner completion -> awaiting_acceptance. Only the
configured reviewer or Mike can explicitly accept. Inspection, promises and
negative acceptance are not completion. No external artifact verification is
claimed. Images/voice without text are not interpreted by this text-only path.

One short question at a time per employee, then wait for their reply. Daily touch
has priority. Agreed future due times suppress ordinary reminders until due.
All individual assignments, reminders and staff private replies are Business-only.
There is NO group fallback. A blocked private item prevents daily duplicate queues;
unknown delivery is held for reconciliation. Group responses answer group questions
without publishing individual instructions or private task information.

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
