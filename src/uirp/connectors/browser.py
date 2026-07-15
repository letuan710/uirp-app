"""Mode B generic — thu bán tự động qua trình duyệt, đa nền tảng (ADR-002/007/008/009).

CDP vào trình duyệt THẬT đã đăng nhập. 4 chế độ: url | keyword | profile | group, URL lấy
từ descriptor nền tảng (platforms.py). Mỗi bài: mở, bung bình luận, lưu HTML + screenshot
+ tải ảnh → cùng pipeline trích xuất phong phú.

RÀO AN TOÀN (mọi nền tảng): chỉ chạy khi Owner gõ lệnh; delay ngẫu nhiên; giới hạn số
bài/phiên; DỪNG khi checkpoint/CAPTCHA (không tự giải). Rủi ro R9 — tài khoản phụ.

⚠️ Nền tảng `auto=False` (Xiaohongshu/Douyin/Kuaishou/Zalo…) anti-bot mạnh/đóng → chỉ cho
`--mode url`, khuyến nghị Mode A. Selector từng nền tảng CẦN TINH CHỈNH khi chạy thật (ADR-007).
"""

from __future__ import annotations

import random
import time

from uirp.config import Config
from uirp.connectors.common import store_and_queue
from uirp.errors import ConfigError, PermanentError
from uirp.platforms import Platform, get


def _mode_url(p: Platform, mode: str, value: str) -> str:
    if mode == "url":
        return value
    tmpl = {"keyword": p.search_url, "profile": p.profile_url, "group": p.group_url}.get(mode)
    if not tmpl:
        raise ConfigError(
            f"{p.display} chưa hỗ trợ chế độ '{mode}' tự động — dùng --mode url hoặc Mode A "
            f"(uirp ingest --platform {p.key})"
        )
    return tmpl.format(q=value, v=value)


def _guard_checkpoint(page) -> None:
    u = (page.url or "").lower()
    if any(x in u for x in ("checkpoint", "/login", "captcha", "verify")):
        raise PermanentError("gặp checkpoint/login/CAPTCHA — DỪNG phiên, KHÔNG tự giải (ADR-002)")


_LAUNCH_ARGS = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]


def _attach(pw, fc: dict):
    """Trả (context, launched). CDP: gắn Chrome thật. launch: tự mở persistent context."""
    if fc["mode"] == "cdp":
        browser = pw.chromium.connect_over_cdp(f"http://localhost:{fc['cdp_port']}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return context, False
    context = pw.chromium.launch_persistent_context(
        user_data_dir="./browser-profile", headless=fc["headless"], args=_LAUNCH_ARGS,
    )
    return context, True


def _discover(page, p: Platform, mode: str, value: str, fc: dict) -> list[str]:
    page.goto(_mode_url(p, mode, value), wait_until="domcontentloaded")
    _guard_checkpoint(page)
    hints = p.post_hints or ("/posts/",)
    urls: list[str] = []
    for _ in range(fc["scroll_depth"]):
        for a in page.query_selector_all("a[href]"):
            href = a.get_attribute("href") or ""
            if any(h in href for h in hints) and href not in urls:
                urls.append(href)
        if len(urls) >= fc["max_posts_per_run"]:
            break
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(int(random.uniform(1500, 3500)))
    return urls[: fc["max_posts_per_run"]]


def _expand_comments(page, maxc: int) -> None:
    for _ in range(min(maxc // 10 + 1, 8)):
        btn = page.query_selector("text=/xem thêm bình luận|view more comments|查看更多评论/i")
        if not btn:
            break
        try:
            btn.click()
            page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            break


def _download_images(conn, cfg: Config, topic_id: str, page, p: Platform, fc: dict) -> None:
    srcs: list[str] = []
    for img in page.query_selector_all("img"):
        s = img.get_attribute("src") or ""
        if s.startswith("http") and s not in srcs:
            srcs.append(s)
    for s in srcs[: fc.get("max_images_per_post", 10)]:
        try:
            r = page.context.request.get(s)
            if not r.ok:
                continue
            ext = s.rsplit(".", 1)[-1].split("?")[0][:4].lower() or "jpg"
            media = f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"
            store_and_queue(conn, cfg, topic_id, r.body(), ext, media,
                            f"{p.key}_image", s, s.rsplit("/", 1)[-1][:40], "browser_assisted")
        except Exception:  # noqa: BLE001
            continue


def _capture(conn, cfg: Config, topic_id: str, page, p: Platform, url: str, fc: dict) -> None:
    page.goto(url, wait_until="domcontentloaded")
    _guard_checkpoint(page)
    if fc["collect_comments"]:
        _expand_comments(page, fc["max_comments_per_post"])
    title = url.rstrip("/").rsplit("/", 1)[-1] or url
    store_and_queue(conn, cfg, topic_id, page.content().encode("utf-8"), "html",
                    "text/html", f"{p.key}_post", url, title, "browser_assisted")
    try:  # screenshot best-effort — lỗi (headless/sandbox) không chặn, HTML đã lưu
        shot = page.screenshot(full_page=True, timeout=8000)
        store_and_queue(conn, cfg, topic_id, shot, "png",
                        "image/png", f"{p.key}_screenshot", url, title, "browser_assisted")
    except Exception:  # noqa: BLE001
        pass
    if fc.get("download_images", True):
        _download_images(conn, cfg, topic_id, page, p, fc)


def collect(conn, cfg: Config, topic_id: str, platform_key: str, mode: str, value: str) -> int:
    p = get(platform_key)
    if not p.auto and mode != "url":
        raise ConfigError(
            f"{p.display}: {p.note or 'Mode B tự động chưa hỗ trợ'} — dùng Mode A: "
            f"uirp ingest --platform {p.key}"
        )
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ConfigError(
            "chưa cài playwright — pip install playwright && playwright install chromium"
        ) from e

    fc = cfg.fetch
    captured = 0
    with sync_playwright() as pw:
        context, launched = _attach(pw, fc)
        page = context.new_page()
        try:
            urls = [value] if mode == "url" else _discover(page, p, mode, value, fc)
            for i, url in enumerate(urls):
                _guard_checkpoint(page)
                _capture(conn, cfg, topic_id, page, p, url, fc)
                captured += 1
                if i + 1 < len(urls):  # delay giữa các bài, không delay sau bài cuối
                    time.sleep(random.uniform(fc["min_delay_seconds"], fc["max_delay_seconds"]))
        finally:
            page.close()
            if launched:
                context.close()
    return captured
