# Error Handling

> No silent fallback at any layer. The exception hierarchy is **deliberately heterogeneous** — different layers raise different base classes because they signal different things to the caller. The rule is *no swallowed failures, no fake success*, not *one base class everywhere*.

**Version:** v1
**Last reviewed:** 2026-05-18

## 1. Universal Rule

Every failure surfaces as a typed exception (or, at one documented boundary, as a structured error envelope). Five layers; each has a base class that matches what the caller is expected to do:

| Layer | Base | Examples | Caller's expected reaction |
|---|---|---|---|
| **Schema / loader / config** | `Exception` | `WorkflowTemplateError`, `TemplateRegistryError`, `OperatorConfigError`, `ToolConfigError` | Programmer fixes the YAML / registration / config; the substrate cannot proceed. |
| **Validation (input shape, slot binding, type compat)** | `ValueError` | `SlotBindingError`, `WorkflowValidationError`, Pydantic `ValidationError` | Caller fixes their input; the substrate refuses to run with bad input. |
| **Component-internal compute** | `<Component>Error(ValueError)` | `CurveSpreadError`, `AlignSeriesError` (operator), `<Primitive>Error` (primitive, optional) | Caller fixes the request or escalates; component refuses to produce a wrong answer. |
| **Execution / runtime** | `RuntimeError` | `WorkflowExecutionError` | Caller decides: retry with same input (transient), surface to user (persistent), or escalate. |
| **User-facing API / MCP** | structured response envelope | `{"error": "...", "code": "...", ...}` over HTTP / MCP | Client renders the error; the wire protocol stays well-typed. |

Per-layer rules:

- **Schema / loader errors subclass `Exception`** because they are programmer-fix errors that should not be caught by an `except ValueError` block (they are not input-shape errors).
- **Validation errors subclass `ValueError`** because callers idiomatically catch `ValueError` for "I passed something invalid".
- **Component-internal errors subclass `ValueError`** *most of the time* — they are validation-shaped (the component refuses to produce a wrong answer). The one documented exception: primitives at the MCP boundary may return `{"error": "..."}` envelopes ([PR11](../02_components/primitive/README.md)) because the MCP protocol delivers them as response bodies, not as transport-level failures.
- **Execution errors subclass `RuntimeError`** because they are not the caller's input fault — they are runtime conditions outside the caller's control (a DB timeout, an upstream API failure, an operator's compute raising mid-DAG). Catching with `except ValueError` would not match.
- **User-facing API responses use envelopes** because HTTP / MCP clients expect structured response bodies; raising past the API boundary leaks Python exception names into the wire protocol.

## 2. Why This Exists

- **[P6](../00_thesis/01_non_negotiables.md) (no silent failure).** Every failure mode the platform can fall into must surface loudly. A silently-swallowed error is a worse failure than a noisy crash — the caller thinks the analysis succeeded.
- **Heterogeneous base classes carry semantics.** When a caller writes `except ValueError`, they mean "I'll handle bad input". When they write `except RuntimeError`, they mean "I'll handle runtime failure". Forcing every error onto one base destroys that signal.
- **Construction-time loudness.** Loader / config / validation errors raise *before* any compute. Catching bugs at the latest deterministic point keeps debugging cheap.

## 3. Applies To

Every layer that can fail. Loaders, validators, primitives, operators, the workflow executor, persistence, the API surface.

## 4. Component Manifestations

| Component | Error class | Base | Notes |
|---|---|---|---|
| Loader | `WorkflowTemplateError`, `OperatorConfigError`, `ToolConfigError` | `Exception` | Raised at parse/load |
| Registry | `TemplateRegistryError` | `Exception` | Raised on conflicting re-registration |
| Slot binding | `SlotBindingError` | `ValueError` | Raised at `template.bind()` |
| Substrate validation | `WorkflowValidationError` | `ValueError` | Raised at `validate_workflow()` |
| Operator | `<Operator>Error` | `ValueError` (per [OPR13](../02_components/operator/README.md)) | Operators never return error envelopes — always raise |
| Primitive | `<Primitive>Error` *or* `{"error": "..."}` envelope | `ValueError` *or* dict (per [PR11](../02_components/primitive/README.md)) | Envelope is the MCP-boundary exception; not allowed past the bridge |
| Workflow executor | `WorkflowExecutionError` | `RuntimeError` | Wraps the failing node's exception with workflow + node context |
| Artifact validator | `ValueError` raised from `@model_validator(mode="after")` | `ValueError` | Per [ART11](../02_components/artifact/README.md) |
| API surface | structured envelope | dict | Per FastAPI / MCP conventions |

## 5. Anti-Patterns

- **Bare `except:` or `except Exception:` swallowing everything.** Catches `KeyboardInterrupt`, masks programmer errors, hides the root cause. Catch the specific exception you can handle; let the rest propagate.
- **`except ValueError: return None`** as a "convenient" fallback. The caller now has `None` and no idea what failed. Either handle the specific failure mode usefully or let the exception propagate.
- **Operator returning `{"error": "..."}`** instead of raising. [OPR13](../02_components/operator/README.md) explicitly forbids this — only primitives at the MCP boundary may envelope, and only because the protocol requires it.
- **Re-raising without context.** `raise MyError("failed")` strips the upstream cause. Use `raise MyError("operator align_series: ...") from e` to preserve the chain.
- **Generic error messages.** `raise ValueError("invalid input")` is useless. Name the specific invariant: `raise ValueError("Series payload index must be sorted ascending; got first 5: [...]")`.
- **`if x is None: return default`** for a value the caller must supply. That's a silent fallback. Raise `ValueError("x is required when ...")`.
- **`Optional[T]` everywhere** to "soften" the API. Optional means "the caller may legitimately pass None"; if `None` is a programmer error, the field should be required and the constructor should raise.
- **Catching exceptions to log + re-raise** without adding context. The log noise without info-add is friction; either add context or let the exception speak for itself.
- **A `try/except` inside a `@model_validator`** that converts the failure into a silent return. The validator's job is to raise on malformed input; suppressing the raise defeats its purpose.

## 6. Reviewer Checks

- [ ] New error classes subclass the layer-appropriate base (Exception / ValueError / RuntimeError).
- [ ] Error messages name the specific invariant.
- [ ] No bare `except:` and no `except Exception:` outside the top-level API surface.
- [ ] Operators raise typed errors; they do not return `{"error": ...}` envelopes (per OPR13).
- [ ] Primitives raise typed errors *or* return well-shaped envelopes only at the MCP boundary (per PR11).
- [ ] The API surface converts substrate exceptions into structured envelopes — never let a Python exception name leak into the HTTP response.
- [ ] Re-raises preserve cause via `raise ... from e`.

## 7. Links

- [P6 (no silent failure)](../00_thesis/01_non_negotiables.md)
- [`typed_boundary_discipline.md`](typed_boundary_discipline.md) — validators are the construction-time gate
- Component manifestations: [PR11](../02_components/primitive/README.md), [OPR13](../02_components/operator/README.md), [ART11](../02_components/artifact/README.md), [WT12](../02_components/workflow_template/README.md)
