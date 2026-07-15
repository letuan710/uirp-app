"""Tiện ích text nhỏ (stdlib, P14). Phát hiện tiếng Trung để tự dịch (ADR-009)."""

from __future__ import annotations


def cjk_ratio(text: str) -> float:
    """Tỉ lệ ký tự Hán (CJK) trên tổng ký tự chữ — 0..1."""
    if not text:
        return 0.0
    cjk = alpha = 0
    for ch in text:
        is_cjk = "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿"
        if is_cjk:
            cjk += 1
            alpha += 1
        elif ch.isalpha():
            alpha += 1
    return cjk / alpha if alpha else 0.0


def is_chinese(text: str, threshold: float = 0.2) -> bool:
    """True nếu nội dung chủ yếu là tiếng Trung (cần dịch sang tiếng Việt)."""
    return cjk_ratio(text) >= threshold
