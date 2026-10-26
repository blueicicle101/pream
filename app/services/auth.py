"""
Admin authentication utilities for Pream.
Provides cookie-based session authentication for the Web UI and HTTP Basic Auth
for automated API clients using ADMIN_USERNAME and ADMIN_PASSWORD from settings.
"""
import hashlib
import hmac
import secrets
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

# Security instance for Basic Auth (auto_error=False allows cookie fallback)
http_basic = HTTPBasic(auto_error=False)

COOKIE_NAME = "pream_session"


def get_secret_key() -> str:
    """Generate a stable secret key derived from credentials and database URL."""
    seed = f"{settings.ADMIN_USERNAME}:{settings.ADMIN_PASSWORD}:{settings.DATABASE_URL}"
    return hashlib.sha256(seed.encode()).hexdigest()


def generate_session_token(username: str) -> str:
    """Generate an HMAC-signed session token for the authenticated admin."""
    secret = get_secret_key()
    signature = hmac.new(secret.encode(), username.encode(), hashlib.sha256).hexdigest()
    return f"{username}:{signature}"


def verify_session_token(token: Optional[str]) -> bool:
    """Verify that a session token is valid and matches the current admin configuration."""
    if not token or ":" not in token:
        return False
    parts = token.split(":", 1)
    if len(parts) != 2:
        return False
    username, signature = parts
    if not secrets.compare_digest(username, settings.ADMIN_USERNAME):
        return False
    expected_token = generate_session_token(username)
    return secrets.compare_digest(token, expected_token)


def verify_credentials(username: Optional[str], password: Optional[str]) -> bool:
    """Validate username and password against configured ADMIN_USERNAME and ADMIN_PASSWORD."""
    if not username or not password:
        return False
    correct_user = secrets.compare_digest(username.strip(), settings.ADMIN_USERNAME.strip())
    correct_pass = secrets.compare_digest(password.strip(), settings.ADMIN_PASSWORD.strip())
    return correct_user and correct_pass


def is_authenticated(request: Request, basic_creds: Optional[HTTPBasicCredentials] = None) -> bool:
    """Check if the current request is authenticated via session cookie or Basic Auth."""
    # 1. Check session cookie
    token = request.cookies.get(COOKIE_NAME)
    if token and verify_session_token(token):
        return True

    # 2. Check HTTP Basic Auth header
    if basic_creds and verify_credentials(basic_creds.username, basic_creds.password):
        return True

    # 3. Check raw Authorization header fallback
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Basic "):
        import base64
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            if ":" in decoded:
                u, p = decoded.split(":", 1)
                if verify_credentials(u, p):
                    return True
        except Exception:
            pass

    return False


def require_admin(request: Request, basic_creds: Optional[HTTPBasicCredentials] = Depends(http_basic)) -> str:
    """
    FastAPI dependency ensuring the caller is authenticated as Admin.
    Redirects browser requests to /login and returns 401 for API clients.
    """
    if is_authenticated(request, basic_creds):
        return settings.ADMIN_USERNAME

    # If it's a browser request for an HTML page, redirect to /login
    accept = request.headers.get("accept", "")
    is_html_request = "text/html" in accept or not request.url.path.startswith("/api/")
    if is_html_request and not request.url.path.startswith("/api/"):
        next_path = quote(request.url.path)
        redirect_url = f"/login?next={next_path}" if request.url.path not in ("/", "/login") else "/login"
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": redirect_url},
        )

    # API request: return 401 with Basic Auth challenge
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Admin authentication required",
        headers={"WWW-Authenticate": 'Basic realm="Pream Admin"'},
    )
