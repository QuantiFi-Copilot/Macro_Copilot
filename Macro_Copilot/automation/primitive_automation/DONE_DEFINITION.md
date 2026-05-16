# Done Definition

A primitive is done only when ALL of the following are true.

## 1. It is genuinely a new primitive concept

It is not:

- universe expansion
- curve-family support work
- a new instrument instance
- documentation masquerading as a tool

## 2. It passes the repo's standardness definition honestly

That includes:

- correct category
- explicit methodology
- visible dependency chain
- no hidden load-bearing choices
- no proxy standing in for missing metadata

## 3. The four-file tool package exists and is coherent

The primitive has:

- `__init__.py`
- `config.yaml`
- `schemas.py`
- `compute.py`

and each file follows the canonical role defined in the repo docs.

## 4. YAML owns conventions

Relevant methodology defaults are in `config.yaml`, not hidden in code,
and use documented source tags and rationale.

## 5. Code owns invariants

Validation and mathematical truth remain in code where they belong.

## 6. Production callers are wired correctly

Where applicable:

- MCP wrapper updated
- config loaded explicitly
- `config=` passed explicitly
- sentinel behavior preserved
- controlled error shape preserved

## 7. Repo integration is complete

Where applicable:

- schema re-export updated
- workflow `PrimitiveSpec` registration added
- `output_field_units` declared honestly
- any other required touchpoints updated

## 8. Tests exist and are meaningful

At minimum:

- compute test
- wiring test
- SQL validation runner

and the SQL validator is excluded from pytest collection in the current
repo style when necessary.

## 9. Offline tests and DB-backed validation both pass

The primitive has:

- passing offline deterministic tests
- passing DB-backed SQL validation in the repo container/dev
  environment
- passing any applicable parity/fixture checks

Offline tests alone are not enough.

## 10. Config lint passes

`python -m shared.config.lint` must pass after the primitive lands.

## 11. Reviewer approves

Codex must clearly conclude:

- `APPROVED`

or equivalent explicit approval.

Anything less is not done.

## 12. Branch rule stayed intact

All work happened on:

`primitive_automation`

without branch switching.

## 13. The DB was not mutated

The automation used the DB only for read-only validation and did not
change DB data or schema state.

## 14. No unresolved honesty debt remains hidden

If the primitive has a real blocker, it must be deferred, not merged in
under a misleading name.
