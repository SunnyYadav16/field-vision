"""
FieldVision Role-Based Authentication Module
JWT-based auth with role and permission management
"""

import json
import jwt
import time
from pathlib import Path
from functools import wraps
from fastapi import HTTPException, Request, WebSocket

# ── Configuration ──
SECRET_KEY = "fieldvision-hackathon-2026"  # Fine for hackathon
ALGORITHM = "HS256"
TOKEN_EXPIRY = 86400  # 24 hours (outlasts the hackathon)

# ── Load user database ──
USERS_DB_PATH = Path(__file__).parent.parent / "users.json"

def load_users():
    with open(USERS_DB_PATH) as f:
        return json.load(f)["users"]

# ── Token Creation ──
def create_token(user_id: str, user_data: dict) -> str:
    """Create a JWT token containing user role and permissions."""
    payload = {
        "user_id": user_id,
        "name": user_data["name"],
        "role": user_data["role"],
        "zone": user_data["zone"],
        "permissions": user_data["permissions"],
        "exp": int(time.time()) + TOKEN_EXPIRY
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

# ── Token Verification ──
def verify_token(token: str) -> dict:
    """Verify and decode a JWT token. Raises HTTPException if invalid."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ── Login Function ──
def authenticate_user(user_id: str, password: str) -> dict | None:
    """Check credentials and return user data if valid."""
    users = load_users()
    user = users.get(user_id)
    if user and user["password"] == password:
        return user
    return None

# ── Permission Checker ──
def has_permission(token_payload: dict, required_permission: str) -> bool:
    """Check if the user has a specific permission."""
    return required_permission in token_payload.get("permissions", [])

# ── FastAPI Dependency: Extract user from HTTP request ──
async def get_current_user(request: Request) -> dict:
    """Extract and verify user from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth token")
    token = auth_header.split(" ")[1]
    return verify_token(token)

# ── WebSocket Auth: Extract user from query param ──
async def get_ws_user(websocket: WebSocket) -> dict:
    """Extract user from WebSocket query parameter ?token=xxx"""
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing auth token")
        return None
    try:
        return verify_token(token)
    except HTTPException:
        await websocket.close(code=4001, reason="Invalid token")
        return None
