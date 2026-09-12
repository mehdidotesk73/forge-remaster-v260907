import importlib.util
import json
import sys
import pytest

from forge.manifest.manifest_build.builder import build_msdk_within_session
from forge.manifest.manifest_core.registry import SchemaConflictError
from tests.conftest import reset_registered_classes


def _write(tmp_path, filename, content):
    (tmp_path / filename).write_text(content)


def _import_generated(generated_file):
    spec = importlib.util.spec_from_file_location(
        "_generated_test_module", generated_file
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["_generated_test_module"] = module
    spec.loader.exec_module(module)
    return module


def test_build_msdk_core_end_to_end(tmp_path, db_session):
    declarations_dir = tmp_path / "declarations"
    declarations_dir.mkdir()
    output_dir = tmp_path / "output"

    _write(
        declarations_dir,
        "widget.py",
        """
from forge.manifest import ManifestObjectDef, ManifestFieldDef, STRING
ManifestObjectDef(
    display_name="BuilderWidget", api_name="BuilderWidget",
    fields={
        "widget_id": ManifestFieldDef(type=STRING, primary_key=True, nullable=False),
        "name": ManifestFieldDef(type=STRING, nullable=True),
    },
)
""",
    )

    generated_file = build_msdk_within_session(
        str(declarations_dir), str(output_dir), db_session
    )
    db_session.commit()
    assert generated_file.exists()

    module = _import_generated(generated_file)
    BuilderWidget = module.BuilderWidget
    assert BuilderWidget._pk_field == "widget_id"

    init_file = output_dir / "__init__.py"
    assert init_file.exists()
    assert "BuilderWidget" in init_file.read_text()
    assert "BuilderWidgetSet" in init_file.read_text()

    registry_file = output_dir / "registry.json"
    assert registry_file.exists()
    registry = json.loads(registry_file.read_text())
    assert "BuilderWidget" in registry["objects"]
    widget_entry = registry["objects"]["BuilderWidget"]
    assert widget_entry["pk_field"] == "widget_id"
    assert "edits_table" in widget_entry
    assert "materialized_table" in widget_entry
    assert widget_entry["properties"] == ["name"]
    assert widget_entry["methods"] == ["create", "delete", "where"]


def test_build_msdk_core_idempotent_on_second_call(tmp_path, db_session):
    declarations_dir = tmp_path / "declarations"
    declarations_dir.mkdir()
    output_dir = tmp_path / "output"

    _write(
        declarations_dir,
        "widget.py",
        """
from forge.manifest import ManifestObjectDef, ManifestFieldDef, STRING
ManifestObjectDef(
    display_name="BuilderWidget2", api_name="BuilderWidget2",
    fields={"widget_id": ManifestFieldDef(type=STRING, primary_key=True, nullable=False)},
)
""",
    )

    build_msdk_within_session(str(declarations_dir), str(output_dir), db_session)
    db_session.commit()

    generated_file_2 = build_msdk_within_session(
        str(declarations_dir), str(output_dir), db_session
    )
    assert generated_file_2.exists()


def test_build_msdk_core_raises_on_schema_conflict(tmp_path, db_session):
    declarations_dir = tmp_path / "declarations"
    declarations_dir.mkdir()
    output_dir = tmp_path / "output"

    _write(
        declarations_dir,
        "widget.py",
        """
from forge.manifest import ManifestObjectDef, ManifestFieldDef, STRING
ManifestObjectDef(
    display_name="BuilderWidget3", api_name="BuilderWidget3",
    fields={"widget_id": ManifestFieldDef(type=STRING, primary_key=True, nullable=False)},
)
""",
    )
    build_msdk_within_session(str(declarations_dir), str(output_dir), db_session)
    db_session.commit()

    reset_registered_classes()  # simulate a fresh process for the "second build"

    _write(
        declarations_dir,
        "widget.py",
        """
from forge.manifest import ManifestObjectDef, ManifestFieldDef, STRING, INT
ManifestObjectDef(
    display_name="BuilderWidget3", api_name="BuilderWidget3",
    fields={
        "widget_id": ManifestFieldDef(type=STRING, primary_key=True, nullable=False),
        "extra": ManifestFieldDef(type=INT, nullable=True),
    },
)
""",
    )

    with pytest.raises(SchemaConflictError):
        build_msdk_within_session(str(declarations_dir), str(output_dir), db_session)
