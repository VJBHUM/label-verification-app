"""Core label-verification logic, powered by a vision-language model.

A normalized image plus the application data go in; a structured
``LabelVerification`` comes back. The model performs the OCR, the judgment-based
field matching, and the strict Government Health Warning check in a single call,
returning schema-validated JSON (structured outputs) so nothing downstream ever
string-parses model text.
"""

import asyncio
import base64
import os

from anthropic import AsyncAnthropic

from .models import ApplicationData, LabelVerification

# The mandatory TTB Government Health Warning (27 CFR 16.21). Word-for-word.
GOVERNMENT_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)

# Haiku 4.5 meets the hard ~5-second interactive target and, in testing, reliably
# catches the checks that matter (all-caps + exact warning text, brand/ABV
# mismatches). Override with LABEL_VERIFIER_MODEL=claude-sonnet-4-6 or
# claude-opus-4-8 for higher-stakes review (more thorough, slower, more likely to
# route borderline calls to needs_review).
MODEL = os.environ.get("LABEL_VERIFIER_MODEL", "claude-haiku-4-5")

# Bound how long a single verification may take so a hung request can't wedge a
# worker; the client also retries transient network/5xx errors once.
REQUEST_TIMEOUT_S = float(os.environ.get("LABEL_VERIFIER_TIMEOUT_S", "30"))
MAX_RETRIES = int(os.environ.get("LABEL_VERIFIER_MAX_RETRIES", "1"))

# Parallel labels per batch run.
BATCH_CONCURRENCY = int(os.environ.get("LABEL_VERIFIER_CONCURRENCY", "8"))
# Global ceiling on concurrent model calls across the whole process — protects
# memory and the upstream API rate limit regardless of how many requests arrive.
GLOBAL_CONCURRENCY = int(os.environ.get("LABEL_VERIFIER_GLOBAL_CONCURRENCY", "24"))

_global_semaphore = asyncio.Semaphore(GLOBAL_CONCURRENCY)

# Structured output via a forced tool call: the model must call this tool, and its
# input is validated against the LabelVerification schema. This is portable across
# SDK/model versions and yields clean, typed JSON with no free-text parsing.
_RESULT_TOOL = {
    "name": "report_label_verification",
    "description": "Report the structured result of verifying the label against the application data.",
    "input_schema": LabelVerification.model_json_schema(),
}

SYSTEM_PROMPT = f"""You are a TTB (Alcohol and Tobacco Tax and Trade Bureau) label \
compliance reviewer. Your job is to verify that the artwork on an alcohol beverage label \
matches the information a producer filed in their COLA application, and that the label \
carries a compliant Government Health Warning.

You receive an image of a label and the application data. For each provided field, read what \
is actually printed on the label and compare it to the application. Only check fields that are \
provided in the application data; if a field is blank in the application, do not include it.

FIELD MATCHING RULES:
- Brand name, class/type, net contents, producer name/address, country of origin: use \
JUDGMENT, not literal string matching. Differences in capitalization, punctuation, spacing, \
line breaks, or obviously equivalent formatting are a MATCH (e.g. "STONE'S THROW" on the label \
vs "Stone's Throw" in the application is a match). A genuinely different value is a mismatch.
- Alcohol content: compare the actual value, not the formatting. "45% Alc./Vol." matches \
"45% Alc./Vol. (90 Proof)" because 45% ABV equals 90 proof. Different numbers are a mismatch. \
Note: alcohol content is not required on every label — some wines and malt beverages may omit \
it. If alcohol content is provided in the application but does not appear on the label, mark it \
"missing" (do not invent a value). If it appears on the label and matches, "match".
- Country of origin is required for imported products. If the application lists a country of \
origin and the label omits it, mark it "missing".
- If a field is provided in the application but you cannot find it on the label, mark it \
"missing" and explain why.

GOVERNMENT HEALTH WARNING — STRICT. Evaluate these independently:
- present: is a government warning on the label at all?
- header_all_caps: the words "GOVERNMENT WARNING" appear in ALL CAPITAL LETTERS.
- text_matches_exactly: the full statement is WORD-FOR-WORD the following text:
{GOVERNMENT_WARNING}
- legible: a reasonable type size, not shrunk to tiny print and not obscured or buried.
- header_bold: the words "GOVERNMENT WARNING" appear in bold. Report your best visual assessment. \
Bold is genuinely hard to judge from a photo, so treat it as advisory (see OVERALL STATUS).

Set the warning `status` to "fail" if it is absent, not in all caps, not word-for-word exact, or \
illegible/tiny. If those all pass, set `status` to "pass" even when bold is uncertain. List concrete \
problems in `issues`. Leave `found_text` EMPTY when the warning passes; populate it with the text you \
read only when the warning fails.

IMAGE QUALITY: If the photo is at an angle, has glare, is blurry, or is poorly lit, still do \
your best to read it, but set image_quality_ok=false and describe the problem. If a specific \
field is unreadable because of image quality, mark that field "missing" and say so.

OUTPUT BREVITY (for speed): keep every `explanation` to 8 words or fewer. Do not restate values \
already in expected_value/found_on_label. Keep `summary` to one sentence.

OVERALL STATUS:
- "pass": every provided field matches AND the government warning passes (including a clearly bold header).
- "fail": any provided field is a clear mismatch, OR the government warning fails (absent, not all \
caps, not exact, or illegible).
- "needs_review": nothing is a clear-cut failure, but you could not verify confidently — the image \
is too poor to read a field, OR the warning is fully compliant EXCEPT that the header does not appear \
clearly bold (a human should confirm bold rather than auto-reject).

Be precise and conservative. A compliance agent relies on your verdict; never guess a value that \
is not clearly present on the label."""


_client_instance: AsyncAnthropic | None = None


def _client() -> AsyncAnthropic:
    # A single shared client keeps the HTTPS connection pool warm across
    # requests, avoiding a fresh TLS handshake on every verification. Credentials
    # and ANTHROPIC_BASE_URL are resolved from the environment on first use.
    global _client_instance
    if _client_instance is None:
        _client_instance = AsyncAnthropic(
            timeout=REQUEST_TIMEOUT_S,
            max_retries=MAX_RETRIES,
        )
    return _client_instance


def _build_user_prompt(app: ApplicationData) -> str:
    lines = ["Verify this label against the following application data.", ""]
    lines.append(f"- Brand name: {app.brand_name}")
    if app.beverage_type:
        lines.append(f"- Beverage type: {app.beverage_type}")
    if app.class_type:
        lines.append(f"- Class/type: {app.class_type}")
    if app.alcohol_content:
        lines.append(f"- Alcohol content: {app.alcohol_content}")
    if app.net_contents:
        lines.append(f"- Net contents: {app.net_contents}")
    if app.producer_name_address:
        lines.append(f"- Name/address of bottler or producer: {app.producer_name_address}")
    if app.country_of_origin:
        lines.append(f"- Country of origin: {app.country_of_origin}")
    lines += [
        "",
        "The Government Health Warning is mandatory on every label — check it even though it "
        "is not part of the application data above.",
    ]
    return "\n".join(lines)


async def verify_label(
    image_bytes: bytes,
    media_type: str,
    app: ApplicationData,
    model: str = MODEL,
) -> LabelVerification:
    """Verify a single (already-normalized) label image against application data."""
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    async with _global_semaphore:
        response = await _client().messages.create(
            model=model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": _build_user_prompt(app)},
                    ],
                }
            ],
            tools=[_RESULT_TOOL],
            tool_choice={"type": "tool", "name": _RESULT_TOOL["name"]},
        )

    for block in response.content:
        if block.type == "tool_use":
            return LabelVerification.model_validate(block.input)
    # Safety-classifier refusal or no tool call — never leak a raw object.
    raise RuntimeError("The label could not be verified (no structured result returned).")


async def verify_batch(items, concurrency: int = BATCH_CONCURRENCY):
    """Verify many labels concurrently.

    ``items`` is an iterable of ``(filename, image_bytes, media_type, ApplicationData)``.
    Returns a list of ``(filename, LabelVerification | None, error_str | None)`` in the
    same order as the input. A single failing item never sinks the batch.
    """
    items = list(items)
    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(filename, image_bytes, media_type, app):
        async with semaphore:
            try:
                result = await verify_label(image_bytes, media_type, app)
                return (filename, result, None)
            except Exception as exc:  # noqa: BLE001 — isolate per-item failures
                return (filename, None, _safe_error(exc))

    tasks = [run_one(*item) for item in items]
    return await asyncio.gather(*tasks)


def _safe_error(exc: Exception) -> str:
    """A short, user-safe error string that never leaks internals or secrets."""
    name = type(exc).__name__
    if "timeout" in name.lower():
        return "The verification timed out. Please try again."
    if "RateLimit" in name:
        return "The service is busy (rate limited). Please retry shortly."
    if "Authentication" in name or "Permission" in name:
        return "The verification service is not configured correctly."
    return "The label could not be verified due to a service error."
