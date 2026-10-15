# C05-F1: actual conversation roles and follow-up focus

## Observed cause and changes

A finite real-provider test used the approved public GOLDWAY website/catalog,
fresh synthetic messages, in-memory SQLite and a capture-only transport. No
employee messages/history or live SQLite were read, and no Telegram API was called.
The old model input contained the full conversation as a field inside a single
JSON user message. Although retrieval supplied real brand/product facts,
gpt-4.1-mini copied a stale broad topic, omitted its offer state and repeated its
offer after acceptance. A shortening request returned to the older brand list.

The provider adapter now sends the bounded authorized conversation as actual
user/assistant message roles, followed by the current JSON context. Its history
field is empty there, avoiding duplicate transmission. Unknown roles, tool/system
messages, malformed order/content and excessive size fail before API/reservation.
History contributes to context caps and conservative token reservations across
OpenAI Chat, Anthropic and optional Responses. Provider-stored conversation IDs
are not used. Current source evidence alone authorizes task actions.

Explicit bounded follow-up metadata selects the latest delivered answer and
retrieval query rather than an old topic. It is not action authority. Unknown
ordinals clarify instead of trusting stale entity names/order. Revoked, foreign,
unsent or unknown-delivery memory cannot supply the anchor. Ordinary questions,
task confirmations and current recipient/source permissions remain guarded.

## Verification and limits

- Integrated synthetic suite:1728passed7.08s.
- After final prompt guidance on individual entity names and partial retrieval,
  the affected model/intelligence/history suite:288passed2.54s (overlap).
- Independent review found two stale-entity cases; both were corrected and
  re-inspected. Review itself did not make API calls or rerun full suites.
- gpt-4.1-mini real-provider probes improved specific-answer shortening and
  ordinal retrieval, but offer follow-through still failed. Do not call that a
  fully accepted intelligent assistant.
- Two-question comparisons with gpt-4.1 and gpt-5-mini continued an accepted
  explanation and supplied catalog-based product examples. Small comparisons
  are not a general model benchmark or a live employee/Telegram task cycle.
- After the final prompt guidance, a five-question gpt-5-mini Chat probe
  completed all five events: accepted offer -> catalog details -> second brand
  product -> shorter current answer -> source attribution. It used27282input/
  7744output tokens, with no tasks created and no Telegram calls. This validates
  that finite provider scenario, not production settings or all user workflows.

The public catalog's brand field also contains non-brand-looking category labels.
A catalog-field match is not a verified complete internal brand master. Keep that
data-quality limitation explicit; a curated brand/category reference remains a
separate knowledge-source prerequisite, not something a model swap resolves.

The current cloud credential rejected one Responses request with HTTP401;
a subsequent read-only model GET succeeded and Chat Completions generation also
succeeded. Do not infer that the working bot's key is globally invalid or rotate
it automatically. The optional gpt-5-mini candidate uses the tested Chat route.

## Optional model decision

`examples/model.gpt5-mini-candidate.json` is a partial overlay only. It changes
the model/route/pricing and provides a6000-token output ceiling including
reasoning. Merge only after explicit selection, keeping the existing daily
budget, context cap, identities, permissions, intake marker and persistent state.
It is never loaded automatically by Docker or the rollout helper.

Official standard token prices inspected on2026-10-15:

| Model | Input per1M | Output per1M |
|---|---:|---:|
|gpt-4.1-mini|$0.40|$1.60|
|gpt-4.1|$2.00|$8.00|
|gpt-5-mini|$0.25|$2.00|

Sources: https://developers.openai.com/api/docs/models/gpt-4.1-mini ,
https://developers.openai.com/api/docs/models/gpt-4.1 ,
https://developers.openai.com/api/docs/models/gpt-5-mini .
Reasoning tokens can increase total reply cost despite a lower input price.
The two-question gpt-5-mini comparison used9995input/4337output tokens, nominal
uncached estimate$0.0112, not an invoice. Reservation totals are not actual bills.

Production model switching, effective server settings, exact deployment and
actual Telegram delivery must be observed separately. Railway diagnostic access
and a genuine employee response remain external prerequisites. Keep the existing
sole worker and do not manufacture a staff result or start another poller.
