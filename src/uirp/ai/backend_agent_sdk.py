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
from uirp.errors import ConfigError, QuotaExceeded, TransientError

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

        # cli_path (tùy chọn): trỏ tới claude.exe đã cài+đăng nhập sẵn trên máy Owner,
        # phòng khi SDK không tự tìm ra (Claude Code không nằm trên PATH). Nếu để trống,
        # SDK dùng bản claude.exe đóng gói kèm theo.
        cli_path = self.cfg.data.get("api", {}).get("cli_path") or None
        opts_kwargs: dict[str, Any] = {"system_prompt": system, "model": model}
        if cli_path:
            opts_kwargs["cli_path"] = cli_path

        async def _run() -> str:
            opts = ClaudeAgentOptions(**opts_kwargs)
            chunks: list[str] = []
            async for message in query(prompt=user, options=opts):
                name = type(message).__name__
                if name == "RateLimitEvent":
                    # Sự kiện này bắn ra MỖI KHI trạng thái rate-limit đổi (kể cả
                    # 'allowed'/'allowed_warning' khi VẪN CÒN quota) — CHỈ 'rejected' mới
                    # là hết thật (types.py). Còn lại là thông báo, bỏ qua & chạy tiếp.
                    info = getattr(message, "rate_limit_info", None)
                    if getattr(info, "status", None) == "rejected":
                        raise QuotaExceeded(
                            retry_at=getattr(info, "resets_at", None),
                            message="hết hạn mức thuê bao Claude (rejected)",
                        )
                    continue
                if name == "ResultMessage" and getattr(message, "is_error", False):
                    # CLI báo is_error=True kèm subtype="success" khi lượt gọi API bên dưới
                    # lỗi — thông điệp thật nằm ở .errors hoặc .result (đã quan sát:
                    # "Not logged in · Please run /login" chỉ có trong .result).
                    status = getattr(message, "api_error_status", None)
                    errs = getattr(message, "errors", None) or []
                    result_text = (getattr(message, "result", None) or "").strip()
                    detail = ("; ".join(errs) or result_text
                              or (f"HTTP {status}" if status else "lỗi API không rõ"))
                    low = detail.lower()
                    if "not logged in" in low or "/login" in low:
                        raise ConfigError(
                            "Claude CLI CHƯA ĐĂNG NHẬP — mở PowerShell chạy `claude` "
                            "rồi gõ `/login` (một lần duy nhất), sau đó chạy lại."
                        )
                    if status == 429 or any(h in low for h in _QUOTA_HINTS):
                        raise QuotaExceeded(message=f"Claude API rate-limit ({detail})")
                    raise TransientError(f"Claude Code báo lỗi: {detail}")
                chunks.append(_extract_text(message))
            return "".join(chunks)

        try:
            text = anyio.run(_run)
        except (QuotaExceeded, TransientError, ConfigError):
            raise
        except Exception as e:  # noqa: BLE001 - phân loại theo thông điệp
            name = type(e).__name__
            if name in ("CLINotFoundError", "CLIConnectionError"):
                raise ConfigError(
                    "cần Claude Code CLI đã cài & đăng nhập cho backend thuê bao"
                ) from e
            if any(h in str(e).lower() for h in _QUOTA_HINTS):
                raise QuotaExceeded(message=str(e)) from e
            # Exception còn lại từ tiến trình CLI (ProcessError, mất kết nối giữa chừng...)
            # đa phần là tạm thời (mạng/CLI hiccup) — cho retry thay vì rớt vĩnh viễn ngay
            # lần đầu (đã quan sát thực tế: coi là PermanentError làm rớt ~80% dữ liệu oan).
            raise TransientError(str(e)) from e

        return AIResponse(
            text=text, model=model,
            tokens_in=0, tokens_out=0,  # SDK thuê bao không phơi token ổn định
            est_cost_usd=None,
        )
