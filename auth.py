"""
Cookie-based session auth for BuyBuddy.

Deliberately simple, in-memory sessions — fine for a personal demo behind
a handful of fixed accounts. Sessions reset if the container restarts.
Swap SESSIONS for Redis and this scheme still works unchanged if this ever
needs to survive restarts / run on more than one instance.
"""

import secrets
from typing import Dict, Optional

from fastapi import Cookie, HTTPException, Response

from users import get_user

COOKIE_NAME = "buybuddy_session"

# token -> {"username": str, "cart": [...], "chat_history": [...], "chat_preferences": {...}}
SESSIONS: Dict[str, dict] = {}


def create_session(username: str) -> str:
    token = secrets.token_hex(24)
    SESSIONS[token] = {
        "username": username,
        "cart": [],          # list of {"product_id": int, "qty": int}
        "chat_history": [],  # list of {"role", "content"}
        "chat_preferences": {},
        "chat_seeded": False,
        "agentic_thread_id": f"{token}:agentic",
        "agentic_turn": 0,
        "agentic_pending": False,  # True while a HITL interrupt is awaiting a decision
    }
    return token


def destroy_session(token: Optional[str]) -> None:
    if token and token in SESSIONS:
        del SESSIONS[token]


def get_session(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    return SESSIONS.get(token)


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,  # 12 hours
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def require_session(buybuddy_session: Optional[str] = Cookie(default=None)) -> dict:
    """FastAPI dependency for API routes — 401s if not logged in."""
    session = get_session(buybuddy_session)
    if not session:
        raise HTTPException(status_code=401, detail="Not logged in")
    return {"token": buybuddy_session, **session}


def current_user(session: dict) -> dict:
    return get_user(session["username"])
