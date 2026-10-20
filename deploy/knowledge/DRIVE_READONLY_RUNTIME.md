# Explicit read-only Drive knowledge account

The ordinary `goldway ... run` and `ingest` entry points can pass a separately
provisioned service-account file through Engine and Intelligence to the existing
read-only source adapter. This does not provision an account or grant access.

Prerequisites for live activation:

- Owner-approved exact `kind: drive` source registrations under enabled
  `intelligence.sources`, with exact `file_id`, explicit roster `readers` and
  verified per-reader `reader_emails`.
- An approved service account with access to those documents and their current
  permission metadata. Service-account access alone never authorizes a reader.
- The host separately provisions the credential file and sets
  `GOOGLE_KNOWLEDGE_SERVICE_ACCOUNT_FILE` to its path. Never publish the file.

The loader requests only `https://www.googleapis.com/auth/drive.readonly`.
It never uses `GOOGLE_SERVICE_ACCOUNT_FILE`, Sheets tokens or ambient Google
credentials. With no enabled registered Drive source, the new variable is ignored
and its file is not read. With no explicit knowledge file, existing Drive sources
remain blocked; no implicit account discovery or authentication occurs. A bad
file emits constant code `knowledge_credentials_unavailable`; the worker keeps
running with Drive sources blocked and other existing sources/task flows intact.

The existing adapter still checks each document's current effective reader ACL,
TTL and exact identity before indexing. Only the existing supported text/Google
document/presentation formats are accepted; this change adds no Sheets or PDF
reader, write operation, roster member or permission grant. Direct trusted host
callers may pass `google_credentials=` to Engine; only exact read-only scopes
and an enabled registered Drive source are accepted.

Synthetic CLI/Engine/provider checks establish wiring only. Real credentials,
documents, current provider permission observations and Telegram delivery require
separate live acceptance. Deployment with the variable absent activates no new
Google access.
