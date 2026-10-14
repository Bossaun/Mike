# Railway preparation (worker disabled by default)

Build from root Dockerfile, branch main, one worker and persistent volume /data.
Start command: `python -m goldway.railway`.

Private runtime variables (never commit their values):
- TELEGRAM_BOT_TOKEN
- MODEL_API_KEY
- GOLDWAY_CONFIG_JSON: verified configuration JSON, with live_send=false,
  sheets_write=false, review_mode=true during preparation.
- GOLDWAY_WORKER_ENABLED=false: no polling, model calls, SQLite writes or sends.
- RAILWAY_RUN_UID=0: Railway volumes are root-mounted. The bootstrap changes only
  the /data directory owner and drops UID/GID to 10001 before writing configuration
  or starting the worker. Existing SQLite files are not modified by bootstrap.
  See https://docs.railway.com/volumes#permissions.

When explicitly enabled, bootstrap validates private config, atomically writes
/data/config.json with mode 600 and executes the single goldway worker. It does
not import old transcripts, tasks or cloud databases. Errors log only their type.

GOLDWAY_WORKER_ENABLED=true starts polling/model processing even if live_send=false;
therefore keep it false until the user authorizes runtime and input processing.
Changing live_send is a separate decision after current recipient/group checks.

When worker is disabled, bootstrap stays idle so Railway can run a container
without polling. This still uses hosting resources: do not deploy unless authorized.
No public domain or HTTP healthcheck is needed. Actual container build, mounted
volume permissions and Telegram/model connectivity require a later Railway check.
Root Directory stays unset (/). Dockerfile path is Dockerfile. Configure in Railway
UI because new services cannot rely on legacy railway.toml config-as-code.

A verified historical test profile may contain test_operator as admin. Preserve
that mapping only for this test; do not automatically assign admin to boss, group
admins or unknown employees. Alen/Nastya remain unconfigured until IDs are verified.
