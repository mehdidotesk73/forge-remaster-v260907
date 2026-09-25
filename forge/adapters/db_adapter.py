from __future__ import annotations
import contextvars
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine
from sqlalchemy.orm import Session

current_session: contextvars.ContextVar[Session] = contextvars.ContextVar(
    "current_session"
)


class NoActiveSessionError(Exception):
    pass


def require_session() -> Session:
    try:
        return current_session.get()
    except LookupError:
        raise NoActiveSessionError(
            "No active session in context. This must be called from within a "
            "unit-of-work decorated function."
        )


@contextmanager
def use_session(session: Session) -> Iterator[Session]:
    """
    Publishes an already-open session to current_session for the duration
    of the block, for callers that need to compose their own transaction
    handling instead of going through unit_of_work().
    """
    token = current_session.set(session)
    try:
        yield session
    finally:
        current_session.reset(token)


@contextmanager
def unit_of_work(engine: Engine) -> Iterator[Session]:
    """
    Self-contained unit of work: opens a session, begins a transaction,
    publishes the session to current_session, and commits on success or
    rolls back on failure — always closing the session. This is what a
    caller (Manifest's own build entry points, Terminal's @manifest_function)
    should wrap around a call stack that needs a database session in context.
    """
    with Session(engine) as session:
        with session.begin():
            with use_session(session):
                yield session
