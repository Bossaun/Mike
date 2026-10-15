# C06: stronger model, larger working context, concrete agent capabilities

This is an additive candidate AFTER C05 patch
`f9a47b2058af47f2df288e6a701556ad311a2e8d9148b2811d8d930468ae6f13`
on main `221d730dce7c305a894c3715a5bb6bbfa7da7f1b`.
C05 remains immutable. Only local root integrates, publishes and changes Railway.
No second poller, Google writes, staff messages or model generation were run by
cloud for C06. No change to recipient roster, task rights, live flags or budget.

## Recommendation, checked against current official sources

Use `gpt-6.1-sol`, standard tier, reasoning medium as the first candidate for Mike.
Do not automatically use the most expensive model or silently fall back to another
model for consequential actions. Model selection remains replaceable configuration.

Official sources retrieved 15 October 2026:

- https://developers.openai.com/api/docs/models/gpt-6.1-sol.md
- https://developers.openai.com/api/docs/models
- https://developers.openai.com/api/docs/pricing
- https://developers.openai.com/api/docs/guides/reasoning
- https://developers.openai.com/api/docs/guides/structured-outputs.md

Sol: 1,050,000 context window; maximum input922,000/output128,000;
standard input$2/output$10 per1M tokens, cache input$0.10.
Prompts over272K input tokens: full request input/cache2x, output1.5x.
Reasoning tokens count as output and consume its cap. Actual usage/latency/quality
must be checked on the workload. Model card suggests near-Astra performance;
that is a provider description, not an independent Mike benchmark.

Read-only GET /v1/models with the configured cloud binding returned200 and listed
Sol, Luna, Astra and gpt-4.1-mini. This proves model-list access only, not successful
generation or what model the Railway process currently uses. No key was printed.

Illustration:10,000 input+1,000 total output tokens on Sol = $0.03;
100such requests=$3. Reasoning/history/system/retrieval must be included, and larger
requests cost more. Current public example oldmodel rates are$0.40/$1.60; compare
actual private profile before changing production. Do not confuse Codex's selected
model with the separately configured Telegram bot model.

## Implemented delta

- Optional `model.api=responses` routes through /v1/responses with the SAME
  Model.propose interface and strict action/state schema. Existing OpenAI Chat
  Completions and Anthropic defaults remain intact.
- `store=false`, no provider conversation IDs, no provider tools enabled. Current
  bounded SQLite history/source ACL stays authoritative; no historical import.
- Validates completed assistant JSON; refuses partial, refusal, malformed usage
  and unsolicited tool calls. It does not execute model-generated code or tools.
- Existing before-call daily reservation remains. Reservation includes new system
  instructions and long-context multipliers using a conservative UTF8 input bound;
  unknown/failed calls keep the reservation. Timeout is bounded at180seconds.
- Bounded configurable dialogue/retrieval, with unchanged defaults. Candidate:
  up to16 role messages,16,000 history chars,12,000 retrieval chars,120,000 entire
  context chars. These are CHAR budgets, not a promise to send1M tokens per request.
  Old revoked/different-chat content remains excluded. More context never grants
  extra source access. More knowledge sources may hit the existing provenance cap;
  code drops protected context wholesale rather than losing its access metadata.
- Model context describes actual task/knowledge/queued Telegram actions and marks
  unavailable spreadsheet/browser/shell/file-write tools explicitly. A larger model
  cannot grant itself a new integration or recipient.

## Agent scope: useful actions, verification and boundaries

Mike already has a narrow executor: model proposes actions; Engine verifies source,
role, deadline, review state and duplication; SQLite applies a transaction; outbox
sends with delivery state. Task mutations are actual actions, not just drafted text.
New C05 knowledge and C06 API are additive; they are not a new autonomous agent
framework or a promise that Mike can control arbitrary applications like Codex.

First useful business chain remains: find authorized information → extract a
concrete next step → propose/confirm task according to role → assign via permitted
channel → collect result → verify completion criterion → brief the manager.
A reply saying “done” cannot substitute for a result or verified API operation.

Current capability map:

| Capability | Present in code | Actual live evidence / remaining step |
| --- | --- | --- |
| Create/update tasks | Existing validated Engine actions | Historical personal cycle passed; new team/knowledge path needs live check |
| Parse meeting/ideas | Existing intent+review flow | Synthetic coverage; do not manufacture staff live evidence |
| Ask/status/reminders | Existing worker+outbox | Routes and recipient permissions remain unchanged |
| Search company knowledge | C05 explicit sources+ACL | Public read-only source/retrieval checked; C05 not yet deployed |
| Stronger model/longer context | C06 opt-in adapter/profile | Mock tests; no paid generation/production switch yet |
| Edit a chosen Google task table | Existing bulk Sheets adapter, NOT an agent tool | Disabled; exact workbook/tab/columns/actions and credentials required |
| Finance/stock/payments | Not permitted | Remain disabled |
| Arbitrary shell/browser/account control | Not provided to runtime | Not enabled by changing a model |

Do not expose current Sheets.sync directly as an agent tool: it is a bulk,
bidirectional projection and may import manual row changes. A first write tool
must be scoped to a separate task workbook or explicitly allowed task tab, stable
row IDs, approved columns and operations, with a before/after preview, idempotency,
authorization, conflict checking and read-back verification. No stock/finance cells,
row deletion, permission changes or arbitrary range selected by a model. A transport
timeout is ambiguous; verify actual destination state before retrying.

To make this concrete, the next write capability should use one permitted tool
such as task-table upsert, not generic “execute whatever the model asks”. Exact
sheet choice and allowed writes are a user decision; meanwhile preparation and
synthetic tests are authorized and the current app continues running.

## Integration and acceptance by local root

1. Apply C05 first, then C06 delta; compare core changes before merge. Run one
   combined suite for the integrated change; do not repeat older acceptance runs.
2. Merge `examples/model.sol-candidate.json` overrides into PRIVATE config only.
   Keep its daily_budget, sources/readers, closed-team gate, credentials/identity,
   one worker, groupsfalse/Sheetsfalse. The example is not an executable replacement
   for complete config and does not enable intelligence or overwrite C05 sources.
3. Check actual current model and budget without dumping config/key. Candidate
   does NOT increase daily_budget. A$1/day cap and conservative reservations may
   limit frequency more severely with a stronger model; do not silently raise it.
4. Verify exact deployment and single worker independently. Use same authorized
   operator dialogue and synthetic content for short chat, “Давай”, second brand,
   draft Friday task/negative consent and result. Check task guards, model usage,
   output/refusal/error behavior, latency and actual delivery. Compare to prior
   model under the agreed budget; model-list GET and mock responses are not quality
   evidence. If budget blocks the test, record exact decision instead of bypassing.
5. Roll back only api/model/context overrides to old profile if needed. Preserve
   C05 ACL/provenance and SQLite; no data copy, history replay, recipient changes
   or second poller. A production model switch has not been done by cloud.
