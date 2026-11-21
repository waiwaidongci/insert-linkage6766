from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from src.constants.enums import ChangeAction, Review

class Request(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')

class Credentials(Request):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

class Upload(Request):
    name: str = Field(min_length=1, max_length=120)
    filename: str = Field(min_length=1, max_length=160)
    csv_text: str = Field(min_length=1, max_length=2_000_000)

class RunRequest(Request):
    dataset_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    params: dict = Field(default_factory=dict)

class ReviewRequest(Request):
    review: Review
    note: str = Field(min_length=1, max_length=500)
    decision: str = Field(default='', max_length=120)

class ScheduleRequest(RunRequest):
    recipient: str = Field(min_length=1, max_length=100)
    first_at: datetime
    repeat_daily: bool = False

class ChangeOrderRequest(Request):
    factory: str = Field(min_length=1, max_length=20)
    order_no: int = Field(gt=0, le=1_000_000)
    action: ChangeAction
    spec_id: str = Field(min_length=1, max_length=80)
    name: str = Field(default='', max_length=120)
    fit_traits: str = Field(default='', max_length=600)

class OrderRulingRequest(Request):
    factory: str = Field(min_length=1, max_length=20)
    order_no: int = Field(gt=0, le=1_000_000)
    keep_id: int = Field(gt=0)

class NoticeRequest(Request):
    factory: str = Field(min_length=1, max_length=20)
    delivery_no: int = Field(gt=0, le=1_000_000)
    covers_through: int = Field(gt=0, le=1_000_000)

class NoticeRulingRequest(Request):
    factory: str = Field(min_length=1, max_length=20)
    delivery_no: int = Field(gt=0, le=1_000_000)
    keep_id: int = Field(gt=0)
