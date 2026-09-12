from __future__ import annotations
import uuid
from pathlib import Path
import tempfile
from sqlalchemy import Engine, inspect
from sqlalchemy.orm import Session

from forge.manifest.manifest_core.registry import (
    ensure_registry_table,
    ensure_registered,
    ObjectRegistry,
)
from .discovery import discover_declarations
from .codegen import (
    generate_module_source,
    generate_build_init_source,
    generate_registry_json,
)
from .git_ops import clone_repo, commit_and_push, tag_repo


def _resolve_table_names(api_name: str, session: Session) -> tuple[str, str, str]:
    """
    Returns (object_rid, edits_table, materialized_table) — reused if already
    registered, freshly generated only for a genuinely new declaration.

    NOTE: table names are currently plain physical table identifiers.
    Once a Dataset abstraction exists (wrapping dotted rids + version/branch
    resolution), this function's return values should become dataset
    references resolved through that layer instead of direct table names —
    this will also require changes to ensure_registered and _make_mapped_class,
    which currently assume a fixed physical table name.
    """
    existing = session.get(ObjectRegistry, api_name)
    if existing is not None:
        return existing.object_rid, existing.edits_table, existing.materialized_table

    object_rid = f"rid.manifest-object.{uuid.uuid4()}"
    edits_table = f"rid_dataset_{uuid.uuid4().hex}"
    materialized_table = f"rid_dataset_{uuid.uuid4().hex}"
    return object_rid, edits_table, materialized_table


def build_msdk_within_session(
    declarations_dir: str, output_dir: str, session: Session
) -> Path:
    """
    Core build logic: discover declarations, register/verify schema for
    each object, generate and write the combined SDK source file.

    Does NOT commit, rollback, or close the session — that is the caller's
    responsibility. This makes the core embeddable in a larger transaction
    (e.g. alongside other Forge bookkeeping) rather than always being its
    own isolated unit of work. Use build_msdk() for a self-contained,
    commit-on-success/rollback-on-failure entry point.
    """
    collector = discover_declarations(declarations_dir)
    print(
        f"[builder] discovered {len(collector.objects)} objects: {[o.api_name for o in collector.objects]}"
    )

    engine = session.get_bind()
    ensure_registry_table(engine)

    resolved_names: dict[str, tuple[str, str]] = {}
    for obj_def in collector.objects:
        object_rid, edits_table, materialized_table = _resolve_table_names(
            obj_def.api_name, session
        )
        _, _, is_new = ensure_registered(
            obj_def.api_name,
            object_rid,
            edits_table,
            materialized_table,
            obj_def.backing_dataset,
            obj_def.fields,
            session,
        )
        resolved_names[obj_def.api_name] = (edits_table, materialized_table)

    source = generate_module_source(
        object_defs=collector.objects,
        link_defs=collector.links,
        resolved_names=resolved_names,
    )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    generated_file = output_path / "_generated.py"
    generated_file.write_text(source)
    print(f"[builder] wrote {generated_file}")

    init_source = generate_build_init_source(collector.objects)
    init_path = output_path / "__init__.py"
    init_path.write_text(init_source)
    print(f"[builder] wrote {init_path}")

    registry_source = generate_registry_json(
        collector.objects, collector.links, resolved_names
    )
    registry_path = output_path / "registry.json"
    registry_path.write_text(registry_source)
    print(f"[builder] wrote {registry_path}")

    return generated_file


def build_msdk(declarations_dir: str, output_dir: str, engine: Engine) -> Path:
    """
    Self-contained entry point: opens a session, runs build_msdk_within_session,
    commits on success or rolls back on any failure, and always closes
    the session. This is what real callers (a script, Forge's orchestrator)
    should use.
    """
    with Session(engine) as session:
        with session.begin():
            result = build_msdk_within_session(declarations_dir, output_dir, session)
        return result


def git_build_manifest_repo(git_url: str, tag: str, engine: Engine) -> dict:
    temp_dir = tempfile.mkdtemp(prefix="forge_git_build_")
    repo_path = clone_repo(git_url, str(Path(temp_dir) / "repo"))

    with Session(engine) as session:
        with session.begin():
            generated_file = build_msdk_within_session(
                str(repo_path / "src" / "declarations"),
                str(repo_path / "_build"),
                session,
            )

    commit_push_result = commit_and_push(str(repo_path))
    tag_result = tag_repo(str(repo_path), tag)

    return {
        "repo_path": str(repo_path),
        "generated_file": str(generated_file),
        "tag": tag_result["tag"],
        **commit_push_result,
    }
