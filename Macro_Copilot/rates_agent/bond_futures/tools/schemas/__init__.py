"""Re-exports for bond_futures tool schemas.

Populated as the OpenClaw primitive-automation factory builds each
catalogued primitive — each tool's schemas re-export here so the MCP
server (``rates_agent/bond_futures/mcp_server.py``) can import a stable
hub. Mirrors ``rates_agent/ois/tools/schemas/__init__.py``'s pattern.
"""
