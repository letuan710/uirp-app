"""AI Adapter: interface duy nhất để pipeline gọi Claude (ARC-009, CHR-053/054).

Pipeline khai báo `tier` (S/M/L), adapter tra config ra model thật, chọn backend,
nạp+render prompt (STD-003), gọi, rồi ghi `usage_log` (CHR-054/STD5-R1).
Không import SDK provider ở đây — chỉ ở các backend (ARC-002).
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from uirp.config import Config
from uirp.errors import ConfigError
from uirp.ids import new_id
from uirp.store import db

_PROMPTS = Path(__file__).parent.parent / "prompts"
_SYSTEM = (
    "You are a precise information-extraction assistant for a research tool. "
    "Follow the rules in the message exactly. Never judge truthfulness or credibility."
)


@dataclass
class AIRequest:
    tier: str                                   # "S" | "M" | "L"
    prompt_ref: str                             # tên file prompt (STD-003)
    payload: dict[str, Any] = field(default_factory=dict)  # biến điền template + dữ liệu cho FakeBackend
    images: list[Path] = field(default_factory=list)
    expect_json: bool = False


@dataclass
class AIResponse:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    est_cost_usd: float | None                  # None với backend thuê bao (ARC-010)


class AIBackend(Protocol):
    def complete(self, model: str, system: str, user: str, req: AIRequest) -> AIResponse: ...


def _load_prompt(name: str) -> tuple[str, str]:
    """Trả (version, body) từ prompts/<name>.md (STD-003)."""
    text = (_PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    version, body = "0", text
    if text.startswith("---"):
        _, front, body = text.split("---", 2)
        for line in front.splitlines():
            if line.strip().startswith("version:"):
                version = line.split(":", 1)[1].strip()
    return version, body.strip()


class AIClient:
    """Điểm gọi AI duy nhất. Chọn backend theo config.api.backend."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.backend_name = cfg.backend
        self.backend = _make_backend(cfg)

    def complete(
        self, req: AIRequest, conn: Any = None, job_id: str | None = None
    ) -> AIResponse:
        model = self.cfg.model_for_tier(req.tier)
        version, body = _load_prompt(req.prompt_ref)
        user = string.Template(body).safe_substitute(req.payload)
        resp = self.backend.complete(model, _SYSTEM, user, req)
        if conn is not None:
            db.insert(conn, "usage_log", {
                "id": new_id("usg"), "job_id": job_id, "backend": self.backend_name,
                "model": resp.model, "tier": req.tier,
                "tokens_in": resp.tokens_in, "tokens_out": resp.tokens_out,
                "est_cost_usd": resp.est_cost_usd,
                "prompt": f"{req.prompt_ref}@{version}",  # STD3-R3
                "called_at": datetime.now(timezone.utc).isoformat(),
            })
        return resp


def _make_backend(cfg: Config) -> AIBackend:
    name = cfg.backend
    if name == "fake":
        from uirp.ai.backend_fake import FakeBackend
        return FakeBackend()
    if name == "claude_agent_sdk":
        from uirp.ai.backend_agent_sdk import AgentSdkBackend
        return AgentSdkBackend(cfg)
    if name == "api_key":
        from uirp.ai.backend_api_key import ApiKeyBackend
        return ApiKeyBackend(cfg)
    raise ConfigError(f"backend AI không hợp lệ: {name!r} (fake|claude_agent_sdk|api_key)")
