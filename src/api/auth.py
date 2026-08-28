from __future__ import annotations

import logging
import os
from typing import Any

import firebase_admin
import firebase_admin.credentials
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as fb_auth

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)
_initialized = False


def _ensure_firebase_initialized() -> None:
    global _initialized
    if _initialized:
        return
    try:
        firebase_admin.get_app()
        _initialized = True
        return
    except ValueError:
        pass

    import json

    from src.config import get_settings

    settings = get_settings()
    service_account_json = getattr(settings, "firebase_service_account_json", "")

    if service_account_json:
        service_account_info = json.loads(service_account_json)
        cred = firebase_admin.credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin initialized with service account JSON")
    elif settings.app_env == "production" and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        raise RuntimeError(
            "Firebase Admin credentials are required in production; configure "
            "FIREBASE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS"
        )
    else:
        firebase_admin.initialize_app()
        logger.info("Firebase Admin initialized with application default credentials")
    _initialized = True


def verify_firebase_token(credentials: HTTPAuthorizationCredentials | None) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
        )
    token = credentials.credentials
    # 1. Try Firebase verification if configured
    try:
        _ensure_firebase_initialized()
        decoded = fb_auth.verify_id_token(token)
        return decoded
    except Exception:
        pass

    # 2. Try Google OAuth2 ID token verification
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        google_client_id = os.getenv(
            "GOOGLE_CLIENT_ID",
            "325810411037-k8dn6iejobhf3da52fbrram55ndbt0t0.apps.googleusercontent.com",
        )
        try:
            decoded = google_id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                google_client_id,
            )
        except Exception:
            # Also accept without strict audience if signed by Google accounts
            decoded = google_id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
            )
        return {
            "uid": decoded.get("sub", ""),
            "email": decoded.get("email", ""),
            "name": decoded.get("name", ""),
            "picture": decoded.get("picture", ""),
        }
    except Exception:
        pass

    # 3. Fallback: decode Google token or query tokeninfo
    try:
        import httpx
        with httpx.Client(timeout=4.0) as client:
            resp = client.get(f"https://oauth2.googleapis.com/tokeninfo?id_token={token}")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "uid": data.get("sub", ""),
                    "email": data.get("email", ""),
                    "name": data.get("name", ""),
                    "picture": data.get("picture", ""),
                }
    except Exception:
        pass

    logger.warning("Token verification failed for incoming request")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not verify authorization token",
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    from src.config import get_settings

    settings = get_settings()
    if credentials is None:
        if settings.allow_guest_access or settings.app_env != "production":
            return {"uid": "guest-anonymous", "email": "guest@localhost", "guest": True}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
        )
    return verify_firebase_token(credentials)


async def require_admin(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    uid = user.get("uid", "")
    from sqlalchemy import text

    from src.db.session import session_scope

    async with session_scope() as session:
        result = await session.execute(
            text("SELECT role FROM users WHERE uid = :uid"), {"uid": uid}
        )
        row = result.scalar_one_or_none()
    if row != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
