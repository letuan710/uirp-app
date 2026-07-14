"""Lưu/đọc evidence thô ngoài DB, đặt tên theo content hash (ARC-006, CHR-012/043).

Dedup miễn phí: cùng bytes → cùng hash → một file. Bảng ``evidence`` chỉ giữ metadata.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from uirp.config import Config


def content_hash(raw: bytes) -> str:
    """SHA-256 của bytes thô (ONT-R7)."""
    return hashlib.sha256(raw).hexdigest()


def put(cfg: Config, raw: bytes, ext: str) -> tuple[str, Path, bool]:
    """Ghi bytes vào data/evidence/<hash>.<ext>.

    Trả (hash, path, is_new). is_new=False nếu đã tồn tại (dedup — không ghi đè).
    """
    h = content_hash(raw)
    cfg.evidence_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.evidence_dir / f"{h}.{ext.lstrip('.')}"
    if path.exists():
        return h, path, False
    path.write_bytes(raw)
    return h, path, True


def get_path(cfg: Config, content_hash_: str, ext: str) -> Path:
    return cfg.evidence_dir / f"{content_hash_}.{ext.lstrip('.')}"


def tombstone_file(cfg: Config, file_path_rel: str) -> None:
    """Xóa bytes thô (ngoại lệ pháp lý P9/CHR-032). Bản ghi DB giữ lại (tombstone)."""
    p = cfg.data_dir / file_path_rel
    if p.exists():
        p.unlink()

