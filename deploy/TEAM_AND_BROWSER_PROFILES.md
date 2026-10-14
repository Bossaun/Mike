# Closed team and browser test profiles

The normal personal profile remains the default configured pilot. New profiles
are opt-in and do not grant access merely because this code is published.

## Browser test

With `GOLDWAY_WORKER_ENABLED=true`, `GOLDWAY_PERSONAL_ONLY=true`, and
`GOLDWAY_BROWSER_TEST=true`, the configured Business account owner can send
fresh ordinary bot DMs as the sole test operator. Business transport is disabled.
The profile uses `/data/browser-tests/config.json`, `personal_start.json`, and
`personal.sqlite`. The original personal configuration, marker and SQLite remain
separate. Model settings are preserved; usage reservations are per database and
are not an aggregate provider-account cost cap.

Only fresh standalone private text is accepted. Start a new artificial dialogue
after activation; do not replay historical messages. This profile reports
temporary test availability. It does not start a second worker.

Rollback: set `GOLDWAY_BROWSER_TEST=false` while retaining personal mode and the
original raw configuration. Verify the normal personal deployment and one worker.
Telegram queue acknowledgement during the test cannot be undone by rollback.

## Closed team preparation

`GOLDWAY_TEAM_ONLY=true` requires `GOLDWAY_PERSONAL_ONLY=false`, the explicit
five-member roster (`test_operator`, `frost`, `alen`, `nastya`, `dasha_it`),
`team_roster_verified=true`, and a distinct configured Business account. Only
`test_operator` is an administrator. Group, Sheets, daily summaries and unknown
onboarding are disabled. `team_live_send` defaults to false.

The profile has its own `/data/team.sqlite` and persistent private `team_start.json`.
Changed identities fail validation. Configured members can supply new plain text
through their ordinary bot DM or Business chat; old messages, edits, forwards and
attachments are rejected before events. Replies require a fresh parent on the
same route. Own-bot parents need a verified bot identity.

This is channel preparation, not a completed team rollout. The current natural
task-intent path is still personal-operator-only. Owner-authored Business task
creation and universal Business-to-bot fallback require separate integration.
The existing automatic route can choose a verified own bot when Business rights
or its 24-hour window are unavailable; an explicitly preferred Business reply
stays blocked. An unknown send is never retried through another route.

Do not enable this profile by reverting to an old broad configuration. Actual
member IDs, configuration and credentials belong only in private server storage.
