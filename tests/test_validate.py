"""核心校验逻辑单元测试（仅依赖标准库）。

运行：python3 -m unittest discover -s tests -v   （或 pytest tests/）
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.service import first_break_index, validate_records

TZ8 = timezone(timedelta(hours=8))
T0 = datetime(2026, 9, 20, 9, 0, tzinfo=TZ8)

EXHIBIT = {"origin_location": "故宫博物院文物库房", "origin_custodian": "张三"}


@dataclass
class Rec:
    location: str = "故宫博物院文物库房"
    from_person: str = "张三"
    to_person: str = "李四"
    seal_no: str = "SEAL-0001"
    occurred_at: datetime = T0
    voucher_digest: str = "sha256:abc"
    seal_change_reason: Optional[str] = None


def codes(item: dict) -> list[str]:
    return [r["code"] for r in item["reasons"]]


class FirstStationTests(unittest.TestCase):
    def test_first_station_ok(self):
        items = validate_records(EXHIBIT, None, [Rec()])
        self.assertTrue(items[0]["ok"])

    def test_first_station_location_mismatch(self):
        items = validate_records(EXHIBIT, None, [Rec(location="别处")])
        self.assertIn("FIRST_STATION_LOCATION_MISMATCH", codes(items[0]))

    def test_first_station_custodian_mismatch(self):
        items = validate_records(EXHIBIT, None, [Rec(from_person="李四")])
        self.assertIn("FIRST_STATION_CUSTODIAN_MISMATCH", codes(items[0]))


class ChainRuleTests(unittest.TestCase):
    def test_person_chain_broken_within_batch(self):
        items = validate_records(
            EXHIBIT,
            None,
            [Rec(), Rec(location="上海", from_person="王五", to_person="赵六",
                        occurred_at=T0 + timedelta(hours=1))],
        )
        self.assertTrue(items[0]["ok"])
        self.assertIn("PERSON_CHAIN_BROKEN", codes(items[1]))

    def test_time_must_strictly_increase(self):
        items = validate_records(
            EXHIBIT,
            None,
            [Rec(), Rec(location="上海", from_person="李四", to_person="王五",
                        occurred_at=T0)],  # 与上一站相同时间
        )
        self.assertIn("TIME_NOT_INCREASING", codes(items[1]))

    def test_seal_change_requires_reason(self):
        items = validate_records(
            EXHIBIT,
            None,
            [Rec(), Rec(location="上海", from_person="李四", to_person="王五",
                        seal_no="SEAL-0002", occurred_at=T0 + timedelta(hours=1))],
        )
        self.assertIn("SEAL_CHANGED_WITHOUT_REASON", codes(items[1]))

    def test_seal_change_with_reason_ok(self):
        items = validate_records(
            EXHIBIT,
            None,
            [Rec(), Rec(location="上海", from_person="李四", to_person="王五",
                        seal_no="SEAL-0002", occurred_at=T0 + timedelta(hours=1),
                        seal_change_reason="原封签破损，授权函 SH-2026-091")],
        )
        self.assertTrue(all(it["ok"] for it in items))

    def test_continuation_from_chain_tail(self):
        tail = {"to_person": "李四", "occurred_at": T0, "seal_no": "SEAL-0001"}
        ok_items = validate_records(
            EXHIBIT, tail,
            [Rec(location="上海", from_person="李四", to_person="王五",
                 occurred_at=T0 + timedelta(hours=2))],
        )
        self.assertTrue(ok_items[0]["ok"])
        bad_items = validate_records(
            EXHIBIT, tail,
            [Rec(location="上海", from_person="张三", to_person="王五",
                 occurred_at=T0 + timedelta(hours=2))],
        )
        self.assertIn("PERSON_CHAIN_BROKEN", codes(bad_items[0]))

    def test_tail_none_only_checks_first_record_as_origin(self):
        # 首条触发首站校验；后续条目不触发首站校验
        items = validate_records(
            EXHIBIT,
            None,
            [Rec(), Rec(location="上海", from_person="李四", to_person="王五",
                        occurred_at=T0 + timedelta(hours=1))],
        )
        self.assertTrue(all(it["ok"] for it in items))


class FirstBreakTests(unittest.TestCase):
    def test_first_break_index(self):
        items = validate_records(
            EXHIBIT,
            None,
            [
                Rec(),
                Rec(location="上海", from_person="李四", to_person="王五",
                    occurred_at=T0 + timedelta(hours=1)),
                Rec(location="杭州", from_person="钱七", to_person="孙八",
                    occurred_at=T0 + timedelta(hours=2)),
            ],
        )
        self.assertEqual(first_break_index(items), 2)

    def test_first_break_none_when_all_ok(self):
        items = validate_records(EXHIBIT, None, [Rec()])
        self.assertIsNone(first_break_index(items))


if __name__ == "__main__":
    unittest.main()
