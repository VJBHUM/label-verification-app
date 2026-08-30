# TTB Label Verification

An AI-powered web application that verifies an alcohol beverage label against the
data filed in its COLA application. A compliance agent uploads a photo of the
label plus the application details; the app reads the label, compares every
mandatory field, applies a strict check to the Government Health Warning, and
returns a clear **PASS / FAIL / NEEDS REVIEW** verdict in about five seconds. It
also supports **batch** verification for peak-season imports of hundreds of
labels at once.

Delivered as a standalone proof-of-concept — no COLA integration and no data
retention, per the Compliance Division and IT discovery sessions — and gated
behind a shared access code so a public demo URL isn't open to the world.

---

## What it checks

Coverage follows the TTB mandatory label elements (27 CFR parts 4, 5, 7, and the
health-warning rule in part 16):

| Element | How it's evaluated |
|---|---|
| **Brand name** | Judgment-based match — capitalization/punctuation/spacing differences pass (e.g. `STONE'S THROW` vs `Stone's Throw`). A genuinely different name fails. |
| **Beverage type** | Distilled spirits / wine / malt beverage — informs the alcohol-content rule below. |
| **Class / type** | Judgment-based match. |
| **Alcohol content** | Compared by value — `45% Alc./Vol.` matches `45% Alc./Vol. (90 Proof)` (90 proof = 45% ABV). Not required on every label (some wine/malt beverages may omit it); only checked when filed. |
| **Net contents** | Judgment-based match. |
| **Bottler / producer name & address** | Judgment-based match. |
| **Country of origin** | Checked for imports; flagged missing if filed but absent from the label. |
| **Government Health Warning** | **Strict.** Must be present, `GOVERNMENT WARNING` in ALL CAPS, the full statement word-for-word, and a legible (not tiny/buried) size. Any of those → **FAIL**, with the specific problems listed. Because **bold** is genuinely hard to judge from a photo, a fully-compliant warning whose header doesn't look clearly bold is routed to **NEEDS REVIEW** for a human to confirm — rather than auto-rejected. |
| **Image quality** | Photos shot at an angle, with glare, or poorly lit are still read on a best-effort basis and flagged. |

---

## Quick start (local)

Requires **Python 3.10+** and an Anthropic API key.

```bash
cd label-verification-app

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# In .env set: ANTHROPIC_API_KEY, APP_ACCESS_CODE (any shared code),
# and APP_SECRET_KEY  (python -c "import secrets; print(secrets.token_hex(32))")

uvicorn backend.main:app --env-file .env --reload
```

Open **http://localhost:8000**, sign in with your `APP_ACCESS_CODE`, and you're in.
Health check (public): **/api/health**.

> Tip: if you leave `APP_ACCESS_CODE` unset, the access gate is disabled (handy
> for local dev) and a warning is logged. Always set it before deploying.

---

## Using it

**Sign in** with the shared access code.

**One label** — choose a photo, fill in the application details (only brand name
is required), click **Verify label**.

**Batch** —
1. Prepare a CSV: `filename, brand_name, beverage_type, class_type,
   alcohol_content, net_contents, producer_name_address, country_of_origin`
   (only `filename` and `brand_name` are required). See `samples/batch_example.csv`.
2. Upload the CSV and select all the label images (named to match `filename`).
3. Click **Verify all labels**. Failures and reviews sort to the top; **Details**
   opens the full breakdown for any label.

---

## Performance

The stakeholders' hard requirement is a result in about five seconds (their
previous scanning vendor took 30–40s and got abandoned). Measured, warm, through
the full HTTP path:

| Model (`LABEL_VERIFIER_MODEL`) | Typical latency | Notes |
|---|---|---|
| **`claude-haiku-4-5`** (default) | **~5 s** | Meets the target and, in testing, reliably catches the checks that matter — all-caps + exact warning text, brand/ABV mismatches, title-case headers. |
| `claude-sonnet-4-6` | ~8–9 s | More thorough; routes more borderline calls to *needs review*. Good for higher-stakes second-pass review. |
| `claude-opus-4-8` | slower | Maximum scrutiny. |

Three levers get us there: images are **downscaled** to a bounded long edge
before the call (the main lever), the model returns **terse structured output**,
and a **single warm HTTPS client** avoids per-request TLS handshakes. Very large
or dense labels can still run longer; batch runs process labels concurrently.

---

## Architecture

One process serves both the API and the UI, so it deploys as a single unit.

```
backend/
  main.py       FastAPI: endpoints, access gate, rate limits, security headers, PII-free logging
  auth.py       Shared-passcode gate: signed session cookies + per-IP rate limiting
  imaging.py    Secure image intake: magic-byte validation, EXIF/GPS stripping, bomb guard, normalization
  verifier.py   The model call + matching rules, timeouts, retries, concurrency guards
  models.py     Pydantic request/result schemas (the structured-output contract)
frontend/
  index.html    Single-page app UI (no build step)
  login.html    Sign-in page
  styles.css    Shared styling — large, high-contrast, keyboard-accessible
  app.js        Progressive rendering; all data inserted via DOM APIs (no innerHTML → no XSS)
  login.js
```

**Why a vision-language model.** This task is not pure OCR — it must read messy
photos, fuzzy-match names, apply an exact-match rule to the warning, and use
judgment about what counts as a real mismatch. A vision-language model does all
of that in one call. The app uses **Anthropic's Claude** with a **forced
tool-call schema** (structured output), so the model returns schema-validated
JSON that the UI renders directly — nothing downstream ever parses free-form
text. The model is swappable via one environment variable.

---

## Security & privacy

Built as a trust boundary around untrusted image uploads and a public URL.

- **Access gate.** A shared passcode is required to reach the app or the API.
  Sessions are stateless, signed **HttpOnly** cookies (HMAC-SHA256, 8-hour
  expiry, `Secure` over HTTPS, `SameSite=Lax`). Unauthenticated requests are
  redirected (pages) or rejected with 401 (API).
- **Rate limiting.** Per-client (IP) limits on verification, plus a tight limit on
  the login route to blunt passcode brute-forcing. A global concurrency ceiling
  protects memory and the upstream API.
- **No data retention.** Uploads live in memory for a single request and are then
  discarded — nothing is written to disk or a database. No PII is persisted.
- **Metadata stripped.** Every image is re-encoded, removing EXIF — including
  **GPS coordinates** phone photos carry. EXIF orientation is applied first so
  rotated photos still read correctly.
- **Validated by content, not by claim.** The browser-supplied content type is
  ignored; images are validated by **magic bytes**.
- **Decompression-bomb guard.** Pillow's pixel ceiling is capped and enforced.
- **Bounded everything.** Per-image size, total request size, per-batch image
  count, and text-field length are all limited.
- **Timeouts & retries** on the model call so a hung upstream can't wedge a worker.
- **Security headers on every response** — a strict Content-Security-Policy,
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy:
  no-referrer`, and a locked-down `Permissions-Policy`.
- **No XSS surface.** All model/user data is inserted with DOM APIs
  (`textContent`), never string-built HTML. Interactive API docs are disabled.
- **Leak-free errors.** Clients get generic messages; full detail is logged
  server-side only, and log lines never contain image bytes or application data.

### What this is *not*

This is a **hardened prototype**, which is what the brief asked for. It is not a
FedRAMP production system, and the shared-passcode gate is cost/abuse protection
for a public demo — not federal identity. A real TTB deployment additionally
needs: FedRAMP authorization; PIV/SSO with per-user accounts and roles; audit
logging; a document-retention policy; COLA integration; a secrets manager and key
rotation; a WAF and dependency scanning; and a penetration test. It would also run
inside the network boundary — the model endpoint is already proxy-friendly via
`ANTHROPIC_BASE_URL` so calls can route through an internal gateway or a
FedRAMP-authorized region rather than a public endpoint.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** Model API credential. |
| `APP_ACCESS_CODE` | — (gate off) | Shared sign-in code. **Set this before deploying.** |
| `APP_SECRET_KEY` | random per boot | Session-cookie signing secret. Set a fixed value so sessions survive restarts. |
| `LABEL_VERIFIER_MODEL` | `claude-haiku-4-5` | `claude-sonnet-4-6` / `claude-opus-4-8` for higher-stakes review. |
| `ANTHROPIC_BASE_URL` | — | Route model calls through an internal proxy/gateway. |
| `LABEL_VERIFIER_MAX_EDGE` | `2200` | Long-edge px cap for normalization (latency lever). |
| `LABEL_VERIFIER_CONCURRENCY` | `8` | Parallel labels per batch. |
| `LABEL_VERIFIER_MAX_BATCH` | `300` | Max images per batch request. |
| `APP_SESSION_TTL` | `28800` | Session lifetime (seconds). |

---

## Deploy

- **Render:** a `render.yaml` blueprint is included — create the service from this
  repo, set `ANTHROPIC_API_KEY` and `APP_ACCESS_CODE` in the dashboard
  (`APP_SECRET_KEY` is generated automatically). A `Procfile` is also provided for
  Railway/Heroku-style hosts.
- **Anywhere else:** `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` with
  the environment variables set.

---

## Requirements traceability

Every point raised in the discovery sessions, and where it is addressed:

| Stakeholder note | Addressed by |
|---|---|
| "Brand name matches? ABV correct? Government warning there?" (Sarah) | Core field checks + strict warning check. |
| "A lot of what we do is just… matching" (Sarah) | The app automates exactly that matching. |
| "If we can't get results back in about 5 seconds, nobody's going to use it" (Sarah) | ~5 s measured with the default Haiku model + image downscaling + terse output + warm client. |
| "Something my mother could figure out — she's 73… clean, obvious" (Sarah) | Large high-contrast UI, single primary action, keyboard-accessible, plain-language verdicts. |
| "Half our team is over 50" (Sarah) | Accessibility: focus states, ARIA roles/labels, large targets. |
| "Batch uploads… 200, 300 at once" (Sarah / Janet) | Batch endpoint + UI, concurrent processing, triaged failures-first. |
| "Standalone proof-of-concept… not integrating with COLA" (Marcus) | Self-contained app; CSV stands in for the COLA feed. |
| "PII considerations, document retention… not storing anything sensitive" (Marcus) | Zero retention; EXIF/GPS stripped; PII-free logging. |
| "Network blocks outbound… firewall blocked their ML endpoints" (Marcus) | `ANTHROPIC_BASE_URL` proxy indirection + documented production path. |
| "You need judgment — 'STONE'S THROW' vs 'Stone's Throw' is the same" (Dave) | Judgment-based matching for names/fields; only the warning is literal. |
| "Don't make my life harder" (Dave) | One screen, one button, verdict + reasons; batch triages what needs attention. |
| Checklist: brand name, ABV, warning statement (Jenny) | All three are first-class checks. |
| "Warning must be exact, word-for-word, all caps and bold" (Jenny) | Strict check: presence, all-caps, exact text, legibility as hard rules; bold routed to human review. |
| "People get creative… smaller font, different wording, tiny text" (Jenny) | Legibility + exact-text checks flag reworded, shrunk, or buried warnings. |
| "'Government Warning' title case → Rejected" (Jenny) | All-caps header check fails title case (verified). |
| "Images that aren't perfectly shot — angles, lighting, glare" (Jenny) | Best-effort read with an image-quality flag; EXIF auto-orientation. |
| TTB elements: class/type, net contents, bottler name/address, country of origin | All included as checked fields. |
| Deliverables: source repo, README, deployed URL, working prototype | This repository, this README, deploy config, and the running app. |

---

## Assumptions & known limitations

- **Bold** on the warning header is treated as advisory (→ *needs review*) rather
  than a hard fail, because visual bold detection from a photo is unreliable and
  false rejections are worse than a human confirmation. Container-size-based
  **type-size minimums** are assessed qualitatively as "legible," not measured.
- Beverage-type nuances beyond the ABV exception (full per-type mandatory
  matrices, wine appellation/vintage rules) are read and reported but not yet
  enforced field-by-field.
- Batch matching is by filename via the uploaded CSV; production would pull
  application data directly from COLA.
- The access gate is a single shared code (cost/abuse protection), and the rate
  limiter is in-process — correct for one instance; a multi-instance deployment
  would use per-user accounts and a shared rate-limit store.
