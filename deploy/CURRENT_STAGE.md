# GOLDWAY current stage

Checkpoint: 2026-10-15. This document describes the release and recorded evidence,
not a live health signal. Verify the exact deployed revision separately.

## Operational knowledge correction C05-F2

Business-problem questions now retrieve a bounded snapshot of current permitted
task records alongside the registered documents. Blocked work, overdue dates,
missing owners and dates are recorded signals, not an independently verified
diagnosis of GOLDWAY. Employees see their own non-draft work; administrators use
their existing role. Source revocation, dependency permissions and current roles
are rechecked before generation and delivery. No old conversations are imported.

This operational route is read-only even if the model proposes an action. A
missing document no longer erases available work evidence behind a generic
response. With no work evidence Mike explains the specific limit and requests a
current meeting summary. Independent task requests exclude earlier work-tainted
conversation before generation, preserving the new task's intended permissions.

Explicit calendar durations such as `Срок 2 дня` use the original source-message
date. Unknown dates, workdays, uncertain/alternative starting events still need
clarification. Deadline-only replies do not cancel an agreed-time check-back.
The screenshot's earlier generic processing error was NOT reproduced by the old
parser; its server-side cause still requires actual error evidence.

Final suite:1927passed7.23s. Focused affected suites329passed2.13s overlap the
full run. Independent review findings were corrected, including the provenance
budget boundary for a short work follow-up. A finite actual gpt-4.1-mini probe
answered a work question, shortened it and cited task records with fictional
data/in-memory capture; it did not use Telegram or prove a deployed staff flow.
No model, budget, recipients, worker or external-source configuration is changed.
See knowledge/C05_WORK_CONTEXT_VALIDATION.md for evidence and limits.

## Follow-up quality release C05-F1

Actual provider probes found that a JSON history snapshot and a stale copied
topic were insufficient: gpt-4.1-mini still repeated its offer after 'Давай'.
The new provider adapter sends the bounded authorized local conversation as
real user/assistant roles, with one final structured current request and no
duplicate history. OpenAI Chat, Anthropic and optional Responses share validation
and history-aware context/budget limits. No remote stored history is fetched.
Short continuations are anchored to the actual latest delivered answer; stale
entity lists cannot silently force a wrong brand. Missing ordinal support asks
for clarification. Current-message task evidence and permission guards remain.

Integrated checks:1728passed7.08s. After the final state/partial-retrieval prompt
clarification,288affectedcasespassed2.54s; these overlap. Independent review found
and verified two corrected stale-entity defects. Provider comparison on public
data with synthetic in-memory chats showed better offer follow-through from
gpt-4.1 and gpt-5-mini than gpt-4.1-mini; that is not Telegram acceptance.
See knowledge/C05_FOLLOWUP_VALIDATION.md for scope and remaining decisions.

The optional examples/model.gpt5-mini-candidate.json is an overlay requiring an
explicit model decision, not an activated runtime profile. It preserves the
existing daily budget/context policy when merged; there is no automatic model
switch. No live model, recipient, group, source policy or bot-worker flag changes
are included in this follow-up release.

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
