"""FastAPI dependency injection: authentication + authorization (task 4.1).

Provides shared dependencies for every route:

- `get_db`: provides a SQLAlchemy `Session` (delegates to `database.get_db`).
- `get_current_user`: extracts the Bearer token from the `Authorization` header,
  verifies it via `verifyToken`; missing/invalid → 401 (`AuthenticationError`).
- `require_role(vaiTro)`: factory that requires the current account to have the
  exact `VaiTro`; insufficient → 403 (`AuthorizationError`) — R10.6.
- `require_workspace_access(minQuyen)`: factory that loads the `KhongGianTaiLieu` by
  the `id` path param, computes `resolveAccess`, and requires at least `minQuyen`.

Status-code mapping convention (R2.6, R3.2, R3.3):
- Missing/invalid token → 401 (`AuthenticationError`).
- Account with NO access (NONE) to a workspace → **404** (`NotFoundError`): does not
  reveal the existence of a workspace owned by another account (R3.1/R3.2
  non-disclosure spirit).
- Account that CAN see the workspace (CHI_DOC or higher) but is **below** the
  required permission (e.g. needs GHI but only has CHI_DOC) → **403**
  (`AuthorizationError`).

Each domain error is mapped to the corresponding HTTP code by the global error
handler. The module logs through the centralized logger; NEVER logs token values.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.tokens import verifyToken
from app.db.database import get_db as _get_db
from app.db.models import KhongGianTaiLieu, TaiKhoan, VaiTro
from app.errors import AuthenticationError, AuthorizationError, NotFoundError
from app.services.share_service import MucTruyCap, resolveAccess

logger = logging.getLogger(__name__)

# Missing/invalid token → 401, generic message (does not reveal which check failed).
_GENERIC_AUTH_ERROR = "Token khong hop le hoac da het hieu luc."

# `auto_error=False`: handle a missing credential ourselves to always return 401
# (HTTPBearer's default is 403) — R2.6.
_bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: provide a `Session` (delegates to `database.get_db`)."""
    yield from _get_db()


def get_document_pipeline(db: Session = Depends(get_db)) -> "DocumentPipeline":
    """FastAPI dependency: build a `DocumentPipeline` on the current Session.

    Defaults to the real configured Embedding_Provider/Vector_Store (resolved per
    workspace at embed time). In tests, override this dependency to inject a fake
    Embedding_Provider + an in-memory Vector_Store.
    """
    from app.pipelines.document_pipeline import DocumentPipeline

    return DocumentPipeline(db)


def get_query_pipeline(db: Session = Depends(get_db)) -> "QueryPipeline":
    """FastAPI dependency: build a `QueryPipeline` on the current Session.

    Defaults to the real configured Embedding_Provider/Vector_Store (resolved per
    workspace at query time); the LLM_Provider (synthesis/verify/normalize) defaults
    to `None` — when absent, synthesis degrades to the original-chunk fallback
    (R7.6/R8.4). In tests, override this dependency to inject a fake LLM/Embedding +
    an in-memory Vector_Store.
    """
    from app.pipelines.query_pipeline import QueryPipeline

    return QueryPipeline(db)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TaiKhoan:
    """Authenticate a Bearer token → return the `TaiKhoan` (R2.5, R2.6).

    Missing header / not Bearer / wrong-expired-revoked token / disabled account →
    `AuthenticationError` (401). Does NOT log token values.
    """
    if credentials is None or not credentials.credentials:
        logger.info("Tu choi yeu cau: thieu Bearer token.")
        raise AuthenticationError(_GENERIC_AUTH_ERROR)

    # verifyToken raises AuthenticationError (401) for every invalid case.
    return verifyToken(db, credentials.credentials)


def require_role(vaiTro: VaiTro) -> Callable[[TaiKhoan], TaiKhoan]:
    """Factory: require the current account to have the exact `vaiTro` (R10.6).

    Returns a dependency that depends on `get_current_user`; if the role does not
    match → `AuthorizationError` (403). On a matching role → return the `TaiKhoan`.
    """

    def _kiemTraVaiTro(
        taiKhoan: TaiKhoan = Depends(get_current_user),
    ) -> TaiKhoan:
        if taiKhoan.vaiTro != vaiTro:
            logger.info(
                "Tu choi thao tac: tai khoan id=%s vai tro=%s, can %s.",
                taiKhoan.id,
                taiKhoan.vaiTro.value,
                vaiTro.value,
            )
            raise AuthorizationError("Khong du quyen thuc hien thao tac nay.")
        return taiKhoan

    return _kiemTraVaiTro


def require_workspace_access(
    minQuyen: MucTruyCap,
) -> Callable[..., KhongGianTaiLieu]:
    """Factory: require at least `minQuyen` on the workspace at the `id` path param.

    Loads the `KhongGianTaiLieu` by `id`; computes `resolveAccess`. Status-code
    mapping:
    - workspace does not exist OR access is NONE → 404 (`NotFoundError`) — does not
      reveal existence.
    - workspace is visible but below `minQuyen` → 403 (`AuthorizationError`).
    Sufficient permission → return the `KhongGianTaiLieu`.
    """

    def _kiemTraTruyCap(
        id: str,
        taiKhoan: TaiKhoan = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> KhongGianTaiLieu:
        khongGian = db.get(KhongGianTaiLieu, id)
        if khongGian is None:
            logger.info("Tu choi truy cap khong gian: khong ton tai (id=%s).", id)
            raise NotFoundError("Khong tim thay khong gian tai lieu.")

        mucTruyCap = resolveAccess(db, taiKhoan, khongGian)
        if mucTruyCap == MucTruyCap.NONE:
            # Do not reveal the existence of a workspace owned by another account → 404.
            logger.info(
                "Tu choi truy cap khong gian: khong co quyen (id=%s, taiKhoanId=%s).",
                id,
                taiKhoan.id,
            )
            raise NotFoundError("Khong tim thay khong gian tai lieu.")
        if mucTruyCap < minQuyen:
            # The workspace is visible but below the required permission → 403.
            logger.info(
                "Tu choi thao tac: muc truy cap %s < yeu cau %s (id=%s, taiKhoanId=%s).",
                mucTruyCap.name,
                minQuyen.name,
                id,
                taiKhoan.id,
            )
            raise AuthorizationError("Khong du quyen voi khong gian tai lieu nay.")

        return khongGian

    return _kiemTraTruyCap
