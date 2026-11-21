from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import Role
from src.controllers.auth import allow, current
from src.models.identity import User
from src.schemas.requests import (ChangeOrderRequest, NoticeRequest, NoticeRulingRequest,
                                  OrderRulingRequest)
from src.services import settlement
from src.utils import views
from src.utils.wakeup import wake

router = APIRouter(prefix='/settlement')


@router.get('/board')
def board(db: Session = Depends(connection), actor: User = Depends(current)):
    view = settlement.board(db)
    db.commit()
    return view


@router.post('/orders', status_code=201)
def submit_order(body: ChangeOrderRequest, db: Session = Depends(connection),
                 actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    row, created, flag = settlement.submit_order(
        db, factory=body.factory, order_no=body.order_no, action=body.action,
        spec_id=body.spec_id, name=body.name, fit_traits=body.fit_traits, actor=actor.username)
    db.commit()
    wake()
    return {**views.change_order(row), 'created': created, 'flag': flag}


@router.post('/orders/rulings', status_code=200)
def rule_order(body: OrderRulingRequest, db: Session = Depends(connection),
               actor: User = Depends(allow(Role.ADMIN))):
    row = settlement.rule_order(db, factory=body.factory, order_no=body.order_no,
                                keep_id=body.keep_id, actor=actor.username)
    db.commit()
    wake()
    return views.change_order(row)


@router.post('/notices', status_code=201)
def submit_notice(body: NoticeRequest, db: Session = Depends(connection),
                  actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    row, created, flag = settlement.submit_notice(
        db, factory=body.factory, delivery_no=body.delivery_no,
        covers_through=body.covers_through, actor=actor.username)
    db.commit()
    wake()
    return {**views.notice(row), 'created': created, 'flag': flag}


@router.post('/notices/rulings', status_code=200)
def rule_notice(body: NoticeRulingRequest, db: Session = Depends(connection),
                actor: User = Depends(allow(Role.ADMIN))):
    row = settlement.rule_notice(db, factory=body.factory, delivery_no=body.delivery_no,
                                 keep_id=body.keep_id, actor=actor.username)
    db.commit()
    wake()
    return views.notice(row)


@router.post('/cycles/{cycle_id}/retry', status_code=200)
def retry_cycle(cycle_id: int, db: Session = Depends(connection),
                actor: User = Depends(allow(Role.ADMIN))):
    cycle = settlement.retry_cycle(db, cycle_id, actor.username)
    if cycle is None:
        raise HTTPException(404, '找不到该交割周期')
    db.commit()
    wake()
    notices = settlement.cycle_notices(db, cycle)
    return views.cycle(cycle, notices, [], None)
