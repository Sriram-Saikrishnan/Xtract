# Phase 1 Deploy Checklist

## 1. Render env vars to verify/set before deploy

| Var | Default (if unset) | Action |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-flash` | **Required to confirm** — set explicitly in Render env even though the code default matches, so it survives a future code default change unnoticed. |
| `GEMINI_TIMEOUT_SECONDS` | `120` | Optional — default is fine for Render's plan; only raise if Gemini responses regularly approach 120s. |
| `STARTUP_RECOVERY_STALE_MINUTES` | `10` | Optional — default fine. Lower only if you want faster reclaim of crashed jobs; higher only if BATCH_DELAY_SECONDS/RETRY_WAIT_SECONDS make 10 min too aggressive (a job legitimately mid-retry-wait could get flipped early otherwise — see note below). |
| `MAX_FILES_PER_UPLOAD` | `150` | Optional — default fine unless you want a stricter/looser per-request cap. |

Note on `STARTUP_RECOVERY_STALE_MINUTES=10`: `RETRY_WAIT_SECONDS=90` (1.5 min) and `BATCH_DELAY_SECONDS=65` (~1 min) are both well under the 10-minute stale cutoff, so a job legitimately waiting mid-retry will not be falsely flipped on a normal boot. No action needed — just documenting why 10 min is safe with current retry/backoff settings.

No other phase-1 setting requires an explicit Render override — all ship with safe defaults in `app/config.py` and `.env.example`.

## 2. Deploy watch — dependency install

New/changed pins this phase: `google-genai==1.75.0`, `pydantic==2.13.4`, `httpx==0.28.1` (all already in `requirements.txt` prior to this phase — no version bumps were made in Phase 1, only new code consuming them; confirming here for resolver-conflict review).

Installed versions of the packages that could conflict with these three, from `requirements.txt`:

```
fastapi==0.111.0
pydantic==2.13.4
pydantic-settings==2.2.1
sqlalchemy==2.0.29
asyncpg==0.29.0
httpx==0.28.1
google-genai==1.75.0
python-jose[cryptography]==3.3.0
```

Watch the Render build log for `pip`'s resolver output. FastAPI 0.111.0 requires pydantic 1.x or 2.x >=2.0 (fine with 2.13.4). `httpx==0.28.1` is consumed both directly (our timeout handling) and transitively by `google-genai`; confirm no resolver downgrade message appears for `httpx` during install.

## 3. First boot — startup recovery

On the very first deploy of this phase, `recover_interrupted_jobs()` (`app/core/startup_recovery.py`) runs once before the app serves traffic and will very likely flip a batch of historically stuck pages/jobs, because this is the first time the sweep has ever run against the production DB.

Exact log line to look for (INFO level, printed once, at end of the sweep):

```
Startup recovery: %d page(s) flipped, %d job(s) reconciled, %d job(s) skipped (still active)
```

e.g. `Startup recovery: 42 page(s) flipped, 7 job(s) reconciled, 1 job(s) skipped (still active)`

May also see, for jobs that had zero `job_pages` rows (died before extraction ever started):

```
Startup recovery: %d job(s) had zero job_pages rows — Interrupted before processing started
```

**This is expected, one-time, and safe.** It marks old stuck pages as `failed` (`error_message="Interrupted by server restart"`) or reconciles orphaned jobs to a terminal status (`error` / `completed_with_errors` / `done`), making them retryable via the existing `/retry` endpoint. It does not delete data or touch already-terminal jobs.

## 4. First client batch — log health signals

Grep-ready exact strings:

| Signal | Level | Exact string to grep |
|---|---|---|
| Healthy — structured parse | INFO | `Extraction OK (structured)` |
| Healthy — fallback parse succeeded | INFO | `Extraction OK (fallback)` |
| Concern — structured parse missed entirely | WARNING | `structured parse missed, fell back to text parse` |
| Concern — structured parse schema mismatch | WARNING | `structured parse produced a schema mismatch` |
| Concern — request timeout | WARNING | `Request timed out after` |
| Concern — 503 backoff in progress | WARNING | `503 received, retrying in` |
| Concern — 503 exhausted (job marked for retry) | ERROR | `Failed after` ... `Gemini demand spike, mark for retry` |
| Concern — 429 backoff in progress | WARNING | `Rate limit (429) received, retrying in` |
| Concern — 429 exhausted | ERROR | `Failed after` ... `rate-limit (429) waits — giving up` |
| Critical — bad model config | WARNING+RuntimeError | `Model unavailable:` |
| Critical — all retries exhausted for a page | ERROR | `All` ... `attempts failed` |

A handful of structured-parse-missed WARNINGs per batch is not alarming (fallback parse absorbs it — check the fallback line also logs `Extraction OK`). Sustained `Model unavailable` or repeated 429/503 exhaustion ERRORs are the ones to act on immediately.

## 5. Rollback

```bash
git revert --no-edit 9cd15f8 a8664fd 3462682 fd26a68 2ee8c22 7fb2e8a
# or, to hard-reset a not-yet-deployed branch:
git reset --hard e83a089
```

(`e83a089` is the commit immediately before Phase 1 Step 1.)

No env var reverts are required — confirmed above, every new/changed setting (`GEMINI_MODEL`, `GEMINI_TIMEOUT_SECONDS`, `STARTUP_RECOVERY_STALE_MINUTES`, `MAX_FILES_PER_UPLOAD`) has a safe default baked into `app/config.py`, so removing the phase-1 code also removes the only consumers of the new settings — nothing is left dangling either direction.
