from forge.msdk import ManifestObjectDef, ManifestFieldDef, STRING, INT, LIST

ManifestObjectDef(
    display_name="Product",
    api_name="Product",
    fields={
        "product_id": ManifestFieldDef(type=STRING, primary_key=True, nullable=False),
        "name": ManifestFieldDef(type=STRING, nullable=True),
        "cost": ManifestFieldDef(type=INT, nullable=True, index=True),
        "part_ids": ManifestFieldDef(type=LIST[STRING], nullable=True, index=True),
    },
)
