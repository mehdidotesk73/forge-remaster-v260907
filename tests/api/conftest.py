import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forge.manifest.manifest_core.registry import ObjectRegistry
from tests.conftest import TEST_DB_URL, reset_registered_classes


@pytest.fixture(autouse=True)
def _use_test_db(monkeypatch):
    monkeypatch.setattr("forge.api.routes.manifest.DB_URL", TEST_DB_URL)


@pytest.fixture(autouse=True)
def _cleanup_built_registrations(engine):
    # /manifest/build commits its registry rows and generated tables/views through
    # its own standalone session, independent of the db_session fixture's
    # rollback-per-test connection — so they persist in the real test DB unless we
    # tear them down ourselves here.
    yield
    with Session(engine) as session:
        with session.begin():
            existing = session.get(ObjectRegistry, "Product")
            if existing is not None:
                session.execute(
                    text(f"DROP MATERIALIZED VIEW IF EXISTS {existing.materialized_table}")
                )
                session.execute(text(f"DROP TABLE IF EXISTS {existing.edits_table}"))
                session.delete(existing)
    reset_registered_classes()
