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

**Confirmed live (via `e2e-harness`):** since nothing refreshes the
materialized view automatically, `Product.where()` will show an **empty
result even immediately after a successful `create()`**, because the view
was created (empty) before any rows existed and nothing has refreshed it
since. This is expected, not a bug — it's the direct, observable
consequence of the trade-off above. A manual materialize/refresh helper is
still a planned, not-yet-built utility — see §7.

---

## 3. Package layout

```
forge/
  manifest/
    pyproject.toml           # name = "forge-manifest" — independently
                              # installable package boundary, distinct from
                              # forge's own root pyproject.toml (see §3.1)
    __init__.py                # declaration-facing surface: ManifestObjectDef,
                                # ManifestLinkDef, ManifestFieldDef, type vocabulary
    manifest_core/
      __init__.py               # internal/runtime-facing surface (fuller than
                                 # forge.manifest itself)
      types.py                   # ManifestType and subclasses, STRING/INT/.../LIST,
                                  # type_to_source() for codegen round-tripping
      defs.py                     # ManifestFieldDef/ManifestObjectDef/ManifestLinkDef,
                                   # DeclarationCollector/bind_collector
      base.py                      # ManifestField, ManifestObject, ManifestObjectSet,
                                    # ManifestLink, current_session/_require_session
      registry.py                   # ObjectRegistry, ensure_registry_table,
                                     # ensure_registered, _make_mapped_class
    manifest_build/
      discovery.py                 # discover_declarations — scans a folder, collects
                                    # + validates ManifestObjectDef/ManifestLinkDef
      links.py                      # infer_join_kind / resolve_link_join_kinds
      codegen.py                     # generate_module_source / generate_build_init_source /
                                      # generate_registry_json — emits generated .py source,
                                      # _build/__init__.py, and _build/registry.json
      builder.py                      # build_msdk_within_session (core, session-scoped,
                                       # no commit/rollback) / build_msdk (self-contained
                                       # wrapper) / git_build_manifest_repo (git-aware,
                                       # composes git_ops — see §4.6)
      spinup.py                        # spinup_manifest_repo (local) / git_spinup_manifest_repo
                                        # (git-aware, composes git_ops — see §4.6)
      open.py                           # open_manifest_repo (local: venv, deps, Pylance config,
                                         # VS Code launch) / git_open_manifest_repo (git-aware,
                                         # reuse-if-present — see §4.6)
      env_init.py                        # init_environment — uv venv / compile / install
                                          # for a spun-up repo's own isolated environment
      git_ops.py                          # _run_git, clone_repo, commit_repo, push_repo,
                                           # commit_and_push, tag_repo — generic git primitives,
                                           # imports nothing else in this package (see §4.6)
  api/
    main.py                   # FastAPI app, includes each layer's router
    cli.py                      # forge-api CLI entry point (registered via root
                                 # pyproject.toml's [project.scripts])
    routes/
      manifest.py                # /manifest/* routes — thin wrappers around
                                  # manifest_build functions, one route per function
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

**A second, orthogonal layering discipline within `manifest_build`
itself** (established when `git_ops.py` was added): `git_ops.py` is a
generic primitive layer with zero knowledge of Manifest, spinup, or
builds — it only knows about git. `spinup.py`, `builder.py`, and `open.py`
each import `git_ops` and compose its primitives for their own specific
purpose (`git_spinup_manifest_repo`, `git_build_manifest_repo`,
`git_open_manifest_repo`). None of these three import each other. This
keeps the dependency graph flat and one-directional: `git_ops` ← {spinup,
builder, open}, never the reverse, and never sideways.

### 3.1 Two separate `pyproject.toml`s, two separate audiences — a real gotcha found this session

`forge/manifest/pyproject.toml` (`name = "forge-manifest"`) is what a
spun-up Manifest repo actually depends on — it must be **independently
installable**, which requires its own `[build-system]` and
`[tool.setuptools.packages.find]` with `where = ["../.."]` (searching from
the true repo root, two levels up from where this file sits) and
`include = ["forge.manifest", "forge.manifest.*"]` (scoping discovery to
just this namespace).

**Root `forge/pyproject.toml`** is a _separate_ file serving Forge's own
local dev/test environment, and also — since the API was added — the
source of the `forge-api` CLI entry point (`[project.scripts]`, see §4.7).
It is deliberately **not** meant to be depended on by external Manifest-
repo consumers as a package (that would transitively pull in Terminal/
Aperture tooling a Manifest repo has no business needing). It carries its
own `[build-system]`/discovery config so that `e2e-harness` (see §5.1) can
install the whole of Forge for **live-mode testing of Forge's own
build-time and API functions** — a different, internal-use case from "a
Manifest repo depends on forge-manifest," and also the case that makes
`forge.api` (which genuinely does need every other layer importable, see
§4.7) installable at all.

**Real bug found and fixed this session:** the first attempt at
`forge/manifest/pyproject.toml`'s `where` setting was `["."]` — searching
relative to `forge/manifest/`'s own location. This caused
`manifest_core`/`manifest_build` to install as **flat, top-level
packages**, silently breaking `forge/manifest/__init__.py`'s own internal
relative imports. Fixed by searching from the true repo root instead.
Worth remembering as a category of risk whenever packaging config
changes — it only surfaces once something actually _installs_ the
package, not when just running tests via `PYTHONPATH`.

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
leaves) because `ARRAY(...)` needs its element type at construction time.
Adding a new leaf type requires defining the class (with its own
`sql_type` and `_to_source()`) and nothing else.

### 4.2 `defs.py` — declarations

`ManifestFieldDef`, `ManifestObjectDef`, `ManifestLinkDef` are pure data
containers — no validation, no I/O, no randomness, safe to construct
repeatedly with identical results. Table names/rids are resolved later, by
the build pipeline, using the registry as source of truth (§4.4).

**Self-registration, not variable assignment.** A coder writes
`ManifestObjectDef(display_name="Product", api_name="Product",
fields={...})` with no variable needed — construction alone registers the
instance with whatever `DeclarationCollector` is currently bound via
`bind_collector()` (a context manager).

**Field naming convention (not yet enforced in code — see §7):** a
field's dict key in `fields={...}` is its **code-facing name**, and is
also, by convention, its backing database column name — code always
addresses a field by this one name. A separate, purely presentational
`display_name` per field (mirroring `ManifestObjectDef`'s existing
`display_name`/`api_name` split) is planned but not yet implemented on
`ManifestFieldDef`/`ManifestField` — see §7.

### 4.3 `base.py` — runtime object machinery

**`ManifestField`** — a descriptor (`__get__`/`__set__`): accessed via an
instance (`some_product.cost`), routes through `_get_field`/`_set_field`
(edits-aware). Accessed via the class (`Product.cost`), returns the raw
materialized column expression, so `Product.cost < 100` reads naturally in
`where()` calls.

**The primary key field is NOT a `ManifestField`.** It's excluded from
`_properties` and never gets a `ManifestField` descriptor — only
`_pk_field` (a plain string naming it) exists on the class. `Product.product_id
== "x"` does **not** work in `.where()`. To query/construct by pk, use
`Product("some_pk")` directly plus a field access to force the existence
check.

**`ManifestObject`** — thin, pk-only wrapper. `create()` permanently
retires a pk once deleted — a deleted pk can never be recreated with the
same identity. `__eq__`/`__hash__` are pk-based.

Confirmed via `e2e-harness`: since deletes are soft and a pk is
permanently retired once touched, a repeatable test script should
generate a **fresh random pk** (e.g. `str(uuid.uuid4())`) per run rather
than reusing a fixed pk.

**`ManifestObjectSet`** — wraps an _unexecuted_ `Select` against
`_materialized_cls`. Link traversal (`some_set.parts`) via `__getattr__`,
building nested subqueries — chaining `product_set.parts.vendors` compiles
to one SQL statement, batched per hop, not truly streaming.

**`ManifestLink`** — relationship metadata:

| `join_kind`       | Shape                              | SQL construct used                                       |
| ----------------- | ---------------------------------- | -------------------------------------------------------- |
| `scalar_fk`       | local scalar FK → target scalar pk | `target_col.in_(select(local_col))`                      |
| `array_fk_parent` | local array → target scalar pk     | `target_col.in_(select(func.unnest(local_col)))`         |
| `array_fk_child`  | local scalar pk ← target array     | `target_col.op("&&")(select(func.array_agg(local_col)))` |

A single `ManifestLinkDef` produces **two** `ManifestLink`s (forward and
reverse). No supported shape for two array columns pointing at each other.

### 4.4 `registry.py` — schema bookkeeping and DDL

**`ObjectRegistry`** is the durable, cross-restart, **transactional**
source of truth: one row per `api_name`, recording resolved table names
and a serialized schema fingerprint. **`_registered_classes`** is a
separate, same-process-only cache preventing SQLAlchemy from
double-mapping a table name within one run.

**`ensure_registered`** enforces three conditions once a registry row
exists:

1. The registry row's referenced tables actually exist in the database.
2. The registry row's tables match the tables passed to this call.
3. The registry row's recorded schema matches the freshly-built schema
   (raises `SchemaConflictError` on mismatch — see §7 for the planned
   safe/unsafe migration classifier that will relax this).

A fourth condition — "a class must already exist in `_registered_classes`
whenever a registry row exists" — was tried and removed; it broke the
single most common real case (first call in a fresh process against an
already-registered object).

On a genuine clean slate, `ensure_registered` creates the edits table, the
materialized view, a unique index, and the registry row — all via the
**same `Session`**, no commit/flush of its own. The caller
(`build_msdk_within_session`) commits once, after looping over every
declared object, giving **cross-object atomicity**.

`ensure_registry_table` bootstraps the registry table itself.

### 4.5 `spinup.py` / `open.py` / `env_init.py` — scaffolding, opening, and environment setup

**`spinup_manifest_repo(target_dir)`** creates a new manifest repo where
`target_dir` **is** both the repo root and the importable Python package.
For **local-directory** spinup, an invalid folder name (e.g. containing
hyphens) is auto-corrected; the function returns the actual path used,
which callers must use. This auto-correction is local-only — a git-clone
target's folder name is authoritative (see §4.6).

`_build/` (underscore) is the generated output directory. The root
`__init__.py` re-exports everything from `_build/`, kept in sync on every
build by `generate_build_init_source`.

**Real bug found and fixed this session:** `build_msdk_within_session`
was writing `_generated.py` but **never actually calling
`generate_build_init_source`**, leaving `_build/__init__.py` permanently
at its placeholder (`__all__ = []`) even after a successful build — so
`from my_manifest_repo import Product` always raised `ImportError`. Only
caught once `e2e-harness`'s CRUD test exercised the actual top-level
import path. **Lesson: a passing unit test for a codegen function existing
and working does not prove the builder actually calls it** —
`test_builder.py` needs its own assertion on `_build/__init__.py`'s
content, not just `_generated.py`'s (now added).

**`open_manifest_repo(repo_dir, open_editor=True)`** (in `open.py`)
prepares a manifest repo for local development: recreates its venv via
`init_environment`, writes `.vscode/settings.json` pointing at the venv's
Python (see §5.1's Pylance gotchas for why this file's content matters),
and — if `open_editor` — launches VS Code via a **single** `code <dir>
<example_file> --goto <readme>` call. This opens with `README.md` focused
and the starter declaration file as a background tab. Using one combined
`code` invocation (rather than two sequential calls) is deliberate — an
earlier two-call version had a race condition where the second call could
land before the first window finished initializing, causing the wrong
file to end up focused.

`open_editor=False` exists specifically so automated tests (and any
future headless/CI use) can exercise the environment-setup half of this
function without a GUI editor window appearing.

**`init_environment(repo_dir)`** sets up an isolated environment using
`uv`: `uv venv --clear`, `uv pip compile pyproject.toml -o
requirements-lock.txt --python <venv>/bin/python`, `uv pip install -r
requirements-lock.txt --python <venv>/bin/python`.

**Two real bugs found and fixed this session:**

- The original `uv venv` call (no `--clear`) would hang **indefinitely**
  waiting for an interactive y/n prompt if a venv already existed at the
  target path. This is invisible when run interactively (you just type
  `y`), but when called from the Forge API server (no attached terminal),
  the request would simply hang forever with no error. Fixed with
  `--clear`, which silently replaces an existing venv with no prompt.
  **Lesson: any subprocess call that could prompt interactively must be
  made non-interactive before it's ever called from a server process —
  it will hang silently, not fail loudly, if you don't.**
- The original calls didn't pass `--python` explicitly; `uv` would
  silently install into whatever venv was ambiently active via the
  calling shell's `VIRTUAL_ENV`, not the freshly-created target venv.
  Fixed by always passing `--python <repo_dir>/.venv/bin/python`
  explicitly.

### 4.6 `git_ops.py` — generic git primitives, and the git-aware composition layer

**Scope decision:** for now, Forge only clones/operates on **already-
existing** remote repos the user provides a URL for — it does not create
new remote repositories via GitHub's API. This avoids needing GitHub API
tokens/auth entirely for this layer; only plain `git` (clone/commit/push/
tag) is used.

**Credentials philosophy:** Forge's git operations never see, store, or
manage any credential. They shell out to plain `git` commands, and
authentication is resolved exactly however the user's own machine already
resolves it for any manual `git push` — an SSH key registered with the
remote (SSH agent), a cached HTTPS credential (OS keychain / credential
helper), or a PAT embedded in the remote URL if the user chose that. This
means **zero new security surface**: Forge is not a secrets store, and a
user who has ever successfully pushed to a given remote from their own
machine needs no additional setup for Forge's git operations to work
against that same remote. The trade-off: a user who has never configured
git authentication at all gets no in-app guidance beyond a clear error
message pointing at GitHub's own SSH setup docs — this is a deliberate,
accepted gap for now, not an oversight.

**`_run_git(args, cwd)`** — the one place every git subprocess call goes
through. Always sets `GIT_TERMINAL_PROMPT=0` in the subprocess's
environment (a copy of the calling process's env, not a mutation of it —
this has zero effect on the user's own shell/git configuration once the
subprocess exits). This guarantees a git operation that would need
interactive input (e.g. a credential prompt) **fails immediately with a
clear error** instead of hanging forever — the same class of fix as
`env_init.py`'s `uv venv --clear`, applied to git specifically.

**`clone_repo(git_url, target_dir, branch=None)`** — clones an
already-existing repo. Raises `FileExistsError` if `target_dir` already
exists (git clone itself requires a nonexistent/empty destination); this
is intentionally **not** auto-corrected the way local spinup's invalid
folder names are — a cloned repo's name/location is caller-determined and
authoritative.

**`commit_repo(repo_dir)`** — stages everything (`git add -A`) and commits
with a fixed message (`FORGE_BUILD_COMMIT_MESSAGE = "Forge build
commit"`). Detects "nothing to commit" via `git diff --cached --quiet`
and returns `{"committed": False}` rather than erroring. Does **not**
push.

**`push_repo(repo_dir)`** — pushes the current branch. Raises
`GitOperationError` with git's own stderr on failure (most commonly an
auth failure or a rejected/behind push).

**`commit_and_push(repo_dir)`** — the generic composition of the two
above; skips the push entirely if there was nothing to commit, rather
than pushing anyway (an earlier draft of this function pushed
unconditionally even with nothing new — reconsidered as misleading: a
caller asking to "publish changes" shouldn't have a silent, unrequested
push happen when there were no changes at all).

**`tag_repo(repo_dir, tag)`** — creates a tag at HEAD and pushes it.
Deliberately a **separate** function from commit/push, not folded into
one "publish with optional tag" function — tagging is a distinct action
with its own intent, not a mode of committing.

**The composed, domain-specific functions** (living in `spinup.py`/
`builder.py`/`open.py`, each importing only `git_ops`, never each other):

- **`git_spinup_manifest_repo(git_url)`** (in `spinup.py`) — clones to a
  temp directory, scaffolds it via `spinup_manifest_repo`, commits and
  pushes the scaffold via `commit_and_push`. No tagging.
- **`git_open_manifest_repo(git_url, branch="main", open_editor=True)`**
  (in `open.py`) — clones to a **deterministic** temp path keyed by repo
  name and branch (`<tempdir>/forge_git_open/<repo_name>/<branch>`,
  derived from the git URL, not randomly generated), then delegates to
  `open_manifest_repo`. **Reuse-if-present**: if that deterministic path
  already exists, it is _not_ re-cloned — the existing local checkout is
  opened as-is. This was a deliberate design change from an earlier
  always-fresh-clone version: always deleting and re-cloning on every
  call meant calling `git-open` a second time while a VS Code window from
  the first call was still open would delete that window's files out from
  under it. Reuse-if-present means repeated `git-open` calls against the
  same repo+branch are safe and idempotent, at the cost of not
  automatically picking up upstream changes pushed by someone else to
  that branch in the meantime — an accepted trade-off for a single-
  developer local workflow.
- **`git_build_manifest_repo(git_url, tag, engine)`** (in `builder.py`) —
  clones fresh to a temp directory (always fresh, unlike `git_open` — a
  build should reflect exactly what's currently on the remote, not a
  possibly-stale local checkout), runs `build_msdk_within_session`,
  commits + pushes the generated output via `commit_and_push`, then tags
  via `tag_repo`. Requires a `tag` — build-and-publish without a
  resulting tag is not a supported combination for the git-aware path,
  since the practical purpose of a git build is producing something a
  downstream repo (e.g. Terminal) can pin a dependency to.

**Validated end-to-end this session** against a real GitHub repo
(`https://github.com/mehdidotesk73/test_manifest_repo_git.git`, an HTTPS
remote — confirming HTTPS-credential-based auth, not just SSH, works
through this whole chain): `git-spinup` → `git-open` (edit a real
declaration in the opened VS Code window) → `git-commit` (via the plain
`commit_and_push` route, using the exact `repo_path` `git-open` returned)
→ `git-build` (fresh clone, real build against `forge_dev`, commit, push,
tag) — all four steps succeeded via real HTTP calls through Swagger UI,
with each git-side effect (commits, the new tag) confirmed directly on
GitHub afterward.

### 4.7 `forge/api/` — the HTTP service layer

**Why this needs every other layer importable, unlike Manifest/Terminal/
Aperture needing each other:** Manifest and Terminal never import each
other's source — a Terminal-built repo can depend on a Manifest-built
repo as an installed _package_, but that's a relationship between
generated artifacts, not between Forge's own layer code. `forge.api`
is structurally different: its entire job is directly importing and
calling each layer's real functions to expose them as HTTP routes (e.g.
`routes/manifest.py` directly calls `spinup_manifest_repo`). There is no
artifact-level indirection possible for this — the API _is_ the wrapping.
This is why `forge-api`'s CLI entry point is declared in **root**
`forge/pyproject.toml` (installing root `forge` gives you every
subpackage, `forge.api` included) rather than `forge/api/` having its own
independent `pyproject.toml` the way `forge/manifest/` does — there is no
realistic scenario where someone wants `forge-api` installed without the
rest of Forge also being present, unlike Manifest, which is deliberately
usable standalone.

**`main.py`** creates the `FastAPI()` app and calls `app.include_router(...)`
once per layer's router — kept intentionally small forever; it should
never accumulate route logic itself as Terminal/Aperture routers are
added later.

**`routes/manifest.py`** — one `APIRouter(prefix="/manifest", tags=["manifest"])`,
one route per `manifest_build` function, each a thin try/except wrapper
translating Python exceptions to HTTP status codes (`FileExistsError` →
409, `FileNotFoundError`/missing-declarations → 404, `GitOperationError`
→ 400 or 401, generic build failure → 400). Routes: `/spinup`, `/build`,
`/open`, `/registry` (GET), `/clone`, `/git-commit`, `/tag`,
`/git-spinup`, `/git-open`, `/git-build`.

**`cli.py`** — `forge-api run` (registered via root `pyproject.toml`'s
`[project.scripts]`). Auto-detects the first open port starting at 8000
(binds a throwaway socket per candidate port to test availability, since
multiple `forge-api run` sessions or leftover processes commonly occupy
8000/8001 during iterative development) rather than failing if 8000 is
taken. Prints a clear startup banner pointing at `/docs` (Swagger UI) —
FastAPI's own default log line only prints the bare base URL, which
doesn't tell a first-time user where the actually-useful interactive page
is.

**Swagger UI (`/docs`), auto-generated by FastAPI from the routes above,
is treated as a first-class user interface, not just documentation** — a
real end user can spin up, open, build, and publish a Manifest repo
entirely by clicking through Swagger UI's "Try it out" forms, with zero
Python knowledge and zero memorized CLI commands. A future custom UI
(e.g. a `forge-gui`) would be strictly additive on top of this same route
surface, not a replacement — and is explicitly understood to be "another
potential point of failure" layered on top of an already-working
interface, not a prerequisite for Forge being usable.

**`registry.json` and `GET /manifest/registry`:** every build now also
writes `_build/registry.json` (via `generate_registry_json` in
`codegen.py`), a machine-readable description of each declared object's
`display_name`, `pk_field`, resolved `edits_table`/`materialized_table`,
`fields` (type/primary_key/nullable/index), `properties`, `links`
(traversal attribute name → target + join_kind), and the fixed `methods`
list (`create`/`delete`/`where` — uniform across every object, so listed
rather than described in detail). `GET /manifest/registry?repo_dir=...`
reads and returns this file. Intended for future consumption by a
Terminal-layer build (to know what it can import) and a future GUI —
**not** a replacement for the database `ObjectRegistry` (see below).

**`registry.json` vs. the database `ObjectRegistry` — related, not
redundant.** The database `ObjectRegistry` is the transactional source of
truth `ensure_registered` depends on for real correctness guarantees:
schema-drift detection and cross-object atomicity only work because they
happen inside one Postgres transaction, against the live database.
`registry.json` is a **derived, read-only export** of that state at build
time, for consumers who need to know a repo's shape without needing
database access or transactional guarantees. `ensure_registered` must
never read from `registry.json`, and `registry.json` must never be
treated as a source of truth for build-time correctness — it is a
snapshot for external consumption only.

---

## 5. Testing

Tests run against **real Postgres**, never SQLite — the design leans on
Postgres-specific features (`MATERIALIZED VIEW`, `ARRAY`, `&&`, `unnest`)
that have no SQLite equivalent.

- `tests/docker-compose.test.yml` defines an isolated, `tmpfs`-backed
  Postgres container, separate from the real dev database.
- `tests/conftest.py`'s session-scoped `engine` fixture starts the
  container, waits for a **real query to succeed** (not just
  `pg_isready`), creates the registry table, tears down at session end.
- The function-scoped `db_session` fixture wraps each test in a rolled-
  back transaction; teardown also removes dynamically-created tables from
  `Base.metadata` and clears `_registered_classes` via a shared
  `reset_registered_classes()` helper (also callable mid-test, for tests
  that deliberately simulate "a fresh process" partway through).

### 5.1 `e2e-harness` — a second, complementary layer of testing (renamed and restructured this session)

Originally a single `example-harness/` directory; restructured into
`e2e-harness/{forge-e2e-testing, forge-api-e2e-testing}`, both sharing one
`harness_config.py`/`init_workspace.py`/`.venv` at the `e2e-harness/`
root. Sharing one environment for both subfolders is deliberate — it
guarantees both testing tiers are always exercising the exact same
version of Forge (live or a specific branch/tag), removing any doubt
about which code path is under test.

- **`forge-e2e-testing/`** — direct Python function calls against
  `forge.manifest.manifest_build.*` (spinup, build, a CRUD test script).
  This is the original `example-harness` content, moved unchanged; its
  scripts' use of `Path(__file__).resolve().parent`-based anchoring meant
  the move required zero code changes.
- **`forge-api-e2e-testing/`** — the same scenarios, but via FastAPI's
  `TestClient(app)`, calling the real HTTP routes in-process (no server
  process, no port, no `uvicorn`) — genuinely exercises the API layer
  itself (request parsing, status codes, response models), not just the
  functions underneath it. Uses `open_editor: False` for `/manifest/open`
  calls specifically — the real, intended use case for that parameter:
  automated tests need to verify the environment-setup logic without a
  VS Code window popping up during a test run.

**Live mode** — Forge installed via editable install (`uv pip install -e
<forge_root> --config-settings editable_mode=compat --no-deps`), then its
declared runtime dependencies separately compiled/installed from root
`pyproject.toml`. Edits to Forge's source take effect immediately with no
reinstall (for genuine editable installs — see the Pylance gotcha below
for why `--config-settings editable_mode=compat` specifically matters).

**Branch mode** — Forge installed via a real, resolved git reference
(`forge @ git+https://...@<branch-or-tag>`), exactly matching what an
external consumer would experience. This is what actually exercises the
packaging/distribution mechanism, catching bugs live mode structurally
cannot (e.g. a stale tag reference baked into a template, or a namespace-
flattening packaging bug — both found and fixed this session, see §3.1
and the note above).

**Real gotcha found and fixed: editable installs are invisible to
Pylance by default.** Modern `pip`/`uv` editable installs use a PEP 660
import-hook mechanism, invisible to Pylance's purely static analysis —
correctly-installed, fully working code appears as an unresolvable import
in the editor. Fixed with `--config-settings editable_mode=compat`
(the older `.pth`-file-based mechanism, a plain path addition Pylance can
see). Baked into `init_workspace.py`'s live-mode install and
`scripts/mount_repo.sh`.

**A second, related gotcha:** VS Code's `python.defaultInterpreterPath`
does not reliably resolve the `${workspaceFolder}` variable in all
versions. Fixed by having the setup script itself write
`.vscode/settings.json` with the venv's fully resolved **absolute** path,
generated fresh on every run — keeping the _scripts_ portable while the
_generated_ settings file is correctly machine-specific and gitignored.

**A recurring, unrelated gotcha worth remembering:** conda's `base`
environment auto-activating in a fresh terminal (if configured to do so)
silently shadows an intended venv, and stale shell command-hash caches
(`hash -r` fixes) can point `pytest`/`python` at the wrong interpreter
even when `$PATH` itself is correct. Both were hit multiple times this
session and are pure local-environment quirks, not Forge bugs — worth
checking `which python`/`which pytest` and the actual resolved path
whenever behavior seems inexplicably wrong.

`scripts/mount_repo.sh` (Forge repo root) is the general-purpose,
bash-CLI version of the same "set up venv, install deps, configure
Pylance, open VS Code" pattern that `open_manifest_repo` now also
provides as a real, reusable Python function — worth eventually having
`mount_repo.sh` call into that function rather than duplicating the logic
in bash, though this consolidation hasn't been done yet (see §7).

---

## 6. Known trade-offs and deliberate gaps

- **Traversal bypasses native SQLAlchemy `relationship()`** — hand-rolled
  `Select`/subquery composition, judged a better fit than
  `relationship()` + `primaryjoin=` boilerplate for the array-based FK
  shapes involved.
- **Materialized-view refresh is out of scope for `manifest_core`** —
  deliberately kept outside so the build/runtime split stays clean.
- **No FK-constraint enforcement at the database level** — referential
  integrity for links is enforced only by `discover_declarations`'s
  application-level validation.
- **Rid/table-name generation is a placeholder for a future `Dataset`
  abstraction** — `edits_table`/`materialized_table` are currently literal
  physical table names; a planned `Dataset` concept will add an
  indirection layer here.
- **Many-to-many via two array columns is unsupported by design.**
- **Forge's git operations never create remote repositories** — only
  clone/operate on already-existing ones the user provides a URL for.
  Creating new remotes via GitHub's API (requiring token-based auth) is
  explicitly out of scope for now — see §7.
- **`git_open_manifest_repo`'s reuse-if-present local checkout can go
  stale** relative to the remote if someone else (or the user, from a
  different machine) pushes to the same branch — no automatic pull/
  refresh happens. Accepted trade-off for a single-developer local
  workflow; would need reconsideration for any multi-developer use case.

---

## 7. What's not yet built

Codegen, builder orchestration, repo spinup/open, the full git-backed
operations layer (clone/commit/push/tag, composed into git-aware
spinup/open/build), the `registry.json` export, and the Forge API service
(with both live and branch-mode `e2e-harness` validation) are now
complete — see §4 and §5 above. What remains, in the currently intended
order:

- **`ManifestFieldDef`/`ManifestField` display names** — a field's dict
  key in `fields={...}` should remain the code-facing/column name
  (unchanged), but `ManifestFieldDef` should gain a `display_name`
  parameter (mirroring `ManifestObjectDef`'s existing `api_name`/
  `display_name` split) for future UI use. Requires updates to
  `ManifestFieldDef`, `ManifestField`, codegen's field-emission logic, and
  `registry.json`'s field entries. Planned to land in the same branch as
  the codegen redundancy cleanup below, since both touch the same code.
- **The `_properties`/`_nullable_map`/`_pk_field` redundancy with
  `ManifestField` declarations** — codegen currently emits this
  information twice. A cleaner design would derive them from the
  `ManifestField` descriptors themselves at class-creation time (e.g. via
  `__init_subclass__`), collapsing the redundancy. Now that the full
  spinup → declare → build → import → git → API pipeline is proven
  end-to-end, this refactor has a working test suite as a safety net.
- **A manual materialize/refresh helper** — a function taking a list of
  object identifiers and running `REFRESH MATERIALIZED VIEW CONCURRENTLY`
  for each. Planned to accept a list and process each object as one
  independent, self-contained call from the start (even though the first
  implementation will simply loop sequentially), so that swapping in
  parallel execution later (e.g. `asyncio.gather`, a thread pool) is a
  change to the loop construct only, not a restructuring of the function
  itself. An API endpoint wrapping it is planned alongside.
- **Schema migration** — `ensure_registered` currently treats _any_ schema
  diff (once a registry row already exists) as fatal via
  `SchemaConflictError`. A safe/unsafe diff classifier (nullable
  relaxation and additive nullable fields auto-apply; type changes, field
  removal, and nullable tightening are breaking and require explicit
  migration) is designed in principle but not implemented.
- **Consolidating `scripts/mount_repo.sh` to call `open_manifest_repo`
  directly** rather than duplicating its logic in bash, now that the
  latter exists as a real, tested Python function.
- **Creating new remote repositories via GitHub's API** — current git
  scope only clones/operates on already-existing remotes; a user must
  create the empty repo themselves before Forge can spin it up.
- **Terminal and Aperture layers** — not started. The same
  spinup/open/build/git-backed-publish/API-route pattern is intended to
  generalize directly to both.
