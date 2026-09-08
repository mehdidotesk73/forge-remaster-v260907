from __future__ import annotations
from forge.msdk.msdk_core import ManifestObjectDef, ManifestLinkDef, ManifestType


class LinkResolutionError(Exception):
    pass


def _scalar_to_list_message() -> str:
    return (
        "source field is scalar but target field is a list — this direction "
        "isn't declarable as-is. The array-holding side must be the 'source' "
        "in a ManifestLinkDef. Try swapping source/target: declare the link "
        "starting from the object with the list field, pointing at the "
        "object with the scalar primary key."
    )


def _list_to_list_message() -> str:
    return (
        "both source and target fields are lists — a direct many-to-many "
        "link between two array columns isn't supported. Instead, define a "
        "separate 'link' object with two scalar fields (one referencing each "
        "side's primary key), then declare two ManifestLinkDefs: one from "
        "the link object to each of the two original objects (each a "
        "scalar-to-scalar or list-to-scalar relationship)."
    )


def infer_join_kind(source_type: ManifestType, target_type: ManifestType) -> str:
    """
    Determines join_kind from the perspective of the FORWARD direction
    (source -> target), given the declared ManifestType of source_field
    and target_field.
    """
    source_is_array = source_type.is_array()
    target_is_array = target_type.is_array()

    if not source_is_array and not target_is_array:
        return "scalar_fk"
    if source_is_array and not target_is_array:
        return "array_fk_parent"
    if not source_is_array and target_is_array:
        raise LinkResolutionError(_scalar_to_list_message())
    raise LinkResolutionError(_list_to_list_message())


def infer_reverse_join_kind(forward_join_kind: str) -> str:
    if forward_join_kind == "scalar_fk":
        return "scalar_fk"
    if forward_join_kind == "array_fk_parent":
        return "array_fk_child"
    raise LinkResolutionError(f"cannot reverse join_kind {forward_join_kind!r}")


def resolve_link_join_kinds(
    link_def: ManifestLinkDef, object_defs: dict[str, ManifestObjectDef]
) -> tuple[str, str]:
    """
    Returns (forward_join_kind, reverse_join_kind) for a ManifestLinkDef.
    forward: source -> target (uses link_def.name)
    reverse: target -> source (uses link_def.reverse_name)
    """
    source_def = object_defs[link_def.source]
    target_def = object_defs[link_def.target]

    if link_def.source_field not in source_def.fields:
        raise LinkResolutionError(
            f"link {link_def.name!r}: field {link_def.source_field!r} not found on {link_def.source!r}"
        )
    if link_def.target_field not in target_def.fields:
        raise LinkResolutionError(
            f"link {link_def.name!r}: field {link_def.target_field!r} not found on {link_def.target!r}"
        )

    source_type = source_def.fields[link_def.source_field].type
    target_type = target_def.fields[link_def.target_field].type

    forward = infer_join_kind(source_type, target_type)
    reverse = infer_reverse_join_kind(forward)
    return forward, reverse
