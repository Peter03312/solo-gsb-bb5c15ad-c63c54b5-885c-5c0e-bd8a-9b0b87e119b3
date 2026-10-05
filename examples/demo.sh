#!/usr/bin/env bash
# 端到端调用示例：登记展品 -> 提交批次 -> 幂等重试 -> 失败批次 -> 审计链查询。
# 用法：先启动服务（docker compose up --build），再执行 examples/demo.sh
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
HERE="$(cd "$(dirname "$0")" && pwd)"
JQ="$(command -v jq || true)"

pp() { if [ -n "$JQ" ]; then jq .; else cat; fi; echo; }

echo "== 0. 健康检查 =="
curl -sS "$BASE/healthz" | pp

echo "== 1. 登记展品 =="
curl -sS -X POST "$BASE/v1/exhibits" \
  -H 'Content-Type: application/json' \
  -d @"$HERE/payloads/01-exhibit.json" | pp

echo "== 2. 提交批次A（首站 + 2 条记录，应 ACCEPTED）=="
curl -sS -X POST "$BASE/v1/handover-batches" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-batch-a' \
  -d @"$HERE/payloads/02-batch-a.json" | pp

echo "== 3. 相同请求重试（同一 verification_id，replayed=true）=="
curl -sS -X POST "$BASE/v1/handover-batches" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-batch-a' \
  -d @"$HERE/payloads/02-batch-a.json" | pp

echo "== 4. 同一 Idempotency-Key 携带不同请求体（应 409）=="
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' -X POST "$BASE/v1/handover-batches" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-batch-a' \
  -d @"$HERE/payloads/03-batch-b-bad.json"

echo "== 5. 批次B：换签未带授权理由（应 REJECTED，首个断点 index=0）=="
curl -sS -X POST "$BASE/v1/handover-batches" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-batch-b-bad' \
  -d @"$HERE/payloads/03-batch-b-bad.json" | pp

echo "== 6. 查询审计链（失败批次未写入，长度应仍为 2）=="
curl -sS "$BASE/v1/exhibits/EX-2026-001/chain" | pp

echo "== 7. 批次B修正：携带换签授权理由（应 ACCEPTED，链长变为 3）=="
curl -sS -X POST "$BASE/v1/handover-batches" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-batch-b-ok' \
  -d @"$HERE/payloads/04-batch-b-ok.json" | pp

echo "== 8. 批次C：人员衔接断裂（应 REJECTED，原因 PERSON_CHAIN_BROKEN）=="
curl -sS -X POST "$BASE/v1/handover-batches" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-batch-c-bad' \
  -d @"$HERE/payloads/05-batch-c-bad.json" | pp

echo "== 9. 审计链与哈希链完整性校验 =="
curl -sS "$BASE/v1/exhibits/EX-2026-001/chain" | pp
curl -sS "$BASE/v1/exhibits/EX-2026-001/chain/verify" | pp

echo "== 10. 该展品的核验报告列表（含被拒绝的批次）=="
curl -sS "$BASE/v1/exhibits/EX-2026-001/verifications" | pp
