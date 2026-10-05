"""规范化序列化与哈希工具（仅依赖标准库，便于单测）。

哈希链设计：
- 每条交接记录写入时计算 record_hash = sha256(canonical_json(payload))，
  payload 中包含 prev_record_hash，形成防篡改的链式结构；
- 链首的 prev_record_hash 为 GENESIS_HASH；
- occurred_at 一律归一化为 UTC 的 ISO-8601 字符串后再参与哈希，
  保证写入与审计重算结果一致。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

GENESIS_HASH = "0" * 64


def canonical_json(obj: Any) -> str:
    """确定性 JSON 序列化：键排序、无空白、保留中文。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_hash(body: Mapping[str, Any]) -> str:
    """请求体指纹，用于幂等键与请求体的绑定校验。"""
    return sha256_hex(canonical_json(body))


def record_payload(
    *,
    exhibit_id: str,
    seq: int,
    location: str,
    from_person: str,
    to_person: str,
    seal_no: str,
    seal_change_reason: str | None,
    occurred_at_utc: str,
    voucher_digest: str,
    prev_record_hash: str,
) -> dict:
    """构造参与哈希的记录载荷（字段固定，写入与审计共用）。"""
    return {
        "exhibit_id": exhibit_id,
        "seq": seq,
        "location": location,
        "from_person": from_person,
        "to_person": to_person,
        "seal_no": seal_no,
        "seal_change_reason": seal_change_reason,
        "occurred_at": occurred_at_utc,
        "voucher_digest": voucher_digest,
        "prev_record_hash": prev_record_hash,
    }


def record_hash(payload: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json(payload))
