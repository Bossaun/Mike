# GOLDWAY current stage for cloud continuation

Evidence checkpoint: 2026-10-14, 19:58 Europe/Moscow. This is a handoff,
not a live health signal. Read current main and Railway before external changes.

The permanent personal pilot is authorized and remains enabled. Do not disable
it because an older five-minute protocol expired. One worker and /data volume.
Normal profile is PERSONAL_ONLY=true, BROWSER_TEST=false, TEAM_ONLY=false.
The integration owner is restoring this normal profile with this release;
confirm its exact successful deployment rather than treating this text as proof.
Canonical /data/personal.sqlite and its cutoff were preserved throughout testing.
The general /data/goldway.sqlite was absent at the last read-only audit.

## Verified live personal task cycle

A separate temporary browser profile and SQLite received only fresh artificial
messages from the configured GOLDWAY account. UI and read-only aggregate audit
confirmed one task, owner test_operator, deadline 2026-10-15, and transitions
proposed -> confirmed -> completed. The almost-done message produced no task
mutation. Seven deliveries went only to the test operator; six model calls.
One earlier failed confirmation remains in the test audit and was not replayed
or rewritten. Two defects found live were fixed: completion words in an explicit
new-task criterion caused a false missing-reference prompt; model intent and
actions contradicted each other on confirmation. The runtime consistency guard
was retained and the model instruction clarified. Fresh confirmation then passed.
No employee/group messages, Sheets writes or history import were performed.
This verifies the personal cycle, not a live team or Business delivery cycle.

## Integrated code

The combined local suite passed 614 tests in 3.06s after merging C03, runtime bot identity, and the
Business-to-own-bot fallback. This includes previous cases and all new cases;
do not add targeted or prior combined runs again. C02 adds 29 synthetic cases:
closed-team admin task intent via bot or inbound Business, draft review,
confirmed employee assignments, employee result, negative confirmation, and
cancellation of obsolete unsent assignments/reminders. Sending/unknown/sent
items are preserved. Employee task permissions were not expanded.

Business-first fallback adds 24 synthetic cases. Explicit business_bot_fallback
boolean true permits a verified own-bot route before a send when Business is
unavailable. Groups are excluded. Unknown send outcomes never trigger fallback.
See BUSINESS_ROUTING.md and TEAM_AND_BROWSER_PROFILES.md.
Team profile and this fallback are prepared; team runtime is not enabled.

The user selected existing GOLDWAY private Business chats as the main outgoing
channel; own bot DMs after /start are the fallback. Mike is administrator; Frost,
Alen, Anastasia and Dasha IT are employees. Private IDs remain outside GitHub.
Business preference primarily concerns assistant outgoing replies. Arbitrary
owner-authored outbound commands are not required; do not remove their guard.

C03 adds 67 synthetic cases for employee conversation, grounded relative dates,
manager notices, and receipt preservation on model failure. Relative dates use
the original Telegram message timestamp when available; an uncertain date does
not clear a known deadline. Employee roles and task ownership remain restricted.
The runtime binds closed-team own-bot identity from authenticated getMe before
handlers; 38 new cases verify own reply parents and conflicting identity rejection.
These cases are included in the 614 combined result, not extra totals. New staff
behavior remains synthetic-verified until closed-team runtime is activated.

## Coordination and schedules

Local root owns integration, GitHub and Railway. Existing cloud chat:
Настройка Mike, thread 01a1390e-0161-7427-8c15-02478cbe606e, host durable.
C02 patch has been accepted; do not reimplement it or repeat personal acceptance.
Cloud may prepare independent useful patches with targeted synthetic evidence.

A hosted cloud Scheduled controller was created, hourly, id
6acfae3aeae48190b11df90e07d237a4. It is a separate persistent controller, not a
native timer bound to that original Codex chat. It coordinates the original chat
only if its tools allow; otherwise performs independent repository work and
reports the limitation once. Local mike remains every 10 minutes; local mike-30
is paused. Do not create duplicate schedules or a second poller. Stay quiet when
nothing useful changed. Preserve owners and brief state between runs.

Remaining: activate/verify the closed-team configuration and Business connection
on the user's latest authorized scope; external backups and technical alerts;
optional exact model trace view. A question about a trace is not a completed UI.
Do not add random audits or import history, change Sheets/finances/warehouse,
broadcast to groups or extend recipients through a generic continue request.
Never print or publish keys, private IDs, real messages or SQLite content.
This document grants no new permission.
