from datetime import datetime
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.constants.enums import CycleState, OrderState
from src.utils.time import now

class ChangeOrder(Base):
    """两厂各自连续编号的变更单。同厂同号不同内容并存，等待厂方裁定。"""
    __tablename__ = 'change_orders'
    __table_args__ = (UniqueConstraint('factory', 'order_no', 'payload_hash'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    factory: Mapped[str] = mapped_column(String(20), index=True)
    order_no: Mapped[int]
    action: Mapped[str] = mapped_column(String(10))
    spec_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str | None] = mapped_column(String(120))
    fit_traits: Mapped[str | None] = mapped_column(String(600))
    payload_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(10), default=OrderState.HELD, index=True)
    ruled: Mapped[bool] = mapped_column(default=False)
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)

class DeliveryNotice(Base):
    """厂方交割号，声明本厂变更单覆盖到哪个编号。"""
    __tablename__ = 'delivery_notices'
    __table_args__ = (UniqueConstraint('factory', 'delivery_no', 'covers_through'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    factory: Mapped[str] = mapped_column(String(20), index=True)
    delivery_no: Mapped[int]
    covers_through: Mapped[int]
    state: Mapped[str] = mapped_column(String(10), default=OrderState.HELD, index=True)
    ruled: Mapped[bool] = mapped_column(default=False)
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)

class SettlementCycle(Base):
    """两厂交割号配对成的交割周期，发布时一次性切换归并批次。"""
    __tablename__ = 'settlement_cycles'
    __table_args__ = (UniqueConstraint('notice_1_id'), UniqueConstraint('notice_2_id'))

    id: Mapped[int] = mapped_column(primary_key=True)
    notice_1_id: Mapped[int] = mapped_column(ForeignKey('delivery_notices.id'))
    notice_2_id: Mapped[int] = mapped_column(ForeignKey('delivery_notices.id'))
    state: Mapped[str] = mapped_column(String(12), default=CycleState.COLLECTING, index=True)
    detail: Mapped[str] = mapped_column(String(500), default='')
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'), unique=True)
    base_dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'))
    attempt: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=now)
    published_at: Mapped[datetime | None]
