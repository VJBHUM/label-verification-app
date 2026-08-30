"""Tests for the access gate: passcode, session tokens, and rate limiting."""

from backend import auth


def test_passcode_check(monkeypatch):
    monkeypatch.setattr(auth, "ACCESS_CODE", "secret")
    assert auth.check_passcode("secret")
    assert not auth.check_passcode("wrong")
    assert not auth.check_passcode("")


def test_session_token_roundtrip():
    token = auth.create_session_token()
    assert auth.verify_session_token(token)


def test_tampered_token_rejected():
    token = auth.create_session_token()
    assert not auth.verify_session_token(token + "x")
    assert not auth.verify_session_token("garbage")
    assert not auth.verify_session_token("")


def test_expired_token_rejected():
    assert not auth.verify_session_token(auth.create_session_token(ttl=-1))


def test_rate_limiter_windows_and_keys():
    rl = auth.FixedWindowLimiter(limit=3, window_seconds=60)
    assert rl.allow("ip-a")
    assert rl.allow("ip-a")
    assert rl.allow("ip-a")
    assert not rl.allow("ip-a")     # 4th blocked
    assert rl.allow("ip-b")         # a different client is unaffected
