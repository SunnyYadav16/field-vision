"""
FieldVision Work Orders Module
Handles creating, storing, querying, and approving work orders
Three-stage workflow: pending_orders → approved_orders → completed_orders
"""

import json
import time
from pathlib import Path
from datetime import datetime

PENDING_ORDERS_PATH = Path(__file__).parent.parent / "pending_orders.json"
APPROVED_ORDERS_PATH = Path(__file__).parent.parent / "approved_orders.json"
COMPLETED_ORDERS_PATH = Path(__file__).parent.parent / "completed_orders.json"

def _load(path: Path) -> list:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []

def _save(path: Path, data: list):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

def generate_order_id() -> str:
    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    return f"WO-{ts}"

def create_work_order(
    equipment_id: str,
    priority: str,
    description: str,
    requested_by: dict,
    badge_verified: bool = True
) -> dict:
    """Create an approved work order (for authorized users)."""
    order = {
        "order_id": generate_order_id(),
        "status": "approved",
        "priority": priority,
        "equipment": equipment_id,
        "description": description,
        "requested_by": requested_by,
        "badge_verified": badge_verified,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "approved_at": datetime.utcnow().isoformat() + "Z",
        "escalated_to": None
    }
    orders = _load(APPROVED_ORDERS_PATH)
    orders.append(order)
    _save(APPROVED_ORDERS_PATH, orders)
    return order

def escalate_work_order(
    equipment_id: str,
    priority: str,
    description: str,
    requested_by: dict,
    escalate_to: str = "sup_007"
) -> dict:
    """Create a pending work order that needs supervisor approval."""
    order = {
        "order_id": generate_order_id(),
        "status": "pending_approval",
        "priority": priority,
        "equipment": equipment_id,
        "description": description,
        "requested_by": requested_by,
        "badge_verified": True,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "escalated_to": escalate_to
    }
    pending = _load(PENDING_ORDERS_PATH)
    pending.append(order)
    _save(PENDING_ORDERS_PATH, pending)
    return order

def get_pending_orders() -> list:
    """Get all pending orders awaiting approval."""
    return _load(PENDING_ORDERS_PATH)

def get_approved_orders() -> list:
    """Get all approved orders (in progress)."""
    return _load(APPROVED_ORDERS_PATH)

def get_completed_orders() -> list:
    """Get all completed orders."""
    return _load(COMPLETED_ORDERS_PATH)

def get_all_orders() -> list:
    """Get all orders across all stages."""
    return _load(APPROVED_ORDERS_PATH)

def approve_pending_order(order_id: str) -> dict | None:
    """Move a pending order to approved status."""
    pending = _load(PENDING_ORDERS_PATH)
    approved = _load(APPROVED_ORDERS_PATH)
    for i, order in enumerate(pending):
        if order["order_id"] == order_id:
            order["status"] = "approved"
            order["approved_at"] = datetime.utcnow().isoformat() + "Z"
            approved.append(order)
            pending.pop(i)
            _save(PENDING_ORDERS_PATH, pending)
            _save(APPROVED_ORDERS_PATH, approved)
            return order
    return None

def complete_order(order_id: str) -> dict | None:
    """Move an approved order to completed status."""
    approved = _load(APPROVED_ORDERS_PATH)
    completed = _load(COMPLETED_ORDERS_PATH)
    for i, order in enumerate(approved):
        if order["order_id"] == order_id:
            order["status"] = "completed"
            order["completed_at"] = datetime.utcnow().isoformat() + "Z"
            completed.append(order)
            approved.pop(i)
            _save(APPROVED_ORDERS_PATH, approved)
            _save(COMPLETED_ORDERS_PATH, completed)
            return order
    return None

