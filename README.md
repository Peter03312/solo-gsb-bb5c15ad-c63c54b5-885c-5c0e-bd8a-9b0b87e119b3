# 文物借展封签交接核验服务

Python (FastAPI) + PostgreSQL 实现的文物借展交接核验服务。调用方提交展品编号与按时间排列的交接记录，
服务完成**首站校验、人员衔接、时间递增、封签连续性**核验（**换签必须携带授权理由**），
提供**幂等提交**、**失败批次零写入**与**可查询的哈希链审计**。

## 快速开始

```bash
# 构建并启动（PostgreSQL 16 + API，首次启动自动执行 db/init.sql 建表）
docker compose up --build -d

# 健康检查
curl http://localhost:8000/healthz

# 运行端到端调用示例（登记展品 → 提交批次 → 幂等重试 → 失败批次 → 审计链）
./examples/demo.sh

# 交互式 API 文档
open http://localhost:8000/docs
```

停止并清空数据：`docker compose down -v`（init.sql 仅在空数据卷首次启动时执行）。

### 配置

| 环境变量       | 默认值                                                        | 说明           |
| -------------- | ------------------------------------------------------------- | -------------- |
| `DATABASE_URL` | `postgresql://handover:handover@localhost:5432/handover`      | PostgreSQL DSN |

Compose 中 API 通过 `DATABASE_URL=postgresql://handover:handover@db:5432/handover` 连接数据库。

## 核验规则

对批次内每条记录逐项判定（第 `i>0` 条的"上一站"为批次内前一条；批次首条的"上一站"为审计链链尾）：

| 规则         | 说明                                                                 |
| ------------ | -------------------------------------------------------------------- |
| 首站校验     | 展品尚无已接受记录时，批次首条的交接地点/交出人必须等于登记的原始地点/原始保管人 |
| 人员衔接     | 本站交出人 == 上一站接收人                                            |
| 时间递增     | 本站交接时间严格晚于上一站（时间必须携带时区）                         |
| 封签连续性   | 封签号与上一站不一致时，必须携带 `seal_change_reason`（换签授权理由）   |

### 原因码

| code                              | 含义                                       |
| --------------------------------- | ------------------------------------------ |
| `FIRST_STATION_LOCATION_MISMATCH` | 首站交接地点与登记原始地点不一致           |
| `FIRST_STATION_CUSTODIAN_MISMATCH`| 首站交出人与登记原始保管人不一致           |
| `PERSON_CHAIN_BROKEN`             | 本站交出人与上一站接收人不一致             |
| `TIME_NOT_INCREASING`             | 交接时间未严格递增                         |
| `SEAL_CHANGED_WITHOUT_REASON`     | 封签变更但未提供换签授权理由               |

## 幂等与原子性

- 提交批次必须携带请求头 `Idempotency-Key`。
- **相同键 + 相同请求体**：重试返回同一核验结果（同一 `verification_id`，`replayed=true`，
  响应头 `X-Idempotent-Replay: true`），不会重复写入交接记录。
- **相同键 + 不同请求体**：返回 `409`。
- **含错误的批次不写入任何交接记录**：核验报告与交接记录在同一事务中提交，
  只有全部通过（`ACCEPTED`）才追加审计链；失败批次仅留存核验报告（用于幂等重放与审计追溯）。

## API 一览

| 方法 | 路径                                     | 说明                                   |
| ---- | ---------------------------------------- | -------------------------------------- |
| POST | `/v1/exhibits`                           | 登记展品（同编号同信息重复登记幂等）   |
| GET  | `/v1/exhibits/{id}`                      | 展品信息 + 链长 + 链尾摘要             |
| POST | `/v1/handover-batches`                   | 提交交接批次核验（需 `Idempotency-Key`）|
| GET  | `/v1/verifications/{verification_id}`    | 查询核验报告（含被拒绝批次）           |
| GET  | `/v1/exhibits/{id}/verifications`        | 展品的核验报告列表                     |
| GET  | `/v1/exhibits/{id}/chain`                | 已接受交接记录构成的审计链             |
| GET  | `/v1/exhibits/{id}/chain/verify`         | 重算哈希链，校验完整性（防篡改）       |
| GET  | `/healthz`                               | 健康检查                               |

### 调用示例

```bash
# 1. 登记展品（首站基准）
curl -X POST http://localhost:8000/v1/exhibits \
  -H 'Content-Type: application/json' \
  -d @examples/payloads/01-exhibit.json

# 2. 提交交接批次
curl -X POST http://localhost:8000/v1/handover-batches \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-batch-a' \
  -d @examples/payloads/02-batch-a.json
```

请求体：

```json
{
  "exhibit_id": "EX-2026-001",
  "records": [
    {
      "location": "故宫博物院文物库房",
      "from_person": "张三",
      "to_person": "李四",
      "seal_no": "SEAL-0001",
      "occurred_at": "2026-09-20T09:00:00+08:00",
      "voucher_digest": "sha256:3a7b..."
    }
  ]
}
```

响应（通过）：

```json
{
  "verification_id": "7c9e2c1a-....",
  "exhibit_id": "EX-2026-001",
  "status": "ACCEPTED",
  "first_break_index": null,
  "items": [{"index": 0, "ok": true, "reasons": []}],
  "accepted_count": 1,
  "chain_length": 1,
  "idempotency_key": "demo-batch-a",
  "replayed": false,
  "created_at": "2026-10-05T..."
}
```

响应（拒绝，含首个断点与逐项原因）：

```json
{
  "status": "REJECTED",
  "first_break_index": 0,
  "items": [
    {
      "index": 0,
      "ok": false,
      "reasons": [
        {"code": "SEAL_CHANGED_WITHOUT_REASON", "message": "封签号发生变更但未提供换签授权理由"}
      ]
    }
  ],
  "accepted_count": 0
}
```

## 审计链

- 通过的批次按展品维度全局序号 `seq` 追加到 `handover_records`；
- 每条记录携带 `prev_record_hash` 与 `record_hash`（对规范化字段的 SHA-256），
  构成防篡改哈希链，链首 `prev_record_hash` 为 64 个 `0`；
- `GET /v1/exhibits/{id}/chain/verify` 重算整条链，检出序号缺口、链接断裂或内容篡改。

## 本地开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# 单元测试（核心校验与哈希逻辑不依赖数据库）
python -m pytest tests/ -v        # 或 python -m unittest discover -s tests -v

# 本地启动（需先启动 PostgreSQL 并执行 db/init.sql）
export DATABASE_URL=postgresql://handover:handover@localhost:5432/handover
uvicorn app.main:app --reload
```

## 目录结构

```
app/
  main.py       # FastAPI 路由：登记、核验、审计查询
  service.py    # 核心核验逻辑（纯标准库，可独立单测）
  hashing.py    # 规范化序列化 / 请求指纹 / 记录哈希链
  schemas.py    # Pydantic 请求模型
  db.py         # 连接池
db/init.sql     # 建表脚本（Compose 首次启动自动执行）
examples/       # 调用示例 payload 与 demo.sh
tests/          # 单元测试
Dockerfile / docker-compose.yml / requirements*.txt
```
