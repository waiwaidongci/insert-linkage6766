"""两厂签收、裁定与交割切换的端到端流程测试（SQLite）。

claim_cycle/drive 使用独立会话，只读取已提交数据，因此准备数据后先 commit。
"""

from datetime import timedelta

import pytest

from src.constants.enums import CycleState, OrderState
from src.models.settlement import ChangeOrder, DeliveryNotice, SettlementCycle
from src.models.source import Dataset
from src.services import settlement
from src.utils.time import now


def _order(db, factory, order_no, action='add', spec_id='CP-X01', name='名称', traits='箱座'):
    return settlement.submit_order(db, factory=factory, order_no=order_no, action=action,
                                   spec_id=spec_id, name=name, fit_traits=traits, actor='test')


def _notice(db, factory, delivery_no, covers_through):
    return settlement.submit_notice(db, factory=factory, delivery_no=delivery_no,
                                    covers_through=covers_through, actor='test')


def _cycles(db):
    return db.query(SettlementCycle).order_by(SettlementCycle.id).all()


def test_out_of_order_order_is_held_then_ready(db, base):
    late, created, flag = _order(db, 'factory-1', 2)
    assert created and flag == 'held' and late.state == OrderState.HELD
    first, _, flag = _order(db, 'factory-1', 1)
    assert flag == 'ready'
    db.refresh(late)
    assert late.state == OrderState.READY


def test_identical_resubmit_is_idempotent(db, base):
    first, created, _ = _order(db, 'factory-1', 1)
    again, created_again, flag = _order(db, 'factory-1', 1)
    assert created and not created_again and flag == 'duplicate'
    assert again.id == first.id
    assert db.query(ChangeOrder).count() == 1


def test_conflict_waits_for_factory_ruling(db, base):
    keep, _, _ = _order(db, 'factory-1', 1, name='版本甲')
    other, created, flag = _order(db, 'factory-1', 1, name='版本乙')
    assert created and flag == 'conflict'
    board = settlement.board(db)
    assert board['factories'][0]['conflicts'] == [1]
    settlement.rule_order(db, factory='factory-1', order_no=1, keep_id=other.id, actor='admin')
    db.refresh(keep)
    db.refresh(other)
    assert keep.state == OrderState.VOID
    assert other.ruled and other.state == OrderState.READY
    assert settlement.board(db)['factories'][0]['conflicts'] == []


def test_ruling_requires_existing_conflict(db, base):
    row, _, _ = _order(db, 'factory-1', 1)
    with pytest.raises(ValueError):
        settlement.rule_order(db, factory='factory-1', order_no=1, keep_id=row.id, actor='admin')


def test_notice_pairs_into_cycle_and_publishes_with_provenance(db, base):
    _order(db, 'factory-1', 1, spec_id='CP-D01', name='新增丁', traits='箱座|缓冲')
    _order(db, 'factory-2', 1, action='correct', spec_id='CP-A01', name='甲改', traits='箱座|缓冲')
    _notice(db, 'factory-1', 1, 1)
    _notice(db, 'factory-2', 1, 1)
    db.commit()
    settlement.drive()
    cycles = _cycles(db)
    assert len(cycles) == 1
    cycle = cycles[0]
    assert cycle.state == CycleState.PUBLISHED
    assert cycle.base_dataset_id == base.id
    batch = db.get(Dataset, cycle.dataset_id)
    assert batch.id != base.id
    by_id = {row['spec_id']: row for row in batch.rows}
    assert by_id['CP-D01']['source_factory'] == 'factory-1'
    assert by_id['CP-D01']['source_order_no'] == 1
    assert by_id['CP-A01']['name'] == '甲改'
    assert by_id['CP-A01']['source_factory'] == 'factory-2'
    assert by_id['CP-B01']['name'] == '轴套清洗转运内衬'  # 未触及的行保持原样
    assert 'source_factory' not in by_id['CP-B01']
    # 当前归并批次已一次性切换
    assert settlement._current_dataset_id(db) == batch.id
    orders = {row.order_no: row for row in db.query(ChangeOrder).all()}
    assert all(row.state == OrderState.APPLIED for row in orders.values())


def test_cycle_blocked_until_gap_filled_and_conflict_ruled(db, base):
    _order(db, 'factory-1', 1)
    _order(db, 'factory-1', 3, spec_id='CP-X03')  # 缺 2 号，3 号暂存
    _order(db, 'factory-2', 1, name='版本甲')
    _order(db, 'factory-2', 1, name='版本乙')  # 冲突待裁定
    _notice(db, 'factory-1', 1, 3)
    _notice(db, 'factory-2', 1, 1)
    db.commit()
    settlement.drive()
    cycle = _cycles(db)[0]
    assert cycle.state == CycleState.COLLECTING
    issues = settlement._issues(db, cycle)
    assert any('缺 2 号' in issue for issue in issues)
    assert any('待厂方裁定' in issue for issue in issues)
    assert db.query(Dataset).count() == 1  # 未补齐前不切换
    # 裁定冲突并补齐缺号后自动切换
    rival = db.query(ChangeOrder).filter_by(factory='factory-2', order_no=1, name='版本乙').one()
    settlement.rule_order(db, factory='factory-2', order_no=1, keep_id=rival.id, actor='admin')
    _order(db, 'factory-1', 2, spec_id='CP-X02')
    db.commit()
    settlement.drive()
    db.refresh(cycle)
    assert cycle.state == CycleState.PUBLISHED
    assert db.query(Dataset).count() == 2


def test_interrupted_switch_resumes_without_duplicate_batch(db, base):
    _order(db, 'factory-1', 1)
    _order(db, 'factory-2', 1)
    _notice(db, 'factory-1', 1, 1)
    _notice(db, 'factory-2', 1, 1)
    db.commit()
    claim = settlement.claim_cycle()
    assert claim is not None
    cycle_id, first_attempt = claim
    # 模拟切换中断：认领后未发布，租约未到期前不会重复认领
    assert settlement.claim_cycle() is None
    cycle = db.get(SettlementCycle, cycle_id)
    cycle.lease_until = now() - timedelta(seconds=1)
    db.commit()
    reclaim = settlement.claim_cycle()
    assert reclaim == (cycle_id, first_attempt + 1)
    settlement.publish_cycle(reclaim)
    db.refresh(cycle)
    assert cycle.state == CycleState.PUBLISHED
    batch_id = cycle.dataset_id
    # 重试与再次驱动都不会重复发布同一交割的批次
    settlement.retry_cycle(db, cycle_id, 'admin')
    db.commit()
    settlement.drive()
    assert db.query(Dataset).count() == 2
    assert db.get(SettlementCycle, cycle_id).dataset_id == batch_id


def test_failed_cycle_waits_for_manual_retry(db, base, monkeypatch):
    _order(db, 'factory-1', 1)
    _order(db, 'factory-2', 1)
    _notice(db, 'factory-1', 1, 1)
    _notice(db, 'factory-2', 1, 1)
    db.commit()
    original_save = settlement.save
    monkeypatch.setattr(settlement, 'save',
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('boom')))
    for _ in range(3):
        claim = settlement.claim_cycle()
        if claim is None:
            break
        settlement.publish_cycle(claim)
        cycle = db.get(SettlementCycle, claim[0])
        db.refresh(cycle)
        cycle.lease_until = now() - timedelta(seconds=1)
        db.commit()
    settlement.claim_cycle()  # 触发失败判定
    cycle = _cycles(db)[0]
    db.refresh(cycle)
    assert cycle.state == CycleState.FAILED
    monkeypatch.setattr(settlement, 'save', original_save)
    settlement.retry_cycle(db, cycle.id, 'admin')
    db.commit()
    settlement.drive()
    db.refresh(cycle)
    assert cycle.state == CycleState.PUBLISHED
    assert db.query(Dataset).count() == 2


def test_queued_run_keeps_original_batch(db, base):
    from src.models.jobs import Run
    run = Run(name='排队中的归并', dataset_id=base.id, params={'cut_distance': 0.4}, author='test')
    db.add(run)
    _order(db, 'factory-1', 1, spec_id='CP-D01')
    _order(db, 'factory-2', 1, spec_id='CP-D02')
    _notice(db, 'factory-1', 1, 1)
    _notice(db, 'factory-2', 1, 1)
    db.commit()
    settlement.drive()
    db.refresh(run)
    assert run.dataset_id == base.id  # 已排队归并仍用原批次
    assert settlement._current_dataset_id(db) != base.id


def test_second_cycle_builds_on_first_batch(db, base):
    _order(db, 'factory-1', 1, spec_id='CP-D01')
    _order(db, 'factory-2', 1, spec_id='CP-D02')
    _notice(db, 'factory-1', 1, 1)
    _notice(db, 'factory-2', 1, 1)
    db.commit()
    settlement.drive()
    first_batch_id = settlement._current_dataset_id(db)
    _order(db, 'factory-1', 2, action='revoke', spec_id='CP-D01', name='', traits='')
    _order(db, 'factory-2', 2, action='correct', spec_id='CP-D02', name='丁改', traits='箱座')
    _notice(db, 'factory-1', 2, 2)
    _notice(db, 'factory-2', 2, 2)
    db.commit()
    settlement.drive()
    cycles = _cycles(db)
    assert [cycle.state for cycle in cycles] == [CycleState.PUBLISHED, CycleState.PUBLISHED]
    second = db.get(Dataset, cycles[1].dataset_id)
    by_id = {row['spec_id']: row for row in second.rows}
    assert 'CP-D01' not in by_id
    assert by_id['CP-D02']['name'] == '丁改'
    assert by_id['CP-D02']['source_order_no'] == 2
    assert cycles[1].base_dataset_id == first_batch_id


def test_notice_conflict_blocks_switch_until_ruled(db, base):
    _order(db, 'factory-1', 1)
    _order(db, 'factory-2', 1)
    _notice(db, 'factory-1', 1, 1)
    _notice(db, 'factory-2', 1, 1)
    db.commit()
    assert len(_cycles(db)) == 1  # 交割号先完成配对
    _notice(db, 'factory-2', 1, 99)  # 同号不同内容 → 冲突
    db.commit()
    settlement.drive()
    cycle = _cycles(db)[0]
    db.refresh(cycle)
    assert cycle.state == CycleState.COLLECTING
    issues = settlement._issues(db, cycle)
    assert any('交割号' in issue and '裁定' in issue for issue in issues)
    # 裁定采用覆盖 99 号的版本后，周期改挂该交割号，但因变更单未补齐继续收集
    rival = db.query(DeliveryNotice).filter_by(factory='factory-2', covers_through=99).one()
    settlement.rule_notice(db, factory='factory-2', delivery_no=1, keep_id=rival.id, actor='admin')
    db.commit()
    db.refresh(cycle)
    assert cycle.notice_2_id == rival.id
    settlement.drive()
    db.refresh(cycle)
    assert cycle.state == CycleState.COLLECTING
    issues = settlement._issues(db, cycle)
    assert any('缺' in issue and '澄浦二厂' in issue for issue in issues)


def test_applied_record_conflict_ruling_keeps_delivered_row(db, base):
    _order(db, 'factory-1', 1, name='已交割版本')
    _order(db, 'factory-2', 1)
    _notice(db, 'factory-1', 1, 1)
    _notice(db, 'factory-2', 1, 1)
    db.commit()
    settlement.drive()
    late, created, flag = _order(db, 'factory-1', 1, name='迟到版本')
    assert created and flag == 'conflict'
    db.commit()
    # 已交割记录不能作废，裁定只能保留已交割版本，迟到记录作废
    with pytest.raises(ValueError):
        settlement.rule_order(db, factory='factory-1', order_no=1, keep_id=late.id, actor='admin')
    delivered = db.query(ChangeOrder).filter_by(factory='factory-1', order_no=1, name='已交割版本').one()
    settlement.rule_order(db, factory='factory-1', order_no=1, keep_id=delivered.id, actor='admin')
    db.refresh(late)
    db.refresh(delivered)
    assert late.state == OrderState.VOID
    assert delivered.state == OrderState.APPLIED and delivered.ruled
