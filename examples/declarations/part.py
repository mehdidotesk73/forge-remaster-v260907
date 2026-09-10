from forge.msdk import ManifestObjectDef, ManifestFieldDef, STRING

ManifestObjectDef(
    display_name="Part",
    api_name="Part",
    fields={
        "part_id": ManifestFieldDef(type=STRING, primary_key=True, nullable=False),
        "name": ManifestFieldDef(type=STRING, nullable=True),
    },
)
