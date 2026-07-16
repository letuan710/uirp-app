# UIRP — code repo

Universal Intelligence Research Platform — nền tảng nghiên cứu tri thức cá nhân (CLI).
Đặc tả đầy đủ ở docs repo: `C:\Users\Admin\UIRP\` (đọc `README.md` ở đó).

## Chạy nhanh (demo offline, không cần Claude)

```bash
# 1. Cấu hình: copy mẫu, để backend = "fake" (đã có sẵn config.toml demo)
#    cp config.toml.example config.toml   # rồi sửa [api] backend nếu muốn

# 2. Chạy (Windows PowerShell/Git Bash), đặt src lên path:
$env:PYTHONPATH="src"                     # PowerShell
export PYTHONPATH=src                      # Git Bash

python -m uirp init                        # tạo data/ + DB
python -m uirp topic add "Chủ đề của tôi"  # → in ra top_...
# thả file .html/.png Facebook vào data/inbox/facebook/
python -m uirp ingest --topic top_XXX      # nuốt file → evidence + job
python -m uirp run --once                  # parse → extract → tri thức
python -m uirp report --topic top_XXX      # → data/reports/top_XXX.md
python -m uirp web                         # web 1 nút: tìm → quét SONG SONG → tự xử lý → báo cáo
python -m uirp cost                        # token/chi phí theo tier
python -m uirp jobs                         # trạng thái job
python -m uirp doctor                       # gom lỗi FAILED lặp
```

## Dùng Claude thật (trên máy có Claude)

Sửa `config.toml`:
```toml
[api]
backend = "claude_agent_sdk"   # dùng gói thuê bao qua Claude Code đã đăng nhập (mặc định)
# hoặc backend = "api_key"     # trả theo token; đặt env ANTHROPIC_API_KEY
```
Cài SDK tương ứng: `pip install claude-agent-sdk` (hoặc `anthropic`).

## Cấu trúc

```
src/uirp/
  ids.py errors.py config.py cli.py       # nền
  store/{schema.sql,db.py,evidence.py}    # SQLite 12 bảng + evidence theo hash
  core/{jobs.py,demo.py}                  # scheduler durable (state machine)
  ai/{adapter.py,backend_*.py}            # Model Adapter + 3 backend (fake/agent_sdk/api_key)
  prompts/*.md                            # prompt có version (STD-003)
  connectors/facebook_manual.py           # Mode A ingest
  pipeline/{parse.py,extract.py}          # Evidence→Observation→Claim/Entity
  report/markdown.py                      # báo cáo Markdown
tests/                                    # PYTHONPATH=src pytest
```

## Trạng thái (ARC §13)

Xong: bước 1-2 (store + scheduler), 3 (AI adapter + tiering + usage_log),
4 (Mode A ingest), 5 (parse + extract), 6 (report). Còn: search FTS5, merge/annotate,
Mode B (Playwright/CDP), OCR local — xem `UIRP/ROADMAP.md`.

Chạy đầy đủ pipeline offline bằng FakeBackend; đổi 1 dòng config sang Claude thật.
