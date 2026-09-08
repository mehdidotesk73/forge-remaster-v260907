# tests/msdk_build/test_discovery.py
import pytest
from pathlib import Path

from forge.msdk.msdk_build.discovery import (
    discover_declarations,
    ManifestValidationError,
)


def _write(tmp_path, filename, content):
    (tmp_path / filename).write_text(content)


def test_discovers_objects_and_links(tmp_path):
    _write(
        tmp_path,
        "product.py",
        """
from forge.msdk.msdk_core import ManifestObjectDef, ManifestFieldDef
Product = ManifestObjectDef(
    display_name="Product", api_name="Product",
    fields={"product_id": ManifestFieldDef(type="string", primary_key=True, nullable=False)},
)
""",
    )
    _write(
        tmp_path,
        "part.py",
        """
from forge.msdk.msdk_core import ManifestObjectDef, ManifestFieldDef
Part = ManifestObjectDef(
    display_name="Part", api_name="Part",
    fields={"part_id": ManifestFieldDef(type="string", primary_key=True, nullable=False)},
)
""",
    )
    _write(
        tmp_path,
        "links.py",
        """
from forge.msdk.msdk_core import ManifestLinkDef
ManifestLinkDef(
    name="parts", reverse_name="products",
    source="Product", source_field="part_ids",
    target="Part", target_field="part_id",
)
""",
    )

    result = discover_declarations(str(tmp_path))
    assert {o.api_name for o in result.objects} == {"Product", "Part"}
    assert len(result.links) == 1


def test_rejects_missing_primary_key(tmp_path):
    _write(
        tmp_path,
        "bad.py",
        """
from forge.msdk.msdk_core import ManifestObjectDef, ManifestFieldDef
Bad = ManifestObjectDef(
    display_name="Bad", api_name="Bad",
    fields={"name": ManifestFieldDef(type="string")},
)
""",
    )
    with pytest.raises(
        ManifestValidationError, match="no field declared with primary_key"
    ):
        discover_declarations(str(tmp_path))


def test_rejects_multiple_primary_keys(tmp_path):
    _write(
        tmp_path,
        "bad.py",
        """
from forge.msdk.msdk_core import ManifestObjectDef, ManifestFieldDef
Bad = ManifestObjectDef(
    display_name="Bad", api_name="Bad",
    fields={
        "id1": ManifestFieldDef(type="string", primary_key=True, nullable=False),
        "id2": ManifestFieldDef(type="string", primary_key=True, nullable=False),
    },
)
""",
    )
    with pytest.raises(
        ManifestValidationError, match="multiple fields declared primary_key"
    ):
        discover_declarations(str(tmp_path))


def test_rejects_duplicate_api_name(tmp_path):
    _write(
        tmp_path,
        "a.py",
        """
from forge.msdk.msdk_core import ManifestObjectDef, ManifestFieldDef
X = ManifestObjectDef(display_name="X", api_name="Dup",
    fields={"id": ManifestFieldDef(type="string", primary_key=True, nullable=False)})
""",
    )
    _write(
        tmp_path,
        "b.py",
        """
from forge.msdk.msdk_core import ManifestObjectDef, ManifestFieldDef
Y = ManifestObjectDef(display_name="Y", api_name="Dup",
    fields={"id": ManifestFieldDef(type="string", primary_key=True, nullable=False)})
""",
    )
    with pytest.raises(ManifestValidationError, match="duplicate api_name"):
        discover_declarations(str(tmp_path))


def test_rejects_link_to_unknown_object(tmp_path):
    _write(
        tmp_path,
        "a.py",
        """
from forge.msdk.msdk_core import ManifestObjectDef, ManifestFieldDef
X = ManifestObjectDef(display_name="X", api_name="X",
    fields={"id": ManifestFieldDef(type="string", primary_key=True, nullable=False)})
""",
    )
    _write(
        tmp_path,
        "link.py",
        """
from forge.msdk.msdk_core import ManifestLinkDef
ManifestLinkDef(name="l", reverse_name="r", source="X", source_field="y_id",
                 target="Y", target_field="id")
""",
    )
    with pytest.raises(
        ManifestValidationError, match="not found among declared objects"
    ):
        discover_declarations(str(tmp_path))
