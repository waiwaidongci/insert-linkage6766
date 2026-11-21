import json
from sqlalchemy import func, select
from src.config.database import Session
from src.constants.enums import Role
from src.domain.demo import demo_feed, demo_rows
from src.domain.spec import SPEC
from src.models.identity import User
from src.models.settlement import ChangeOrder, SettlementCycle
from src.models.source import Dataset
from src.services import settlement
from src.services.datasets import save
from src.utils.security import hash_password


def seed_feed(db):
    """按演示变更流签收两厂变更单与交割号，重复执行幂等。"""
    feed = demo_feed()
    received = 0
    for factory, order_no, action, spec_id, name, traits in feed['orders']:
        _, created, _ = settlement.submit_order(
            db, factory=factory, order_no=order_no, action=action,
            spec_id=spec_id, name=name, fit_traits=traits, actor='seed')
        received += created
    for factory, delivery_no, covers_through in feed['notices']:
        _, created, _ = settlement.submit_notice(
            db, factory=factory, delivery_no=delivery_no,
            covers_through=covers_through, actor='seed')
        received += created
    return received


def seed():
    with Session.begin() as db:
        for role in Role:
            if not db.get(User, role.value):
                db.add(User(username=role.value, role=role, password_hash=hash_password(f'{role.value}-demo-2611')))
        item, created = save(db, SPEC['default_dataset'], SPEC['sample_name'], demo_rows(), 'seed')
        received = seed_feed(db)
    settlement.drive()
    with Session() as db:
        return {'users': db.scalar(select(func.count()).select_from(User)),
                'datasets': db.scalar(select(func.count()).select_from(Dataset)),
                'seed_dataset_id': item.id, 'row_count': item.row_count, 'checksum': item.checksum,
                'created_dataset': created, 'feed_received': received,
                'change_orders': db.scalar(select(func.count()).select_from(ChangeOrder)),
                'settlement_cycles': db.scalar(select(func.count()).select_from(SettlementCycle))}


if __name__ == '__main__':
    print(json.dumps(seed(), ensure_ascii=False))
