"""ORM models + enums for the Multi-User RAG Platform (task 2.1).

Declares every relational entity on `Base` (SQLAlchemy 2.0, Mapped/mapped_column
style) following the "Entity definitions" section in design.md. Conventions:

- Naming: entity/field names in Vietnamese without diacritics; verb/method names in
  English; enum values in Vietnamese (NhanXacMinh keeps its diacritics per the design).
- UUID primary keys stored as strings (`str(uuid4())`) for portability with SQLite.
- Python enums map through SQLAlchemy `Enum(..., values_callable=...)` so they STORE
  the enum `value` (not the member name) — important for NhanXacMinh.
- Constraints: UNIQUE(email), UNIQUE(tenDangNhap), UNIQUE(khongGianId, taiKhoanId).
- Foreign-key relationships follow the ER diagram in design.md.

This module is lazy-imported by `init_db()` (database.py) before `create_all`, so
every table registers into `Base.metadata`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


# --- Default-value helpers -------------------------------------------------
def _uuid() -> str:
    """Generate a string UUID primary key (portable for SQLite)."""
    return str(uuid.uuid4())


def _now() -> datetime:
    """Current moment (UTC, timezone-aware)."""
    return datetime.now(timezone.utc)


def _enum_col(enum_cls: type[enum.Enum]) -> SAEnum:
    """Build an Enum column that stores the member `value` (not the name)."""
    return SAEnum(
        enum_cls,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


# --- Enums ------------------------------------------------------------------
class VaiTro(str, enum.Enum):
    NGUOI_DUNG = "NGUOI_DUNG"
    QUAN_TRI = "QUAN_TRI"


class TrangThaiTaiKhoan(str, enum.Enum):
    HOAT_DONG = "HOAT_DONG"
    VO_HIEU_HOA = "VO_HIEU_HOA"


class MucQuyen(str, enum.Enum):
    CHI_DOC = "CHI_DOC"
    GHI = "GHI"


class TrangThaiTaiLieu(str, enum.Enum):
    NAP = "NAP"
    PARSE = "PARSE"
    DA_PARSE_CHO_DUYET = "DA_PARSE_CHO_DUYET"
    DA_EMBED = "DA_EMBED"


class NhanXacMinh(str, enum.Enum):
    DA_XAC_MINH = "đã xác minh"
    CO_MAU_THUAN = "có mâu thuẫn"
    CHUA_XAC_MINH = "chưa xác minh"


# --- Accounts & authentication sessions ------------------------------------
class TaiKhoan(Base):
    """User account (R1, R2, R10)."""

    __tablename__ = "tai_khoan"
    __table_args__ = (
        UniqueConstraint("email", name="uq_tai_khoan_email"),
        UniqueConstraint("tenDangNhap", name="uq_tai_khoan_ten_dang_nhap"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    tenDangNhap: Mapped[str] = mapped_column(String(30), nullable=False)
    matKhauHash: Mapped[str] = mapped_column(String(255), nullable=False)
    vaiTro: Mapped[VaiTro] = mapped_column(
        _enum_col(VaiTro), nullable=False, default=VaiTro.NGUOI_DUNG
    )
    trangThai: Mapped[TrangThaiTaiKhoan] = mapped_column(
        _enum_col(TrangThaiTaiKhoan), nullable=False, default=TrangThaiTaiKhoan.HOAT_DONG
    )
    # R2.4: count of failed logins + temporary lockout timestamp
    soLanDangNhapThatBai: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    khoaDenThoiDiem: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    # Relationships
    khongGian: Mapped[list["KhongGianTaiLieu"]] = relationship(
        back_populates="chuSoHuu", cascade="all, delete-orphan"
    )
    phien: Mapped[list["PhienXacThuc"]] = relationship(
        back_populates="taiKhoan", cascade="all, delete-orphan"
    )
    chiaSe: Mapped[list["ChiaSe"]] = relationship(
        back_populates="taiKhoan", cascade="all, delete-orphan"
    )
    hanMuc: Mapped["HanMuc"] = relationship(
        back_populates="taiKhoan", cascade="all, delete-orphan", uselist=False
    )
    khoaApi: Mapped[list["KhoaApiNguoiDung"]] = relationship(
        back_populates="taiKhoan", cascade="all, delete-orphan"
    )
    lichSu: Mapped[list["LichSuTroChuyen"]] = relationship(
        back_populates="taiKhoan", cascade="all, delete-orphan"
    )


class PhienXacThuc(Base):
    """Authentication session (jti is a claim in the HMAC token) — R2, R25."""

    __tablename__ = "phien_xac_thuc"

    # id = jti of the HMAC token
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    taiKhoanId: Mapped[str] = mapped_column(
        String(36), ForeignKey("tai_khoan.id"), nullable=False, index=True
    )
    issuedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    expiresAt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revokedAt: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    taiKhoan: Mapped["TaiKhoan"] = relationship(back_populates="phien")


# --- Document workspaces & sharing -----------------------------------------
class KhongGianTaiLieu(Base):
    """Document workspace — the unit of data isolation per owner (R3, R4)."""

    __tablename__ = "khong_gian_tai_lieu"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ten: Mapped[str] = mapped_column(String(100), nullable=False)
    moTa: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    chuSoHuuId: Mapped[str] = mapped_column(
        String(36), ForeignKey("tai_khoan.id"), nullable=False, index=True
    )
    chienLuocChunkMacDinh: Mapped[str] = mapped_column(
        String(50), nullable=False, default="auto"
    )
    embeddingProvider: Mapped[str] = mapped_column(String(50), nullable=False)
    collectionName: Mapped[str] = mapped_column(String(100), nullable=False)
    createdAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    chuSoHuu: Mapped["TaiKhoan"] = relationship(back_populates="khongGian")
    taiLieu: Mapped[list["TaiLieu"]] = relationship(
        back_populates="khongGian", cascade="all, delete-orphan"
    )
    chiaSe: Mapped[list["ChiaSe"]] = relationship(
        back_populates="khongGian", cascade="all, delete-orphan"
    )
    cauHinh: Mapped["CauHinhTruyXuat"] = relationship(
        back_populates="khongGian", cascade="all, delete-orphan", uselist=False
    )
    lichSu: Mapped[list["LichSuTroChuyen"]] = relationship(
        back_populates="khongGian", cascade="all, delete-orphan"
    )


class ChiaSe(Base):
    """Grant access to a workspace for another account (R11)."""

    __tablename__ = "chia_se"
    __table_args__ = (
        UniqueConstraint("khongGianId", "taiKhoanId", name="uq_chia_se_khong_gian_tai_khoan"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    khongGianId: Mapped[str] = mapped_column(
        String(36), ForeignKey("khong_gian_tai_lieu.id"), nullable=False, index=True
    )
    taiKhoanId: Mapped[str] = mapped_column(
        String(36), ForeignKey("tai_khoan.id"), nullable=False, index=True
    )
    mucQuyen: Mapped[MucQuyen] = mapped_column(_enum_col(MucQuyen), nullable=False)

    khongGian: Mapped["KhongGianTaiLieu"] = relationship(back_populates="chiaSe")
    taiKhoan: Mapped["TaiKhoan"] = relationship(back_populates="chiaSe")


# --- Documents / chunks / summaries / rules --------------------------------
class TaiLieu(Base):
    """Document within a workspace (state machine NAP..DA_EMBED) — R5, R18."""

    __tablename__ = "tai_lieu"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    khongGianId: Mapped[str] = mapped_column(
        String(36), ForeignKey("khong_gian_tai_lieu.id"), nullable=False, index=True
    )
    tenFile: Mapped[str] = mapped_column(String(255), nullable=False)
    dinhDang: Mapped[str] = mapped_column(String(20), nullable=False)
    kichThuoc: Mapped[int] = mapped_column(Integer, nullable=False)
    # Extracted original text (kept to re-chunk/reset and rerun the strategy — R5.12,
    # R18.6, R18.7). NOT used for display; only serves rechunk/resetToDefault.
    vanBanGoc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trangThai: Mapped[TrangThaiTaiLieu] = mapped_column(
        _enum_col(TrangThaiTaiLieu), nullable=False, default=TrangThaiTaiLieu.NAP
    )
    chienLuocChunk: Mapped[str] = mapped_column(String(50), nullable=False)
    thamSoChunk: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    soChunk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    createdAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    khongGian: Mapped["KhongGianTaiLieu"] = relationship(back_populates="taiLieu")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="taiLieu", cascade="all, delete-orphan"
    )
    tomTat: Mapped["TomTatTaiLieu"] = relationship(
        back_populates="taiLieu", cascade="all, delete-orphan", uselist=False
    )
    quyTac: Mapped[list["QuyTacRanhGioi"]] = relationship(
        back_populates="taiLieu", cascade="all, delete-orphan"
    )


class Chunk(Base):
    """Preview chunk (RDB) — embedded into the Vector_Store when the document is DA_EMBED."""

    __tablename__ = "chunk"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    taiLieuId: Mapped[str] = mapped_column(
        String(36), ForeignKey("tai_lieu.id"), nullable=False, index=True
    )
    thuTu: Mapped[int] = mapped_column(Integer, nullable=False)
    viTriBatDau: Mapped[int] = mapped_column(Integer, nullable=False)
    viTriKetThuc: Mapped[int] = mapped_column(Integer, nullable=False)
    noiDung: Mapped[str] = mapped_column(Text, nullable=False)
    # Attribute name avoids `metadata` (reserved by DeclarativeBase); the DB column
    # is still named "metadata" per the design.
    chunkMetadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)

    taiLieu: Mapped["TaiLieu"] = relationship(back_populates="chunks")
    trichDan: Mapped[list["TrichDan"]] = relationship(back_populates="chunk")


class TomTatTaiLieu(Base):
    """Document summary + outline (R5.10, R16) — 1-1 with TaiLieu."""

    __tablename__ = "tom_tat_tai_lieu"

    taiLieuId: Mapped[str] = mapped_column(
        String(36), ForeignKey("tai_lieu.id"), primary_key=True
    )
    tomTat: Mapped[str] = mapped_column(Text, nullable=False)
    outline: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    taiLieu: Mapped["TaiLieu"] = relationship(back_populates="tomTat")


class QuyTacRanhGioi(Base):
    """Chunk boundary rule stored as data (R18.4)."""

    __tablename__ = "quy_tac_ranh_gioi"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    phamVi: Mapped[str] = mapped_column(String(50), nullable=False)
    phamViId: Mapped[str] = mapped_column(
        String(36), ForeignKey("tai_lieu.id"), nullable=False, index=True
    )
    tuKhoaHoacMau: Mapped[str] = mapped_column(String(255), nullable=False)
    dieuKien: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    taiLieu: Mapped["TaiLieu"] = relationship(back_populates="quyTac")


# --- Retrieval config & prompt templates -----------------------------------
class CauHinhTruyXuat(Base):
    """Per-workspace retrieval config (R6, R19) — defaults 0.3/0.5/k=8/0.5/0.5."""

    __tablename__ = "cau_hinh_truy_xuat"

    khongGianId: Mapped[str] = mapped_column(
        String(36), ForeignKey("khong_gian_tai_lieu.id"), primary_key=True
    )
    nguongKhongTimThay: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)
    nguongDuLienQuan: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    k: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    trongSoVector: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    trongSoBm25: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)

    khongGian: Mapped["KhongGianTaiLieu"] = relationship(back_populates="cauHinh")


class MauPrompt(Base):
    """Prompt template per role (synthesis | verify | normalize) — R20.

    Immutable safety constraints live in code (not stored here). `vaiTro` is the
    primary key: each role has exactly one current prompt.
    """

    __tablename__ = "mau_prompt"

    vaiTro: Mapped[str] = mapped_column(String(50), primary_key=True)
    noiDung: Mapped[str] = mapped_column(Text, nullable=False)
    isDefault: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# --- API keys (BYOK) & quotas ----------------------------------------------
class KhoaApiNguoiDung(Base):
    """User API key, encrypted at rest (Fernet) — R22; never stores plaintext."""

    __tablename__ = "khoa_api_nguoi_dung"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    taiKhoanId: Mapped[str] = mapped_column(
        String(36), ForeignKey("tai_khoan.id"), nullable=False, index=True
    )
    providerTen: Mapped[str] = mapped_column(String(50), nullable=False)
    vaiTro: Mapped[str] = mapped_column(String(50), nullable=False)
    khoaMaHoa: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    taiKhoan: Mapped["TaiKhoan"] = relationship(back_populates="khoaApi")


class HanMuc(Base):
    """Per-account resource quota (R12) — defaults 50 / 5GB / 1000 / frequency."""

    __tablename__ = "han_muc"

    taiKhoanId: Mapped[str] = mapped_column(
        String(36), ForeignKey("tai_khoan.id"), primary_key=True
    )
    soKhongGianToiDa: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    dungLuongToiDa: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5 * 1024**3
    )
    soTaiLieuToiDaMoiKhongGian: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1000
    )
    tanSuatTruyVanMoiPhut: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    taiKhoan: Mapped["TaiKhoan"] = relationship(back_populates="hanMuc")


# --- Chat history & citations ----------------------------------------------
class LichSuTroChuyen(Base):
    """An account's Q&A history within a workspace (R9)."""

    __tablename__ = "lich_su_tro_chuyen"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    taiKhoanId: Mapped[str] = mapped_column(
        String(36), ForeignKey("tai_khoan.id"), nullable=False, index=True
    )
    khongGianId: Mapped[str] = mapped_column(
        String(36), ForeignKey("khong_gian_tai_lieu.id"), nullable=False, index=True
    )
    cauHoi: Mapped[str] = mapped_column(Text, nullable=False)
    traLoi: Mapped[str] = mapped_column(Text, nullable=False)
    nhanXacMinh: Mapped[NhanXacMinh] = mapped_column(_enum_col(NhanXacMinh), nullable=False)
    createdAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    # R9.8: marks whether the source (citation) is still available after the document is re-chunked
    nguonConKhaDung: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    taiKhoan: Mapped["TaiKhoan"] = relationship(back_populates="lichSu")
    khongGian: Mapped["KhongGianTaiLieu"] = relationship(back_populates="lichSu")
    trichDan: Mapped[list["TrichDan"]] = relationship(
        back_populates="lichSu", cascade="all, delete-orphan"
    )


class TrichDan(Base):
    """Citation [n] <-> chunk within a single Q&A turn (R7.5)."""

    __tablename__ = "trich_dan"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    lichSuId: Mapped[str] = mapped_column(
        String(36), ForeignKey("lich_su_tro_chuyen.id"), nullable=False, index=True
    )
    marker: Mapped[int] = mapped_column(Integer, nullable=False)
    chunkId: Mapped[str] = mapped_column(
        String(36), ForeignKey("chunk.id"), nullable=False, index=True
    )
    taiLieuId: Mapped[str] = mapped_column(
        String(36), ForeignKey("tai_lieu.id"), nullable=False, index=True
    )
    noiDung: Mapped[str] = mapped_column(Text, nullable=False)

    lichSu: Mapped["LichSuTroChuyen"] = relationship(back_populates="trichDan")
    chunk: Mapped["Chunk"] = relationship(back_populates="trichDan")
