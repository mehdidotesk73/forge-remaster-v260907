from forge.manifest.manifest_core.defs import ManifestFieldDef
from forge.manifest.manifest_core.registry import ensure_registered, ObjectRegistry
from forge.manifest.manifest_core.base import (
    ManifestObject,
    ManifestObjectSet,
    ManifestField,
    current_session,
)
from forge.manifest.manifest_core.types import STRING


def _build_widget(session):
    fields = {
        "widget_id": ManifestFieldDef(type=STRING, primary_key=True, nullable=False),
        "name": ManifestFieldDef(type=STRING, nullable=True),
    }
    edit_cls, materialized_cls, is_new = ensure_registered(
        "Widget",
        "rid.manifest-object.test-widget",
        "widget_edits_test",
        "widget_materialized_test",
        None,
        fields,
        session,
    )
    session.commit()

    class Widget(ManifestObject):
        _edit_cls = edit_cls
        _materialized_cls = materialized_cls
        _pk_field = "widget_id"
        _properties = ("name",)
        _nullable_map = {"name": True}
        name = ManifestField("name")

    class WidgetSet(ManifestObjectSet):
        _element_cls = Widget

    Widget._set_cls = WidgetSet
    return Widget, is_new


def test_ensure_registered_creates_on_first_run(db_session):
    Widget, is_new = _build_widget(db_session)
    assert is_new is True

    row = db_session.get(ObjectRegistry, "Widget")
    assert row is not None
    assert row.edits_table == "widget_edits_test"


def test_ensure_registered_idempotent_on_second_call(db_session):
    _build_widget(db_session)
    Widget, is_new = _build_widget(db_session)
    assert is_new is False


def test_create_and_edit_widget(db_session):
    Widget, _ = _build_widget(db_session)

    token = current_session.set(db_session)
    try:
        w = Widget.create("w1", name="Test Widget")
        assert w.name == "Test Widget"

        w.name = "Renamed Widget"
        assert w.name == "Renamed Widget"

        db_session.commit()
    finally:
        current_session.reset(token)


def test_get_nonexistent_widget_raises_on_field_access(db_session):
    Widget, _ = _build_widget(db_session)

    token = current_session.set(db_session)
    try:
        w = Widget("does_not_exist")
        try:
            w.name
            assert False, "expected ValueError"
        except ValueError:
            pass
    finally:
        current_session.reset(token)
