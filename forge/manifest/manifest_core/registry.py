from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Boolean,
    JSON,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session

from .defs import ManifestFieldDef


class Base(DeclarativeBase):
    pass


class ObjectRegistry(Base):
    __tablename__ = "object_registry"
    api_name: Mapped[str] = mapped_column(primary_key=True)
    object_rid: Mapped[str]
    edits_table: Mapped[str]
    materialized_table: Mapped[str]
    backing_table: Mapped[str | None]
    schema_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime]


class SchemaConflictError(Exception):
    pass


class SchemaBuildError(Exception):
    pass


_registered_classes: dict[str, type] = {}


def _make_mapped_class(
    class_name: str,
    table_name: str,
    fields: dict[str, ManifestFieldDef],
    *,
    include_deleted: bool,
    is_view: bool = False,
):
    if class_name in _registered_classes:
        return _registered_classes[class_name]

    attrs = {"__tablename__": table_name}
    if is_view:
        attrs["__table_args__"] = {"info": {"is_view": True}}

    for field_name, field_def in fields.items():
        attrs[field_name] = Column(
            field_def.type.sql_type,
            primary_key=field_def.primary_key,
            index=field_def.index,
            nullable=field_def.nullable,
        )

    if include_deleted:
        attrs["_deleted"] = Column(Boolean, default=False, nullable=False)

    mapped_cls = type(class_name, (Base,), attrs)
    _registered_classes[class_name] = mapped_cls
    return mapped_cls


def _serialize_obj_schema(fields: dict[str, ManifestFieldDef]) -> dict:
    return {
        field_name: {
            "type": str(field_def.type.sql_type),
            "primary_key": field_def.primary_key,
            "nullable": field_def.nullable,
            "index": bool(field_def.index),
        }
        for field_name, field_def in fields.items()
    }


def _serialize_columns(mapped_cls) -> dict:
    return {
        col.name: {
            "type": str(col.type),
            "primary_key": col.primary_key,
            "nullable": col.nullable,
            "index": bool(col.index),
        }
        for col in mapped_cls.__table__.columns
        if col.name != "_deleted"
    }


def _pk_field_name(fields: dict[str, ManifestFieldDef]) -> str:
    for field_name, field_def in fields.items():
        if field_def.primary_key:
            return field_name
    raise SchemaBuildError("no field declared with primary_key=True")


def ensure_registry_table(engine) -> None:
    inspector = inspect(engine)
    if not inspector.has_table(ObjectRegistry.__tablename__):
        ObjectRegistry.__table__.create(engine)


def ensure_registered(
    api_name: str,
    object_rid: str,
    edits_table: str,
    materialized_table: str,
    backing_table: str | None,
    fields: dict[str, ManifestFieldDef],
    session: Session,
):
    class_name = f"{api_name}Edit"
    in_mem_exists = class_name in _registered_classes

    edit_cls = _make_mapped_class(class_name, edits_table, fields, include_deleted=True)
    materialized_cls = _make_mapped_class(
        f"{api_name}Materialized",
        materialized_table,
        fields,
        include_deleted=False,
        is_view=True,
    )

    declared = _serialize_obj_schema(fields)
    built = _serialize_columns(edit_cls)
    if declared != built:
        raise SchemaBuildError(
            f"{api_name}: _make_mapped_class output differs from declaration"
        )

    existing = session.get(ObjectRegistry, api_name)

    if existing is None and not in_mem_exists:
        edit_cls.__table__.create(session.connection())
        pk_col = _pk_field_name(fields)
        session.execute(text(f"""
            CREATE MATERIALIZED VIEW {materialized_table} AS
            SELECT * FROM {edits_table} WHERE NOT _deleted
        """))
        session.execute(text(f"""
            CREATE UNIQUE INDEX ON {materialized_table} ({pk_col})
        """))
        session.add(
            ObjectRegistry(
                api_name=api_name,
                object_rid=object_rid,
                edits_table=edits_table,
                materialized_table=materialized_table,
                backing_table=backing_table,
                schema_json=built,
                created_at=datetime.now(timezone.utc),
            )
        )
        return edit_cls, materialized_cls, True

    if existing is None:
        # in_mem_exists is True here — a class is cached under this table name in
        # this process, but no registry row exists for it. Real inconsistency:
        # likely two different api_names colliding on the same table name.
        raise RuntimeError(
            f"{api_name}: in-memory class exists but no registry row found."
        )

    # existing is not None — normal case whether this is the first call in this
    # process (in_mem_exists was False, class just built fresh above) or a
    # repeat call (in_mem_exists was True, cached class returned).
    inspector = inspect(session.connection())
    if not (
        inspector.has_table(existing.edits_table)
        and inspector.has_table(existing.materialized_table)
    ):
        raise RuntimeError(
            f"{api_name}: registry row exists but its referenced tables are missing."
        )

    if (
        existing.edits_table != edits_table
        or existing.materialized_table != materialized_table
    ):
        raise RuntimeError(
            f"{api_name}: registry row's tables ({existing.edits_table}, {existing.materialized_table}) "
            f"do not match the tables passed to this call ({edits_table}, {materialized_table})."
        )

    if existing.schema_json != built:
        raise SchemaConflictError(f"{api_name} schema changed since registration.")

    return edit_cls, materialized_cls, False
