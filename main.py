"""
FieldVision - Industrial Safety Assistant
FastAPI Main Application
"""

import os
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import structlog
from fastapi import FastAPI, WebSocket, Depends, Request, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.auth import (
    authenticate_user, create_token, get_current_user,
    get_ws_user, has_permission, verify_token
)

from app.config import get_settings
from app.websocket_handler import get_connection_manager, active_camera_feeds
from app.audit import get_audit_logger
from app.work_orders import (
    get_pending_orders, get_all_orders, approve_pending_order,
    get_approved_orders, get_completed_orders, complete_order
)
from app.report_generator import generate_work_orders_report

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


# ── Camera Feeds for Manager Dashboard ──
@app.get("/api/camera-feeds")
async def list_camera_feeds(user: dict = Depends(get_current_user)):
    """List all active technician camera feeds - managers/supervisors only"""
    if not has_permission(user, "approve_work_order"):
        raise HTTPException(status_code=403, detail="Not authorized")
    
    feeds = []
    for user_id, feed_data in active_camera_feeds.items():
        feeds.append({
            "user_id": user_id,
            "name": feed_data.get("name", "Unknown"),
            "zone": feed_data.get("zone", "unknown"),
            "role": feed_data.get("role", "technician"),
            "has_frame": feed_data.get("frame") is not None
        })
    return {"feeds": feeds, "count": len(feeds)}


@app.get("/api/camera-feeds/{user_id}/frame")
async def get_camera_frame(user_id: str, user: dict = Depends(get_current_user)):
    """Get latest camera frame for a specific technician"""
    if not has_permission(user, "approve_work_order"):
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if user_id not in active_camera_feeds:
        raise HTTPException(status_code=404, detail="Feed not found")
    
    frame_bytes = active_camera_feeds[user_id].get("frame")
    if not frame_bytes:
        raise HTTPException(status_code=404, detail="No frame available")
    
    return StreamingResponse(
        iter([frame_bytes]),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


@app.get("/api/reports/work-orders")
async def get_work_orders_report(
    start: str = Query(..., description="Start date ISO format"),
    end: str = Query(..., description="End date ISO format"),
    user: dict = Depends(get_current_user)
):
    """Generate PDF report of work orders in date range"""
    if not has_permission(user, "approve_work_order"):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to generate reports"
        )
    
    try:
        start_date = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
        end_date = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    pdf_bytes = generate_work_orders_report(start_date, end_date)
    
    filename = f"work_orders_report_{start[:10]}_to_{end[:10]}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/api/reports/{session_id}")
async def get_session_report(session_id: str):
    """Generate HTML report for session"""
    from fastapi.responses import HTMLResponse
    from app.reporting import AuditReporter
    
    audit_logger = get_audit_logger()
    reporter = AuditReporter(audit_logger)
    html = await reporter.generate_session_report(session_id)
    return HTMLResponse(content=html, status_code=200)


@app.get("/api/reports/site-wide-summary")
async def get_site_wide_summary(hours: int = Query(24, description="Time window in hours")):
    """Get summarized activity across all sessions for the dashboard"""
    audit_logger = get_audit_logger()
    all_sessions = audit_logger.get_all_sessions()
    
    # Filter by time
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    
    recent_sessions = [
        s for s in all_sessions 
        if datetime.fromisoformat(s["start_time"]) > cutoff
    ]
    
    summary = {
        "window_hours": hours,
        "total_sessions": len(recent_sessions),
        "total_hazards": sum(s["event_count"] for s in recent_sessions),
        "critical_interventions": sum(s["critical_events"] for s in recent_sessions),
        "active_zones": list(set(s.get("zone", "Zone A") for s in recent_sessions)) # Mock zone if missing
    }
    
    return summary


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
