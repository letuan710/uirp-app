"""Backend api_key — gọi Claude API trả theo token qua SDK `anthropic` (CHR-053 dự phòng).

Đọc ANTHROPIC_API_KEY từ env (STD4-R7). 429 → QuotaExceeded (đọc retry-after);
5xx → NetworkError (retry). Tính est_cost_usd từ bảng giá.
"""

from __future__ import annotations

import time

from uirp.ai.adapter import AIRequest, AIResponse
from uirp.config import Config
from uirp.errors import ConfigError, NetworkError, QuotaExceeded

# Giá tham chiếu USD / 1 triệu token (in, out) — 2026-06 (STD-001 §4). Đổi khi giá đổi.
_PRICE = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
}


def _estimate_cost(model: str, tin: int, tout: int) -> float | None:
    for key, (pin, pout) in _PRICE.items():
        if model.startswith(key):
            return round(tin / 1e6 * pin + tout / 1e6 * pout, 6)
    return None


class ApiKeyBackend:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def complete(self, model: str, system: str, user: str, req: AIRequest) -> AIResponse:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise ConfigError("chưa cài anthropic (pip install anthropic)") from e

        client = anthropic.Anthropic()  # đọc ANTHROPIC_API_KEY từ env
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.RateLimitError as e:
            retry_after = None
            if getattr(e, "response", None) is not None:
                retry_after = e.response.headers.get("retry-after")
            retry_at = time.time() + float(retry_after) if retry_after else None
            raise QuotaExceeded(retry_at=retry_at, message="rate limit (429)") from e
        except anthropic.APIStatusError as e:  # pragma: no cover
            if e.status_code and e.status_code >= 500:
                raise NetworkError(f"server {e.status_code}") from e
            raise

        text = "".join(getattr(b, "text", "") for b in resp.content if b.type == "text")
        u = resp.usage
        return AIResponse(
            text=text, model=resp.model,
            tokens_in=u.input_tokens, tokens_out=u.output_tokens,
            est_cost_usd=_estimate_cost(resp.model, u.input_tokens, u.output_tokens),
        )
