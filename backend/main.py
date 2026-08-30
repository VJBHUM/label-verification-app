"""FastAPI application: serves the UI and the verification endpoints.

Design notes (security & privacy):
- Uploads are held in memory for the lifetime of a single request and then
  discarded. Nothing is written to disk or a database — no retention of PII.
- Images are validated by magic bytes and normalized (EXIF/GPS stripped) before
  any processing. The browser-declared content type is never trusted.
- Request size, per-image size, and batch item counts are all bounded.
- Security headers (CSP, nosniff, frame-deny) are set on every response.
- Errors returned to clients are generic; details are logged server-side only,
  and log lines never contain image bytes, brand names, or other application PII.

Endpoints:
  GET  /                 -> the single-page UI
  GET  /api/health       -> liveness + whether an API key is configured
  POST /api/verify       -> verify one label (image + form fields)
  POST /api/verify-batch -> verify many labels (images + a CSV mapping)
"""

import csv
import io
import logging
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .imaging import ImageError, normalize_image
from .models import ApplicationData, BatchItemResult, BatchResponse
from .verifier import verify_batch, verify_label

logger = logging.getLogger("label_verification")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Limits (all overridable via env).
MAX_REQUEST_BYTES = int(os.environ.get("LABEL_VERIFIER_MAX_REQUEST_BYTES", str(400 * 1024 * 1024)))
MAX_BATCH_IMAGES = int(os.environ.get("LABEL_VERIFIER_MAX_BATCH", "300"))
MAX_TEXT_FIELD_LEN = 500
VALID_BEVERAGE_TYPES = {"", "Distilled Spirits", "Wine", "Malt Beverage"}

CSP = (
    "default-src 'self'; "
    "img-src 'self' blob: data:; "
    "script-src 'self'; "
    "style-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

# Paths reachable without a session (the login flow, the login page's own assets,
# and health). Everything else requires a valid session cookie when the gate is on.
PUBLIC_PATHS = {
    "/login", "/api/login", "/api/logout", "/api/health",
    "/styles.css", "/app.js", "/login.js", "/favicon.ico",
}

# Per-client (IP) rate limits. Login is tight to blunt passcode brute-forcing.
_login_limiter = auth.FixedWindowLimiter(limit=10, window_seconds=300)
_verify_limiter = auth.FixedWindowLimiter(limit=40, window_seconds=60)

app = FastAPI(title="TTB Label Verification", version="1.0.0", docs_url=None, redoc_url=None)


def _apply_headers(response):
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    response.headers["Server"] = "label-verification"
    return response


@app.middleware("http")
async def security_and_limits(request: Request, call_next):
    # Reject oversized requests early, before buffering the whole body.
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                return _apply_headers(JSONResponse(status_code=413, content={"detail": "Request too large."}))
        except ValueError:
            return _apply_headers(JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header."}))

    # Access gate: require a valid session for everything but the public paths.
    path = request.url.path
    if auth.auth_enabled() and path not in PUBLIC_PATHS:
        token = request.cookies.get(auth.SESSION_COOKIE, "")
        if not auth.verify_session_token(token):
            if path.startswith("/api/"):
                return _apply_headers(JSONResponse(status_code=401, content={"detail": "Authentication required. Please sign in."}))
            return _apply_headers(RedirectResponse(url="/login", status_code=303))

    response = await call_next(request)
    return _apply_headers(response)


@app.get("/login")
async def login_page():
    return FileResponse(FRONTEND_DIR / "login.html")


@app.post("/api/login")
async def login(request: Request, passcode: str = Form(...)):
    if not _login_limiter.allow(auth.client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a minute and try again.")
    if not auth.check_passcode(passcode):
        raise HTTPException(status_code=401, detail="Incorrect access code.")
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        auth.SESSION_COOKIE,
        auth.create_session_token(),
        max_age=auth.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    return resp


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


def _clean_text(value: str, field: str) -> str:
    """Trim, cap length, and strip control characters from a text field."""
    value = (value or "").strip()
    if len(value) > MAX_TEXT_FIELD_LEN:
        raise HTTPException(status_code=400, detail=f"{field} is too long (max {MAX_TEXT_FIELD_LEN} characters).")
    # Drop control chars (keep normal printable text/newlines out entirely for single-line fields).
    return "".join(ch for ch in value if ch == " " or ch.isprintable())


def _application_from_fields(
    brand_name: str,
    beverage_type: str,
    class_type: str,
    alcohol_content: str,
    net_contents: str,
    producer_name_address: str,
    country_of_origin: str,
) -> ApplicationData:
    brand = _clean_text(brand_name, "Brand name")
    if not brand:
        raise HTTPException(status_code=400, detail="Brand name is required.")
    bev = _clean_text(beverage_type, "Beverage type")
    if bev not in VALID_BEVERAGE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid beverage type.")
    return ApplicationData(
        brand_name=brand,
        beverage_type=bev,  # type: ignore[arg-type]
        class_type=_clean_text(class_type, "Class/type"),
        alcohol_content=_clean_text(alcohol_content, "Alcohol content"),
        net_contents=_clean_text(net_contents, "Net contents"),
        producer_name_address=_clean_text(producer_name_address, "Producer name/address"),
        country_of_origin=_clean_text(country_of_origin, "Country of origin"),
    )


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


@app.post("/api/verify")
async def verify(
    request: Request,
    image: UploadFile = File(...),
    brand_name: str = Form(...),
    beverage_type: str = Form(""),
    class_type: str = Form(""),
    alcohol_content: str = Form(""),
    net_contents: str = Form(""),
    producer_name_address: str = Form(""),
    country_of_origin: str = Form(""),
):
    """Verify a single label."""
    if not _verify_limiter.allow(auth.client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down and try again shortly.")
    app_data = _application_from_fields(
        brand_name, beverage_type, class_type, alcohol_content,
        net_contents, producer_name_address, country_of_origin,
    )

    raw = await image.read()
    try:
        normalized, media_type = normalize_image(raw)
    except ImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        del raw  # release the untrusted bytes promptly

    try:
        result = await verify_label(normalized, media_type, app_data)
    except Exception as exc:  # noqa: BLE001
        logger.exception("single verify failed")  # full detail server-side only
        raise HTTPException(status_code=502, detail="Verification failed. Please try again.") from exc

    return result


@app.post("/api/verify-batch")
async def verify_batch_endpoint(
    request: Request,
    csv_file: UploadFile = File(...),
    images: list[UploadFile] = File(...),
):
    """Verify a batch of labels.

    Upload a CSV plus all the label images. The CSV maps each image filename to
    its application data. Required columns: `filename`, `brand_name`. Optional:
    `beverage_type`, `class_type`, `alcohol_content`, `net_contents`,
    `producer_name_address`, `country_of_origin`.
    """
    if not _verify_limiter.allow(auth.client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down and try again shortly.")
    if len(images) > MAX_BATCH_IMAGES:
        raise HTTPException(
            status_code=413,
            detail=f"Too many images in one batch (max {MAX_BATCH_IMAGES}). Please split into smaller batches.",
        )

    # Index and normalize the uploaded images by filename.
    images_by_name: dict[str, tuple[bytes, str] | str] = {}
    for upload in images:
        raw = await upload.read()
        try:
            images_by_name[upload.filename] = normalize_image(raw)
        except ImageError as exc:
            images_by_name[upload.filename] = f"__ERROR__{exc}"
        finally:
            del raw

    # Parse the CSV.
    csv_bytes = await csv_file.read()
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded.") from exc
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames or []
    if "filename" not in fieldnames or "brand_name" not in fieldnames:
        raise HTTPException(
            status_code=400,
            detail="CSV must have at least 'filename' and 'brand_name' columns.",
        )

    items = []
    upfront_errors: list[BatchItemResult] = []
    seen_filenames: set[str] = set()

    for row in reader:
        filename = (row.get("filename") or "").strip()
        if not filename:
            continue
        if filename in seen_filenames:
            upfront_errors.append(BatchItemResult(filename=filename, error="Duplicate row for this filename in the CSV."))
            continue
        seen_filenames.add(filename)

        entry = images_by_name.get(filename)
        if entry is None:
            upfront_errors.append(BatchItemResult(filename=filename, error="No matching image uploaded for this row."))
            continue
        if isinstance(entry, str):  # normalization error sentinel
            upfront_errors.append(BatchItemResult(filename=filename, error=entry.removeprefix("__ERROR__")))
            continue

        try:
            app_data = _application_from_fields(
                row.get("brand_name", ""), row.get("beverage_type", ""), row.get("class_type", ""),
                row.get("alcohol_content", ""), row.get("net_contents", ""),
                row.get("producer_name_address", ""), row.get("country_of_origin", ""),
            )
        except HTTPException as exc:
            upfront_errors.append(BatchItemResult(filename=filename, error=str(exc.detail)))
            continue

        normalized, media_type = entry
        items.append((filename, normalized, media_type, app_data))

    # Flag images uploaded but never referenced in the CSV.
    for name, entry in images_by_name.items():
        if name not in seen_filenames:
            msg = entry.removeprefix("__ERROR__") if isinstance(entry, str) else "Image uploaded but not listed in the CSV."
            upfront_errors.append(BatchItemResult(filename=name, error=msg))

    if not items and not upfront_errors:
        raise HTTPException(status_code=400, detail="No valid rows to verify.")

    verified = await verify_batch(items)
    results = [BatchItemResult(filename=f, result=r, error=e) for (f, r, e) in verified]
    results.extend(upfront_errors)

    passed = sum(1 for r in results if r.result and r.result.overall_status == "pass")
    failed = sum(1 for r in results if r.result and r.result.overall_status == "fail")
    review = sum(1 for r in results if r.result and r.result.overall_status == "needs_review")
    errored = sum(1 for r in results if r.error)

    return BatchResponse(
        total=len(results),
        passed=passed,
        failed=failed,
        needs_review=review,
        errored=errored,
        results=results,
    )


# --- Static frontend ---


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


# Mounted last so it never shadows the API routes above.
app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")
