"""Sinh Canonical ID: tiền tố loại + ULID (ONT §4, ONT-R7).

ULID = 48-bit timestamp (ms) + 80-bit ngẫu nhiên, mã hóa Crockford base32 (26 ký tự):
sắp xếp được theo thời gian, sinh offline, gần như không đụng độ. Chỉ dùng stdlib (P14).
"""

from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # bỏ I, L, O, U cho dễ đọc

# Tiền tố loại object (ONT §4)
PREFIXES = frozenset(
    {"io", "ev", "obs", "clm", "ent", "ali", "mrg", "rel", "top", "job", "usg", "ann"}
)


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def ulid() -> str:
    """Một ULID 26 ký tự."""
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    rnd = int.from_bytes(os.urandom(10), "big")  # 80 bit
    return _encode(ts, 10) + _encode(rnd, 16)


def new_id(prefix: str) -> str:
    """ID chuẩn ``<prefix>_<ulid>``, ví dụ ``ev_01J...``."""
    if prefix not in PREFIXES:
        raise ValueError(f"tiền tố ID không hợp lệ: {prefix!r}")
    return f"{prefix}_{ulid()}"
