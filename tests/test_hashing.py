"""哈希链工具单元测试（仅依赖标准库）。"""

from __future__ import annotations

import unittest

from app.hashing import (
    GENESIS_HASH,
    canonical_json,
    record_hash,
    record_payload,
    request_hash,
)


class HashingTests(unittest.TestCase):
    def test_canonical_json_is_deterministic(self):
        a = canonical_json({"b": 1, "a": "中文"})
        b = canonical_json({"a": "中文", "b": 1})
        self.assertEqual(a, b)
        self.assertIn("中文", a)

    def test_request_hash_stable(self):
        body = {"exhibit_id": "EX-1", "records": [{"seal_no": "S1"}]}
        self.assertEqual(request_hash(body), request_hash(dict(body)))

    def test_record_hash_chain_linkage(self):
        p1 = record_payload(
            exhibit_id="EX-1", seq=0, location="库房", from_person="张三",
            to_person="李四", seal_no="S1", seal_change_reason=None,
            occurred_at_utc="2026-09-20T01:00:00+00:00",
            voucher_digest="sha256:x", prev_record_hash=GENESIS_HASH,
        )
        h1 = record_hash(p1)
        p2 = record_payload(
            exhibit_id="EX-1", seq=1, location="上海", from_person="李四",
            to_person="王五", seal_no="S1", seal_change_reason=None,
            occurred_at_utc="2026-09-22T06:30:00+00:00",
            voucher_digest="sha256:y", prev_record_hash=h1,
        )
        h2 = record_hash(p2)
        self.assertNotEqual(h1, h2)
        # 篡改任一字段 -> 哈希变化
        p2_tampered = dict(p2, seal_no="S2")
        self.assertNotEqual(record_hash(p2_tampered), h2)


if __name__ == "__main__":
    unittest.main()
