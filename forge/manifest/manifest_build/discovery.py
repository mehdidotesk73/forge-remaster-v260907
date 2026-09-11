from __future__ import annotations
import importlib.util
import sys
from pathlib import Path

from forge.manifest.manifest_core.defs import (
    ManifestObjectDef,
    ManifestLinkDef,
    DeclarationCollector,
    bind_collector,
)


class ManifestValidationError(Exception):
    pass


def _load_module_from_file(py_file: Path):
    module_name = f"_manifest_declarations_{py_file.stem}"
    spec = importlib.util.spec_from_file_location(module_name, py_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _validate_object_def(obj_def: ManifestObjectDef) -> None:
    pk_fields = [name for name, f in obj_def.fields.items() if f.primary_key]

    if len(pk_fields) == 0:
        raise ManifestValidationError(
            f"{obj_def.api_name}: no field declared with primary_key=True"
        )
    if len(pk_fields) > 1:
        raise ManifestValidationError(
            f"{obj_def.api_name}: multiple fields declared primary_key=True: {pk_fields} "
            f"— exactly one primary key field is required"
        )

    pk_field = obj_def.fields[pk_fields[0]]
    if pk_field.nullable:
        raise ManifestValidationError(
            f"{obj_def.api_name}: primary key field {pk_fields[0]!r} must not be nullable"
        )


def _validate_link_def(link_def: ManifestLinkDef, known_api_names: set[str]) -> None:
    if link_def.source not in known_api_names:
        raise ManifestValidationError(
            f"link {link_def.name!r}: source object {link_def.source!r} not found among declared objects"
        )
    if link_def.target not in known_api_names:
        raise ManifestValidationError(
            f"link {link_def.name!r}: target object {link_def.target!r} not found among declared objects"
        )


def discover_declarations(declarations_dir: str) -> DeclarationCollector:
    collector = DeclarationCollector()
    declarations_path = Path(declarations_dir)

    with bind_collector(collector):
        for py_file in sorted(declarations_path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            _load_module_from_file(py_file)

    api_names_seen = set()
    for obj_def in collector.objects:
        if obj_def.api_name in api_names_seen:
            raise ManifestValidationError(
                f"duplicate api_name declared: {obj_def.api_name!r}"
            )
        api_names_seen.add(obj_def.api_name)
        _validate_object_def(obj_def)

    for link_def in collector.links:
        _validate_link_def(link_def, api_names_seen)

    return collector
