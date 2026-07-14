"""Cây exception UIRP (STD4-R4/R5).

Scheduler quyết retry bằng ``isinstance(e, TransientError)`` — không đoán theo chuỗi.
``error_kind`` = tên lớp exception, là khóa gom nhóm của ``uirp doctor`` (ARC-013b).
"""

from __future__ import annotations


class UirpError(Exception):
    """Gốc của mọi lỗi UIRP."""


class TransientError(UirpError):
    """Lỗi tạm — ĐƯỢC retry (mạng, I/O, timeout)."""


class PermanentError(UirpError):
    """Lỗi vĩnh viễn — FAILED ngay, không retry."""


# --- Transient cụ thể ---
class NetworkError(TransientError):
    """Lỗi mạng tạm thời."""


class QuotaExceeded(TransientError):
    """Hết hạn mức Claude → job vào WAITING_QUOTA (không phải FAILED). CHR-057."""

    def __init__(self, retry_at: float | None = None, message: str = "quota exceeded") -> None:
        super().__init__(message)
        self.retry_at = retry_at


# --- Permanent cụ thể ---
class ParseError(PermanentError):
    """Không bóc tách được nội dung nguồn."""


class SchemaError(PermanentError):
    """AI trả JSON sai schema."""


class SourceFileError(PermanentError):
    """File nguồn hỏng/không đọc được."""


class ConfigError(PermanentError):
    """Cấu hình sai."""


def error_kind(exc: BaseException) -> str:
    """Tên lớp exception — khóa gom nhóm cho uirp doctor (STD4-R5)."""
    return exc.__class__.__name__
