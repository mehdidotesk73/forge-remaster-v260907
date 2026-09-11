# Manifest System Design

This document describes the design of the Manifest layer — the object
layer that lets a coder declare data objects once and get, in return, a
generated Python SDK (an "msdk" — Manifest SDK, the generated artifact,
not the package name) with correct, edits-aware CRUD, filtering, and
relationship traversal. It's meant as a reference for anyone (including
future-you) working on `manifest_core` or `manifest_build`.

**Naming note:** the package layer is `forge.manifest` (renamed from the
original `forge.msdk`). The generated SDK artifact itself is still
referred to as "msdk" in identifiers like `build_msdk_within_session` —
that's intentional: "msdk" names the _thing produced_, "manifest" names
the _layer that produces it_. Don't conflate the two when reading function
names.

---

## 1. Problem this solves

Forge objects are backed by three kinds of data:

- **Backing table** (optional) — populated by external code (e.g. a scraper),
  outside the object system's control. Read-only from the object system's
  perspective.
- **Edits table** — every create/update/delete made through the object API
  lands here, as a full-row upsert (never a partial-field row).
- **Materialized table/view** — the overlay of edits on top of backing:
  edits rows win where present, backing rows fill in everywhere else.
  Deletes are represented as `_deleted = True` rows in edits, never as row
  removal.

A naive design would either (a) always join backing+edits at read time
(correct, but expensive at scale), or (b) maintain one flat table with no
way to reinitialize backing independently of user edits. Neither fits the
requirement that backing can be swapped/refreshed independently while user
edits persist across that swap.

### The chosen shape

- `materialized` is a **Postgres `MATERIALIZED VIEW`** (`edits WHERE NOT
_deleted` `UNION ALL` `backing LEFT JOIN edits WHERE edits.pk IS NULL`),
  refreshed via `REFRESH MATERIALIZED VIEW CONCURRENTLY` on some external
  cadence (not owned by `manifest_core` — see §7).
- Because a materialized view only reflects state as of the last refresh,
  **single-object reads never go through it directly** — see §2.

---

## 2. The core consistency rule

> **Single-object access (`Object(pk)`, its property getters/setters,
> `create`/`delete`) is always edits-then-materialized, live, and
> transaction-correct — no dependency on refresh.
> Bulk access (`Object.where(...)`, link traversal) reads the materialized
> table/view only, and is only as fresh as the last refresh.**

This is the load-bearing design decision of the whole system. It's possible
because:

- `session.get(EditCls, pk)` benefits from SQLAlchemy's **autoflush** (a
  pending create/edit in this session is visible before the next query) and
  **identity map** (repeated access within a session doesn't re-query).
- A field read checks the edits table first; only on a miss does it fall
  back to materialized. A field write always ends up as a full-row upsert
  in edits (reading current state from edits-or-materialized first, if this
  is the first touch in the session).
- Bulk queries (`where()`) reference `_materialized_cls` only — link
  traversal has no merge and is materialized-only, full stop.

**Consequence for the coder:** `Product("some_pk")` never fails at
construction — it's a plain, thin wrapper holding only a `pk`. The first
_field access_ is what does existence-checking, and raises `ValueError` if
the pk doesn't exist anywhere. This is a documented, intentional
trade-off, not an oversight.

**Confirmed live (this session, via example-harness):** since nothing
refreshes the materialized view automatically, `Product.where()` will
show an **empty result even immediately after a successful `create()`**,
because the view was created (empty) before any rows existed and nothing
has refreshed it since. This is expected, not a bug — it's the direct,
observable consequence of the trade-off above. A helper that takes an
object's rid and manually triggers `REFRESH MATERIALIZED VIEW
CONCURRENTLY` for it would let tests exercise the bulk-access path
deliberately; this is a good, small, not-yet-built utility (see §7).

---

## 3. Package layout

```
forge/manifest/
  pyproject.toml           # name = "forge-manifest" — independently
                            # installable package boundary, distinct from
                            # forge's own root pyproject.toml (see §3.1)
  __init__.py               # declaration-facing surface: ManifestObjectDef,
                             # ManifestLinkDef, ManifestFieldDef, type vocabulary
  manifest_core/
    __init__.py              # internal/runtime-facing surface (fuller than
                              # forge.manifest itself)
    types.py                  # ManifestType and subclasses, STRING/INT/.../LIST,
                               # type_to_source() for codegen round-tripping
    defs.py                    # ManifestFieldDef/ManifestObjectDef/ManifestLinkDef,
                                # DeclarationCollector/bind_collector
    base.py                     # ManifestField, ManifestObject, ManifestObjectSet,
                                 # ManifestLink, current_session/_require_session
    registry.py                  # ObjectRegistry, ensure_registry_table,
                                  # ensure_registered, _make_mapped_class
  manifest_build/
    discovery.py               # discover_declarations — scans a folder, collects
                                # + validates ManifestObjectDef/ManifestLinkDef
    links.py                    # infer_join_kind / resolve_link_join_kinds
    codegen.py                   # generate_module_source / generate_build_init_source
                                  # — emits generated .py source + _build/__init__.py
    builder.py                    # build_msdk_within_session (core, session-scoped,
                                   # no commit/rollback) / build_msdk (self-contained
                                   # wrapper: opens session, commits or rolls back,
                                   # always closes)
    spinup.py                      # spinup_manifest_repo — scaffolds a new
                                    # declarations repo at a local directory
    env_init.py                     # init_environment — uv venv / compile / install
                                     # for a spun-up repo's own isolated environment
```

**Import layering (three tiers, deliberate):**

| Layer                                      | Who imports it                                                   | What it exposes                                                                                                                                            |
| ------------------------------------------ | ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `forge.manifest`                           | Declaration files (`product.py`)                                 | `ManifestObjectDef`, `ManifestLinkDef`, `ManifestFieldDef`, type vocabulary (`STRING`, `LIST`, ...) only                                                   |
| `forge.manifest.manifest_core`             | `manifest_build` modules, tests, generated code                  | Everything in `forge.manifest` **plus** `ManifestObject`, `ManifestObjectSet`, `ManifestLink`, `ManifestType`, `ensure_registered`, `ObjectRegistry`, etc. |
| `forge.manifest.manifest_core.<submodule>` | Anything needing a specific internal (e.g. `_make_mapped_class`) | Direct submodule import — not re-exported at package level unless genuinely public                                                                         |

A declaration file only ever needs the top tier. Nothing in
`forge.manifest` should ever require the coder to know `manifest_core`
exists.

### 3.1 Two separate `pyproject.toml`s, two separate audiences — a real gotcha found this session

`forge/manifest/pyproject.toml` (`name = "forge-manifest"`) is what a
spun-up Manifest repo actually depends on — it must be **independently
installable**, which requires its own `[build-system]` and
`[tool.setuptools.packages.find]` with `where = ["../.."]` (searching from
the true repo root, two levels up from where this file sits) and
`include = ["forge.manifest", "forge.manifest.*"]` (scoping discovery to
just this namespace).

**Root `forge/pyproject.toml`** is a _separate_ file serving Forge's own
local dev/test environment. It is deliberately **not** meant to be
depended on by external consumers as a package in the same way — nothing
a Manifest repo does should ever depend on the whole `forge` package
(which would transitively pull in future Terminal/Aperture tooling a
Manifest repo has no business needing). It was later also given a
`[build-system]`/discovery config, but only so that `example-harness`
(see §5.1) can install Forge itself, as a whole, for **live-mode
testing of Forge's own build-time functions** — a different, internal-use
case from "a Manifest repo depends on forge-manifest."

**Real bug found and fixed this session:** the first attempt at
`forge/manifest/pyproject.toml`'s `where` setting was `["."]` — searching
relative to `forge/manifest/`'s own location. This caused
`manifest_core`/`manifest_build` to install as **flat, top-level
packages** (importable as bare `import manifest_core`, not nested under
`forge.manifest`), silently breaking `forge/manifest/__init__.py`'s own
internal relative imports (`from .manifest_core...`), since no
`forge.manifest` namespace existed in the installed package at all. Fixed
by searching from the true repo root instead. This is the kind of bug
that only surfaces when something actually _installs_ the package (not
when just running tests via `PYTHONPATH`) — worth remembering as a
category of risk whenever packaging config changes.

---

## 4. Component reference

### 4.1 `types.py` — the field type system

`ManifestType` is a base class with `.sql_type` (a constructed SQLAlchemy
type instance, e.g. `String()`), `.is_array()`, `.element_type()`, and a
private `_to_source()` (public wrapper: `type_to_source()`) used only by
codegen to round-trip a type instance back into the literal Python source
text that reconstructs it.
Leaf types (`StringType`, `IntType`, `FloatType`, `BoolType`,
`DateTimeType`) are exposed as pre-built singletons (`STRING`, `INT`, ...).
`ArrayType` wraps any other `ManifestType` and is spelled `LIST[STRING]`
via `__class_getitem__` (mirroring `typing.List[X]`).

`ArrayType.sql_type` is a `@property` (not a class attribute like the
leaves) because `ARRAY(...)` needs its element type at construction time —
there's no single generic "the array type" the way there's a single "the
string type." Every `ManifestType` subclass still exposes `.sql_type` as
the uniform accessor; `_make_mapped_class` never branches on array-ness.

Adding a new leaf type (e.g. a future `GeometryType`) requires defining the
class (with its own `sql_type` and `_to_source()`) and nothing else —
`ArrayType`, `_make_mapped_class`, `infer_join_kind`, and `type_to_source`
are all written generically against the `ManifestType` interface, never
against a fixed set of concrete types or a separate registry dict that
could drift out of sync with the type list.

### 4.2 `defs.py` — declarations

`ManifestFieldDef`, `ManifestObjectDef`, `ManifestLinkDef` are pure data
containers — no validation, no I/O, no randomness, safe to construct
repeatedly with identical results. This is deliberate: table names/rids are
resolved later, by the build pipeline, using the registry as source of
truth (see §4.4) — never generated inside a declaration's `__init__`,
which would break idempotency across repeated builds.

**Self-registration, not variable assignment.** A coder writes:

```python
ManifestObjectDef(display_name="Product", api_name="Product", fields={...})
```

with no variable needed — construction alone registers the instance with
whatever `DeclarationCollector` is currently bound via `bind_collector()`
(a context manager). This was chosen over scanning `vars(module)` because
declarations are self-describing (`api_name` on objects; `source`/`target`
on links) — the coder's local variable name is irrelevant metadata that
discovery shouldn't depend on.

`_active_collector` is a module-level global, but scoping is safe because
`bind_collector` is a context manager: binding/unbinding always happens in
pairs, even on exception, so one bad declaration file can't leave stale
state for the next discovery run.

### 4.3 `base.py` — runtime object machinery

**`ManifestField`** — a descriptor (`__get__`/`__set__`) doing double duty:
accessed via an instance (`some_product.cost`), it routes through
`_get_field`/`_set_field` (edits-aware). Accessed via the class
(`Product.cost`), it returns the raw materialized column expression, so
`Product.cost < 100` reads naturally in `where()` calls with no
`ProductMaterialized` ever visible to the coder. This works because
Python's descriptor protocol calls `__get__(obj, objtype)` with `obj=None`
specifically for class-level access — the same mechanism that lets a plain
function become a bound method on an instance.

**The primary key field is NOT a `ManifestField`.** It's excluded from
`_properties` and never gets a `ManifestField` descriptor generated for
it — only `_pk_field` (a plain string naming it) exists on the class.
**This means `Product.product_id == "x"` does not work in a `.where()`
call** — there is no `Product.product_id` attribute at all. To query or
construct by primary key, use `Product("some_pk")` directly (construction
never fails) plus a field access to force the existence check, not a
`.where()` filter on the pk's name. This tripped up testing more than
once this session — worth remembering as the correct idiom, not a bug.

**`ManifestObject`** — thin, pk-only wrapper (`__init__(self, pk)`). Field
access is always live (edits-then-materialized). `create()` permanently
retires a pk once deleted — a deleted pk can never be recreated with the
same identity (a deliberate simplification; an explicit `undelete()` would
be a new, separate operation if ever needed). `__eq__`/`__hash__` are
pk-based, not identity-based, since `_wrap()` constructs a fresh Python
object on every query result — two `Product` instances with the same pk
are semantically the same object and should compare equal.

Confirmed via `example-harness` this session: since deletes are soft
(`_deleted=True`, row never removed) and a pk is permanently retired once
touched, a repeatable test script should generate a **fresh random pk**
(e.g. `str(uuid.uuid4())`) per run rather than reusing a fixed pk like
`"p1"` — reusing a fixed pk across runs will eventually hit "already
exists or was previously deleted" once that pk has ever been created.

**`ManifestObjectSet`** — wraps an _unexecuted_ SQLAlchemy `Select`
against `_materialized_cls`. `.where(...)` builds it; `.all()`/`.first()`/
iteration execute it. Link traversal (`some_set.parts`) is implemented via
`__getattr__`, looking up a `ManifestLink` on `_element_cls._links` and
building a **nested subquery** — chaining `product_set.parts.vendors`
compiles to one SQL statement with two levels of subqueries, not two
separate round trips. `.distinct()` on the final query handles dedup at
the database level.

Consuming a chained traversal (e.g. via `.all()`) forces the whole nested
subquery to execute at once — each hop is "lazy until consumed, batched
per hop," not truly streaming row-by-row across hops. This is an
intentional trade-off: true per-item streaming and N+1-avoidance are
mutually exclusive, and batched is the right choice for the stated scale
(tens of thousands of rows per bulk operation, not millions).

**`ManifestLink`** — relationship metadata (`join_kind`, `local_field`,
`remote_field`), consumed by `ManifestObjectSet.__getattr__` to build the
correct join condition:

| `join_kind`       | Shape                              | SQL construct used                                       |
| ----------------- | ---------------------------------- | -------------------------------------------------------- |
| `scalar_fk`       | local scalar FK → target scalar pk | `target_col.in_(select(local_col))`                      |
| `array_fk_parent` | local array → target scalar pk     | `target_col.in_(select(func.unnest(local_col)))`         |
| `array_fk_child`  | local scalar pk ← target array     | `target_col.op("&&")(select(func.array_agg(local_col)))` |

A single `ManifestLinkDef` (declared once, on whichever side holds the FK)
produces **two** `ManifestLink`s — forward and reverse — with correspondingly
different `join_kind`s and swapped `local_field`/`remote_field`. There is
no supported shape for two array columns pointing at each other
(many-to-many via a join-table entity is the documented workaround — see
the error message in `manifest_build/links.py`).

### 4.4 `registry.py` — schema bookkeeping and DDL

**`ObjectRegistry`** is the durable, cross-restart source of truth: one row
per `api_name`, recording resolved table names and a serialized schema
fingerprint. **`_registered_classes`** is a separate, same-process-only
cache preventing SQLAlchemy from double-mapping a table name within one
run — it must be cleared at the start of every build (and between tests;
see §5) since it does not track database state at all, only "have I built
this Python class in this process."

**`ensure_registered`** enforces three conditions once a registry row exists:

1. The registry row's referenced tables actually exist in the database.
2. The registry row's tables match the tables passed to this call.
3. The registry row's recorded schema matches the freshly-built schema
   (raises `SchemaConflictError` on mismatch).

A fourth condition — "a class must already exist in `_registered_classes`
whenever a registry row exists" — was tried and removed. It's wrong for the
single most common real case: the _first_ call in a fresh process against
an _already-registered_ object (a normal second build, run later, in a new
process) legitimately has no cached class yet — `_registered_classes`
starts empty every process, by design. Treating that as an error would
make every real rebuild fail on its very first `ensure_registered` call.
The one case worth still catching — a class cached under this name with
_no_ matching registry row at all, meaning two different `api_name`s are
colliding on one table name within a process — remains a hard error.

On a genuine clean slate (no row, no cached class), it creates the edits
table, the materialized view, a unique index, and the registry row — all
via the **same `Session`**, never its own transaction. This is deliberate:
`ensure_registered` performs no commit and no flush; the caller
(`build_msdk_within_session`) commits once, after looping over every
declared object, giving **cross-object atomicity** — either every object
in a build registers together, or (on any single failure) the whole build
rolls back, including DDL, which is transactional in Postgres.

Any partial mismatch among the conditions above is a **hard error**, not
an auto-repair — the documented recovery path is manual: inspect the
database, likely delete the offending `ObjectRegistry` row, and rebuild.

`ensure_registry_table` bootstraps the registry table itself and must be
called once, before any `ensure_registered` calls, against a fresh
database.

### 4.5 `spinup.py` / `env_init.py` — scaffolding a standalone declarations repo

**`spinup_manifest_repo(target_dir)`** creates a new manifest repo where
`target_dir` **is** both the git repo root and the importable Python
package — `pyproject.toml`, `README.md`, `__init__.py`, `src/declarations/`,
and `_build/` all live directly inside it, flat, with no extra nesting.
This means `target_dir`'s own folder name must be a valid Python
identifier (no hyphens), since it doubles as the package name a consumer
will `import`. For **local-directory** spinup specifically, an invalid
name is auto-corrected (hyphens → underscores, lowercased) rather than
rejected — the function creates the scaffold at a sibling, corrected path
and returns that actual path, which callers must use rather than assuming
it matches their input string. This auto-correction is intentionally
local-only: a future git-clone-based spinup path must take a cloned
repo's folder name as authoritative and raise instead of silently
renaming it, since a git repo's name isn't something spinup can rewrite.

`_build/` (underscore, not a literal dot) is the generated output
directory — named with a leading underscore specifically so it's both a
valid importable Python package name and carries the "internal, don't
touch" convention. The root `__init__.py` re-exports everything from
`_build/`, so a consumer always writes `from my_manifest_repo import
Product`, never anything referencing `_build` directly — codegen's
`generate_build_init_source` produces `_build/__init__.py`'s
`__all__`/import lines on every build, kept in sync with whatever objects
were actually declared.

**Real bug found and fixed this session:** `build_msdk_within_session`
was writing `_generated.py` correctly but **never actually calling
`generate_build_init_source`**, even though that function existed
correctly in `codegen.py`. This meant `_build/__init__.py` stayed
permanently at its spinup-time placeholder (`__all__ = []`), even after a
fully successful build — so `from my_manifest_repo import Product` always
raised `ImportError`, silently, while `_generated.py` itself was
completely correct. This went undetected for a while because most testing
either imported `_generated.py`'s contents directly (bypassing the
`_build/__init__.py` re-export) or used `exec()`-based codegen tests that
never touched the file-writing step. It was only caught once
`example-harness`'s CRUD test exercised the actual, intended top-level
import path. **Lesson: a passing unit test for `generate_build_init_source`
existing and working correctly does not prove `builder.py` calls it —
`test_builder.py` needs its own assertion on `_build/__init__.py`'s
content, not just `_generated.py`'s.**

**`init_environment(repo_dir)`** sets up an isolated environment for a
spun-up repo using `uv` (a standalone binary, not a Python package):
`uv venv` creates the venv, `uv pip compile pyproject.toml -o
requirements-lock.txt` resolves the repo's declared (range-based)
dependencies into an exact, reproducible lock file, `uv pip install -r
requirements-lock.txt` installs from that lock file. This mirrors the
same declare-loosely/pin-exactly split used everywhere else in this
system — `pyproject.toml` is human-facing and never installed from
directly; `requirements-lock.txt` is the machine-generated, reproducible
artifact that installation actually reads. This function is layer-agnostic
(no Manifest-specific logic) and is intended to be reused as-is for
Terminal/Aperture repo spinup later.

**Real bug found and fixed this session:** the original `init_environment`
didn't pass `--python` explicitly to `uv pip compile`/`install`. `uv`
resolves which environment to target partly via the inherited
`VIRTUAL_ENV` environment variable — if the calling shell already had a
_different_ venv active (e.g. Forge's own dev venv), `uv` silently
installed into that ambient environment instead of the freshly-created
one at `repo_dir/.venv`, with no error. Fixed by passing
`--python <repo_dir>/.venv/bin/python` explicitly on every `uv` call.
**Lesson: never rely on an ambient `VIRTUAL_ENV`/`PATH` state when a
script needs to target a specific, just-created venv — always pass
`--python`/an explicit interpreter path.**

---

## 5. Testing

Tests run against **real Postgres**, never SQLite — the design leans on
Postgres-specific features (`MATERIALIZED VIEW`, `ARRAY`, `&&`, `unnest`)
that have no SQLite equivalent, so anything less than the real engine would
give false confidence.

- `tests/docker-compose.test.yml` defines an isolated, `tmpfs`-backed
  (non-persistent) Postgres container, separate from the real dev database
  in the repo-root `docker-compose.yml`.
- `tests/conftest.py`'s session-scoped `engine` fixture starts the
  container, waits for a **real query to succeed** (not just
  `pg_isready` — a Postgres container can report ready during its internal
  restart-during-init cycle and still refuse the next connection), creates
  the registry table, and tears the container down at session end.
- The function-scoped `db_session` fixture wraps each test in a
  connection-level transaction that's always rolled back — Postgres DDL is
  transactional, so this cleanly undoes `CREATE TABLE`/`CREATE MATERIALIZED
VIEW` per test, not just row data.
- Because `_registered_classes` and SQLAlchemy's own declarative
  registry are process-level state independent of the database rollback,
  `db_session`'s teardown also removes any dynamically-created tables from
  `Base.metadata` and clears `_registered_classes` — otherwise a second
  test reusing the same `api_name` sees a same-process cache the database
  rollback never touched, producing confusing "class exists but registry
  row doesn't" errors that have nothing to do with the code under test.
  A shared `reset_registered_classes()` helper does this and is also
  callable mid-test, for tests that deliberately simulate "a fresh
  process" partway through (e.g. schema-conflict detection tests).

### 5.1 `example-harness` — a second, complementary layer of testing

Separate from the unit-test suite above, a sibling directory
(`example-harness/`, outside Forge's own repo, alongside it) exists for
**end-to-end validation against a real, persisting dev database**, for
manual review (e.g. via TablePlus) — something the ephemeral, rolled-back
unit-test database structurally cannot provide.

The harness has its own venv and `pyproject.toml`/`init_workspace.py`
setup, driven by a small `harness_config.py` (`MODE = "live"` or
`"branch"`, plus a `REF` for branch mode). This distinguishes two tiers of
end-to-end testing:

- **Live mode** — Forge itself is installed via an **editable install**
  pointed directly at Forge's own repo on disk (`uv pip install -e
<forge_root>`), so edits to Forge's source take effect immediately with
  no reinstall. This is the fast inner loop for validating changes while
  actively developing Forge.
- **Branch mode** — Forge is installed via a real, resolved git reference
  (`forge @ git+https://...@<branch-or-tag>`), exactly matching what an
  external consumer would experience. This is what actually exercises the
  packaging/distribution mechanism itself, catching the class of bug
  described in §3.1 and §4.5 that live mode (or unit tests) cannot catch,
  since neither of those ever goes through a real install.

**Real gotcha found and fixed this session: editable installs are
invisible to Pylance by default.** Modern `pip`/`uv` editable installs use
a PEP 660 import-hook mechanism (a generated finder module) rather than a
simple path redirect — this works perfectly at runtime, but VS Code's
Pylance does purely _static_ analysis and cannot see through the runtime
hook, making a correctly-installed editable package look like an empty,
unresolvable import in the editor even though it works when actually run.
Fixed by installing with `--config-settings editable_mode=compat`, which
uses the older, `.pth`-file-based editable mechanism — a plain path
addition, fully visible to static analysis. This flag is now baked into
both `example-harness/init_workspace.py`'s live-mode install and
`scripts/mount_repo.sh`.

A second, related gotcha: VS Code's `python.defaultInterpreterPath`
setting does not reliably resolve the `${workspaceFolder}` variable in
all versions — the fix used here is to have the setup script itself
(`init_workspace.py`, `mount_repo.sh`) write `.vscode/settings.json` with
the venv's **fully resolved absolute path**, generated fresh on every
run, rather than a variable-based path a human hand-writes once. This
keeps the _scripts_ portable (they compute the path from an anchor, same
as everywhere else in this codebase) while the _generated_ settings file
is correctly machine-specific and gitignored, not committed.

`scripts/mount_repo.sh` (Forge repo root) is the general-purpose version
of this same "set up venv, install deps, configure Pylance, open VS Code"
pattern, intended for any Forge-managed repo (a real spun-up Manifest
repo, later a Terminal repo) — not just harness-internal testing repos.

---

## 6. Known trade-offs and deliberate gaps

- **Traversal bypasses native SQLAlchemy `relationship()`.** Given the
  array-based FK shapes and the edits/materialized split, hand-rolled
  `Select`/subquery composition was judged a better fit than
  `relationship()` + `primaryjoin=` boilerplate for every shape. This means
  bugs in traversal are bugs in `ManifestObjectSet`/`ManifestLink`, not
  something SQLAlchemy's own loader-strategy machinery can be blamed for
  or fixed independently.
- **Materialized-view refresh is out of scope for `manifest_core`.**
  Nothing in this module calls `REFRESH MATERIALIZED VIEW CONCURRENTLY`.
  That responsibility belongs to whatever owns "keep the database fresh"
  (a scheduled job, a Terminal-layer hook) — deliberately kept outside
  `manifest_core` so the build/runtime split stays clean (Forge is
  build-time only; nothing it produces should depend on Forge still
  running). Confirmed via `example-harness` this session: `.where()`
  genuinely returns nothing until a manual refresh, even right after a
  successful `create()` — see §2 and §7.
- **No FK-constraint enforcement at the database level.** Materialized
  views can't carry real foreign keys, and nothing writes directly to
  them anyway. Referential integrity for links is enforced only by
  application logic (`discover_declarations`'s validation), not by
  Postgres.
- **Rid/table-name generation is a placeholder for a future `Dataset`
  abstraction.** `edits_table`/`materialized_table` are currently literal
  physical table names. A planned `Dataset` concept (wrapping dotted rids,
  with version/branch resolution) will change this into an indirection
  layer — `ensure_registered` and `_make_mapped_class` will need rework at
  that point.
- **Many-to-many via two array columns is unsupported by design**, not by
  omission — `infer_join_kind` raises with an explicit suggestion (a
  join-table entity with two scalar-or-list links) rather than attempting
  to guess intent.

---

## 7. What's not yet built

Codegen, builder orchestration (including cross-object-atomic
registration and table-name reuse across rebuilds), repo spinup, and a
working end-to-end test harness (`example-harness`, both live and branch
mode) are now complete and validated against real Postgres — see §4 and
§5 above. What remains:

- **A manual materialize/refresh helper** — a small function taking an
  object's rid (or api_name) and running `REFRESH MATERIALIZED VIEW
CONCURRENTLY` for just that object's materialized table, so tests (and
  possibly a future Terminal-layer hook) can deliberately exercise the
  bulk-access path without waiting for or building a full scheduler.
  Small, not yet built — noted here so it isn't lost.
- **Schema migration** — `ensure_registered` currently treats _any_ schema
  diff (once a registry row already exists) as fatal via
  `SchemaConflictError`. A planned safe/unsafe diff classifier (nullable
  relaxation and additive nullable fields auto-apply; type changes, field
  removal, and nullable tightening require explicit migration) is designed
  but not implemented.
- **Git-backed spinup/build/publish** — `spinup_manifest_repo`/
  `build_msdk_within_session`/`init_environment` all operate on plain
  local filesystem paths only, by design. The git layer around them —
  clone-to-temp, build, commit, tag, push — is designed conceptually but
  not yet implemented.
- **Forge API service** — an HTTP layer exposing spinup/build/publish as
  endpoints (so a caller can trigger these without direct Python function
  calls) is planned but not started. Deliberately sequenced _after_ the
  git-backed layer above, so the API has the complete functionality to
  expose rather than only the local-directory subset.
- **`_properties`/`_nullable_map`/`_pk_field` redundancy with
  `ManifestField` declarations** — codegen currently emits this
  information twice (once via `ManifestFieldDef` in `_fields_X`, once via
  these class attributes). A cleaner design would derive them from the
  `ManifestField` descriptors themselves at class-creation time (e.g. via
  `__init_subclass__`), collapsing the redundancy. Deliberately postponed
  until the full spinup → declare → build → import → git pipeline is
  proven end-to-end (now largely true for the local-path version — still
  worth waiting for the git-backed layer too), so this refactor has a
  working test suite as a safety net rather than being done mid-pipeline.
- **Terminal and Aperture layers** — not started. The same
  spinup/build/publish pattern (directory-level build, git-level publish,
  `pyproject.toml` + lock file dependency management via `uv`,
  `mount_repo.sh`-style IDE setup) is intended to generalize directly to
  both, per design discussion, but no code exists yet for either.
