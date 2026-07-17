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
from urllib.parse import parse_qs, urljoin, urlparse

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
    # "sorry" = trang chặn bot của Google (google.com/sorry/index?...) — quan sát thật
    # khi tìm bằng trình duyệt tự động không có lịch sử duyệt web (ADR-012).
    if any(x in u for x in ("checkpoint", "/login", "captcha", "verify", "/sorry/")):
        raise PermanentError(
            "gặp checkpoint/login/CAPTCHA/chặn-bot — DỪNG phiên, KHÔNG tự giải (ADR-002). "
            "Với Google: dùng mode=cdp bám Chrome thật đã đăng nhập để giảm bị chặn."
        )


_LAUNCH_ARGS = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]


def _attach(pw, fc: dict, profile_suffix: str = ""):
    """Trả (context, launched). CDP: gắn Chrome thật (ít bị chặn hơn — ADR-002/007).
    Chrome chưa mở debug port → TỰ CHUYỂN sang launch (trình duyệt riêng) để luôn quét được,
    không bắt Owner phải nhớ mở Chrome trước.
    profile_suffix: quét SONG SONG nhiều nền tảng — mỗi thread một profile riêng
    (persistent context khóa profile, không mở trùng được)."""
    if fc["mode"] == "cdp":
        try:
            browser = pw.chromium.connect_over_cdp(f"http://localhost:{fc['cdp_port']}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            return context, False
        except Exception:  # noqa: BLE001 - Chrome thật chưa mở debug port
            try:
                print(f"  (không bám được Chrome thật ở cổng {fc['cdp_port']} "
                      f"→ tự mở trình duyệt riêng)")
            except UnicodeEncodeError:
                print(f"  (CDP attach failed on port {fc['cdp_port']} -> fallback to launch)")
    profile_dir = f"./browser-profile{profile_suffix}"
    try:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir, headless=fc["headless"], args=_LAUNCH_ARGS,
        )
    except Exception as e:  # noqa: BLE001
        if "Executable doesn't exist" not in str(e):
            raise
        # Chromium của Playwright chưa tải (chưa chạy `playwright install chromium`)
        # → dùng luôn Google Chrome đã cài trên máy (channel="chrome").
        try:
            print("  (Chromium của Playwright chưa tải → dùng Google Chrome đã cài trên máy)")
        except UnicodeEncodeError:
            print("  (Playwright chromium not downloaded -> using installed Google Chrome)")
        context = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir, headless=fc["headless"], args=_LAUNCH_ARGS,
            channel="chrome",
        )
    return context, True


def _dismiss_consent(page) -> None:
    """Google/YouTube hay chặn màn hình đồng ý cookie trước kết quả thật ở profile mới —
    bấm qua (best-effort, im lặng nếu không có) để không mất trắng kết quả (ADR-012)."""
    for text in ("Reject all", "Từ chối tất cả", "I agree", "Tôi đồng ý", "Accept all"):
        try:
            btn = page.get_by_role("button", name=text, exact=False)
            if btn.count() > 0:
                btn.first.click(timeout=2000)
                page.wait_for_timeout(500)
                return
        except Exception:  # noqa: BLE001 - không có nút này, thử nút khác
            continue


# Nền tảng mà TÌM KIẾM cần đăng nhập mới ra kết quả thật (không đăng nhập → trang tự
# nhét gợi ý không liên quan, có vẻ như "quét được" nhưng thực ra là rác). Phát hiện qua
# cụm text đặc trưng trên trang — đã xác nhận thật với Bilibili (ADR-012).
_SEARCH_LOGIN_WALL = {"bilibili": "登录后你可以"}


def _guard_search_login_wall(page, p: Platform) -> None:
    text = _SEARCH_LOGIN_WALL.get(p.key)
    if not text:
        return
    try:
        body = page.inner_text("body", timeout=3000)
    except Exception:  # noqa: BLE001
        return
    if text in body:
        raise PermanentError(
            f"{p.display}: tìm kiếm cần đăng nhập (chưa đăng nhập → không có kết quả "
            f"thật, trang tự nhét gợi ý KHÔNG LIÊN QUAN) — dùng mode=cdp bám Chrome thật "
            f"đã đăng nhập."
        )


def _unwrap_google_redirect(url: str) -> str:
    """Google đôi khi bọc link kết quả qua /url?q=<đích thật>&... (hoặc tham số 'url') —
    bóc URL đích ra. CHỈ áp dụng cho path "/url" (link bọc thật) — path khác (vd.
    "/search?q=..." là tìm kiếm liên quan nội bộ) trả nguyên vẹn (ADR-012)."""
    parsed = urlparse(url)
    if "google." not in parsed.netloc or parsed.path != "/url":
        return url
    qs = parse_qs(parsed.query)
    for key in ("q", "url"):
        if qs.get(key):
            return qs[key][0]
    return url


def _discover(page, p: Platform, mode: str, value: str, fc: dict) -> list[str]:
    page.goto(_mode_url(p, mode, value), wait_until="domcontentloaded")
    if p.key in ("google", "youtube"):
        _dismiss_consent(page)
    if p.key in _SEARCH_LOGIN_WALL:
        page.wait_for_timeout(2500)  # nội dung (kể cả tường đăng nhập) render bằng JS
    _guard_checkpoint(page)
    _guard_search_login_wall(page, p)
    hints = p.post_hints or ("/posts/",)
    urls: list[str] = []
    for _ in range(fc["scroll_depth"]):
        for a in page.query_selector_all("a[href]"):
            href = a.get_attribute("href") or ""
            # href có thể tương đối (vd. "/shorts/") — quy về URL tuyệt đối trước khi lưu,
            # nếu không page.goto() sau này sẽ báo "invalid URL" (không phải lỗi môi trường).
            full = urljoin(page.url, href)
            if p.key == "google":
                # Google đôi khi bọc link kết quả qua /url?q=<đích thật>&... — bóc URL đích
                # ra trước khi lọc, nếu không sẽ loại nhầm cả kết quả thật (chứa "google."
                # trong URL bọc dù đích đến là trang ngoài — ADR-012).
                full = _unwrap_google_redirect(full)
                # Máy tìm kiếm: chỉ lấy trang ĐÍCH bên ngoài, loại link nội bộ Google.
                if (not full.startswith("http")
                        or any(d in full for d in ("google.", "gstatic.", "googleusercontent."))):
                    continue
            elif not any(h in href for h in hints):
                continue
            if full not in urls:
                urls.append(full)
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
    _OK_EXT = ("png", "jpg", "jpeg", "webp", "gif")
    for s in srcs[: fc.get("max_images_per_post", 10)]:
        try:
            r = page.context.request.get(s)
            if not r.ok:
                continue
            ext = s.rsplit(".", 1)[-1].split("?")[0].lower()
            if ext not in _OK_EXT:
                ext = "jpg"  # URL không có đuôi rõ ràng → mặc định jpg
            media = f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"
            store_and_queue(conn, cfg, topic_id, r.body(), ext, media,
                            f"{p.key}_image", s, s.rsplit("/", 1)[-1][:40], "browser_assisted")
        except Exception:  # noqa: BLE001
            continue


def _goto_with_wayback_fallback(page, url: str) -> str:
    """Mở URL; trang sập/mất (lỗi mạng hoặc HTTP 4xx/5xx) → thử bản lưu Wayback Machine
    (ADR-012). Trả về URL THỰC TẾ đã mở được (gốc hoặc bản lưu trữ)."""
    try:
        resp = page.goto(url, wait_until="domcontentloaded")
        if resp is not None and resp.status >= 400:
            raise PermanentError(f"HTTP {resp.status}")
        return url
    except Exception:  # noqa: BLE001 - trang gốc lỗi, thử Wayback Machine trước khi bỏ cuộc
        wb_url = f"https://web.archive.org/web/2/{url}"
        try:
            page.goto(wb_url, wait_until="domcontentloaded")
        except Exception:  # noqa: BLE001
            # goto lỗi trước có thể còn đang chuyển sang trang lỗi nội bộ của Chrome
            # (chrome-error://...), đụng độ điều hướng — chờ ổn định rồi thử lại 1 lần.
            page.wait_for_timeout(1000)
            page.goto(wb_url, wait_until="domcontentloaded")
        return wb_url


def _capture(conn, cfg: Config, topic_id: str, page, p: Platform, url: str, fc: dict) -> None:
    actual_url = _goto_with_wayback_fallback(page, url)
    _guard_checkpoint(page)
    if fc["collect_comments"]:
        _expand_comments(page, fc["max_comments_per_post"])
    title = url.rstrip("/").rsplit("/", 1)[-1] or url
    store_and_queue(conn, cfg, topic_id, page.content().encode("utf-8"), "html",
                    "text/html", f"{p.key}_post", actual_url, title, "browser_assisted")
    if fc.get("screenshot", False):
        try:  # screenshot best-effort — lỗi (headless/sandbox) không chặn, HTML đã lưu
            shot = page.screenshot(full_page=True, timeout=8000)
            store_and_queue(conn, cfg, topic_id, shot, "png",
                            "image/png", f"{p.key}_screenshot", actual_url, title, "browser_assisted")
        except Exception:  # noqa: BLE001
            pass
    if fc.get("download_images", False):
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
    lo = p.min_delay if p.min_delay is not None else fc["min_delay_seconds"]
    hi = p.max_delay if p.max_delay is not None else fc["max_delay_seconds"]
    captured = 0
    with sync_playwright() as pw:
        context, launched = _attach(pw, fc, profile_suffix=f"-{p.key}")
        page = context.new_page()
        try:
            urls = [value] if mode == "url" else _discover(page, p, mode, value, fc)
            for i, url in enumerate(urls):
                _guard_checkpoint(page)
                _capture(conn, cfg, topic_id, page, p, url, fc)
                captured += 1
                if i + 1 < len(urls):  # delay giữa các bài, không delay sau bài cuối
                    time.sleep(random.uniform(lo, hi))
        finally:
            page.close()
            if launched:
                context.close()
    return captured
