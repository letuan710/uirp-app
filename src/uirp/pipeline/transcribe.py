"""Bước transcribe — trích NỘI DUNG video qua phụ đề (transcript-first CHR-042, ADR-011).

KHÔNG tải video (nặng, chậm); chỉ lấy phụ đề / auto-caption qua yt-dlp.
Ưu tiên vi > en > zh; transcript tiếng Trung sẽ được bước translate tự dịch (ADR-009).
Video không có phụ đề → lưu tiêu đề + mô tả (video_description) làm dữ liệu tối thiểu.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from uirp.config import Config
from uirp.errors import ConfigError, NetworkError, PermanentError
from uirp.pipeline.parse import _add_obs, route_obs
from uirp.store import db

_LANG_PREF = ("vi", "en", "zh-Hans", "zh-Hant", "zh")


def _vtt_to_text(vtt: str) -> str:
    lines: list[str] = []
    seen = None
    for ln in vtt.splitlines():
        s = ln.strip()
        if (not s or s == "WEBVTT" or "-->" in s or s.isdigit()
                or s.startswith(("Kind:", "Language:", "NOTE", "STYLE"))):
            continue
        s = re.sub(r"<[^>]+>", "", s)  # bỏ tag thời gian <00:00:01.000><c>
        if s and s != seen:  # phụ đề cuộn lặp dòng — khử trùng lặp liền kề
            lines.append(s)
            seen = s
    return "\n".join(lines)


def _sub_to_text(raw: bytes) -> str:
    """VTT hoặc JSON (YouTube json3 / Bilibili) → text thuần."""
    txt = raw.decode("utf-8", errors="replace")
    if txt.lstrip().startswith("{"):
        try:
            data = json.loads(txt)
        except ValueError:
            return ""
        if isinstance(data, dict) and "events" in data:  # YouTube json3
            segs = [s.get("utf8", "") for e in data.get("events") or []
                    for s in (e.get("segs") or [])]
            return "\n".join(x.strip() for x in segs if x.strip())
        body = data.get("body") if isinstance(data, dict) else None  # Bilibili
        if body:
            return "\n".join(x.get("content", "").strip() for x in body if x.get("content"))
        return ""
    return _vtt_to_text(txt)


def _pick_sub(info: dict) -> tuple[str, str] | None:
    """Chọn (lang, url): phụ đề người làm trước, auto-caption sau; vi > en > zh."""
    for source in ("subtitles", "automatic_captions"):
        subs = info.get(source) or {}
        for pref in _LANG_PREF:
            for lang in [k for k in subs if k == pref or k.startswith(pref + "-")]:
                for fmt in subs.get(lang) or []:
                    if fmt.get("ext") in ("vtt", "json3", "json", "srt") and fmt.get("url"):
                        return lang, fmt["url"]
    return None


def make_handler():
    def transcribe(conn, cfg: Config, payload: dict[str, Any], job_id: str) -> list:
        ev = db.get(conn, "evidence", payload["evidence_id"])
        url = payload.get("url")
        if ev is None or not url:
            return []
        if db.query(conn, "SELECT id FROM observation WHERE evidence_id=? "
                    "AND kind IN ('transcript','video_description') LIMIT 1", (ev["id"],)):
            return []  # idempotency (ARC-014)
        try:
            import yt_dlp
        except ImportError as e:
            raise ConfigError(
                "chưa cài yt-dlp cho trích nội dung video — pip install yt-dlp"
            ) from e

        opts = {"skip_download": True, "quiet": True, "no_warnings": True,
                "writesubtitles": True, "writeautomaticsub": True}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except yt_dlp.utils.DownloadError as e:
            raise PermanentError(f"không lấy được thông tin video: {e}") from e

        picked = _pick_sub(info)
        if picked:
            lang, sub_url = picked
            try:
                with urllib.request.urlopen(sub_url, timeout=30) as r:
                    raw = r.read()
            except OSError as e:
                raise NetworkError(f"không tải được phụ đề: {e}") from e
            text = _sub_to_text(raw)
            if text:
                oid = _add_obs(conn, ev["id"], "transcript", text,
                               {"lang": lang, "video": url})
                return [route_obs(text, oid)]  # transcript tiếng Trung cũng tự dịch

        merged = "\n".join(x for x in (info.get("title"), info.get("description")) if x).strip()
        if merged:
            oid = _add_obs(conn, ev["id"], "video_description", merged,
                           {"video": url})
            return [route_obs(merged, oid)]
        return []

    return transcribe
