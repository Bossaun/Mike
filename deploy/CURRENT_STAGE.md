# GOLDWAY current stage

Checkpoint: 2026-10-15. This document describes the release and recorded evidence,
not a live health signal. Verify the exact deployed revision separately.

## Current approved runtime

The existing closed five-person TEAM_ONLY worker is authorized permanently.
The last independently reported runtime was main 221d730, Railway deployment
`d295d7a4-5fd7-4061-9378-55e78138008b`: one worker, live sending to the closed
team, one administrator, persistent team SQLite, personal SQLite preserved.
Groups, old history, Sheets, finance and warehouse writes remain disabled.
Five human invitations have already been sent; do not send them again.
Business is preferred for assistant replies, own-bot DMs after /start are the
fallback. At that checkpoint there were no verified live Business connections.
Do not infer a staff task result or Business delivery from an Active deployment.

## Knowledge, memory and scheduled follow-ups in this release

C05 adds ACL-scoped read-only retrieval, source dates/conflicts/citations and
bounded conversation memory of actually delivered replies. Short continuations
reuse the topic and pending offer within the same user/chat/transport context.
C06 adds an optional model-provider/context profile; it does not change the
production model, token settings or daily budget automatically.
C07 adds persisted agreed-time reminders and recurring task occurrences using
the existing tick and outbox. Relative times use the original message date.
Questions do not create tasks or reminders. Genuine progress and completion can
supersede unsent checks; unknown delivery is never retried by scheduling.
Recurring occurrences are separate tasks, preserving earlier results and ACLs.

The shipped Docker profile `knowledge-reminders-v1` supplies intelligence and
scheduling defaults only for the validated existing closed team when their
private configuration fields are absent. Existing explicit fields take priority.
It reads only the approved public GOLDWAY site and catalog Worker, with explicit
existing team readers. Private Drive, accounting Sheets and CRM administration
are not connected. Personal/browser/disabled profiles do not apply these defaults.
See `PRIORITY_ROLLOUT.md` for activation, acceptance and rollback details.

## Verification

The integrated synthetic suite passed 1423 tests in 6.06 seconds. After removing
an unused bootstrap log, the 64 affected rollout cases passed in 0.08 seconds.
These are overlapping results, not 1487 distinct cases. A Python wheel was built.
The source tests cover knowledge rights, short continuations, task regression,
source-time scheduling, restart/deduplication and closed-team bootstrap. They do
not prove the intended flags or real replies on a deployed instance.

The earlier personal live task cycle was accepted. New knowledge answers,
employee assignment through a genuine employee result, and live scheduled
follow-ups require separate observations after this release is deployed. Never
manufacture an employee response to satisfy acceptance.

## Continuation ownership and remaining actions

The user paused the local periodic coordinator and instructed the cloud chat to
continue. The cloud prepared this integrated release under the direct request to
install memory/knowledge, complete a staff cycle and add scheduling. Do not start
a second coordinator, second Telegram poller or change recipient/model/budget
permissions. Existing schedules must not be duplicated.

Next: verify exact successful deployment; inspect feature/source aggregates
through authorized server access; verify fresh company questions and natural
continuations in an allowed chat; complete one real employee task and timed
check-back. Real employee availability and server access are external dependencies.
Deferred: Instagram ingestion, live voice and new operational data/write tools.
