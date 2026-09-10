# msdk_core/types.py
from __future__ import annotations
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ARRAY


class ManifestType:
    sql_type = None

    def is_array(self) -> bool:
        return False

    def element_type(self):
        return None

    def _to_source(self) -> str:
        """Returns Python source text that reconstructs this type, assuming
        the standard type-name imports are in scope. Private — used only by
        codegen, never part of the runtime type interface."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not define _to_source() — "
            f"needed for codegen to reconstruct this type in generated source"
        )

    def __eq__(self, other):
        return isinstance(other, self.__class__)

    def __hash__(self):
        return hash(self.__class__)

    def __repr__(self):
        return self.__class__.__name__


class StringType(ManifestType):
    sql_type = String()

    def _to_source(self) -> str:
        return "STRING"


class IntType(ManifestType):
    sql_type = Integer()

    def _to_source(self) -> str:
        return "INT"


class FloatType(ManifestType):
    sql_type = Float()

    def _to_source(self) -> str:
        return "FLOAT"


class BoolType(ManifestType):
    sql_type = Boolean()

    def _to_source(self) -> str:
        return "BOOL"


class DateTimeType(ManifestType):
    sql_type = DateTime()

    def _to_source(self) -> str:
        return "DATETIME"


class ArrayType(ManifestType):
    def __init__(self, element: ManifestType):
        self.element = element

    def is_array(self) -> bool:
        return True

    def element_type(self):
        return self.element

    @property
    def sql_type(self):
        return ARRAY(self.element.sql_type)

    def _to_source(self) -> str:
        return f"LIST[{self.element._to_source()}]"

    def __eq__(self, other):
        return isinstance(other, ArrayType) and self.element == other.element

    def __hash__(self):
        return hash(("ArrayType", self.element))

    def __repr__(self):
        return f"LIST[{self.element!r}]"


class _ListFactory:
    def __getitem__(self, element: ManifestType) -> ArrayType:
        return ArrayType(element)


# Singleton instances — fields reference these directly
STRING = StringType()
INT = IntType()
FLOAT = FloatType()
BOOL = BoolType()
DATETIME = DateTimeType()
LIST = _ListFactory()


def type_to_source(t: ManifestType) -> str:
    """Public codegen entry point — wraps the private per-class _to_source()."""
    return t._to_source()
