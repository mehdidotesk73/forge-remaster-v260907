from __future__ import annotations
import contextvars
from sqlalchemy import func, select
from sqlalchemy.orm import Session

current_session: contextvars.ContextVar[Session] = contextvars.ContextVar(
    "current_session"
)


class NoActiveSessionError(Exception):
    pass


def _require_session() -> Session:
    try:
        return current_session.get()
    except LookupError:
        raise NoActiveSessionError(
            "No active session in context. This must be called from within a "
            "unit-of-work decorated function."
        )


class ManifestField:
    def __init__(self, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return getattr(objtype._materialized_cls, self.name)
        return obj._get_field(self.name)

    def __set__(self, obj, value):
        obj._set_field(self.name, value)


class ManifestLink:
    def __init__(
        self, name, target_cls, target_set_cls, join_kind, local_field, remote_field
    ):
        self.name = name
        self.target_cls = target_cls
        self.target_set_cls = target_set_cls
        self.join_kind = join_kind  # "scalar_fk" | "array_fk_child" | "array_fk_parent"
        self.local_field = local_field
        self.remote_field = remote_field

    def build_condition(self, source_pks_subquery):
        target_mat = self.target_cls._materialized_cls
        target_col = getattr(target_mat, self.remote_field)
        local_col = getattr(source_pks_subquery.c, self.local_field)
        if self.join_kind == "scalar_fk":
            return target_col.in_(select(local_col))
        elif self.join_kind == "array_fk_child":
            source_array = select(func.array_agg(local_col)).scalar_subquery()
            return target_col.op("&&")(source_array)
        elif self.join_kind == "array_fk_parent":
            return target_col.in_(select(func.unnest(local_col)))
        raise ValueError(f"unknown join_kind {self.join_kind!r}")


class ManifestObject:
    _edit_cls = None
    _materialized_cls = None
    _set_cls = None
    _pk_field = None
    _properties: tuple[str, ...] = ()
    _nullable_map: dict[str, bool] = {}
    _links: dict[str, ManifestLink] = {}

    def __init__(self, pk):
        self.pk = pk

    def __eq__(self, other):
        return isinstance(other, self.__class__) and self.pk == other.pk

    def __hash__(self):
        return hash((self.__class__, self.pk))

    def __repr__(self):
        return f"{self.__class__.__name__}(pk={self.pk!r})"

    @classmethod
    def _wrap(cls, row):
        return cls(pk=getattr(row, cls._pk_field))

    def as_set(self):
        pk_col = getattr(self._materialized_cls, self._pk_field)
        stmt = select(self._materialized_cls).where(pk_col == self.pk)
        return self._set_cls(stmt)

    @classmethod
    def where(cls, *conditions):
        return cls._set_cls.where(*conditions)

    @classmethod
    def _field_nullable(cls, name):
        return cls._nullable_map[name]

    def _get_field(self, name):
        session = _require_session()
        edit_row = session.get(self._edit_cls, self.pk)
        if edit_row is not None:
            if edit_row._deleted:
                raise ValueError(
                    f"{self.__class__.__name__}(pk={self.pk!r}) has been deleted"
                )
            return getattr(edit_row, name)
        mat_row = session.get(self._materialized_cls, self.pk)
        if mat_row is None:
            raise ValueError(
                f"{self.__class__.__name__}(pk={self.pk!r}) does not exist"
            )
        return getattr(mat_row, name)

    def _set_field(self, name, value):
        session = _require_session()
        edit_row = session.get(self._edit_cls, self.pk)
        if edit_row is not None:
            if edit_row._deleted:
                raise ValueError(
                    f"cannot edit deleted {self.__class__.__name__}(pk={self.pk!r})"
                )
            setattr(edit_row, name, value)
            return
        mat_row = session.get(self._materialized_cls, self.pk)
        if mat_row is None:
            raise ValueError(
                f"{self.__class__.__name__}(pk={self.pk!r}) does not exist"
            )
        current = {p: getattr(mat_row, p) for p in self._properties}
        current[name] = value
        current[self._pk_field] = self.pk
        session.add(self._edit_cls(_deleted=False, **current))

    @classmethod
    def create(cls, pk, **initial_values):
        session = _require_session()
        edit_row = session.get(cls._edit_cls, pk)
        if edit_row is not None:
            raise ValueError(
                f"{cls.__name__}(pk={pk!r}) already exists or was previously deleted"
            )
        if session.get(cls._materialized_cls, pk) is not None:
            raise ValueError(f"{cls.__name__}(pk={pk!r}) already exists")

        missing = [
            p
            for p in cls._properties
            if p not in initial_values and not cls._field_nullable(p)
        ]
        if missing:
            raise ValueError(
                f"{cls.__name__}.create() missing required fields: {missing}"
            )

        defaults = {p: initial_values.get(p) for p in cls._properties}
        defaults[cls._pk_field] = pk
        session.add(cls._edit_cls(_deleted=False, **defaults))
        return cls(pk=pk)

    def delete(self):
        session = _require_session()
        edit_row = session.get(self._edit_cls, self.pk)
        if edit_row is None:
            mat_row = session.get(self._materialized_cls, self.pk)
            if mat_row is None:
                raise ValueError(
                    f"{self.__class__.__name__}(pk={self.pk!r}) does not exist"
                )
            current = {p: getattr(mat_row, p) for p in self._properties}
            current[self._pk_field] = self.pk
            session.add(self._edit_cls(_deleted=True, **current))
        else:
            edit_row._deleted = True


class ManifestObjectSet:
    _element_cls: type[ManifestObject] = None

    def __init__(self, stmt):
        self._stmt = stmt

    def __iter__(self):
        session = _require_session()
        for row in session.execute(self._stmt).scalars():
            yield self._element_cls._wrap(row)

    def all(self):
        return list(self)

    def first(self):
        session = _require_session()
        row = session.execute(self._stmt.limit(1)).scalars().first()
        return self._element_cls._wrap(row) if row else None

    def __getattr__(self, name):
        link = self._element_cls._links.get(name)
        if link is None:
            raise AttributeError(f"{self._element_cls.__name__} has no link '{name}'")
        local_col = getattr(self._element_cls._materialized_cls, link.local_field)
        source_subq = self._stmt.with_only_columns(local_col).subquery()
        new_stmt = (
            select(link.target_cls._materialized_cls)
            .where(link.build_condition(source_subq))
            .distinct()
        )
        return link.target_set_cls(new_stmt)

    @classmethod
    def where(cls, *conditions):
        stmt = select(cls._element_cls._materialized_cls).where(*conditions)
        return cls(stmt)
