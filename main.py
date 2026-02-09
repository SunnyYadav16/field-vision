"""
FieldVision - Industrial Safety Assistant
FastAPI Main Application
"""

import os
from pathlib import Path
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, WebSocket, Depends, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from app.auth import (
    authenticate_user, create_token, get_current_user,
    get_ws_user, has_permission, verify_token
)

from app.config import get_settings
from app.websocket_handler import get_connection_manager
from app.audit import get_audit_logger
from app.work_orders import (
    get_pending_orders, get_all_orders, approve_pending_order,
    get_approved_orders, get_completed_orders, complete_order
)

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


# ── Login Endpoint ──
@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    user_id = body.get("user_id", "")
    password = body.get("password", "")

    user = authenticate_user(user_id, password)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid credentials"}
        )

    token = create_token(user_id, user)
    return {
        "token": token,
        "user": {
            "id": user_id,
            "name": user["name"],
            "role": user["role"],
            "zone": user["zone"],
            "permissions": user["permissions"]
        }
    }


# ── Get Current User Info ──
@app.get("/api/me")
async def get_me(user: dict = Depends(get_current_user)):
    return user


# ── Serve Login Page (no auth needed) ──
@app.get("/login")
async def login_page():
    login_path = static_dir / "login.html"
    if login_path.exists():
        return FileResponse(login_path)
    return JSONResponse({"error": "Login page not found"}, status_code=404)


# ── Serve Main App (auth checked client-side) ──
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


@app.get("/manager")
async def manager_page():
    manager_path = static_dir / "manager.html"
    if manager_path.exists():
        return FileResponse(manager_path)
    return JSONResponse({"error": "Manager page not found"}, status_code=404)


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


@app.get("/api/audit/logs")
async def get_audit_logs():
    """Get summarized audit history for all sessions"""
    audit_logger = get_audit_logger()
    sessions = audit_logger.get_all_sessions()
    return {"sessions": sessions, "total_sessions": len(sessions)}


@app.get("/api/reports/{session_id}")
async def get_session_report(session_id: str):
    """Generate HTML report for session"""
    from fastapi.responses import HTMLResponse
    from app.reporting import AuditReporter
    
    audit_logger = get_audit_logger()
    reporter = AuditReporter(audit_logger)
    html = await reporter.generate_session_report(session_id)
    return HTMLResponse(content=html, status_code=200)


# ── Work Orders API ──
@app.get("/api/work-orders")
async def list_work_orders(user: dict = Depends(get_current_user)):
    """List work orders - supervisors see all, technicians see their own"""
    if has_permission(user, "approve_work_order"):
        return {
            "pending": get_pending_orders(),
            "approved": get_approved_orders(),
            "completed": get_completed_orders()
        }
    # Technicians see only their own
    all_orders = get_all_orders() + get_pending_orders()
    my_orders = [
        o for o in all_orders
        if o["requested_by"]["id"] == user["user_id"]
    ]
    return {"my_orders": my_orders}


@app.post("/api/work-orders/{order_id}/approve")
async def approve_order(
    order_id: str,
    user: dict = Depends(get_current_user)
):
    """Approve a pending work order - supervisors/managers only"""
    if not has_permission(user, "approve_work_order"):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to approve work orders"
        )
    order = approve_pending_order(order_id)
    if order:
        return {"status": "approved", "order": order}
    raise HTTPException(
        status_code=404, detail="Order not found"
    )


@app.post("/api/work-orders/{order_id}/complete")
async def mark_order_complete(
    order_id: str,
    user: dict = Depends(get_current_user)
):
    """Mark an approved work order as completed - supervisors/managers only"""
    if not has_permission(user, "approve_work_order"):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to complete work orders"
        )
    order = complete_order(order_id)
    if order:
        return {"status": "completed", "order": order}
    raise HTTPException(
        status_code=404, detail="Order not found or not in approved state"
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time communication with auth"""
    # ── Auth Gate ──
    user = await get_ws_user(websocket)
    if user is None:
        return  # Connection was rejected in get_ws_user

    # Store user context for this session
    session_user = {
        "user_id": user["user_id"],
        "name": user["name"],
        "role": user["role"],
        "zone": user["zone"],
        "permissions": user["permissions"]
    }

    # Accept connection and pass user context
    manager = get_connection_manager()
    connection = await manager.connect(websocket, session_user)
    
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
