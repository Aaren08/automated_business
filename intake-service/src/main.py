"""Main FastAPI application entrypoint."""
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from src.config import settings
from src.api.routes import router as api_router
from src.api.auth import verify_api_key

# Configure structured logging
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(__import__("logging"), settings.LOG_LEVEL)
    ),
)

logger = structlog.get_logger()

app = FastAPI(
    title="Product Intelligence Intake Service",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# API Key middleware
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Verify API key on all routes except health check."""
    if request.url.path in ["/healthz", "/docs", "/redoc", "/openapi.json"]:
        return await call_next(request)
    
    try:
        verify_api_key(request)
    except Exception as e:
        logger.warning("auth_failed", path=request.url.path, error=str(e))
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )
    
    return await call_next(request)


@app.get("/healthz", tags=["health"])
async def health_check():
    """Liveness/readiness probe."""
    return {"status": "ok", "service": "product-intake", "version": "0.1.0"}


app.include_router(api_router, prefix="/api/v1")


@app.on_event("startup")
async def startup():
    logger.info("service_starting", version="0.1.0")


@app.on_event("shutdown")
async def shutdown():
    logger.info("service_stopping")