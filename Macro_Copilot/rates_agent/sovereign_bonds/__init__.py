"""rates_agent.sovereign_bonds — cash sovereign bond primitives + MCP server.

PR-10F gap #2: declares the metadata the orchestrator's
``domain_registry`` discovers at import time so this domain is
registered automatically — no edits required in
``orchestrator/contracts.py``, ``orchestrator/config.py``, or
``orchestrator/open_dag/resolver_keys.py``.
"""

__domain_id__ = "sovereign_bonds"
__domain_label__ = "cash sovereign bonds"
__mcp_server_module__ = "rates_agent.sovereign_bonds.mcp_server"
__mcp_client_key__ = "sovereign_bonds"
__resolver_key_convention__ = "bare"
