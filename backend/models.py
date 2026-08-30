"""Pydantic models for label verification requests and results.

Field coverage follows the TTB mandatory label elements (27 CFR parts 4, 5, 7
and the health-warning rule in part 16): brand name, class/type, alcohol
content, net contents, name/address of the bottler or producer, country of
origin for imports, and the Government Health Warning.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

BeverageType = Literal["", "Distilled Spirits", "Wine", "Malt Beverage"]


class ApplicationData(BaseModel):
    """The information the producer filed in their COLA application.

    This is what the label artwork is checked *against*. Only ``brand_name`` is
    strictly required; every other field is checked only when it is provided.
    """

    brand_name: str = Field(..., description="Brand name as filed")
    beverage_type: BeverageType = Field("", description="Distilled Spirits / Wine / Malt Beverage")
    class_type: str = Field("", description="Class/type designation, e.g. 'Kentucky Straight Bourbon Whiskey'")
    alcohol_content: str = Field("", description="Alcohol content as filed, e.g. '45% Alc./Vol. (90 Proof)'")
    net_contents: str = Field("", description="Net contents as filed, e.g. '750 mL'")
    producer_name_address: str = Field("", description="Name and address of the bottler/producer/importer")
    country_of_origin: str = Field("", description="Country of origin (required for imports)")


class FieldCheck(BaseModel):
    """Result of comparing one application field to the label."""

    field_name: str = Field(..., description="Human-readable field name")
    expected_value: str = Field(..., description="Value from the application")
    found_on_label: str = Field(..., description="Value read from the label ('' if not found)")
    status: Literal["match", "mismatch", "missing"]
    explanation: str = Field(..., description="Short reason for the verdict")


class GovernmentWarningCheck(BaseModel):
    """Result of the mandatory Government Health Warning check.

    The strict one. Per 27 CFR 16.21/16.22 the statement must be the exact
    mandated text, the words 'GOVERNMENT WARNING' must appear in capital letters
    and bold, and the warning must be legible (readily legible, not buried in
    tiny type). Any deviation fails.
    """

    present: bool = Field(..., description="Is a government warning present at all?")
    header_all_caps: bool = Field(..., description="Is 'GOVERNMENT WARNING' in all capital letters?")
    header_bold: bool = Field(..., description="Do the words 'GOVERNMENT WARNING' appear in bold?")
    text_matches_exactly: bool = Field(..., description="Does the full statement match the required text word-for-word?")
    legible: bool = Field(..., description="Is the warning a legible size, not buried in tiny/obscured text?")
    status: Literal["pass", "fail"]
    found_text: str = Field(..., description="The warning text as read from the label ('' if none)")
    issues: List[str] = Field(..., description="Specific problems found (empty if it passes)")


class LabelVerification(BaseModel):
    """Full verification result for a single label."""

    overall_status: Literal["pass", "fail", "needs_review"]
    summary: str = Field(..., description="One-sentence plain-language summary for the agent")
    field_checks: List[FieldCheck]
    government_warning: GovernmentWarningCheck
    image_quality_ok: bool = Field(..., description="Was the image clear enough to read confidently?")
    image_quality_note: str = Field(..., description="Note on angle/glare/lighting issues, '' if fine")


class BatchItemResult(BaseModel):
    """One row in a batch run: the filename, plus its result or an error."""

    filename: str
    result: Optional[LabelVerification] = None
    error: Optional[str] = None


class BatchResponse(BaseModel):
    """Aggregate response for a batch run."""

    total: int
    passed: int
    failed: int
    needs_review: int
    errored: int
    results: List[BatchItemResult]
