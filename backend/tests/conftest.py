"""测试运行前设置环境，避免依赖 MySQL 与 Redis。"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

os.environ['DATABASE_URL'] = f"sqlite:///{BACKEND}/tests/test-settlement.db"
os.environ['JWT_SECRET'] = 'test-only-secret'
os.environ['SEED_DEMO'] = 'false'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6399/0'  # 无服务时 wake() 静默跳过

import pytest


@pytest.fixture()
def db():
    from src.config.database import Base, Session, engine
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as session:
        yield session
        session.rollback()


@pytest.fixture()
def base(db):
    from src.domain.demo import demo_rows
    from src.services.datasets import save
    dataset, _ = save(db, '基线台账', 'base.csv', demo_rows(), 'test')
    db.commit()  # 让其他会话（drive/应用）可见
    return dataset
