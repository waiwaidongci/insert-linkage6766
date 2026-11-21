"""两厂变更单与交割的领域口径测试。"""

import pytest

from src.domain import settlement as domain


def test_sequence_held_marks_gap_followers():
    assert domain.sequence_held([1, 2, 3]) == set()
    assert domain.sequence_held([1, 2, 4]) == {4}
    assert domain.sequence_held([2, 3]) == {2, 3}
    assert domain.sequence_held([1, 3, 4, 5]) == {3, 4, 5}
    # 已交割区段（小于连续起点的编号）不会卡住后续
    assert domain.sequence_held([1, 2, 3, 5, 6]) == {5, 6}


def test_conflict_numbers_only_for_distinct_contents():
    assert domain.conflict_numbers([1, 2, 2, 3]) == {2}
    assert domain.conflict_numbers([1, 2, 3]) == set()


def test_normalize_content_revoke_ignores_payload():
    content = domain.normalize_content('revoke', ' CP-A01 ', '忽略', '忽略')
    assert content == {'action': 'revoke', 'spec_id': 'CP-A01', 'name': '', 'fit_traits': ''}


def test_normalize_content_add_requires_name_and_traits():
    with pytest.raises(ValueError):
        domain.normalize_content('add', 'CP-X01', '', '箱座')
    with pytest.raises(ValueError):
        domain.normalize_content('correct', 'CP-X01', '名称', '')
    with pytest.raises(ValueError):
        domain.normalize_content('add', 'CP-X01', '名称', '箱座||缓冲')


def test_normalize_content_dedupes_and_sorts_traits():
    content = domain.normalize_content('add', 'CP-X01', '名称', ' 缓冲 | 箱座 |缓冲 ')
    assert content['fit_traits'] == '|'.join(sorted({'缓冲', '箱座'}))
    assert domain.payload_hash(content) == domain.payload_hash(
        domain.normalize_content('add', 'CP-X01', '名称', '箱座|缓冲'))


def test_apply_orders_add_correct_revoke_with_provenance():
    base_rows = [
        {'spec_id': 'CP-A01', 'name': '甲', 'fit_traits': '箱座'},
        {'spec_id': 'CP-B01', 'name': '乙', 'fit_traits': '弧面'},
    ]
    orders = [
        ('factory-1', 1, 'add', 'CP-C01', '丙', '分隔'),
        ('factory-2', 2, 'correct', 'cp-a01', '甲改', '箱座|缓冲'),
        ('factory-1', 3, 'revoke', 'CP-B01', '', ''),
        ('factory-2', 4, 'revoke', 'CP-Z99', '', ''),  # 缺失规格撤销为空操作
    ]
    rows = domain.apply_orders(base_rows, orders)
    by_id = {row['spec_id']: row for row in rows}
    assert set(by_id) == {'CP-A01', 'CP-C01'}
    assert by_id['CP-A01']['name'] == '甲改'
    assert by_id['CP-A01']['source_factory'] == 'factory-2'
    assert by_id['CP-A01']['source_order_no'] == 2
    assert by_id['CP-C01']['source_factory'] == 'factory-1'
    assert by_id['CP-C01']['source_order_no'] == 1


def test_apply_orders_keeps_carried_provenance():
    base_rows = [{'spec_id': 'CP-A01', 'name': '甲', 'fit_traits': '箱座',
                  'source_factory': 'factory-1', 'source_order_no': 7}]
    rows = domain.apply_orders(base_rows, [('factory-2', 1, 'correct', 'CP-B01', '乙', '弧面')])
    carried = next(row for row in rows if row['spec_id'] == 'CP-A01')
    assert carried['source_factory'] == 'factory-1'
    assert carried['source_order_no'] == 7


def test_apply_orders_result_sorted_by_spec_id():
    rows = domain.apply_orders(
        [{'spec_id': 'CP-C01', 'name': '丙', 'fit_traits': '分隔'}],
        [('factory-1', 1, 'add', 'CP-A01', '甲', '箱座')])
    assert [row['spec_id'] for row in rows] == ['CP-A01', 'CP-C01']
