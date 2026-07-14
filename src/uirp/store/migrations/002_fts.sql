-- Migration 002: FTS5 cho search (ARC-016, bước 6). Forward-only (STD4-R10).
-- Nếu build SQLite thiếu FTS5, migration lỗi → db.py bắt và bỏ qua có kiểm soát.

CREATE VIRTUAL TABLE observation_fts USING fts5(content, obs_id UNINDEXED);
CREATE VIRTUAL TABLE claim_fts USING fts5(statement, clm_id UNINDEXED);

-- Đồng bộ tự động khi có bản ghi mới.
CREATE TRIGGER observation_fts_ai AFTER INSERT ON observation BEGIN
  INSERT INTO observation_fts(content, obs_id) VALUES (new.content, new.id);
END;
CREATE TRIGGER claim_fts_ai AFTER INSERT ON claim BEGIN
  INSERT INTO claim_fts(statement, clm_id) VALUES (new.statement, new.id);
END;

-- Nạp lại dữ liệu đã có trước migration.
INSERT INTO observation_fts(content, obs_id) SELECT content, id FROM observation;
INSERT INTO claim_fts(statement, clm_id) SELECT statement, id FROM claim;
