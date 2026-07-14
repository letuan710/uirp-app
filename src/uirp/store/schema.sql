-- UIRP schema v1 — khớp 1:1 với UIRP-ONT-001 §5 (ONT-R8).
-- Đổi schema = tăng số version + thêm file migrations/NNN (STD4-R9/R10).
-- FTS5 (search, bước 6) và cột embedding lấp dữ liệu (bước P2) thêm ở migration sau.

PRAGMA foreign_keys = ON;

-- Metadata của chính DB (STD4-R9): schema_version để migration forward-only.
CREATE TABLE _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 1. Research Topic (CHR-017, ONT §3.7)
CREATE TABLE topic (
    id          TEXT PRIMARY KEY,           -- top_<ulid>
    name        TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'active',  -- active | paused | archived
    created_at  TEXT NOT NULL
);

-- 2. Information Object — khái niệm cha mọi nguồn (CHR-024, ONT §3.1)
CREATE TABLE information_object (
    id              TEXT PRIMARY KEY,       -- io_<ulid>
    source_type     TEXT NOT NULL,          -- facebook_post | facebook_screenshot | web_page | ...
    source_url      TEXT,                   -- có thể NULL (Mode A file thả tay)
    title           TEXT,
    topic_id        TEXT NOT NULL REFERENCES topic(id),
    published_time  TEXT,                   -- nguồn công bố (ONT-R2)
    captured_time   TEXT,                   -- hệ thống thu (ONT-R2)
    prev_version_id TEXT REFERENCES information_object(id),  -- version chain (CHR-013)
    evidence_id     TEXT NOT NULL REFERENCES evidence(id),
    created_at      TEXT NOT NULL
);

-- 3. Evidence — bất biến (P9/ONT-R1, CHR-012)
CREATE TABLE evidence (
    id               TEXT PRIMARY KEY,      -- ev_<ulid>
    content_hash     TEXT NOT NULL UNIQUE,  -- SHA-256, dedup (CHR-043, ONT-R7)
    file_path        TEXT NOT NULL,         -- tương đối trong data/evidence/
    media_type       TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL,
    capture_method   TEXT NOT NULL,         -- manual_ingest | browser_assisted
    captured_time    TEXT NOT NULL,
    tombstoned_at    TEXT,                  -- NULL trừ khi xóa pháp lý (CHR-032)
    tombstone_reason TEXT,
    created_at       TEXT NOT NULL
);

-- 4. Observation — máy bóc ra, trung tính (ONT §3.3)
CREATE TABLE observation (
    id                 TEXT PRIMARY KEY,    -- obs_<ulid>
    evidence_id        TEXT NOT NULL REFERENCES evidence(id),
    kind               TEXT NOT NULL,       -- body_text | author_display | comment | ocr_text | translation
    content            TEXT NOT NULL,
    locator            TEXT,                -- JSON: vị trí trong nguồn
    lang               TEXT,
    derived_from_obs_id TEXT REFERENCES observation(id),  -- bản dịch trỏ về gốc (ONT-R3)
    provenance         TEXT,                -- JSON: model/version/tier/prompt/confidence (STD3-R3)
    embedding          BLOB,                -- NULL ở giai đoạn 1 (ADR-005, ARC-016b)
    created_at         TEXT NOT NULL
);

-- 5. Claim — tuyên bố có chủ thể phát ngôn (ONT §3.4, ONT-R4)
CREATE TABLE claim (
    id                    TEXT PRIMARY KEY, -- clm_<ulid>
    statement             TEXT NOT NULL,
    asserted_by_entity_id TEXT REFERENCES entity(id),  -- có thể NULL nếu chưa resolve
    event_time            TEXT,             -- thời điểm sự việc (ONT-R2)
    observation_id        TEXT NOT NULL REFERENCES observation(id),
    provenance            TEXT,
    embedding             BLOB,             -- NULL ở giai đoạn 1
    created_at            TEXT NOT NULL
);

-- 6. Entity — danh tính, Canonical ID (ONT §3.5)
CREATE TABLE entity (
    id             TEXT PRIMARY KEY,        -- ent_<ulid>
    entity_type    TEXT NOT NULL,           -- person | organization | product | place | event | concept
    canonical_name TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active',  -- active | merged_into:<id> (ONT-R5)
    created_at     TEXT NOT NULL
);

-- 7. Entity Alias (ONT §3.5)
CREATE TABLE entity_alias (
    id                TEXT PRIMARY KEY,     -- ali_<ulid>
    entity_id         TEXT NOT NULL REFERENCES entity(id),
    alias             TEXT NOT NULL,
    lang              TEXT,
    first_seen_obs_id TEXT REFERENCES observation(id),
    created_at        TEXT NOT NULL
);

-- 8. Merge Proposal — máy đề xuất, Owner quyết (ONT-R5, ARC-016c)
CREATE TABLE merge_proposal (
    id           TEXT PRIMARY KEY,          -- mrg_<ulid>
    entity_id_a  TEXT NOT NULL REFERENCES entity(id),
    entity_id_b  TEXT NOT NULL REFERENCES entity(id),
    confidence   REAL,                      -- technical confidence (CHR-005)
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    created_at   TEXT NOT NULL
);

-- 9. Relationship — tựa vào Claim (ONT §3.6, ONT-R6)
CREATE TABLE relationship (
    id                TEXT PRIMARY KEY,     -- rel_<ulid>
    subject_entity_id TEXT NOT NULL REFERENCES entity(id),
    predicate         TEXT NOT NULL,
    object_entity_id  TEXT NOT NULL REFERENCES entity(id),
    claim_id          TEXT NOT NULL REFERENCES claim(id),
    created_at        TEXT NOT NULL
);

-- 10. Job — hàng đợi durable (CHR-056/057, ARC §8, DGM-002)
CREATE TABLE job (
    id          TEXT PRIMARY KEY,           -- job_<ulid>
    job_type    TEXT NOT NULL,              -- ingest | capture | parse | extract | translate | report | ...
    state       TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING | RUNNING | DONE | FAILED | WAITING_QUOTA
    payload     TEXT,                       -- JSON
    retry_at    TEXT,                       -- WAITING_QUOTA: mốc thử lại
    retry_count INTEGER NOT NULL DEFAULT 0, -- ARC-013b
    error       TEXT,
    error_kind  TEXT,                       -- tên lớp exception (STD4-R5)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX idx_job_state ON job(state, created_at);

-- 11. Usage Log — token/chi phí mỗi lời gọi AI (CHR-054, STD5-R1)
CREATE TABLE usage_log (
    id           TEXT PRIMARY KEY,          -- usg_<ulid>
    job_id       TEXT REFERENCES job(id),
    backend      TEXT NOT NULL,             -- claude_agent_sdk | api_key
    model        TEXT NOT NULL,
    tier         TEXT,                      -- S | M | L
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    est_cost_usd REAL,                      -- NULL với backend thuê bao
    prompt       TEXT,                      -- <name>@<version> (STD3-R3)
    called_at    TEXT NOT NULL
);

-- 12. Annotation — của con người, tách riêng (tầng 4, CHR-038/039, ONT §3.8)
CREATE TABLE annotation (
    id         TEXT PRIMARY KEY,            -- ann_<ulid>
    target_id  TEXT NOT NULL,              -- ID object bất kỳ được ghi chú
    body       TEXT NOT NULL,
    verdict    TEXT,                        -- credible | doubtful | false | noted (chỉ người đặt — CHR-004)
    created_at TEXT NOT NULL
);

INSERT INTO _meta(key, value) VALUES ('schema_version', '1');
