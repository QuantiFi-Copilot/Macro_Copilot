# =============================================================================
# Macro_Copilot — Makefile
# -----------------------------------------------------------------------------
# Hand-curated targets that wrap common CI invocations.  Kept minimal
# on purpose: the project's primary toolchains are pytest (backend)
# and npm scripts (frontend); this Makefile only adds targets that
# don't fit cleanly into either, OR are cross-cutting (touching both
# sides).
#
# Stage 2 (frontend module architecture, per docs_revamped/06_roadmap/
# frontend_migration.md) introduced the first such target:
# ``check-module-parity`` is a Python script that READS the backend's
# _PRIMITIVE_SPECS + WORKFLOW_INCOMPATIBLE_TOOLS dicts and compares
# them against the frontend's src/modules/ folder set.  Living
# outside both ecosystems, it's invoked here.
#
# Backwards compatibility
# -----------------------
# This file is NEW (no prior Makefile existed).  Adding it does not
# change any existing build / test / dev workflow — those continue to
# work via their native tools (``pytest``, ``npm run …``).
# =============================================================================

.PHONY: help check-module-parity check-module-parity-strict

# -----------------------------------------------------------------------------
# Default: print the available targets.
# -----------------------------------------------------------------------------

help:
	@echo "Macro_Copilot — Makefile targets"
	@echo ""
	@echo "  check-module-parity         Assert backend ↔ frontend module"
	@echo "                              parity (Stage 2).  Tolerates the"
	@echo "                              Stage-4 refactor whitelist."
	@echo ""
	@echo "  check-module-parity-strict  Same, but fail if any whitelisted"
	@echo "                              tool hasn't migrated yet.  Used"
	@echo "                              from Stage N+ once the migration"
	@echo "                              is complete."
	@echo ""
	@echo "Frontend (npm) — run from UI/macro-copilot-dashboard-polished/:"
	@echo "  npm run typecheck           TypeScript strict typecheck"
	@echo "  npm run test:build          Build-folder test suite"
	@echo "  npm run test:modules        src/modules/ + src/lib/__tests__/"
	@echo "  npm run test:registries     (alias for test:modules today)"
	@echo "  npm run test                Both test:build and test:modules"
	@echo "  npm run build               Production build"
	@echo ""
	@echo "Backend (pytest) — run from repo root:"
	@echo "  pytest tests/               Full backend test suite"

# -----------------------------------------------------------------------------
# Module-parity check (Stage 2).
# -----------------------------------------------------------------------------
#
# Calls ``tools/check_module_parity.py`` which imports backend Python
# modules (read-only) and walks the frontend's src/modules/ folder
# set.  Exits 0 on parity holds, 1 on drift, 2 on environment problem.

check-module-parity:
	@python3 tools/check_module_parity.py

check-module-parity-strict:
	@python3 tools/check_module_parity.py --strict
