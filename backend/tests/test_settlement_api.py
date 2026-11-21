"""交割接口的鉴权与签收行为测试。"""

import pytest
from fastapi.testclient import TestClient

from src.constants.enums import Role
from src.main import app
from src.models.identity import User
from src.utils.security import hash_password


@pytest.fixture()
def client(db):
    for role in Role:
        db.add(User(username=role.value, role=role,
                    password_hash=hash_password(f'{role.value}-pass')))
    db.commit()
    with TestClient(app) as test_client:
        yield test_client


def _login(client, username):
    response = client.post('/auth/login', json={'username': username, 'password': f'{username}-pass'})
    assert response.status_code == 200, response.text
    return {'Authorization': f"Bearer {response.json()['token']}"}


def test_board_requires_login(client):
    assert client.get('/settlement/board').status_code == 401


def test_submit_order_flow_and_permissions(client):
    analyst = _login(client, 'analyst')
    viewer = _login(client, 'viewer')
    admin = _login(client, 'admin')
    denied = client.post('/settlement/orders', headers=viewer,
                         json={'factory': 'factory-1', 'order_no': 1, 'action': 'add',
                               'spec_id': 'CP-X01', 'name': '甲', 'fit_traits': '箱座'})
    assert denied.status_code == 403
    created = client.post('/settlement/orders', headers=analyst,
                          json={'factory': 'factory-1', 'order_no': 1, 'action': 'add',
                                'spec_id': 'CP-X01', 'name': '甲', 'fit_traits': '箱座'})
    assert created.status_code == 201 and created.json()['flag'] == 'ready'
    duplicate = client.post('/settlement/orders', headers=analyst,
                            json={'factory': 'factory-1', 'order_no': 1, 'action': 'add',
                                  'spec_id': 'CP-X01', 'name': '甲', 'fit_traits': '箱座'})
    assert duplicate.status_code == 201 and duplicate.json()['flag'] == 'duplicate'
    conflict = client.post('/settlement/orders', headers=analyst,
                           json={'factory': 'factory-1', 'order_no': 1, 'action': 'add',
                                 'spec_id': 'CP-X01', 'name': '乙', 'fit_traits': '箱座'})
    assert conflict.json()['flag'] == 'conflict'
    board = client.get('/settlement/board', headers=viewer).json()
    assert board['factories'][0]['conflicts'] == [1]
    keep_id = conflict.json()['id']
    denied_rule = client.post('/settlement/orders/rulings', headers=analyst,
                              json={'factory': 'factory-1', 'order_no': 1, 'keep_id': keep_id})
    assert denied_rule.status_code == 403
    ruled = client.post('/settlement/orders/rulings', headers=admin,
                        json={'factory': 'factory-1', 'order_no': 1, 'keep_id': keep_id})
    assert ruled.status_code == 200 and ruled.json()['ruled'] is True
    board = client.get('/settlement/board', headers=viewer).json()
    assert board['factories'][0]['conflicts'] == []


def test_notice_validation_and_cycle_board(client):
    admin = _login(client, 'admin')
    bad = client.post('/settlement/notices', headers=admin,
                      json={'factory': 'factory-9', 'delivery_no': 1, 'covers_through': 1})
    assert bad.status_code == 422
    for factory in ('factory-1', 'factory-2'):
        assert client.post('/settlement/orders', headers=admin,
                           json={'factory': factory, 'order_no': 1, 'action': 'add',
                                 'spec_id': f'CP-{factory[-1]}01', 'name': '甲',
                                 'fit_traits': '箱座'}).status_code == 201
        assert client.post('/settlement/notices', headers=admin,
                           json={'factory': factory, 'delivery_no': 1,
                                 'covers_through': 1}).status_code == 201
    board = client.get('/settlement/board', headers=admin).json()
    assert len(board['cycles']) == 1
    cycle = board['cycles'][0]
    assert cycle['state'] in ('collecting', 'switching', 'published')
    retry = client.post(f"/settlement/cycles/{cycle['id']}/retry", headers=admin)
    assert retry.status_code == 200
    # 重试不会重复发布同一交割的批次
    again = client.get('/settlement/board', headers=admin).json()
    assert len(again['cycles']) == 1


def test_settlement_batch_carries_provenance(client, db, base):
    admin = _login(client, 'admin')
    for factory in ('factory-1', 'factory-2'):
        assert client.post('/settlement/orders', headers=admin,
                           json={'factory': factory, 'order_no': 1, 'action': 'add',
                                 'spec_id': f'CP-{factory[-1]}01', 'name': '甲',
                                 'fit_traits': '箱座'}).status_code == 201
        assert client.post('/settlement/notices', headers=admin,
                           json={'factory': factory, 'delivery_no': 1,
                                 'covers_through': 1}).status_code == 201
    db.commit()
    from src.services import settlement
    settlement.drive()
    board = client.get('/settlement/board', headers=admin).json()
    current = board['current']
    assert current['cycle_id'] == 1
    detail = client.get(f"/datasets/{current['dataset_id']}", headers=admin).json()
    assert detail['provenance'] is True
    touched = {row['spec_id']: row for row in detail['rows'] if 'source_factory' in row}
    assert touched['CP-101']['source_factory'] == 'factory-1'
    assert touched['CP-101']['source_order_no'] == 1
    assert touched['CP-201']['source_factory'] == 'factory-2'
    # 初始台账没有溯源标记
    baseline = client.get(f"/datasets/{base.id}", headers=admin).json()
    assert baseline['provenance'] is False
