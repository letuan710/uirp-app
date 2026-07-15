"""Registry nền tảng mạng xã hội (ADR-009).

Mỗi nền tảng khai báo metadata; connector generic (manual/browser) dùng chung. Mode A
(lưu tay) chạy cho MỌI nền tảng ở đây ngay. Mode B tự động chỉ khả thi khi `auto=True`
và vẫn cần tinh chỉnh selector ở lần chạy thật (ADR-007/009).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from uirp.errors import ConfigError


@dataclass(frozen=True)
class Platform:
    key: str
    display: str
    region: str  # "VN" | "CN" | "global"
    base_url: str
    auto: bool  # Mode B tự động khả thi (khung generic) hay không
    search_url: str | None = None   # template có {q}
    profile_url: str | None = None  # template có {v}
    group_url: str | None = None    # template có {v}
    post_hints: tuple[str, ...] = ()
    note: str = ""


_P: list[Platform] = [
    # --- Web nói chung (báo chí, blog, mọi trang) qua máy tìm kiếm — ADR-011 ---
    Platform("google", "Google (web/báo chí)", "global", "https://www.google.com", True,
             "https://www.google.com/search?q={q}", None, None, ("http",),
             note="Thu trang ĐÍCH trong kết quả tìm (mọi nguồn web); link nội bộ Google bị loại."),
    # --- VN / global ---
    Platform("facebook", "Facebook", "global", "https://www.facebook.com", True,
             "https://www.facebook.com/search/posts/?q={q}",
             "https://www.facebook.com/{v}", "https://www.facebook.com/groups/{v}",
             ("/posts/", "/permalink/", "story_fbid=", "/videos/", "/photo")),
    Platform("youtube", "YouTube", "global", "https://www.youtube.com", True,
             "https://www.youtube.com/results?search_query={q}",
             "https://www.youtube.com/@{v}", None, ("/watch?v=", "/shorts/")),
    Platform("tiktok", "TikTok", "global", "https://www.tiktok.com", True,
             "https://www.tiktok.com/search?q={q}", "https://www.tiktok.com/@{v}",
             None, ("/video/",)),
    Platform("instagram", "Instagram", "global", "https://www.instagram.com", True,
             None, "https://www.instagram.com/{v}/", None, ("/p/", "/reel/")),
    Platform("threads", "Threads", "global", "https://www.threads.net", True,
             None, "https://www.threads.net/@{v}", None, ("/post/",)),
    Platform("x", "X (Twitter)", "global", "https://x.com", True,
             "https://x.com/search?q={q}", "https://x.com/{v}", None, ("/status/",)),
    Platform("zalo", "Zalo", "VN", "https://zalo.me", False,
             None, None, None, (), "Chủ yếu là app, nội dung đóng → dùng Mode A (chụp màn hình)."),
    Platform("voz", "Voz Forum", "VN", "https://voz.vn", True,
             None, None, None, ("/t/", "/p/"), "Forum HTML — Mode A/B đều dễ."),
    Platform("tinhte", "Tinh Tế", "VN", "https://tinhte.vn", True,
             None, None, None, ("/thread/", "/p/"), "Forum HTML."),
    # --- China ---
    Platform("weibo", "微博 Weibo", "CN", "https://weibo.com", True,
             "https://s.weibo.com/weibo?q={q}", "https://weibo.com/{v}", None, ("/detail/",)),
    Platform("bilibili", "哔哩哔哩 Bilibili", "CN", "https://www.bilibili.com", True,
             "https://search.bilibili.com/all?keyword={q}", "https://space.bilibili.com/{v}",
             None, ("/video/",)),
    Platform("zhihu", "知乎 Zhihu", "CN", "https://www.zhihu.com", True,
             "https://www.zhihu.com/search?q={q}", None, None, ("/question/", "/answer/", "/p/")),
    Platform("tieba", "百度贴吧 Tieba", "CN", "https://tieba.baidu.com", True,
             "https://tieba.baidu.com/f/search/res?qw={q}", None, None, ("/p/",)),
    Platform("xiaohongshu", "小红书 RED", "CN", "https://www.xiaohongshu.com", False,
             None, None, None, ("/explore/",),
             "Anti-bot mạnh + chữ ký JS (như MediaCrawler) → Mode A."),
    Platform("douyin", "抖音 Douyin", "CN", "https://www.douyin.com", False,
             None, None, None, ("/video/",),
             "Anti-bot mạnh + chữ ký JS → Mode A."),
    Platform("kuaishou", "快手 Kuaishou", "CN", "https://www.kuaishou.com", False,
             None, None, None, ("/short-video/",),
             "Anti-bot mạnh + chữ ký JS → Mode A."),
]

REGISTRY: dict[str, Platform] = {p.key: p for p in _P}


def get(key: str) -> Platform:
    p = REGISTRY.get(key)
    if p is None:
        raise ConfigError(
            f"nền tảng không rõ: {key!r}. Xem: uirp platforms"
        )
    return p


def all_platforms() -> list[Platform]:
    return _P
