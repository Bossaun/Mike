# Scoped Telegram photo reading

Existing authenticated five-person TEAM intake accepts fresh Telegram photo sizes
and JPEG/PNG image documents. It preserves the existing Business account, rights,
private reply transport and group scope. Other media and forwarded input stay
outside this path. TXT meeting attachments keep their existing admin-only rules.

One largest Telegram photo is selected per message, including without a caption.
getFile and streamed downloads enforce 5MiB, JPEG/PNG signatures, relative Telegram
paths and no redirects. Bytes are transient. Telegram IDs/base64/URLs do not enter
the textual dialogue, logs or SQLite. File metadata and a bounded transcription
stay with the source event. The 5MiB limit is checked again while streaming.

The same configured gpt-4.1-mini handles transcription via Chat Completions or
Responses image content. Both reserve the existing shared daily usage budget before
network access, including a conservative 16384-token image allowance. No extra
provider/key/model or spending limit is introduced. Images are untrusted source
data; extracted text, a short highlighted-row summary and uncertainty are separate
from the author's literal caption. Image instructions cannot authorize task changes.

A successful reading is cached before dialogue. A later model failure does not
remove it from recipient-scoped followup context. Interrupted/failed OCR is not
replayed automatically, preventing ambiguous repeated billing. A clear photo retry
request is sent on failure; successful OCR remains available as a short fallback.

Recognition is a model observation, not a verified stock quantity, SKU, expiry date,
price, website state or employee result. Blur/cropping is marked instead of guessed.
Individual task dispatch and staff private replies remain Business-private-only.

Validation: bounded mocked transport and real Updates.handle integration, scoped
history/privacy/authority tests, followed by a real owner-supplied image probe and
exact deployed revision plus one worker. No employee answer/result is fabricated.
