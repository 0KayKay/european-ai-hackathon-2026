import pytest

from hackathon.config import get_settings


@pytest.fixture(autouse=True)
def test_settings(monkeypatch):
    for key, value in {
        "AGENTGATEWAY_URL": "http://localhost:4000/v1",
        "AGENTGATEWAY_API_KEY": "test",
        "GOOGLE_STORAGE_BUCKET": "",
        "PRETTY_DOMAIN": "localhost",
        "DB_USER": "app",
        "DB_PASSWORD": "devpassword",
        "DB_NAME": "app",
        "PHOENIX_OTLP_ENDPOINT": "",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
