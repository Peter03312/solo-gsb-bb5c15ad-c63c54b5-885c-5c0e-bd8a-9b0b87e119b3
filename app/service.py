"""交接批次核验核心逻辑（纯标准库，不依赖 Web 框架，便于单测）。

校验规则（对批次内每条记录逐项判定）：
1. 首站校验：该展品尚无任何已接受记录时，批次首条记录的交接地点必须等于
   展品登记的原始地点，且交出人必须等于登记的原始保管人；
2. 人员衔接：本站交出人必须等于上一站接收人（上一站为链尾或批次内前一条）；
3. 时间递增：本站交接时间必须严格晚于上一站；
4. 封签连续性：本站封签号与上一站不一致时，必须携带换签授权理由。

返回逐项结果；首个 ok=False 的下标即为首个断点。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

# 原因码 -> 中文说明（同时写入 API 响应，便于调用方直接展示）
REASON_MESSAGES = {
    "FIRST_STATION_LOCATION_MISMATCH": "首站交接地点与展品登记的原始地点不一致",
    "FIRST_STATION_CUSTODIAN_MISMATCH": "首站交出人与展品登记的原始保管人不一致",
    "PERSON_CHAIN_BROKEN": "人员衔接断裂：本站交出人与上一站接收人不一致",
    "TIME_NOT_INCREASING": "交接时间未严格递增：必须晚于上一站交接时间",
    "SEAL_CHANGED_WITHOUT_REASON": "封签号发生变更但未提供换签授权理由",
}


class RecordLike(Protocol):
    location: str
    from_person: str
    to_person: str
    seal_no: str
    occurred_at: datetime
    voucher_digest: str
    seal_change_reason: str | None


def _reasons(codes: list[str]) -> list[dict]:
    return [{"code": c, "message": REASON_MESSAGES[c]} for c in codes]


def validate_records(
    exhibit: Mapping[str, Any],
    tail: Mapping[str, Any] | None,
    records: Sequence[RecordLike],
) -> list[dict]:
    """逐项校验，返回 [{"index", "ok", "reasons": [{"code", "message"}]}]。

    exhibit: 展品登记信息（需含 origin_location / origin_custodian）。
    tail:    当前审计链链尾记录（需含 to_person / occurred_at / seal_no），
             为 None 表示该展品尚无任何已接受记录，触发首站校验。
    """
    items: list[dict] = []
    for i, rec in enumerate(records):
        codes: list[str] = []
        if i == 0 and tail is None:
            # 首站校验
            if rec.location != exhibit["origin_location"]:
                codes.append("FIRST_STATION_LOCATION_MISMATCH")
            if rec.from_person != exhibit["origin_custodian"]:
                codes.append("FIRST_STATION_CUSTODIAN_MISMATCH")
        else:
            # 上一站：批次首条取链尾，否则取批次内前一条
            if i == 0:
                prev_to = tail["to_person"]  # type: ignore[index]
                prev_time = tail["occurred_at"]  # type: ignore[index]
                prev_seal = tail["seal_no"]  # type: ignore[index]
            else:
                prev = records[i - 1]
                prev_to, prev_time, prev_seal = (
                    prev.to_person,
                    prev.occurred_at,
                    prev.seal_no,
                )
            # 人员衔接
            if rec.from_person != prev_to:
                codes.append("PERSON_CHAIN_BROKEN")
            # 时间严格递增
            if rec.occurred_at <= prev_time:
                codes.append("TIME_NOT_INCREASING")
            # 封签连续性：换签必须带授权理由
            if rec.seal_no != prev_seal and not rec.seal_change_reason:
                codes.append("SEAL_CHANGED_WITHOUT_REASON")
        items.append({"index": i, "ok": not codes, "reasons": _reasons(codes)})
    return items


def first_break_index(items: Sequence[Mapping[str, Any]]) -> int | None:
    """首个断点下标；全部通过返回 None。"""
    for it in items:
        if not it["ok"]:
            return it["index"]  # type: ignore[return-value]
    return None
