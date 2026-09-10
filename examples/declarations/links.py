from forge.msdk import ManifestLinkDef

ManifestLinkDef(
    name="parts",
    reverse_name="products",
    source="Product",
    source_field="part_ids",
    target="Part",
    target_field="part_id",
)
