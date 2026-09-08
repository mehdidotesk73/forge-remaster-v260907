from __future__ import annotations
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ARRAY


class ManifestType:
    sql_type = None

    def is_array(self) -> bool:
        return False

    def element_type(self) -> "ManifestType | None":
        return None

    def __eq__(self, other):
        return isinstance(other, self.__class__)

    def __hash__(self):
        return hash(self.__class__)

    def __repr__(self):
        return self.__class__.__name__


class StringType(ManifestType):
    sql_type = String()


class IntType(ManifestType):
    sql_type = Integer()


class FloatType(ManifestType):
    sql_type = Float()


class BoolType(ManifestType):
    sql_type = Boolean()


class DateTimeType(ManifestType):
    sql_type = DateTime()


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

    def __eq__(self, other):
        return isinstance(other, ArrayType) and self.element == other.element

    def __hash__(self):
        return hash(("ArrayType", self.element))

    def __repr__(self):
        return f"LIST[{self.element!r}]"


class _ListFactory:
    def __getitem__(self, element: ManifestType) -> ArrayType:
        return ArrayType(element)


# Singleton instances — fields reference these directly, no string parsing anywhere
STRING = StringType()
INT = IntType()
FLOAT = FloatType()
BOOL = BoolType()
DATETIME = DateTimeType()
LIST = _ListFactory()
