"""API Key authentication."""
from fastapi import HTTPException, Request
from src.config import settings


def verify_api_key(request: Request) -> None:
    """Verify the X-API-Key header matches INTAKE_API_KEY."""
    api_key = request.headers.get("X-API-Key")
    if not api_key or api_key != settings.INTAKE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")