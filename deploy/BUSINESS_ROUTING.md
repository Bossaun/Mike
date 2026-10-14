Business-first, verified own-bot fallback (base 599f73d1)

Existing preferred=None already chooses Business, then bot, then group.
Existing preferred=business is Business-only. Both defaults are retained.

Explicit opt-in: business_bot_fallback must be the JSON boolean true.
For a queued preferred=business item, Business remains first. Before any
sendMessage attempt, if its configured account, live rights or reply window
cannot authorize Business, the dispatcher may select the employee's own bot DM.
It never selects a group for this explicit policy.

Bot fallback requires an existing bot_chat recorded by accepted private inbound
(/start is sufficient), matching that employee's confirmed positive integer
telegram_user_id. Roster, recipient allowlist, onboarding and sender gates remain
in force. Merely configuring an employee ID does not create a bot route or invite
the person. Unknown /start remains rejected by the closed team gate.

Business requires the configured GOLDWAY account, enabled connection, can_reply
and 0 <= current time - last inbound < 86400 seconds. A failed rights lookup can
choose bot only before a send; it is not an ambiguous send result.

flush selects one route, claims the item, attempts one send and records that
actual destination. There is no second-route retry within that attempt. Timeout,
ambiguous DeliveryError, malformed result or unexpected response leaves unknown;
unknown is excluded from subsequent automatic sends and needs reconciliation.
A definite failure remains failed. Existing explicit rate-limit retry behavior
is retained; this patch adds no retry mechanism.

Only goldway/telegram.py route selection is changed. Config/intake/railway/engine
and task intent are untouched. To enable later, the integration owner must opt in
on the intended profile; this preparation does not activate a runtime, publish,
send invitations or read a private roster. Synthetic IDs and text are test-only.
