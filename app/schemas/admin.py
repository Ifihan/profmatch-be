from datetime import datetime
from pydantic import BaseModel


class MetricsResponse(BaseModel):
    total_users: int
    active_users: int
    total_searches: int
    paid_users: int


class PromoResponse(BaseModel):
    id: str
    code: str
    credits: int
    max_redemptions: int | None = None
    times_redeemed: int
    is_disabled: bool


class CreatePromoResponse(BaseModel):
    id: str
    code: str


class PromoToggleResponse(BaseModel):
    ok: bool
    is_disabled: bool


class OkResponse(BaseModel):
    ok: bool


class UserSummary(BaseModel):
    id: str
    name: str
    email: str
    is_admin: bool
    is_disabled: bool
    created_at: datetime


class UserDetail(UserSummary):
    credit_balance: int