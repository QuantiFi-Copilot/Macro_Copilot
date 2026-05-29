"""
shared.config — Per-tool configuration loader.

Public API
----------
- ``ToolConfig``         the validated representation of a tool's ``config.yaml``.
- ``Convention``         one entry inside ``ToolConfig.conventions``.
- ``ConventionExposure`` the optional ``exposure:`` sub-block on a ``Convention`` —
                         per the methodology-exposure standard
                         (``docs_revamped/03_standards/methodology_exposure.md``).
- ``ToolMeta``           the ``tool:`` block (name, domain, description).
- ``MethodologyMeta``    the ``methodology:`` block (what_it_does, assumptions, citations).
- ``load_tool_config``   read + validate a YAML, with process-wide caching.
- ``clear_tool_config_cache``  reset the cache (mostly for tests).
- ``ToolConfigError``    raised by the loader on missing / malformed / invalid YAML.

The MCP server, REST routes, and any future override layer all consume
this module.  The Python tools themselves should not call ``yaml.safe_load``
directly.

The consistency lint at ``shared.config.lint`` is intentionally NOT
re-exported here.  It is invoked via the CLI (``python -m
shared.config.lint``) or imported explicitly by CI scripts and tests
(``from shared.config.lint import check_yaml_consistency``).  Re-
exporting it from this package init causes a ``RuntimeWarning`` when
``python -m`` is used because the module ends up imported twice.
"""

from shared.config.tool_config import (
    Convention,
    ConventionExposure,
    MethodologyMeta,
    ToolConfig,
    ToolConfigError,
    ToolMeta,
    clear_tool_config_cache,
    load_tool_config,
)

__all__ = [
    "Convention",
    "ConventionExposure",
    "MethodologyMeta",
    "ToolConfig",
    "ToolConfigError",
    "ToolMeta",
    "clear_tool_config_cache",
    "load_tool_config",
]
