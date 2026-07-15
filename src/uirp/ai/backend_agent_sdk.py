"""Backend claude_agent_sdk — MẶC ĐỊNH: dùng hạn mức gói thuê bao Claude (CHR-053, OI-8).

Gọi Claude qua `claude-agent-sdk` chạy trên Claude Code đã đăng nhập trên máy Owner.
Hết hạn mức → QuotaExceeded → job vào WAITING_QUOTA, chờ cửa sổ reset ~5h (CHR-057).
est_cost_usd = None (thuê bao không tính theo token — ARC-010).

⚠️ Cần xác minh trên máy thật ở lần chạy đầu (ARC §13 bước 3 verify): API stream-message
của claude-agent-sdk có thể khác giữa các phiên bản; hàm _extract_text duck-type phòng xa.
"""

from __future__ import annotations

from typing import Any

from uirp.ai.adapter import AIRequest, AIResponse
from uirp.config import Config
from uirp.errors import ConfigError, QuotaExceeded

_QUOTA_HINTS = ("rate limit", "quota", "usage limit", "429", "overloaded")


def _extract_text(message: Any) -> str:
    """Bóc text từ một message của agent-sdk, chịu được vài dạng cấu trúc."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            t = getattr(block, "text", None)
            if t is None and isinstance(block, dict):
                t = block.get("text")
            if t:
                parts.append(t)
        return "".join(parts)
    text = getattr(message, "text", None)
    return text if isinstance(text, str) else ""


class AgentSdkBackend:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def complete(self, model: str, system: str, user: str, req: AIRequest) -> AIResponse:
        try:
            import anyio
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError as e:  # pragma: no cover
            raise ConfigError(
                "chưa cài claude-agent-sdk, hoặc Claude Code chưa đăng nhập "
                "(pip install claude-agent-sdk)"
            ) from e

        async def _run() -> str:
            opts = ClaudeAgentOptions(system_prompt=system, model=model)
            chunks: list[str] = []
            async for message in query(prompt=user, options=opts):
                if type(message).__name__ == "RateLimitEvent":  # hết hạn mức thuê bao
                    raise QuotaExceeded(message="hết hạn mức thuê bao Claude (rate limit)")
                chunks.append(_extract_text(message))
            return "".join(chunks)

        try:
            text = anyio.run(_run)
        except QuotaExceeded:
            raise
        except Exception as e:  # noqa: BLE001 - phân loại theo thông điệp
            name = type(e).__name__
            if name in ("CLINotFoundError", "CLIConnectionError"):
                raise ConfigError(
                    "cần Claude Code CLI đã cài & đăng nhập cho backend thuê bao"
                ) from e
            if any(h in str(e).lower() for h in _QUOTA_HINTS):
                raise QuotaExceeded(message=str(e)) from e
            raise

        return AIResponse(
            text=text, model=model,
            tokens_in=0, tokens_out=0,  # SDK thuê bao không phơi token ổn định
            est_cost_usd=None,
        )
