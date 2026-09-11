from __future__ import annotations
from forge.manifest.manifest_core import (
    ManifestObjectDef,
    ManifestLinkDef,
    type_to_source,
)
from .links import resolve_link_join_kinds


def _fields_dict_source(obj_def: ManifestObjectDef) -> str:
    lines = []
    for field_name, field_def in obj_def.fields.items():
        lines.append(
            f'    "{field_name}": ManifestFieldDef('
            f"type={type_to_source(field_def.type)}, "
            f"primary_key={field_def.primary_key!r}, "
            f"nullable={field_def.nullable!r}, "
            f"index={field_def.index!r}),"
        )
    body = "\n".join(lines)
    return f"{{\n{body}\n}}"


def generate_object_source(
    obj_def: ManifestObjectDef, edits_table: str, materialized_table: str
) -> str:
    api_name = obj_def.api_name
    pk_field = next(name for name, f in obj_def.fields.items() if f.primary_key)
    properties = tuple(
        name for name in obj_def.fields if not obj_def.fields[name].primary_key
    )
    nullable_map = {name: obj_def.fields[name].nullable for name in properties}

    field_props = "\n".join(
        f'    {name} = ManifestField("{name}")' for name in properties
    )

    return f"""
_fields_{api_name} = {_fields_dict_source(obj_def)}

_edit_cls_{api_name} = _make_mapped_class(
    "{api_name}Edit", "{edits_table}", _fields_{api_name}, include_deleted=True
)
_materialized_cls_{api_name} = _make_mapped_class(
    "{api_name}Materialized", "{materialized_table}", _fields_{api_name},
    include_deleted=False, is_view=True
)


class {api_name}(ManifestObject):
    _edit_cls = _edit_cls_{api_name}
    _materialized_cls = _materialized_cls_{api_name}
    _pk_field = "{pk_field}"
    _properties = {properties!r}
    _nullable_map = {nullable_map!r}

{field_props}


class {api_name}Set(ManifestObjectSet):
    _element_cls = {api_name}


{api_name}._set_cls = {api_name}Set
""".strip("\n")


def generate_links_source(
    link_defs: list[ManifestLinkDef],
    object_defs: dict[str, ManifestObjectDef],
) -> str:
    per_object_links: dict[str, list[str]] = {}

    for link_def in link_defs:
        forward_kind, reverse_kind = resolve_link_join_kinds(link_def, object_defs)

        forward_entry = (
            f'    "{link_def.name}": ManifestLink('
            f'"{link_def.name}", target_cls={link_def.target}, target_set_cls={link_def.target}Set, '
            f'join_kind="{forward_kind}", local_field="{link_def.source_field}", '
            f'remote_field="{link_def.target_field}"),'
        )
        per_object_links.setdefault(link_def.source, []).append(forward_entry)

        reverse_entry = (
            f'    "{link_def.reverse_name}": ManifestLink('
            f'"{link_def.reverse_name}", target_cls={link_def.source}, target_set_cls={link_def.source}Set, '
            f'join_kind="{reverse_kind}", local_field="{link_def.target_field}", '
            f'remote_field="{link_def.source_field}"),'
        )
        per_object_links.setdefault(link_def.target, []).append(reverse_entry)

    blocks = []
    for api_name, entries in per_object_links.items():
        body = "\n".join(entries)
        blocks.append(f"{api_name}._links = {{\n{body}\n}}")

    return "\n\n".join(blocks)


def generate_build_init_source(object_defs: list[ManifestObjectDef]) -> str:
    """
    Generates the content for _build/__init__.py — re-exports every
    declared object and its Set class from _generated.py, so a consumer
    can write `from my_manifest_repo import Product` without ever
    needing to know _generated.py exists.
    """
    names = []
    for obj_def in object_defs:
        names.append(obj_def.api_name)
        names.append(f"{obj_def.api_name}Set")

    import_line = f"from ._generated import {', '.join(names)}"
    all_line = f"__all__ = {names!r}"
    return f"{import_line}\n\n{all_line}\n"


def generate_module_source(
    object_defs: list[ManifestObjectDef],
    link_defs: list[ManifestLinkDef],
    resolved_names: dict[str, tuple[str, str]],
) -> str:
    """
    resolved_names: api_name -> (edits_table, materialized_table)
    """
    object_defs_by_name = {o.api_name: o for o in object_defs}

    header = (
        "from forge.manifest.manifest_core import (\n"
        "    ManifestObject, ManifestObjectSet, ManifestField, ManifestLink,\n"
        ")\n"
        "from forge.manifest.manifest_core.registry import _make_mapped_class\n"
        "from forge.manifest.manifest_core.defs import ManifestFieldDef\n"
        "from forge.manifest.manifest_core.types import STRING, INT, FLOAT, BOOL, DATETIME, LIST\n"
    )

    object_blocks = [
        generate_object_source(obj_def, *resolved_names[obj_def.api_name])
        for obj_def in object_defs
    ]

    links_block = generate_links_source(link_defs, object_defs_by_name)

    parts = [header] + object_blocks
    if links_block:
        parts.append(links_block)

    return "\n\n\n".join(parts) + "\n"
