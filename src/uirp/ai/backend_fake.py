"""FakeBackend — backend giả cho demo/test OFFLINE (STD4-R11). KHÔNG gọi Claude.

Heuristic tối giản để chứng minh DÒNG CHẢY DỮ LIỆU (evidence→observation→claim→entity),
KHÔNG phải chất lượng AI. Backend thật thay vào là xong (đúng tinh thần Model Adapter).
"""

from __future__ import annotations

import json
import re

from uirp.ai.adapter import AIRequest, AIResponse

# Từ hay đứng đầu câu, không phải tên riêng — lọc bớt nhiễu cho demo.
_STOP = {"Cảnh", "Tôi", "Quản", "Nhóm", "Sàn", "Bài", "Họ", "Ông", "Bà", "Anh", "Chị", "Khi"}


class FakeBackend:
    def complete(self, model: str, system: str, user: str, req: AIRequest) -> AIResponse:
        text_in = str(req.payload.get("text", ""))
        if req.prompt_ref == "classify_relevance":
            out = json.dumps({"relevant": True})
        elif req.prompt_ref == "extract_claims":
            out = self._extract(text_in)
        else:
            out = "{}"
        return AIResponse(
            text=out, model=f"fake-{model}",
            tokens_in=max(1, len(user) // 4), tokens_out=max(1, len(out) // 4),
            est_cost_usd=0.0,
        )

    @staticmethod
    def _extract(text: str) -> str:
        # Câu = tách theo xuống dòng hoặc dấu kết câu; giữ câu đủ dài làm claim.
        sentences = [
            s.strip() for s in re.split(r"\n+|(?<=[.!?])\s+", text) if len(s.strip()) >= 20
        ]
        # Tên riêng = cụm từ LIÊN TIẾP bắt đầu bằng chữ hoa (isupper — đúng Unicode tiếng Việt),
        # xét trong từng đoạn (ngắt theo xuống dòng + dấu kết câu) để không dính qua ranh giới câu.
        names: list[str] = []
        for seg in re.split(r"[\n.!?]+", text):
            cur: list[str] = []
            for tok in re.findall(r"[^\s,:;\"'()]+", seg):
                if tok[:1].isupper() and len(tok) > 1:
                    cur.append(tok)
                else:
                    if cur:
                        names.append(" ".join(cur))
                    cur = []
            if cur:
                names.append(" ".join(cur))

        seen: set[str] = set()
        entities: list[dict] = []
        for raw_name in names:
            toks = raw_name.split()
            while toks and toks[0] in _STOP:  # bỏ từ đứng đầu câu lẫn vào tên
                toks.pop(0)
            n = " ".join(toks)
            if not n or n in seen:
                continue
            seen.add(n)
            if len(toks) == 1 and n in _STOP:
                continue
            entities.append({"name": n, "type": "unknown", "confidence": 0.3})

        return json.dumps(
            {
                "claims": [
                    {"statement": s, "asserted_by": None, "confidence": 0.3}
                    for s in sentences[:20]
                ],
                "entities": entities[:20],
                "relationships": [],
            },
            ensure_ascii=False,
        )
