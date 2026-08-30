"""Unit tests for the deterministic verdict logic (`_finalize`).

The model reads the label; the pass/fail roll-up is pure logic computed in code.
These tests pin that logic exactly — no API calls involved.
"""

from backend.models import FieldCheck, GovernmentWarningCheck, LabelVerification
from backend.verifier import _finalize


def _gw(present=True, caps=True, bold=True, exact=True, legible=True):
    return GovernmentWarningCheck(
        present=present, header_all_caps=caps, header_bold=bold,
        text_matches_exactly=exact, legible=legible,
        status="pass", found_text="", issues=[],
    )


def _field(status, name="Brand name"):
    return FieldCheck(field_name=name, expected_value="X", found_on_label="X",
                      status=status, explanation="")


def _lv(fields, gw, image_ok=True):
    return LabelVerification(
        overall_status="pass", summary="", field_checks=fields,
        government_warning=gw, image_quality_ok=image_ok, image_quality_note="",
    )


def test_all_good_is_pass():
    r = _finalize(_lv([_field("match")], _gw()))
    assert r.government_warning.status == "pass"
    assert r.overall_status == "pass"


def test_titlecase_header_fails():
    r = _finalize(_lv([_field("match")], _gw(caps=False)))
    assert r.government_warning.status == "fail"
    assert r.overall_status == "fail"


def test_reworded_text_fails():
    assert _finalize(_lv([_field("match")], _gw(exact=False))).overall_status == "fail"


def test_illegible_warning_fails():
    assert _finalize(_lv([_field("match")], _gw(legible=False))).overall_status == "fail"


def test_absent_warning_fails():
    assert _finalize(_lv([_field("match")], _gw(present=False))).overall_status == "fail"


def test_field_mismatch_fails():
    assert _finalize(_lv([_field("mismatch")], _gw())).overall_status == "fail"


def test_non_bold_header_routes_to_review():
    r = _finalize(_lv([_field("match")], _gw(bold=False)))
    assert r.government_warning.status == "pass"   # not a hard failure
    assert r.overall_status == "needs_review"      # but a human should confirm bold


def test_missing_field_routes_to_review():
    assert _finalize(_lv([_field("missing")], _gw())).overall_status == "needs_review"


def test_poor_image_routes_to_review():
    assert _finalize(_lv([_field("match")], _gw(), image_ok=False)).overall_status == "needs_review"


def test_hard_failure_outranks_review():
    # A mismatch is a hard fail even when a non-bold header would only be review.
    assert _finalize(_lv([_field("mismatch")], _gw(bold=False))).overall_status == "fail"
