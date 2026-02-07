"""
FieldVision - Industrial Safety Assistant
FastAPI Main Application
"""

import os
from pathlib import Path
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from app.config import get_settings
from app.websocket_handler import get_connection_manager
from app.audit import get_audit_logger

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    settings = get_settings()
    logger.info(
        "fieldvision_starting",
        host=settings.host,
        port=settings.port,
        model=settings.gemini_model
    )
    
    # Ensure logs directory exists
    Path(settings.audit_log_path).parent.mkdir(parents=True, exist_ok=True)
    
    yield
    
    logger.info("fieldvision_shutdown")


# Create FastAPI app
app = FastAPI(
    title="FieldVision",
    description="AI-Powered Industrial Safety Assistant",
    version="1.0.0",
    lifespan=lifespan
)

# Mount static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def root():
    """Serve the main application page"""
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse(
        {"error": "Frontend not found"},
        status_code=404
    )


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "FieldVision",
        "version": "1.0.0"
    }


@app.get("/api/session/{session_id}/summary")
async def get_session_summary(session_id: str):
    """Get audit summary for a session"""
    audit_logger = get_audit_logger()
    summary = await audit_logger.get_session_summary(session_id)
    return summary


@app.get("/api/session/{session_id}/events")
async def get_session_events(session_id: str):
    """Get all events for a session"""
    audit_logger = get_audit_logger()
    events = audit_logger.get_session_events(session_id)
    return {"session_id": session_id, "events": [e.to_dict() for e in events]}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time communication"""
    manager = get_connection_manager()
    connection = await manager.connect(websocket)
    
    try:
        await connection.handle_messages()
    finally:
        await manager.disconnect(connection.connection_id)


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )
