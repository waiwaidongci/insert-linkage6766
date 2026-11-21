"""两厂变更单签收、厂方裁定与交割批次切换的数据库流程。"""

import logging
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from src.config.database import Session
from src.constants.enums import CycleState, OrderState
from src.domain import settlement as domain
from src.domain.analysis import validate_rows
from src.models.settlement import ChangeOrder, DeliveryNotice, SettlementCycle
from src.models.source import Dataset
from src.services.audit import record
from src.services.datasets import save
from src.utils import views
from src.utils.time import now

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_LEASE_MINUTES = 3


# ---------- 查询助手 ----------

def _orders(db, factory):
    """一厂全部未作废变更单，按编号升序。"""
    return db.scalars(select(ChangeOrder)
                      .where(ChangeOrder.factory == factory, ChangeOrder.state != OrderState.VOID)
                      .order_by(ChangeOrder.order_no, ChangeOrder.id)).all()


def _notices(db, factory):
    return db.scalars(select(DeliveryNotice)
                      .where(DeliveryNotice.factory == factory, DeliveryNotice.state != OrderState.VOID)
                      .order_by(DeliveryNotice.delivery_no, DeliveryNotice.id)).all()


def _refresh(rows, number_of):
    """按连续编号口径重排暂存与就位状态，已交割记录保持不变。"""
    held = domain.sequence_held(sorted({number_of(row) for row in rows}))
    for row in rows:
        if row.state == OrderState.APPLIED:
            continue
        row.state = OrderState.HELD if number_of(row) in held else OrderState.READY


def _refresh_factory(db, factory):
    _refresh(_orders(db, factory), lambda row: row.order_no)
    _refresh(_notices(db, factory), lambda row: row.delivery_no)


def _current_dataset_id(db):
    """当前归并批次：最近一期已发布交割的批次，否则取最早导入的台账。"""
    published = db.scalar(select(SettlementCycle.dataset_id)
                          .where(SettlementCycle.state == CycleState.PUBLISHED)
                          .order_by(SettlementCycle.id.desc()).limit(1))
    if published:
        return published
    return db.scalar(select(Dataset.id).order_by(Dataset.id).limit(1))


def cycle_notices(db, cycle):
    first = db.get(DeliveryNotice, cycle.notice_1_id)
    second = db.get(DeliveryNotice, cycle.notice_2_id)
    return {domain.FACTORIES[0]: first, domain.FACTORIES[1]: second}


def _issues(db, cycle):
    """交割周期就绪前仍待解决的事项：缺号、待裁定冲突或缺少基线台账。"""
    issues = []
    if _current_dataset_id(db) is None:
        issues.append('尚未导入基线台账，请先在规格与适配特征导入')
    for factory, notice in cycle_notices(db, cycle).items():
        label = domain.FACTORY_LABEL[factory]
        siblings = [item for item in _notices(db, factory) if item.delivery_no == notice.delivery_no]
        if len(siblings) > 1:
            issues.append(f'{label}交割号 {notice.delivery_no} 号待厂方裁定')
        rows = _orders(db, factory)
        present = [row.order_no for row in rows]
        missing = domain.missing_numbers(present, notice.covers_through)
        if missing:
            issues.append(f'{label}变更单缺 {domain.compact_numbers(missing)} 号')
        disputed = sorted(number for number in domain.conflict_numbers(present)
                          if number <= notice.covers_through)
        if disputed:
            issues.append(f'{label}变更单 {domain.compact_numbers(disputed)} 号待厂方裁定')
    return issues


def repair_cycles(db):
    """把两厂已就位的交割号按顺序配对成周期，并刷新收集中的说明。"""
    paired = set()
    for notice_id in db.scalars(select(SettlementCycle.notice_1_id)).all():
        paired.add(notice_id)
    for notice_id in db.scalars(select(SettlementCycle.notice_2_id)).all():
        paired.add(notice_id)
    waiting = []
    for factory in domain.FACTORIES:
        singles = {}
        for notice in _notices(db, factory):
            if notice.state != OrderState.READY or notice.id in paired:
                continue
            singles.setdefault(notice.delivery_no, []).append(notice)
        # 同号内容冲突未裁定时不参与配对
        waiting.append([group[0] for _, group in sorted(singles.items()) if len(group) == 1])
    for first, second in zip(*waiting):
        cycle = SettlementCycle(notice_1_id=first.id, notice_2_id=second.id)
        db.add(cycle)
        db.flush()
        record(db, 'worker', 'settlement.paired', cycle.id,
               notice_1_id=first.id, notice_2_id=second.id)
    collecting = db.scalars(select(SettlementCycle)
                            .where(SettlementCycle.state == CycleState.COLLECTING)).all()
    for cycle in collecting:
        issues = _issues(db, cycle)
        cycle.detail = '；'.join(issues) if issues else '就绪，等待切换'


# ---------- 变更单与交割号签收 ----------

def _insert(db, model, lookup, row):
    """按内容指纹幂等入库；同号不同内容并存即形成待裁定冲突。"""
    existing = db.scalar(lookup)
    if existing:
        return existing, False
    db.add(row)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        existing = db.scalar(lookup)
        if existing:
            return existing, False
        raise
    return row, True


def submit_order(db, *, factory, order_no, action, spec_id, name, fit_traits, actor):
    domain.check_factory(factory)
    content = domain.normalize_content(action, spec_id, name, fit_traits)
    digest = domain.payload_hash({'factory': factory, 'order_no': order_no, **content})
    lookup = select(ChangeOrder).where(ChangeOrder.factory == factory,
                                       ChangeOrder.order_no == order_no,
                                       ChangeOrder.payload_hash == digest)
    row = ChangeOrder(factory=factory, order_no=order_no, action=content['action'],
                      spec_id=content['spec_id'], name=content['name'] or None,
                      fit_traits=content['fit_traits'] or None, payload_hash=digest, author=actor)
    row, created = _insert(db, ChangeOrder, lookup, row)
    if not created:
        return row, False, 'duplicate'
    _refresh_factory(db, factory)
    repair_cycles(db)
    siblings = [item for item in _orders(db, factory) if item.order_no == order_no]
    flag = 'conflict' if len(siblings) > 1 else row.state
    record(db, actor, 'change_order.received', row.id, factory=factory, order_no=order_no, flag=flag)
    return row, True, flag


def submit_notice(db, *, factory, delivery_no, covers_through, actor):
    domain.check_factory(factory)
    if covers_through < 1:
        raise ValueError('交割号覆盖到的变更单号须从 1 起。')
    lookup = select(DeliveryNotice).where(DeliveryNotice.factory == factory,
                                          DeliveryNotice.delivery_no == delivery_no,
                                          DeliveryNotice.covers_through == covers_through)
    row = DeliveryNotice(factory=factory, delivery_no=delivery_no,
                         covers_through=covers_through, author=actor)
    row, created = _insert(db, DeliveryNotice, lookup, row)
    if not created:
        return row, False, 'duplicate'
    _refresh_factory(db, factory)
    repair_cycles(db)
    siblings = [item for item in _notices(db, factory) if item.delivery_no == delivery_no]
    flag = 'conflict' if len(siblings) > 1 else row.state
    record(db, actor, 'notice.received', row.id, factory=factory, delivery_no=delivery_no, flag=flag)
    return row, True, flag


# ---------- 厂方裁定 ----------

def _rule(db, model, factory, number_of, number, keep_id, actor, event):
    rows = [row for row in db.scalars(select(model).where(model.factory == factory,
                                                          model.state != OrderState.VOID,
                                                          number_of == number)).all()]
    if len(rows) < 2:
        raise ValueError('该编号没有待裁定的内容冲突。')
    keep = next((row for row in rows if row.id == keep_id), None)
    if keep is None:
        raise ValueError('裁定采用的记录须属于该厂该编号。')
    if any(row.state == OrderState.APPLIED for row in rows) and keep.state != OrderState.APPLIED:
        raise ValueError('该编号已随交割发布，裁定只能保留已交割记录；如需更正请由厂方签发新编号。')
    voided = [row for row in rows if row.id != keep_id]
    for row in rows:
        if row.id == keep_id:
            row.ruled = True
        else:
            row.state = OrderState.VOID
    _refresh_factory(db, factory)
    record(db, actor, event, keep.id, factory=factory, number=number)
    return keep, voided


def rule_order(db, *, factory, order_no, keep_id, actor):
    keep, _ = _rule(db, ChangeOrder, factory, ChangeOrder.order_no, order_no, keep_id,
                    actor, 'change_order.ruled')
    repair_cycles(db)
    return keep


def rule_notice(db, *, factory, delivery_no, keep_id, actor):
    keep, voided = _rule(db, DeliveryNotice, factory, DeliveryNotice.delivery_no, delivery_no,
                         keep_id, actor, 'notice.ruled')
    # 被作废的交割号若已配对进未发布的周期，周期改挂裁定采用的记录并重新收集
    for column in (SettlementCycle.notice_1_id, SettlementCycle.notice_2_id):
        cycles = db.scalars(select(SettlementCycle)
                            .where(column.in_([row.id for row in voided]),
                                   SettlementCycle.state.in_([CycleState.COLLECTING,
                                                              CycleState.SWITCHING]))
                            .with_for_update()).all()
        for cycle in cycles:
            if cycle.state not in (CycleState.COLLECTING, CycleState.SWITCHING):
                continue
            if column.key == 'notice_1_id':
                cycle.notice_1_id = keep.id
            else:
                cycle.notice_2_id = keep.id
            cycle.state = CycleState.COLLECTING
            cycle.attempt = 0
            cycle.lease_until = None
    repair_cycles(db)
    return keep


# ---------- 交割周期切换（后台驱动，可恢复且幂等） ----------

def claim_cycle():
    """认领一个就绪或租约过期的周期；多次中断的周期置为失败等待重试。"""
    stamp = now()
    with Session.begin() as db:
        repair_cycles(db)
        candidates = db.scalars(
            select(SettlementCycle)
            .where(or_(SettlementCycle.state == CycleState.COLLECTING,
                       and_(SettlementCycle.state == CycleState.SWITCHING,
                            SettlementCycle.lease_until < stamp)))
            .order_by(SettlementCycle.id).limit(5).with_for_update(skip_locked=True)).all()
        for cycle in candidates:
            if cycle.state == CycleState.SWITCHING and cycle.attempt >= _MAX_ATTEMPTS:
                cycle.state = CycleState.FAILED
                cycle.detail = '切换多次中断，请核对两厂数据后重试'
                record(db, 'worker', 'settlement.failed', cycle.id, reason=cycle.detail)
                continue
            if cycle.state == CycleState.COLLECTING:
                issues = _issues(db, cycle)
                cycle.detail = '；'.join(issues) if issues else '就绪，等待切换'
                if issues:
                    continue
            cycle.state = CycleState.SWITCHING
            cycle.attempt += 1
            cycle.lease_until = stamp + timedelta(minutes=_LEASE_MINUTES)
            return cycle.id, cycle.attempt
    return None


def publish_cycle(claim):
    """单事务切换归并批次：崩溃即回滚，租约到期后重新认领，重试不重复发布。"""
    cycle_id, attempt = claim
    try:
        with Session.begin() as db:
            cycle = db.get(SettlementCycle, cycle_id, with_for_update=True)
            if cycle.attempt != attempt or cycle.state != CycleState.SWITCHING:
                return
            issues = _issues(db, cycle)
            if issues:
                # 认领后又出现缺号或冲突，退回收集等待
                cycle.state = CycleState.COLLECTING
                cycle.lease_until = None
                cycle.detail = '；'.join(issues)
                return
            notices = cycle_notices(db, cycle)
            base_id = _current_dataset_id(db)
            base = db.get(Dataset, base_id)
            orders = []
            for factory, notice in notices.items():
                orders += [(factory, row.order_no, row.action, row.spec_id, row.name, row.fit_traits)
                           for row in _orders(db, factory)
                           if row.state == OrderState.READY and row.order_no <= notice.covers_through]
            orders.sort(key=lambda item: (item[1], item[0]))
            rows = domain.apply_orders(base.rows, orders)
            validate_rows(rows)  # 保证新批次满足归并口径
            dataset, _ = save(db, f'两厂交割批次 · 第 {cycle.id} 期', f'两厂交割_第{cycle.id:04d}期.json',
                              rows, 'settlement')
            for factory, notice in notices.items():
                for row in _orders(db, factory):
                    if row.state == OrderState.READY and row.order_no <= notice.covers_through:
                        row.state = OrderState.APPLIED
                notice.state = OrderState.APPLIED
            cycle.dataset_id = dataset.id
            cycle.base_dataset_id = base.id
            cycle.state = CycleState.PUBLISHED
            cycle.lease_until = None
            cycle.published_at = now()
            cycle.detail = f'已切换到 {dataset.row_count} 条生效规格'
            record(db, 'worker', 'settlement.published', cycle.id,
                   dataset_id=dataset.id, rows=dataset.row_count)
    except Exception:
        log.exception('Settlement cycle %s publish interrupted', cycle_id)


def drive():
    """后台主循环入口：逐个发布就绪周期，直到没有可认领的周期。"""
    for _ in range(20):
        claim = claim_cycle()
        if claim is None:
            return
        publish_cycle(claim)


def retry_cycle(db, cycle_id, actor):
    """人工重试失败或卡住的周期；已发布的周期保持幂等不重复发布。"""
    cycle = db.get(SettlementCycle, cycle_id, with_for_update=True)
    if cycle is None:
        return None
    if cycle.state == CycleState.PUBLISHED:
        return cycle
    if cycle.state == CycleState.SWITCHING and cycle.lease_until and cycle.lease_until > now():
        raise ValueError('该周期正在切换，请稍候再试。')
    cycle.state = CycleState.COLLECTING
    cycle.attempt = 0
    cycle.lease_until = None
    record(db, actor, 'settlement.retried', cycle.id)
    repair_cycles(db)
    return cycle


# ---------- 看板视图 ----------

def board(db):
    repair_cycles(db)
    factories = []
    conflicts = {'orders': {}, 'notices': {}}
    for factory in domain.FACTORIES:
        orders = _orders(db, factory)
        notices = _notices(db, factory)
        order_conflicts = domain.conflict_numbers([row.order_no for row in orders])
        notice_conflicts = domain.conflict_numbers([row.delivery_no for row in notices])
        conflicts['orders'][factory] = sorted(order_conflicts)
        conflicts['notices'][factory] = sorted(notice_conflicts)
        factories.append({
            'key': factory, 'label': domain.FACTORY_LABEL[factory],
            'applied': sum(row.state == OrderState.APPLIED for row in orders),
            'ready': sum(row.state == OrderState.READY for row in orders),
            'held': sum(row.state == OrderState.HELD for row in orders),
            'conflicts': sorted(order_conflicts),
        })
    order_rows = db.scalars(select(ChangeOrder).order_by(ChangeOrder.id.desc()).limit(100)).all()
    notice_rows = db.scalars(select(DeliveryNotice).order_by(DeliveryNotice.id.desc()).limit(100)).all()
    cycles = db.scalars(select(SettlementCycle).order_by(SettlementCycle.id.desc()).limit(50)).all()
    paired = {cycle.notice_1_id: cycle.id for cycle in cycles}
    paired.update({cycle.notice_2_id: cycle.id for cycle in cycles})
    current_id = _current_dataset_id(db)
    current = None
    if current_id is not None:
        dataset = db.get(Dataset, current_id)
        cycle = db.scalar(select(SettlementCycle).where(SettlementCycle.dataset_id == current_id))
        current = {'dataset_id': dataset.id, 'name': dataset.name, 'row_count': dataset.row_count,
                   'cycle_id': cycle.id if cycle else None}
    return {
        'factories': factories,
        'orders': [views.change_order(row, conflict=row.order_no in conflicts['orders'].get(row.factory, [])
                                      and row.state != OrderState.VOID) for row in order_rows],
        'notices': [views.notice(row, conflict=row.delivery_no in conflicts['notices'].get(row.factory, [])
                                 and row.state != OrderState.VOID,
                                 cycle_id=paired.get(row.id)) for row in notice_rows],
        'cycles': [views.cycle(cycle, cycle_notices(db, cycle),
                               _issues(db, cycle) if cycle.state == CycleState.COLLECTING else [],
                               current_id) for cycle in cycles],
        'current': current,
    }
