import os
import subprocess
import time
import pytest
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from forge.msdk.msdk_core.registry import Base, ObjectRegistry, _registered_classes

COMPOSE_FILE = "tests/docker-compose.test.yml"
ENV_FILE = "tests/.env"


def _load_env_file(path: str) -> dict:
    env = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


_test_env = _load_env_file(ENV_FILE)
TEST_DB_URL = (
    f"postgresql://{_test_env['POSTGRES_USER']}:{_test_env['POSTGRES_PASSWORD']}"
    f"@localhost:5433/{_test_env['POSTGRES_DB']}"
)


def reset_registered_classes():
    for mapped_cls in list(_registered_classes.values()):
        table_name = mapped_cls.__tablename__
        if table_name in Base.metadata.tables:
            Base.metadata.remove(Base.metadata.tables[table_name])
    _registered_classes.clear()


@pytest.fixture(scope="session")
def engine():
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "--env-file", ENV_FILE, "up", "-d"],
        check=True,
    )

    eng = create_engine(TEST_DB_URL)

    last_error = None
    for _ in range(30):
        try:
            with eng.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            break
        except Exception as e:
            last_error = e
            time.sleep(1)
    else:
        raise RuntimeError(f"Test database did not become ready in time: {last_error}")

    Base.metadata.create_all(eng, tables=[ObjectRegistry.__table__])
    yield eng
    eng.dispose()
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "--env-file", ENV_FILE, "down", "-v"],
        check=True,
    )


@pytest.fixture(autouse=True)
def db_session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()

    reset_registered_classes()
