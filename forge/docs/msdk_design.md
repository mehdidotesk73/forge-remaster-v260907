# MSDK System Design

This document describes the design of the Manifest SDK (MSDK) — the object
layer that lets a coder declare data objects once and get, in return, a
generated Python SDK with correct, edits-aware CRUD, filtering, and
relationship traversal. It's meant as a reference for anyone (including
future-you) working on `msdk_core` or `msdk_build`.

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
  cadence (not owned by msdk_core — see §7).
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
- Bulk queries (`where()`) reference `_materialized_cls` only, with an
  additional edits-side query merged in for `where()` specifically (see
  §4) — link traversal has no such merge and is materialized-only, full
  stop.

**Consequence for the coder:** `Product("some_pk")` never fails at
construction — it's a plain, thin wrapper holding only a `pk`. The first
_field access_ is what does existence-checking, and raises `ValueError` if
the pk doesn't exist anywhere. This is a documented, intentional
trade-off, not an oversight.

---

## 3. Package layout

```
forge/msdk/
  __init__.py            # declaration-facing surface: ManifestObjectDef,
                          # ManifestLinkDef, ManifestFieldDef, type vocabulary
  msdk_core/
    __init__.py           # internal/runtime-facing surface (fuller than msdk/)
    types.py               # ManifestType and subclasses, STRING/INT/.../LIST
    defs.py                 # ManifestFieldDef/ManifestObjectDef/ManifestLinkDef,
                             # DeclarationCollector/bind_collector
    base.py                  # ManifestField, ManifestObject, ManifestObjectSet,
                              # ManifestLink, current_session/_require_session
    registry.py               # ObjectRegistry, ensure_registry_table,
                               # ensure_registered, _make_mapped_class
  msdk_build/
    discovery.py            # discover_declarations — scans a folder, collects
                             # + validates ManifestObjectDef/ManifestLinkDef
    links.py                 # infer_join_kind / resolve_link_join_kinds
    codegen.py                # (not yet built) emits generated .py source
    builder.py                 # (not yet built) build_msdk orchestrator
```

**Import layering (three tiers, deliberate):**

| Layer                              | Who imports it                                                   | What it exposes                                                                                                                                        |
| ---------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `forge.msdk`                       | Declaration files (`product.py`)                                 | `ManifestObjectDef`, `ManifestLinkDef`, `ManifestFieldDef`, type vocabulary (`STRING`, `LIST`, ...) only                                               |
| `forge.msdk.msdk_core`             | `msdk_build` modules, tests, generated code                      | Everything in `forge.msdk` **plus** `ManifestObject`, `ManifestObjectSet`, `ManifestLink`, `ManifestType`, `ensure_registered`, `ObjectRegistry`, etc. |
| `forge.msdk.msdk_core.<submodule>` | Anything needing a specific internal (e.g. `_make_mapped_class`) | Direct submodule import — not re-exported at package level unless genuinely public                                                                     |

A declaration file only ever needs the top tier. Nothing in `forge.msdk`
should ever require the coder to know `msdk_core` exists.

---

## 4. Component reference

### 4.1 `types.py` — the field type system

`ManifestType` is a base class with `.sql_type` (a constructed SQLAlchemy
type instance, e.g. `String()`), `.is_array()`, `.element_type()`.
Leaf types (`StringType`, `IntType`, `FloatType`, `BoolType`,
`DateTimeType`) are exposed as pre-built singletons (`STRING`, `INT`, ...).
`ArrayType` wraps any other `ManifestType` and is spelled `LIST[STRING]`
via `__class_getitem__` (mirroring `typing.List[X]`).

`ArrayType.sql_type` is a `@property` (not a class attribute like the
leaves) because `ARRAY(...)` needs its element type at construction time —
there's no single generic "the array type" the way there's a single "the
string type." Every `ManifestType` subclass still exposes `.sql_type` as
the uniform accessor; `_make_mapped_class` never branches on array-ness.

Adding a new leaf type (e.g. a future `GeometryType`) requires no changes
elsewhere — `ArrayType`, `_make_mapped_class`, and `infer_join_kind` are
all written generically against the `ManifestType` interface, not against
a fixed set of concrete types.

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

**`ManifestObject`** — thin, pk-only wrapper (`__init__(self, pk)`). Field
access is always live (edits-then-materialized). `create()` permanently
retires a pk once deleted — a deleted pk can never be recreated with the
same identity (a deliberate simplification; an explicit `undelete()` would
be a new, separate operation if ever needed). `__eq__`/`__hash__` are
pk-based, not identity-based, since `_wrap()` constructs a fresh Python
object on every query result — two `Product` instances with the same pk
are semantically the same object and should compare equal.

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
the error message in `msdk_build/links.py`).

### 4.4 `registry.py` — schema bookkeeping and DDL

**`ObjectRegistry`** is the durable, cross-restart source of truth: one row
per `api_name`, recording resolved table names and a serialized schema
fingerprint. **`_registered_classes`** is a separate, same-process-only
cache preventing SQLAlchemy from double-mapping a table name within one
run — it must be cleared at the start of every build (and between tests;
see §5) since it does not track database state at all, only "have I built
this Python class in this process."

**`ensure_registered`** enforces four conditions on every call:

1. A row exists in the DB registry.
2. A class exists in the in-memory cache.
3. The registry row's referenced tables actually exist in the database.
4. The registry row's tables match the tables passed to this call.

On a genuine clean slate (no row, no cached class), it creates the edits
table, the materialized view, a unique index, and the registry row — all
via the **same `Session`**, never its own transaction. This is deliberate:
`ensure_registered` performs no commit and no flush; the caller (the
future `build_msdk`) commits once, after looping over every declared
object, giving **cross-object atomicity** — either every object in a build
registers together, or (on any single failure) the whole build rolls back,
including DDL, which is transactional in Postgres.

Any partial mismatch among the four conditions above is a **hard error**,
not an auto-repair — the documented recovery path is manual: inspect the
database, likely delete the offending `ObjectRegistry` row, and rebuild.

`ensure_registry_table` bootstraps the registry table itself and must be
called once, before any `ensure_registered` calls, against a fresh
database.

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

---

## 6. Known trade-offs and deliberate gaps

- **Traversal bypasses native SQLAlchemy `relationship()`.** Given the
  array-based FK shapes and the edits/materialized split, hand-rolled
  `Select`/subquery composition was judged a better fit than
  `relationship()` + `primaryjoin=` boilerplate for every shape — see the
  conversation history for the fuller comparison. This means bugs in
  traversal are bugs in `ManifestObjectSet`/`ManifestLink`, not something
  SQLAlchemy's own loader-strategy machinery can be blamed for or fixed
  independently.
- **Materialized-view refresh is out of scope for `msdk_core`.** Nothing
  in this module calls `REFRESH MATERIALIZED VIEW CONCURRENTLY`. That
  responsibility belongs to whatever owns "keep the database
  fresh" (a scheduled job, a Terminal-layer hook) — deliberately kept
  outside `msdk_core` so the build/runtime split stays clean (Forge is
  build-time only; nothing it produces should depend on Forge still
  running).
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
  that point. See the `NOTE` comment at the table-name-resolution call site
  when it's implemented.
- **Many-to-many via two array columns is unsupported by design**, not by
  omission — `infer_join_kind` raises with an explicit suggestion (a
  join-table entity with two scalar-or-list links) rather than attempting
  to guess intent.

---

## 7. What's not yet built

- **Codegen** (`msdk_build/codegen.py`) — turns a validated
  `ManifestObjectDef` + resolved `ManifestLink`s into the actual generated
  `.py` source (`class Product(ManifestObject): ...`, `ProductSet`,
  `_links = {...}`).
- **Builder orchestration** (`msdk_build/builder.py`) — ties together
  `discover_declarations` → `ensure_registry_table` → per-object
  `ensure_registered` (one shared session, one commit/rollback) → `resolve_link_join_kinds`
  → codegen → file output.
- **Schema migration** — `ensure_registered` currently treats _any_ schema
  diff as fatal. A planned safe/unsafe diff classifier (nullable
  relaxation and additive nullable fields auto-apply; type changes, field
  removal, and nullable tightening require explicit migration) is designed
  but not implemented.
- **Table-name/rid resolution helper** (`_resolve_table_names`) — reuses
  existing names from the registry on a re-build, generates fresh ones only
  for a genuinely new `api_name`. Designed, not yet written into
  `builder.py`.
