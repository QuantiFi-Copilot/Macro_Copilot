# Primitive Build Rules

This file tells the builder exactly how to build a primitive in this
repo.

Follow these steps in order.

## 1. Preflight

Before writing any code:

1. Run `automation/primitive_automation/check_branch.sh`.
2. Read the primitive catalog entry.
3. Read:
   - `DESIGN_PRINCIPLES.md`
   - `STANDARD_TOOL_AND_YAML_RULES.md`
   - `TESTING_AND_DB_VALIDATION_POLICY.md`
   - `NO_GO_RULES.md`
   - `DONE_DEFINITION.md`
   - `REPO_REFERENCE_MAP.md`
4. Inspect the live reference implementations named in the catalog.
5. Inspect the live tests and docs referenced in
   `REPO_REFERENCE_MAP.md`.

Do not begin coding from memory.

## 2. First question: is this actually a new primitive?

Answer this before building anything.

If the requested thing is merely:

- a new universe member
- a new curve family
- a new instrument example
- a new region/country on an existing finance concept

then it is probably NOT a new primitive.

If it is not a genuinely new primitive concept:

- stop
- mark for defer/reclassify
- do not build

## 3. Second question: is all required metadata available and trustworthy?

If the real primitive requires metadata the repo does not yet have or
cannot yet trust, stop and defer it.

Consult:

- the primitive's own requirements
- `docs/technical_debt.md`
- the current playbooks / schema / existing fetch patterns

The builder must never invent a proxy to get around missing metadata.

## 4. Choose the correct category honestly

Pick one:

- `desk_invariant_primitive`
- `quant_standard_analytic`

Do not choose based on convenience.
Choose based on the concept's true interpretation burden.

## 5. Follow the exact four-file pattern

Create or update:

```text
rates_agent/<domain>/tools/<tool_name>/
  __init__.py
  config.yaml
  schemas.py
  compute.py
```

No alternate shape.

## 6. Offload conventions into YAML

All system-controlled methodology defaults belong in `config.yaml`
where appropriate:

- windows
- ddof
- fill limits
- rounding
- default field names
- thresholds

Do not hide them in `compute.py`.

But do NOT move mathematical invariants to YAML.

## 7. Keep only legitimate inputs in the input schema

Inputs are only the fields the LLM/user legitimately controls per
query.

Do not expose:

- ancillary methodology
- internal helper toggles
- structural formula choices
- YAML-owned conventions

If in doubt, keep it out of the input schema.

## 8. Preserve the caller wiring pattern

Production call sites must:

- load the bundled config explicitly
- pass `config=` explicitly
- preserve sentinel behavior on optional `field_name` style inputs
- fail in the same controlled error shape the repo uses

## 9. Update all required integration touchpoints

At minimum, consider whether the primitive requires edits to:

- `rates_agent/<domain>/mcp_server.py`
- `rates_agent/<domain>/tools/schemas/__init__.py`
- `rates_agent/workflows/__init__.py`
- `tests/conftest.py`
- relevant docs if the tool inventory is maintained there

Do not stop at the four core files if the rest of the repo would remain
incoherent.

## 10. Mirror the test shape used by the repo

Add:

- `tests/test_<tool>_compute.py`
- `tests/test_<tool>_wiring.py`
- `tests/test_<tool>_sql_validation.py`

and update `tests/conftest.py` if the SQL validator follows the current
non-pytest collection pattern.

The test suite must be modeled after the existing migrated tools, not
invented from scratch.

## 11. Run the required checks extremely thoroughly

You must run BOTH layers:

### Layer A — offline deterministic checks

- targeted compute tests
- targeted wiring tests
- `python -m shared.config.lint`

### Layer B — DB-backed grounding checks

- the tool's SQL validation runner
- any applicable parity/fixture checks
- equivalent SQL-based cross-checks of the Python logic against the
  real DB-backed container/dev environment

If the primitive is registered in workflows, ensure any directly
affected workflow/unit surfaces remain coherent.

Do NOT claim completion after Layer A only.

## 12. DB safety rules during testing

DB-backed validation must be read-only.

Forbidden:

- ingestion jobs
- mutating SQL
- schema changes
- any command that changes DB data/state

Allowed:

- read-only `SELECT` validation
- deterministic SQL cross-checks
- reviewed fixture capture when applicable

## 13. SQL-equivalent validation is expected

If the primitive computes something in Python, you must try to verify
that logic independently with SQL against the real DB as far as the
repo's current SQL surfaces allow.

Do not make the SQL validation runner ceremonial.

It should independently validate the core computation, not just smoke
test that the tool returns a value.

## 14. Stop when blocked

If you encounter:

- missing metadata
- a repo technical-debt blocker
- a concept that is not actually standard
- an architecture contradiction
- inability to run the required DB-backed validation honestly

do not improvise.

Stop and report the reason clearly.
