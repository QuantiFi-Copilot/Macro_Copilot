"""rates_agent.policy_futures — SFR / fed-funds / ESTR / SONIA / OIS policy-rate futures + MCP server.

Uses the 'prefixed' resolver-key convention because its primitives
share names with bond_futures (e.g. get_futures_price_level_tool);
the prefix is what disambiguates the two domains at L2 dispatch.
"""

__domain_id__ = "policy_futures"
__domain_label__ = "policy-rate futures"
__mcp_server_module__ = "rates_agent.policy_futures.mcp_server"
__mcp_client_key__ = "policy_futures"
__resolver_key_convention__ = "prefixed"
