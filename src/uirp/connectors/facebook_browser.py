"""Connector Facebook — Mode B: thu bán tự động qua trình duyệt (ADR-002/007/008).

MẶC ĐỊNH CDP: gắn Playwright vào Chrome THẬT đã đăng nhập của Owner (khó bị phát hiện,
ADR-007). 4 chế độ enumerate: url | keyword | profile | group (ADR8-3, như MediaCrawler).
Mỗi bài: mở trang, bung bình luận, lưu HTML + screenshot → Evidence → cùng pipeline
trích xuất phong phú (body + comment + ảnh) của Mode A.

RÀO AN TOÀN (bắt buộc, ADR-002/007/008-5): chỉ chạy khi Owner gõ `uirp fetch`; delay ngẫu
nhiên; giới hạn số bài/phiên; DỪNG khi gặp checkpoint/login/CAPTCHA (KHÔNG tự giải).
Rủi ro R9 (khóa tài khoản) — khuyến nghị tài khoản Facebook phụ.

⚠️ Selector đặc thù Facebook (nút "xem thêm bình luận", mẫu URL bài) CẦN TINH CHỈNH ở lần
chạy thật vì Facebook đổi DOM liên tục (ADR-007). Khung kết nối/lưu/pipeline thì ổn định.
"""

from __future__ import annotations

import random
import time

from uirp.config import Config
from uirp.connectors.common import store_and_queue
from uirp.errors import ConfigError, PermanentError

# Gợi ý nhận diện URL bài (tinh chỉnh khi chạy thật).
_POST_HINTS = ("/posts/", "/permalink/", "story_fbid=", "/videos/", "/photo")


def _mode_url(mode: str, value: str) -> str:
    if mode == "url":
        return value
    if mode == "keyword":
        return f"https://www.facebook.com/search/posts/?q={value}"
    if mode == "profile":
        return f"https://www.facebook.com/{value}"
    if mode == "group":
        return f"https://www.facebook.com/groups/{value}"
    raise ConfigError(f"chế độ fetch không hợp lệ: {mode} (url|keyword|profile|group)")


def _guard_checkpoint(page) -> None:
    u = (page.url or "").lower()
    if any(x in u for x in ("checkpoint", "/login", "captcha")):
        raise PermanentError(
            "gặp checkpoint/login/CAPTCHA — DỪNG phiên, KHÔNG tự giải (ADR-002)"
        )


def _attach(p, fc: dict):
    """Gắn vào Chrome thật qua CDP (mặc định) hoặc tự mở persistent context (dự phòng)."""
    if fc["mode"] == "cdp":
        browser = p.chromium.connect_over_cdp(f"http://localhost:{fc['cdp_port']}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return browser, context, False
    context = p.chromium.launch_persistent_context(
        user_data_dir="./browser-profile", headless=fc["headless"]
    )
    return context.browser, context, True


def _discover(page, mode: str, value: str, fc: dict) -> list[str]:
    page.goto(_mode_url(mode, value), wait_until="domcontentloaded")
    _guard_checkpoint(page)
    urls: list[str] = []
    for _ in range(fc["scroll_depth"]):
        for a in page.query_selector_all("a[href]"):
            href = a.get_attribute("href") or ""
            if any(h in href for h in _POST_HINTS) and href not in urls:
                urls.append(href)
        if len(urls) >= fc["max_posts_per_run"]:
            break
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(int(random.uniform(1500, 3500)))
    return urls[: fc["max_posts_per_run"]]


def _expand_comments(page, maxc: int) -> None:
    # ⚠️ FB-specific — tinh chỉnh selector khi chạy thật (ADR-007).
    for _ in range(min(maxc // 10 + 1, 8)):
        btn = page.query_selector("text=/xem thêm bình luận|view more comments/i")
        if not btn:
            break
        try:
            btn.click()
            page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            break


def _capture_post(conn, cfg: Config, topic_id: str, page, url: str, fc: dict) -> None:
    page.goto(url, wait_until="domcontentloaded")
    _guard_checkpoint(page)
    if fc["collect_comments"]:
        _expand_comments(page, fc["max_comments_per_post"])
    title = url.rstrip("/").rsplit("/", 1)[-1] or url
    store_and_queue(conn, cfg, topic_id, page.content().encode("utf-8"), "html",
                    "text/html", "facebook_post", url, title, "browser_assisted")
    store_and_queue(conn, cfg, topic_id, page.screenshot(full_page=True), "png",
                    "image/png", "facebook_screenshot", url, title, "browser_assisted")


def collect(conn, cfg: Config, topic_id: str, mode: str, value: str) -> int:
    """Kết nối → enumerate → capture từng bài. Trả số bài đã thu."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ConfigError(
            "chưa cài playwright — pip install playwright && playwright install chromium"
        ) from e

    fc = cfg.fetch
    captured = 0
    with sync_playwright() as p:
        browser, context, launched = _attach(p, fc)
        page = context.new_page()
        try:
            urls = [value] if mode == "url" else _discover(page, mode, value, fc)
            for url in urls:
                _guard_checkpoint(page)
                _capture_post(conn, cfg, topic_id, page, url, fc)
                captured += 1
                time.sleep(random.uniform(fc["min_delay_seconds"], fc["max_delay_seconds"]))
        finally:
            page.close()
            if launched:
                browser.close()
    return captured
