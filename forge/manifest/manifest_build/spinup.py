from __future__ import annotations
import re
from pathlib import Path

_PYPROJECT_TEMPLATE = """[project]
name = "{repo_name}"
version = "0.0.0"
dependencies = [
    "forge-manifest @ git+https://github.com/mehdidotesk73/forge-remaster-v260907.git@v0.2.0#subdirectory=forge/manifest",
]
"""

_README_TEMPLATE = """# {repo_name}

Manifest declarations for this project.

## Where to write your declarations

Define your objects in `src/declarations/` — one file per object (or per
related group of objects/links) is the convention. Each file should declare
ManifestObjectDef/ManifestLinkDef instances using forge.manifest, e.g.:

    from forge.manifest import ManifestObjectDef, ManifestFieldDef, STRING, INT

    ManifestObjectDef(
        display_name="Product", api_name="Product",
        fields={{
            "product_id": ManifestFieldDef(type=STRING, primary_key=True, nullable=False),
            "cost": ManifestFieldDef(type=INT, nullable=True),
        }},
    )

## What NOT to touch

- `_build/` — fully regenerated on every build. Any manual edits here will
  be silently overwritten.
- The root `__init__.py` — re-exports from `_build/`, managed by spinup.
- `pyproject.toml`'s `forge-manifest` dependency line — managed by version-bump
  tooling; feel free to add your own extra dependencies below it.

## After building

Once a build has run, import your objects directly:

    from {repo_name} import Product
"""

_ROOT_INIT_TEMPLATE = """from ._build import *
from ._build import __all__
"""

_BUILD_INIT_PLACEHOLDER = """# This file is regenerated on every build. Do not edit by hand.
__all__ = []
"""

_STARTER_DECLARATION_TEMPLATE = '''"""
Example declaration file. Delete or rename this once you have real
objects — this is just a starting point showing the imports you need.
"""
from forge.manifest import ManifestObjectDef, ManifestLinkDef, ManifestFieldDef, STRING, INT, LIST

# Example:
#
# ManifestObjectDef(
#     display_name="Product", api_name="Product",
#     fields={
#         "product_id": ManifestFieldDef(type=STRING, primary_key=True, nullable=False),
#         "name": ManifestFieldDef(type=STRING, nullable=True),
#         "cost": ManifestFieldDef(type=INT, nullable=True, index=True),
#     },
# )
'''


def _to_valid_identifier(name: str) -> str:
    """Converts an arbitrary folder name into a valid Python identifier:
    replaces any run of non-alphanumeric characters with underscores,
    lowercases, and prefixes with '_' if it would otherwise start with a
    digit or be empty."""
    converted = re.sub(r"[^0-9a-zA-Z_]+", "_", name).strip("_").lower()
    if not converted:
        converted = "manifest_repo"
    if converted[0].isdigit():
        converted = f"_{converted}"
    return converted


def spinup_manifest_repo(target_dir: str) -> Path:
    """
    Scaffolds a new manifest declarations repo. target_dir IS both the
    repo root and the importable Python package (pyproject.toml, README.md,
    __init__.py, src/declarations/, _build/ all live directly inside it).

    If target_dir's own folder name isn't a valid Python identifier (e.g.
    contains hyphens), it is automatically converted to one, and the
    scaffold is created at that corrected path instead — the returned
    Path reflects the actual location used, which may differ from the
    requested target_dir. Callers must use the returned value, not assume
    it matches the input string.

    This auto-correction is intentionally local-directory-only behavior.
    A future git-based spinup path (cloning an existing repo) must NOT
    auto-rename — a cloned repo's folder name is authoritative and should
    be taken as-is, raising instead if it isn't a valid identifier.
    """
    requested = Path(target_dir)
    valid_name = _to_valid_identifier(requested.name)

    target = (
        requested if requested.name == valid_name else requested.parent / valid_name
    )
    repo_name = target.name

    target.mkdir(parents=True, exist_ok=True)

    expected_paths = [
        target / "pyproject.toml",
        target / "README.md",
        target / "__init__.py",
        target / "src" / "declarations",
        target / "_build",
    ]
    for p in expected_paths:
        if p.exists():
            raise FileExistsError(
                f"{p} already exists — refusing to overwrite an existing repo scaffold"
            )

    (target / "pyproject.toml").write_text(
        _PYPROJECT_TEMPLATE.format(repo_name=repo_name)
    )
    (target / "README.md").write_text(_README_TEMPLATE.format(repo_name=repo_name))
    (target / "__init__.py").write_text(_ROOT_INIT_TEMPLATE)

    declarations_dir = target / "src" / "declarations"
    declarations_dir.mkdir(parents=True)
    (declarations_dir / "example.py").write_text(_STARTER_DECLARATION_TEMPLATE)

    build_dir = target / "_build"
    build_dir.mkdir()
    (build_dir / "__init__.py").write_text(_BUILD_INIT_PLACEHOLDER)

    return target
