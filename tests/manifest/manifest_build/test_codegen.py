from forge.manifest.manifest_core import (
    ManifestObjectDef,
    ManifestFieldDef,
    ManifestLinkDef,
    current_session,
)
from forge.manifest.manifest_core.registry import ensure_registered
from forge.manifest.manifest_core.types import STRING, INT, LIST
from forge.manifest.manifest_build.codegen import generate_module_source


def test_generate_and_exec_single_object(db_session):
    product_def = ManifestObjectDef(
        display_name="Product",
        api_name="Product",
        fields={
            "product_id": ManifestFieldDef(
                type=STRING, primary_key=True, nullable=False
            ),
            "name": ManifestFieldDef(type=STRING, nullable=True),
            "cost": ManifestFieldDef(type=INT, nullable=True, index=True),
        },
    )

    edit_cls, materialized_cls, is_new = ensure_registered(
        "Product",
        "rid.manifest-object.test-product",
        "product_edits_codegen_test",
        "product_materialized_codegen_test",
        None,
        product_def.fields,
        db_session,
    )
    db_session.commit()
    assert is_new is True

    source = generate_module_source(
        object_defs=[product_def],
        link_defs=[],
        resolved_names={
            "Product": (
                "product_edits_codegen_test",
                "product_materialized_codegen_test",
            )
        },
    )

    namespace = {}
    exec(compile(source, "<generated>", "exec"), namespace)

    Product = namespace["Product"]
    ProductSet = namespace["ProductSet"]
    assert Product._pk_field == "product_id"
    assert Product._set_cls is ProductSet

    token = current_session.set(db_session)
    try:
        p = Product.create("p1", name="Widget", cost=42)
        assert p.name == "Widget"
        assert p.cost == 42
    finally:
        current_session.reset(token)


def test_generate_with_links(db_session):
    product_def = ManifestObjectDef(
        display_name="Product",
        api_name="Product",
        fields={
            "product_id": ManifestFieldDef(
                type=STRING, primary_key=True, nullable=False
            ),
            "part_ids": ManifestFieldDef(type=LIST[STRING], nullable=True),
        },
    )
    part_def = ManifestObjectDef(
        display_name="Part",
        api_name="Part",
        fields={
            "part_id": ManifestFieldDef(type=STRING, primary_key=True, nullable=False)
        },
    )
    link_def = ManifestLinkDef(
        name="parts",
        reverse_name="products",
        source="Product",
        source_field="part_ids",
        target="Part",
        target_field="part_id",
    )

    for api_name, fields, edits, mat in [
        (
            "Product",
            product_def.fields,
            "product_edits_link_test",
            "product_materialized_link_test",
        ),
        (
            "Part",
            part_def.fields,
            "part_edits_link_test",
            "part_materialized_link_test",
        ),
    ]:
        ensure_registered(
            api_name,
            f"rid.manifest-object.{api_name.lower()}",
            edits,
            mat,
            None,
            fields,
            db_session,
        )
    db_session.commit()

    source = generate_module_source(
        object_defs=[product_def, part_def],
        link_defs=[link_def],
        resolved_names={
            "Product": ("product_edits_link_test", "product_materialized_link_test"),
            "Part": ("part_edits_link_test", "part_materialized_link_test"),
        },
    )

    namespace = {}
    exec(compile(source, "<generated>", "exec"), namespace)

    Product = namespace["Product"]
    Part = namespace["Part"]
    assert "parts" in Product._links
    assert "products" in Part._links
    assert Product._links["parts"].join_kind == "array_fk_parent"
    assert Part._links["products"].join_kind == "array_fk_child"
