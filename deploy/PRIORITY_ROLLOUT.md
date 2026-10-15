# Memory, knowledge and scheduled follow-ups

This change extends the existing worker. It does not start another Telegram
poller, import past messages or enable Google/financial/warehouse writes.
Code publication, feature activation and a live accepted task cycle are separate
milestones. A successful deployment with features disabled is not acceptance.

## Configuration and rollout

Both additions are opt-in in the Python configuration. The shipped Docker image
requests `GOLDWAY_FEATURE_PROFILE=knowledge-reminders-v1`: the Railway TEAM_ONLY
bootstrap applies its public defaults only to a validated existing five-person
team and only when private feature fields are absent. Explicit private
`intelligence`/`scheduling` fields, including disabled values, take precedence.
Personal/browser modes and the disabled worker do not apply the profile.
`scheduling` accepts exactly `{"enabled": true|false}`. Its SQLite tables are
created only when the feature is enabled. Set the environment profile to `off`
to stop injecting defaults; this does not override explicit private feature fields.

For explicit configuration use `examples/knowledge-and-reminders.closed-team.json` as an overlay, never a
replacement for the private config. The reader keys are the existing five from
the validated closed-team profile; compare with the current roster before use.
It contains no Telegram IDs or credentials. Copy only `intelligence` and
`scheduling` into the existing private config. Preserve model, token caps,
daily_budget, account, admin, employee identities, routes and intake start marker.
The two sources are explicitly allowed PUBLIC site/catalog GETs. Accounting
Sheets, CRM administration and private Drive documents remain disconnected.

Keep the existing sole TEAM_ONLY worker, one replica, persistent `/data` and
private config permissions. Keep all group/history/Sheets flags disabled. Do not
reset the update offset, team start marker or existing personal/team databases.
No automatic model or budget increase: C06's stronger model remains an optional
separate profile. Do not replace production credentials with cloud placeholders.

The local coordinator was paused by the user's direct instruction; record which
coordinator owns each publication before changing main. Do not restart its
monitor or a second deployment writer to overcome missing cloud server access.

## Scheduled follow-ups

Supported natural requests include:

- `По задаче №1 напомни через 30 минут`.
- `Напомни завтра в 10:00` with one permitted focused task.
- An owner promise `Сделаю через 30 минут` with one permitted focused task.
- A bare `Через 30 минут` immediately after an actually delivered task reminder
  in the same user/chat/transport/Business connection.
- `Напоминай каждый день в 09:00` or `каждый понедельник в 10:30` for a confirmed
  template. Recurring series are administrator actions.
- `Отмени напоминание` or `Отмени повторение` for a referenced task.

Missing/ambiguous task or time produces one clarification, not a guessed schedule.
Uncertain, negated and unsupported times are not silently accepted. A check-back
does not change the calendar deadline. Relative time is anchored to the original
message; a delayed expired replacement must not erase the existing check.
The existing worker checks due times on its next loop; a blocking model/network
call can delay that loop. This is persistent minute-precision scheduling, not a
hard real-time audio/OS alarm or a separate background delivery process.
Genuinely new progress updates can supersede a pending check; questions cannot.
Receipt and original message times jointly prevent an old report from cancelling
a newer promise. Completion cancels remaining unsent reminders; unknown delivery
is never resent by scheduling.

New explicit recurring-task creation remains a proposal in review mode. Its
cadence appears on the card, then activates only after administrator confirmation
and known owner. Each occurrence is a distinct task; a completed earlier task
is preserved. Restart creates at most the current overdue occurrence, not a
backlog. Changed template/owner/source access pauses recurrence for review.
Task knowledge provenance follows cloned tasks and outgoing messages even if
knowledge intake is later disabled. Scheduling only queues into the existing
Dispatcher and its Business/own-bot routing and permission checks.

## Acceptance after deployment

Use the existing allowed operator/team chats and synthetic task content. Do not
re-send earlier invitations, manufacture an employee result or import old chats.

1. Verify exact commit/deployment, one worker, retained state/config permissions,
   enabled feature flags and public-source status aggregates without extracting
   private config, message text or SQLite. Restore confidence in server access
   before interpreting an old Active status as a new deployment.
2. In an allowed operator dialogue ask company/brands/source questions, continue
   with `Давай`, `о втором бренде`, `сделай короче`. Observe actual model usage and
   delivered replies. Missing facts stay unknown; fictional test brands must not
   appear as company knowledge. Do not substitute model-list GET for generation.
3. Create and confirm one synthetic task with a known employee, explicit result
   criterion and deadline. Confirm the actual employee receives it and supplies
   an actual response/result. `Почти готово` does not complete it. A replay must
   not duplicate the task or delivery. Human cooperation is a real dependency.
4. Ask `Напомни через 2 минуты` against that task, observe no early delivery and
   one due reminder, then completion/cancellation. Use `30 минут` in the real
   requested scenario; accelerated fake-clock tests prove the deterministic timer
   separately. Never report local clock simulation as a live timed delivery.
5. Test one approved recurring template, future occurrence, and pause. Report
   queue state separately from actual delivery and require a real completion.

## Rollback

Disable the Docker profile with `GOLDWAY_FEATURE_PROFILE=off` and restore previous
explicit `intelligence`/`scheduling` fields without touching identities,
budget or intake marker. Disabling scheduling alone does not remove already
queued reminders: reconcile or cancel ONLY unsent `reminder:c07:*` and recurring
assignment rows under the existing delivery procedure before disabling. Never
retry `unknown`/`sending`, delete a live database or reset offsets. Keep the
working bot running; apply code rollback through the single deployment owner.
