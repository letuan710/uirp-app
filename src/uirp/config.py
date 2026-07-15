"""Đọc config.toml + biến môi trường (ARC-018, STD4-R7).

Không secret trong config: API key đọc từ env ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Giá trị mặc định — config.toml chỉ cần ghi đè phần muốn đổi.
DEFAULTS: dict[str, dict[str, Any]] = {
    "api": {"backend": "claude_agent_sdk"},
    "models": {
        "tier_s": "claude-haiku-4-5",
        "tier_m": "claude-sonnet-5",
        "tier_l": "claude-opus-4-8",
    },
    "ocr": {"backend": "claude_vision"},
    "quota": {"default_wait_seconds": 3600},
    "jobs": {"max_retry": 3, "running_timeout_minutes": 30},
    "fetch": {
        "mode": "cdp", "cdp_port": 9222, "headless": False,
        "max_posts_per_run": 20, "collect_comments": True,
        "max_comments_per_post": 50, "scroll_depth": 5,
        "download_images": True, "max_images_per_post": 10,
        "min_delay_seconds": 20, "max_delay_seconds": 60,
    },
}


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    """Cấu hình đã nạp, kèm các đường dẫn chuẩn dưới ``root/data`` (STD-001 §7)."""

    root: Path
    data: dict[str, Any]

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "uirp.sqlite"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def backend(self) -> str:
        return str(self.data["api"]["backend"])

    def model_for_tier(self, tier: str) -> str:
        return str(self.data["models"][f"tier_{tier.lower()}"])

    @property
    def max_retry(self) -> int:
        return int(self.data["jobs"]["max_retry"])

    @property
    def running_timeout_minutes(self) -> int:
        return int(self.data["jobs"]["running_timeout_minutes"])

    @property
    def default_wait_seconds(self) -> int:
        return int(self.data["quota"]["default_wait_seconds"])

    @property
    def fetch(self) -> dict[str, Any]:
        return self.data["fetch"]

    def api_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY")


def load(root: Path | None = None) -> Config:
    """Nạp config từ ``root/config.toml`` (nếu có), trộn lên DEFAULTS."""
    root = (root or Path.cwd()).resolve()
    data = dict(DEFAULTS)
    cfg_file = root / "config.toml"
    if cfg_file.exists():
        user = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
        data = _merge(DEFAULTS, user)
    return Config(root=root, data=data)
