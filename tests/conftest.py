"""Test configuration.

Sets a known access code/secret before the app is imported (the auth module
reads them at import time), and resets the in-process rate limiters between
tests so login/verify limits don't leak across cases.
"""

import os

os.environ.setdefault("APP_ACCESS_CODE", "test-code")
os.environ.setdefault("APP_SECRET_KEY", "0" * 64)
# Keep the model unset for tests; nothing here makes a live API call.
os.environ.pop("ANTHROPIC_API_KEY", None)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    from backend import main

    main._login_limiter._hits.clear()
    main._verify_limiter._hits.clear()
    yield
