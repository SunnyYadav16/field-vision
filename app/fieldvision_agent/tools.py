import asyncio
import json
import structlog
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = structlog.get_logger(__name__)

# Module-level storage for latest video frames per session (for evidence capture)
_latest_frames: dict[str, bytes] = {}


def set_latest_frame(session_id: str, frame_data: bytes) -> None:
    """Store the latest video frame for a session (called from upstream task)."""
    _latest_frames[session_id] = frame_data


def clear_session_frame(session_id: str) -> None:
    """Clean up frame data when session ends."""
    _latest_frames.pop(session_id, None)


def _save_evidence_sync(session_id: str, frame_data: bytes) -> Optional[str]:
    """Save a video frame as evidence for a safety event."""
    try:
        evidence_dir = Path("static/evidence")
        evidence_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"evidence_{session_id}_{timestamp}.jpg"
        filepath = evidence_dir / filename
        
        with open(filepath, "wb") as f:
            f.write(frame_data)
        
        logger.info("evidence_captured", path=str(filepath), session_id=session_id)
        return f"/static/evidence/{filename}"
    except Exception as e:
        logger.error("evidence_capture_failed", error=str(e), session_id=session_id)
        return None


def log_safety_event(event_type: str, severity: int, description: str, tool_context=None) -> dict:
    """
    Log a safety observation, hazard detection, or compliance event to the audit trail.

    Args:
        event_type: Type of safety event. One of: missing_ppe, hazard_detected,
                    unsafe_position, procedure_violation, equipment_issue,
                    environment_hazard, step_verified, safety_check_passed
        severity: Severity level 1-5 (1=info, 2=low, 3=medium, 4=high, 5=critical)
        description: Detailed description of the observation
        tool_context: ADK ToolContext (auto-injected by framework)

    Returns:
        dict with status and event details
    """
    session_id = "unknown"
    if tool_context and hasattr(tool_context, 'state'):
        session_id = tool_context.state.get("session_id", "unknown")

    # Capture visual evidence for severity >= 3
    evidence_url = None
    frame_data = _latest_frames.get(session_id)
    if frame_data and severity >= 3:
        evidence_url = _save_evidence_sync(session_id, frame_data)

    # Log to audit trail
    from app.audit import get_audit_logger
    audit_logger = get_audit_logger()

    # Use sync wrapper since ADK tools run in sync context
    event_data = {
        "session_id": session_id,
        "event_type": event_type,
        "severity": severity,
        "description": description,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "evidence_url": evidence_url,
        "source": "ai"
    }

    # Write to audit log file directly (sync)
    try:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"audit_{session_id}.json"

        existing = []
        if log_file.exists():
            with open(log_file) as f:
                existing = json.load(f)

        existing.append(event_data)
        with open(log_file, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        logger.error("audit_log_write_failed", error=str(e))

    # Also log to conversation transcript
    try:
        from app.conversation_logger import conversation_logger
        # Fire and forget in background
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(conversation_logger.log_interaction(session_id, {
                "speaker": "SYSTEM",
                "type": "tool_call",
                "content": f"log_safety_event: {event_type}",
                "metadata": {"event_type": event_type, "severity": severity, "description": description}
            }))
    except Exception:
        pass

    logger.info("safety_event_logged",
                event_type=event_type,
                severity=severity,
                session_id=session_id,
                evidence=bool(evidence_url))

    return {
        "status": "logged",
        "event_type": event_type,
        "severity": severity,
        "evidence_captured": bool(evidence_url),
        "message": f"Safety event '{event_type}' logged successfully"
    }


def create_work_order(equipment_id: str, priority: str, description: str, tool_context=None) -> dict:
    """
    Creates a maintenance work order when the technician requests one via voice.
    IMPORTANT: After this tool is called, you MUST ask the technician to show their
    ID badge to the camera before the order can be processed. Do NOT confirm the
    order until badge verification is complete via the verify_badge tool.

    Args:
        equipment_id: The equipment name or ID
        priority: Priority level - one of: low, medium, high, critical
        description: Description of the issue
        tool_context: ADK ToolContext (auto-injected by framework)

    Returns:
        dict indicating badge verification is required
    """
    # Store pending work order in session state via ToolContext
    if tool_context and hasattr(tool_context, 'state'):
        tool_context.state["pending_work_order"] = {
            "equipment_id": equipment_id,
            "priority": priority,
            "description": description,
        }
        session_id = tool_context.state.get("session_id", "unknown")
    else:
        session_id = "unknown"

    logger.info("work_order_requested",
                equipment=equipment_id,
                priority=priority,
                session_id=session_id)

    return {
        "status": "badge_verification_required",
        "message": (
            "Work order request received. Badge verification "
            "is required before processing. Please ask the "
            "technician to hold their employee ID badge up "
            "to the camera."
        )
    }


def verify_badge(employee_name: str, employee_id: str, department: str = "", tool_context=None) -> dict:
    """
    Verifies an employee ID badge seen in the video feed. Call this when you
    can see a badge being held up to the camera. Extract the employee name,
    ID number, and department visible on the badge.

    Args:
        employee_name: Name visible on the badge
        employee_id: Employee ID number on the badge
        department: Department shown on the badge (optional)
        tool_context: ADK ToolContext (auto-injected by framework)

    Returns:
        dict with authorization status and work order details
    """
    from app.auth import load_users
    from app.work_orders import create_work_order as create_wo, escalate_work_order

    # Get pending work order from session state
    pending = {}
    session_id = "unknown"
    if tool_context and hasattr(tool_context, 'state'):
        pending = tool_context.state.get("pending_work_order", {})
        session_id = tool_context.state.get("session_id", "unknown")

    # Look up this employee in users.json
    users = load_users()
    badge_user = users.get(employee_id)

    if badge_user is None:
        logger.warning("badge_not_found", employee_id=employee_id, session_id=session_id)
        return {
            "status": "badge_not_found",
            "message": (
                f"Employee ID '{employee_id}' was not "
                f"found in the system. Please try scanning "
                f"the badge again or verify the ID."
            )
        }

    elif "create_work_order" in badge_user.get("permissions", []):
        # AUTHORIZED: Create the work order
        order = create_wo(
            equipment_id=pending.get("equipment_id", "unknown"),
            priority=pending.get("priority", "medium"),
            description=pending.get("description", ""),
            requested_by={
                "id": employee_id,
                "name": employee_name,
                "role": badge_user["role"]
            }
        )
        # Clear pending work order from state
        if "pending_work_order" in tool_context.state:
            tool_context.state["pending_work_order"] = {}

        logger.info("work_order_authorized",
                     order_id=order["order_id"],
                     employee=employee_id,
                     session_id=session_id)

        return {
            "status": "authorized",
            "order_id": order["order_id"],
            "message": (
                f"Work order {order['order_id']} created "
                f"successfully. {order['priority'].upper()} "
                f"priority ticket for {order['equipment']} "
                f"- {order['description']}. Assigned to "
                f"maintenance queue."
            )
        }

    else:
        # NOT AUTHORIZED: Escalate to supervisor
        order = escalate_work_order(
            equipment_id=pending.get("equipment_id", "unknown"),
            priority=pending.get("priority", "medium"),
            description=pending.get("description", ""),
            requested_by={
                "id": employee_id,
                "name": employee_name,
                "role": badge_user["role"]
            },
            escalate_to="sup_007"
        )
        # Clear pending work order from state
        if "pending_work_order" in tool_context.state:
            tool_context.state["pending_work_order"] = {}

        logger.info("work_order_escalated",
                     order_id=order["order_id"],
                     employee=employee_id,
                     session_id=session_id)

        return {
            "status": "escalated",
            "order_id": order["order_id"],
            "message": (
                f"Employee {employee_name} ({employee_id}) "
                f"does not have work order creation "
                f"permission. Request {order['order_id']} has "
                f"been escalated to supervisor Morgan Chen "
                f"(sup_007) for approval."
            )
        }