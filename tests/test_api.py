"""End-to-end tests for the HTTP layer via Starlette's TestClient.

None of these make a live model call — they exercise the access gate, security
headers, validation, and error handling.
"""

import io

import pytest
from PIL import Image
from starlette.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (80, 80)).save(buf, format="PNG")
    return buf.getvalue()


def test_health_is_public_and_sets_security_headers(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert "Content-Security-Policy" in r.headers
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_root_redirects_to_login_when_unauthenticated(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_api_requires_authentication(client):
    r = client.post("/api/verify", data={"brand_name": "X"},
                    files={"image": ("x.png", _png(), "image/png")})
    assert r.status_code == 401


def test_login_rejects_wrong_code(client):
    assert client.post("/api/login", data={"passcode": "nope"}).status_code == 401


def test_login_then_app_is_accessible(client):
    assert client.post("/api/login", data={"passcode": "test-code"}).status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 200


def test_missing_brand_is_rejected(client):
    client.post("/api/login", data={"passcode": "test-code"})
    r = client.post("/api/verify", files={"image": ("x.png", _png(), "image/png")})
    assert r.status_code == 422


def test_invalid_image_rejected_before_model(client):
    client.post("/api/login", data={"passcode": "test-code"})
    r = client.post("/api/verify", data={"brand_name": "X"},
                    files={"image": ("x.png", b"not-an-image", "image/png")})
    assert r.status_code == 400


def test_batch_requires_valid_csv_columns(client):
    client.post("/api/login", data={"passcode": "test-code"})
    r = client.post("/api/verify-batch", files=[
        ("csv_file", ("a.csv", b"wrong,cols\n1,2", "text/csv")),
        ("images", ("x.png", _png(), "image/png")),
    ])
    assert r.status_code == 400


def test_login_brute_force_is_rate_limited(client):
    statuses = [client.post("/api/login", data={"passcode": "x"}).status_code for _ in range(12)]
    assert 429 in statuses


def test_static_assets_served(client):
    for path in ("/styles.css", "/app.js", "/login.js"):
        assert client.get(path).status_code == 200
