import pytest


@pytest.fixture(autouse=True)
def api_test_token(monkeypatch):
    """Isolate all tests from real production credentials."""
    monkeypatch.setenv("BACKYARD_API_TOKEN", "backyard-test-token")
