"""Account authentication routes (task 13.1) — `/api/auth/*` + `DELETE /api/account`.

Wires the REST endpoints to `AuthService`: register, login, logout, refresh session,
change password, request password reset, reset password, and self-delete account.
Per the "API endpoints" table in design.md:

| Method & Path                          | Permission      | Description      |
|----------------------------------------|-----------------|------------------|
| POST   /api/auth/register              | public          | R1               |
| POST   /api/auth/login                 | public          | R2.1 → {token,vaiTro} |
| POST   /api/auth/logout                | authenticated   | R2.8             |
| POST   /api/auth/refresh               | authenticated   | R25.5            |
| POST   /api/auth/password/change       | authenticated   | R25.1            |
| POST   /api/auth/password/reset-request| public          | R25.2-3          |
| POST   /api/auth/password/reset        | public          | R25.4            |
| DELETE /api/account                    | authenticated   | R25.6            |

Principles:
- Domain errors (ValidationError/AuthenticationError/ConflictError/LockedError...) are
  left to BUBBLE UP to the global error handler (`api/middleware/error_handler.py`) →
  NOT caught and swallowed. An invalid request DTO format → FastAPI returns 400 via
  the shared handler.
- Authenticated endpoints depend on `get_current_user`; logout/refresh/change-password
  also need the current token's `jti` (via `_get_current_token`).
- Logs key events at INFO through the centralized logger. NEVER logs passwords /
  tokens / reset tokens.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.auth.auth_service import AuthService
from app.auth.tokens import getTokenJti
from app.db.models import TaiKhoan
from app.errors import AuthenticationError
from app.models.schemas import (
    ChangePasswordInput,
    LoginInput,
    LoginResponse,
    RegisterInput,
    ResetPasswordInput,
    ResetRequestInput,
    TokenResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])

# Missing/invalid Bearer token → 401 (same as dependencies._GENERIC_AUTH_ERROR).
_GENERIC_AUTH_ERROR = "Token khong hop le hoac da het hieu luc."

# `auto_error=False` to return 401 ourselves (HTTPBearer's default is 403) when the token is missing.
_bearer_scheme = HTTPBearer(auto_error=False)


def _get_current_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """Extract the raw Bearer token string from the header (to get the current session's `jti`).

    Missing header / not Bearer → `AuthenticationError` (401). Does NOT log the token.
    """
    if credentials is None or not credentials.credentials:
        logger.info("Tu choi yeu cau: thieu Bearer token.")
        raise AuthenticationError(_GENERIC_AUTH_ERROR)
    return credentials.credentials


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterInput,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Register a new account (R1) → returns id + email + tenDangNhap (does not leak the hash)."""
    service = AuthService(db)
    taiKhoan = service.register(
        email=payload.email,
        tenDangNhap=payload.tenDangNhap,
        matKhau=payload.matKhau,
    )
    logger.info("POST /api/auth/register thanh cong: id=%s", taiKhoan.id)
    return {
        "id": taiKhoan.id,
        "email": taiKhoan.email,
        "tenDangNhap": taiKhoan.tenDangNhap,
    }


@router.post("/auth/login", response_model=LoginResponse)
def login(
    payload: LoginInput,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Log in (R2.1) → `{token, vaiTro}`. Failure → generic auth error (401)."""
    service = AuthService(db)
    token, vaiTro = service.login(
        tenDangNhap=payload.tenDangNhap,
        matKhau=payload.matKhau,
    )
    logger.info("POST /api/auth/login thanh cong: tenDangNhap=%s", payload.tenDangNhap)
    return LoginResponse(token=token, vaiTro=vaiTro)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    _taiKhoan: TaiKhoan = Depends(get_current_user),
    token: str = Depends(_get_current_token),
    db: Session = Depends(get_db),
) -> None:
    """Log out (R2.8): revoke the current session (idempotent)."""
    jti = getTokenJti(token)
    if jti is not None:
        AuthService(db).logout(jti)
    logger.info("POST /api/auth/logout thanh cong.")


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(
    token: str = Depends(_get_current_token),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Refresh the session (R25.5): issue a new token, revoke the old one. Invalid token → 401."""
    tokenMoi = AuthService(db).refreshSession(token)
    logger.info("POST /api/auth/refresh thanh cong.")
    return TokenResponse(token=tokenMoi)


@router.post("/auth/password/change", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordInput,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    token: str = Depends(_get_current_token),
    db: Session = Depends(get_db),
) -> None:
    """Change the password (R25.1): revoke other sessions, keep the current one."""
    jtiHienTai = getTokenJti(token)
    AuthService(db).changePassword(
        taiKhoan=taiKhoan,
        matKhauCu=payload.matKhauCu,
        matKhauMoi=payload.matKhauMoi,
        jtiHienTai=jtiHienTai,
    )
    logger.info("POST /api/auth/password/change thanh cong: id=%s", taiKhoan.id)


@router.post(
    "/auth/password/reset-request", status_code=status.HTTP_204_NO_CONTENT
)
def request_password_reset(
    payload: ResetRequestInput,
    db: Session = Depends(get_db),
) -> None:
    """Request a password reset (R25.2-3): generic response, does not leak the email."""
    AuthService(db).requestPasswordReset(email=payload.email)
    logger.info("POST /api/auth/password/reset-request da xu ly.")


@router.post("/auth/password/reset", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    payload: ResetPasswordInput,
    db: Session = Depends(get_db),
) -> None:
    """Reset the password using a reset token (R25.4). Invalid/expired token → 401."""
    AuthService(db).resetPassword(
        tokenReset=payload.tokenReset,
        matKhauMoi=payload.matKhauMoi,
    )
    logger.info("POST /api/auth/password/reset thanh cong.")


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Self-delete the account (R25.6): cascade-delete data + revoke all sessions."""
    taiKhoanId = taiKhoan.id
    AuthService(db).deleteOwnAccount(taiKhoan)
    logger.info("DELETE /api/account thanh cong: id=%s", taiKhoanId)
