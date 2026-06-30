"""Unit tests for the sensitive-field masking utility (R14.4 / R22.3)."""

from __future__ import annotations

from app.logging_redaction import MASK_PLACEHOLDER, mask_sensitive


def test_mask_top_level_sensitive_fields():
    data = {"tenDangNhap": "alice", "matKhau": "secret123", "token": "abc.def"}
    masked = mask_sensitive(data)

    assert masked["tenDangNhap"] == "alice"
    assert masked["matKhau"] == MASK_PLACEHOLDER
    assert masked["token"] == MASK_PLACEHOLDER


def test_mask_nested_sensitive_fields_at_all_levels():
    data = {
        "user": {
            "email": "a@b.com",
            "profile": {"apiKey": "k-123", "ten": "Alice"},
        },
        "sessions": [{"jti": "j1"}, {"jti": "j2", "active": True}],
    }
    masked = mask_sensitive(data)

    assert masked["user"]["email"] == MASK_PLACEHOLDER
    assert masked["user"]["profile"]["apiKey"] == MASK_PLACEHOLDER
    assert masked["user"]["profile"]["ten"] == "Alice"
    assert masked["sessions"][0]["jti"] == MASK_PLACEHOLDER
    assert masked["sessions"][1]["jti"] == MASK_PLACEHOLDER
    assert masked["sessions"][1]["active"] is True


def test_mask_does_not_mutate_original():
    data = {"matKhau": "secret"}
    mask_sensitive(data)

    assert data["matKhau"] == "secret"


def test_non_sensitive_values_preserved():
    data = {"soChunk": 5, "ten": "khong gian", "items": [1, 2, 3]}
    assert mask_sensitive(data) == data
