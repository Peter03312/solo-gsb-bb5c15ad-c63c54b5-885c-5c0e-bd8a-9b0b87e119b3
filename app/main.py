"""文物借展封签交接核验服务 — HTTP API。

核验语义：
- 批次整体校验：全部记录通过才写入审计链（事务内原子提交）；
- 含错误的批次不写入任何交接记录，仅留存核验报告（用于幂等重放与审计）；
- 幂等：同一 Idempotency-Key + 相同请求体，重试返回同一核验结果（同一 verification_id）；
  同一 Idempotency-Key 携带不同请求体，返回 409。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from . import db
from .hashing import GENESIS_HASH, record_hash, record_payload, request_hash
from .schemas import BatchRequest, ExhibitIn
from .service import first_break_index, validate_records


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_pool()
    yield
    db.close_pool()


app = FastAPI(
    title="文物借展封签交接核验服务",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(UniqueViolation)
async def unique_violation_handler(_: Request, exc: UniqueViolation) -> JSONResponse:
    # 并发下同键/同编号冲突：提示客户端按原请求体重试即可拿到同一结果
    return JSONResponse(
        status_code=409,
        content={"detail": "唯一性冲突（Idempotency-Key 或展品编号并发冲突），请以原请求体重试"},
    )


# ---------------------------------------------------------------- 健康检查


@app.get("/healthz")
def healthz() -> dict:
    with db.get_pool().connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


# ---------------------------------------------------------------- 展品登记


def _exhibit_out(row: dict) -> dict:
    return {
        "exhibit_id": row["exhibit_id"],
        "name": row["name"],
        "origin_location": row["origin_location"],
        "origin_custodian": row["origin_custodian"],
        "created_at": row["created_at"].isoformat(),
    }


@app.post("/v1/exhibits", status_code=201)
def register_exhibit(body: ExhibitIn, response: Response) -> dict:
    """登记展品。同编号同信息重复登记为幂等成功（200）；信息不一致返回 409。"""
    with db.get_pool().connection() as conn:
        row = conn.execute(
            "SELECT * FROM exhibits WHERE exhibit_id = %s", (body.exhibit_id,)
        ).fetchone()
        if row is not None:
            same = (
                row["name"] == body.name
                and row["origin_location"] == body.origin_location
                and row["origin_custodian"] == body.origin_custodian
            )
            if not same:
                raise HTTPException(409, "展品编号已存在且登记信息不一致")
            response.status_code = 200
            return _exhibit_out(row)
        conn.execute(
            "INSERT INTO exhibits (exhibit_id, name, origin_location, origin_custodian)"
            " VALUES (%s, %s, %s, %s)",
            (body.exhibit_id, body.name, body.origin_location, body.origin_custodian),
        )
        row = conn.execute(
            "SELECT * FROM exhibits WHERE exhibit_id = %s", (body.exhibit_id,)
        ).fetchone()
        return _exhibit_out(row)


@app.get("/v1/exhibits/{exhibit_id}")
def get_exhibit(exhibit_id: str) -> dict:
    with db.get_pool().connection() as conn:
        row = conn.execute(
            "SELECT * FROM exhibits WHERE exhibit_id = %s", (exhibit_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"展品 {exhibit_id} 未登记")
        tail = conn.execute(
            "SELECT seq, record_hash, seal_no, to_person, occurred_at"
            " FROM handover_records WHERE exhibit_id = %s ORDER BY seq DESC LIMIT 1",
            (exhibit_id,),
        ).fetchone()
    out = _exhibit_out(row)
    out["chain_length"] = (tail["seq"] + 1) if tail else 0
    out["chain_tail"] = (
        {
            "seq": tail["seq"],
            "record_hash": tail["record_hash"],
            "seal_no": tail["seal_no"],
            "to_person": tail["to_person"],
            "occurred_at": tail["occurred_at"].isoformat(),
        }
        if tail
        else None
    )
    return out


# ---------------------------------------------------------------- 批次核验


def _insert_accepted_records(
    conn, exhibit_id: str, verification_id: str, tail: dict | None, records
) -> None:
    """批次通过后将记录追加进审计链（哈希链），与核验报告同事务提交。"""
    prev_hash = tail["record_hash"] if tail else GENESIS_HASH
    seq = (tail["seq"] + 1) if tail else 0
    for batch_index, rec in enumerate(records):
        occurred_utc = rec.occurred_at.astimezone(timezone.utc).isoformat()
        payload = record_payload(
            exhibit_id=exhibit_id,
            seq=seq,
            location=rec.location,
            from_person=rec.from_person,
            to_person=rec.to_person,
            seal_no=rec.seal_no,
            seal_change_reason=rec.seal_change_reason,
            occurred_at_utc=occurred_utc,
            voucher_digest=rec.voucher_digest,
            prev_record_hash=prev_hash,
        )
        h = record_hash(payload)
        conn.execute(
            "INSERT INTO handover_records ("
            " exhibit_id, verification_id, seq, batch_index, location,"
            " from_person, to_person, seal_no, seal_change_reason,"
            " occurred_at, voucher_digest, prev_record_hash, record_hash)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                exhibit_id,
                verification_id,
                seq,
                batch_index,
                rec.location,
                rec.from_person,
                rec.to_person,
                rec.seal_no,
                rec.seal_change_reason,
                rec.occurred_at,
                rec.voucher_digest,
                prev_hash,
                h,
            ),
        )
        prev_hash = h
        seq += 1


@app.post("/v1/handover-batches")
def submit_batch(
    body: BatchRequest,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=200),
) -> dict:
    """提交交接批次进行核验。需携带 Idempotency-Key 请求头保证幂等。"""
    rhash = request_hash(body.model_dump(mode="json"))
    with db.get_pool().connection() as conn:
        # 锁定展品行，串行化同一展品的批次提交，保证链尾读取一致
        exhibit = conn.execute(
            "SELECT * FROM exhibits WHERE exhibit_id = %s FOR UPDATE",
            (body.exhibit_id,),
        ).fetchone()
        if exhibit is None:
            raise HTTPException(404, f"展品 {body.exhibit_id} 未登记，请先登记")

        # 幂等重放：同键同体直接返回已存报告
        existing = conn.execute(
            "SELECT request_hash, report FROM verifications WHERE idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != rhash:
                raise HTTPException(409, "Idempotency-Key 已被不同的请求体使用")
            report = dict(existing["report"])
            report["replayed"] = True
            response.headers["X-Idempotent-Replay"] = "true"
            return report

        tail = conn.execute(
            "SELECT * FROM handover_records WHERE exhibit_id = %s"
            " ORDER BY seq DESC LIMIT 1",
            (body.exhibit_id,),
        ).fetchone()

        items = validate_records(exhibit, tail, body.records)
        brk = first_break_index(items)
        status = "ACCEPTED" if brk is None else "REJECTED"
        verification_id = str(uuid.uuid4())
        base_seq = (tail["seq"] + 1) if tail else 0
        accepted_count = len(body.records) if status == "ACCEPTED" else 0
        now = datetime.now(timezone.utc)
        report: dict[str, Any] = {
            "verification_id": verification_id,
            "exhibit_id": body.exhibit_id,
            "status": status,
            "first_break_index": brk,
            "items": items,
            "accepted_count": accepted_count,
            "chain_length": base_seq + accepted_count,
            "idempotency_key": idempotency_key,
            "replayed": False,
            "created_at": now.isoformat(),
        }
        # 核验报告（含失败批次）始终留存，供幂等重放与审计查询；
        # 仅当批次通过时才写入交接记录（同事务，原子提交）。
        conn.execute(
            "INSERT INTO verifications ("
            " verification_id, idempotency_key, request_hash, exhibit_id,"
            " status, first_break_index, report, created_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                verification_id,
                idempotency_key,
                rhash,
                body.exhibit_id,
                status,
                brk,
                Jsonb(report),
                now,
            ),
        )
        if status == "ACCEPTED":
            _insert_accepted_records(conn, body.exhibit_id, verification_id, tail, body.records)
        return report


# ---------------------------------------------------------------- 审计查询


@app.get("/v1/verifications/{verification_id}")
def get_verification(verification_id: str) -> dict:
    try:
        uuid.UUID(verification_id)
    except ValueError:
        raise HTTPException(400, "verification_id 必须是合法的 UUID") from None
    with db.get_pool().connection() as conn:
        row = conn.execute(
            "SELECT report FROM verifications WHERE verification_id = %s",
            (verification_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"核验报告 {verification_id} 不存在")
    return row["report"]


@app.get("/v1/exhibits/{exhibit_id}/verifications")
def list_verifications(exhibit_id: str, limit: int = 50) -> dict:
    with db.get_pool().connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM exhibits WHERE exhibit_id = %s", (exhibit_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(404, f"展品 {exhibit_id} 未登记")
        rows = conn.execute(
            "SELECT verification_id, idempotency_key, status, first_break_index, created_at"
            " FROM verifications WHERE exhibit_id = %s"
            " ORDER BY created_at DESC, verification_id DESC LIMIT %s",
            (exhibit_id, max(1, min(limit, 200))),
        ).fetchall()
    return {
        "exhibit_id": exhibit_id,
        "verifications": [
            {
                "verification_id": str(r["verification_id"]),
                "idempotency_key": r["idempotency_key"],
                "status": r["status"],
                "first_break_index": r["first_break_index"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ],
    }


def _record_out(r: dict) -> dict:
    return {
        "seq": r["seq"],
        "verification_id": str(r["verification_id"]),
        "batch_index": r["batch_index"],
        "location": r["location"],
        "from_person": r["from_person"],
        "to_person": r["to_person"],
        "seal_no": r["seal_no"],
        "seal_change_reason": r["seal_change_reason"],
        "occurred_at": r["occurred_at"].isoformat(),
        "voucher_digest": r["voucher_digest"],
        "prev_record_hash": r["prev_record_hash"],
        "record_hash": r["record_hash"],
        "created_at": r["created_at"].isoformat(),
    }


@app.get("/v1/exhibits/{exhibit_id}/chain")
def get_chain(exhibit_id: str) -> dict:
    """查询展品的已接受交接审计链。"""
    with db.get_pool().connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM exhibits WHERE exhibit_id = %s", (exhibit_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(404, f"展品 {exhibit_id} 未登记")
        rows = conn.execute(
            "SELECT * FROM handover_records WHERE exhibit_id = %s ORDER BY seq",
            (exhibit_id,),
        ).fetchall()
    return {
        "exhibit_id": exhibit_id,
        "length": len(rows),
        "records": [_record_out(r) for r in rows],
    }


@app.get("/v1/exhibits/{exhibit_id}/chain/verify")
def verify_chain(exhibit_id: str) -> dict:
    """重算哈希链：校验序号连续、哈希链接与内容完整性（防篡改审计）。"""
    with db.get_pool().connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM exhibits WHERE exhibit_id = %s", (exhibit_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(404, f"展品 {exhibit_id} 未登记")
        rows = conn.execute(
            "SELECT * FROM handover_records WHERE exhibit_id = %s ORDER BY seq",
            (exhibit_id,),
        ).fetchall()
    problems: list[dict] = []
    prev_hash = GENESIS_HASH
    for expected_seq, r in enumerate(rows):
        if r["seq"] != expected_seq:
            problems.append({"seq": r["seq"], "problem": "SEQ_GAP"})
        if r["prev_record_hash"] != prev_hash:
            problems.append({"seq": r["seq"], "problem": "HASH_LINK_BROKEN"})
        payload = record_payload(
            exhibit_id=exhibit_id,
            seq=r["seq"],
            location=r["location"],
            from_person=r["from_person"],
            to_person=r["to_person"],
            seal_no=r["seal_no"],
            seal_change_reason=r["seal_change_reason"],
            occurred_at_utc=r["occurred_at"].astimezone(timezone.utc).isoformat(),
            voucher_digest=r["voucher_digest"],
            prev_record_hash=r["prev_record_hash"],
        )
        if record_hash(payload) != r["record_hash"]:
            problems.append({"seq": r["seq"], "problem": "RECORD_HASH_MISMATCH"})
        prev_hash = r["record_hash"]
    return {
        "exhibit_id": exhibit_id,
        "length": len(rows),
        "valid": not problems,
        "problems": problems,
    }
