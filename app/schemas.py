"""请求/响应数据模型（Pydantic v2）。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class HandoverRecordIn(BaseModel):
    """单条交接记录（调用方按时间先后排列提交）。"""

    location: str = Field(min_length=1, max_length=200, description="交接地点")
    from_person: str = Field(min_length=1, max_length=100, description="交出人")
    to_person: str = Field(min_length=1, max_length=100, description="接收人")
    seal_no: str = Field(min_length=1, max_length=100, description="封签号")
    occurred_at: datetime = Field(description="交接时间（必须携带时区）")
    voucher_digest: str = Field(min_length=1, max_length=200, description="凭证摘要")
    seal_change_reason: Optional[str] = Field(
        default=None, max_length=500, description="换签授权理由（封签变更时必填）"
    )

    @field_validator("occurred_at")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("occurred_at 必须携带时区偏移，如 2026-10-01T09:00:00+08:00")
        return v

    @field_validator("seal_change_reason")
    @classmethod
    def _blank_reason_as_none(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            return None
        return v


class BatchRequest(BaseModel):
    """交接批次核验请求。"""

    exhibit_id: str = Field(min_length=1, max_length=100, description="展品编号")
    records: list[HandoverRecordIn] = Field(
        min_length=1, max_length=500, description="按时间先后排列的交接记录"
    )


class ExhibitIn(BaseModel):
    """展品登记请求（首站校验的基准）。"""

    exhibit_id: str = Field(min_length=1, max_length=100, description="展品编号")
    name: str = Field(min_length=1, max_length=200, description="展品名称")
    origin_location: str = Field(min_length=1, max_length=200, description="原始地点（首站）")
    origin_custodian: str = Field(min_length=1, max_length=100, description="原始保管人（首站交出人）")
