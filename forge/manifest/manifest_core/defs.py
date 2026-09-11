# msdk_core/defs.py
from __future__ import annotations
from typing import Optional
from .types import ManifestType


class ManifestFieldDef:
    def __init__(
        self,
        type: ManifestType,
        primary_key: bool = False,
        nullable: bool = True,
        index: bool = False,
        backing_column: Optional[str] = None,
    ):
        self.type = type
        self.primary_key = primary_key
        self.nullable = nullable
        self.index = index
        self.backing_column = backing_column

    def __repr__(self):
        return (
            f"ManifestFieldDef(type={self.type!r}, primary_key={self.primary_key}, "
            f"nullable={self.nullable}, index={self.index})"
        )


class DeclarationCollector:
    def __init__(self):
        self.objects: list["ManifestObjectDef"] = []
        self.links: list["ManifestLinkDef"] = []

    def add_object(self, obj_def: "ManifestObjectDef") -> None:
        self.objects.append(obj_def)

    def add_link(self, link_def: "ManifestLinkDef") -> None:
        self.links.append(link_def)


_active_collector: Optional[DeclarationCollector] = None


def bind_collector(collector: DeclarationCollector):
    class _Binder:
        def __enter__(self):
            global _active_collector
            self._previous = _active_collector
            _active_collector = collector
            return collector

        def __exit__(self, *exc):
            global _active_collector
            _active_collector = self._previous

    return _Binder()


class ManifestObjectDef:
    def __init__(
        self,
        display_name: str,
        api_name: str,
        fields: dict[str, ManifestFieldDef],
        backing_dataset: Optional[str] = None,
    ):
        self.display_name = display_name
        self.api_name = api_name
        self.fields = fields
        self.backing_dataset = backing_dataset
        if _active_collector is not None:
            _active_collector.add_object(self)

    def __repr__(self):
        return f"ManifestObjectDef(api_name={self.api_name!r})"


class ManifestLinkDef:
    def __init__(
        self,
        name: str,
        reverse_name: str,
        source: str,
        source_field: str,
        target: str,
        target_field: str,
    ):
        self.name = name
        self.reverse_name = reverse_name
        self.source = source
        self.source_field = source_field
        self.target = target
        self.target_field = target_field
        if _active_collector is not None:
            _active_collector.add_link(self)

    def __repr__(self):
        return f"ManifestLinkDef({self.source}.{self.source_field} -> {self.target}.{self.target_field})"
