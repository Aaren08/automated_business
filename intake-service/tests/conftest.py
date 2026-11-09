"""Pytest configuration and fixtures."""
import pytest
from httpx import AsyncClient, ASGITransport
from src.main import app


@pytest.fixture
async def async_client():
    """Async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def api_headers():
    """Headers with valid API key."""
    from src.config import settings
    return {"X-API-Key": settings.INTAKE_API_KEY}