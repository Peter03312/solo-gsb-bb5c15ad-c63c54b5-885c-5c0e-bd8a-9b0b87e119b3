-- 文物借展封签交接核验服务 — 数据库初始化脚本
-- 由 docker-compose 挂载至 /docker-entrypoint-initdb.d/，仅在数据卷为空时执行一次。

-- 展品登记：首站校验的基准（原始地点 + 原始保管人）
CREATE TABLE IF NOT EXISTS exhibits (
    exhibit_id        TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    origin_location   TEXT NOT NULL,
    origin_custodian  TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 核验报告：每次批次提交（无论通过与否）留存一份，
-- 同时承载幂等语义：idempotency_key 唯一，request_hash 绑定请求体指纹。
CREATE TABLE IF NOT EXISTS verifications (
    verification_id   UUID PRIMARY KEY,
    idempotency_key   TEXT NOT NULL UNIQUE,
    request_hash      TEXT NOT NULL,
    exhibit_id        TEXT NOT NULL REFERENCES exhibits (exhibit_id),
    status            TEXT NOT NULL CHECK (status IN ('ACCEPTED', 'REJECTED')),
    first_break_index INTEGER,
    report            JSONB NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 交接记录（审计链）：仅核验通过的批次才会写入；
-- seq 为展品维度全局递增序号，prev_record_hash/record_hash 构成防篡改哈希链。
CREATE TABLE IF NOT EXISTS handover_records (
    id                 BIGSERIAL PRIMARY KEY,
    exhibit_id         TEXT NOT NULL REFERENCES exhibits (exhibit_id),
    verification_id    UUID NOT NULL REFERENCES verifications (verification_id),
    seq                INTEGER NOT NULL,
    batch_index        INTEGER NOT NULL,
    location           TEXT NOT NULL,
    from_person        TEXT NOT NULL,
    to_person          TEXT NOT NULL,
    seal_no            TEXT NOT NULL,
    seal_change_reason TEXT,
    occurred_at        TIMESTAMPTZ NOT NULL,
    voucher_digest     TEXT NOT NULL,
    prev_record_hash   TEXT NOT NULL,
    record_hash        TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (exhibit_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_records_exhibit_seq ON handover_records (exhibit_id, seq);
CREATE INDEX IF NOT EXISTS idx_verifications_exhibit ON verifications (exhibit_id, created_at);
