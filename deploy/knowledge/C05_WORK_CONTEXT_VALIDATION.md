# C05-F2: permitted operational evidence and duration clarification

Date:2026-10-15. Base remote main b418d9caaee655a94ebf4300ff8d757b9c574b4c.

## Confirmed causes and changes

The screenshot's business question returned the exact deterministic `not_found`
replacement from Intelligence.validate. Retrieval used registered document
evidence, while operator task candidates were not available as citable work
evidence. Thus a model could ignore accessible work and the finalizer would
replace its answer with the same generic missing-information text.

The new read-only work projection uses existing current-business tasks and
current role/source permissions. It emits at most five records and6000JSON
characters, with authentic internal task references, observation time, state,
deadline and recorded next step. It never reads source bodies, private chat
history, provider logs or finance/warehouse/Sheets. No new source connection,
table, recipient, permission, model or budget is introduced.

Task records confirm their recorded state only. Proposals remain proposals;
overdue dates do not independently prove failure; missing records do not prove
that business problems are absent. Work and document evidence can be cited
together. The existing persistent memory/outbox provenance gates now understand
current task-role grants and source dependencies, including revocation after a
model call or before transport. A snapshot change during generation fails closed.

Work questions and their explanation follow-ups cannot mutate tasks. When the
model misclassifies them, trusted code produces a read-only recorded-work answer.
For an independent explicit task request, earlier work-tainted history/state is
filtered BEFORE generation, rather than keeping its text and dropping its ACL.
Provenance budget trimming retains the read-only work route even on short replies.

The deadline parser previously did not recognize `Срок 2 дня`; a source-cited
nullable update was positively reproduced against the old resolver as a successful
clarification, not the screenshot's generic failure. New explicit calendar-day
durations use the original event date. Uncertain, conflicting, workday or alternate
starting-event assertions preserve the previous fact and ask for clarification.
Deadline-only replies preserve pending/fired agreed-time checks across restart.
The earlier generic error's exact cause remains unknown without server evidence.

## Checks actually executed

Final source:

```sh
/workspace/Mike/.venv/bin/python -m pytest -q
```

Result:1927passed7.23s, zero failures/skips. Affected suites immediately before it
passed329in2.13s; they overlap and are not added to the total. An earlier full run
passed1926before the final overflow correction and its regression. No tests were
disabled or existing assertions weakened. The package wheel is built separately.

Independent review reproduced and closed: read-only action coercion, unrelated
assignment provenance, alternate duration anchors, and the16-reference short
follow-up boundary. Closure of the last change was inspection; root ran the tests.

A finite actual gpt-4.1-mini Chat probe used only fictional task records, an
in-memory SQLite and captured transport. Three completed turns: the screenshot
question, shortening, then asking for its source. All had actions:[], and real
Telegram API calls were zero. The initial harness lacked its fictional delivery
route, causing IndexError after one generation; this harness failure is preserved
separately. After adding the synthetic route, the three-turn run completed.
Total provider generations for this stage:4, not3. No production model was changed.

The probe preceded final prompt hardening and reviewed guards. It demonstrates
that the existing model can use supplied task records; it is not a statistical
quality evaluation or actual Telegram/Railway acceptance. Finite probe artifacts
remain outside the repository and contain no real employee records.

## Activation and remaining evidence

Existing intelligence.enabled=true uses this code without an additional profile
or worker. Intelligence-disabled operation remains opt-in as before. Runtime
feature settings have not been inspected here. Publish exact source and verify
the successful deployment separately. Do not start a second poller or manually
replay the screenshot's earlier deadline message against an unknown task.

Real internal facts require current authorized tasks/meeting notes/documents in
the running system. Public catalog data cannot supply them. A successful deploy
does not prove populated operational records or live reply delivery. Server
access is still needed for effective-settings/error aggregates, followed by one
fresh allowed real dialogue. Never export the live SQLite to perform that check.
