import yaml
from pathlib import Path
from typing import Dict, List, Any

# Correct path within the Docker container
PLAYBOOK_PATH = Path("/app/earnings_agent/playbooks/sebi/metrics/sebi_banking.yml")

def _get_leaf_ids_recursive(nodes: List[Dict[str, Any]]) -> List[str]:
    """Recursively traverses nodes to find all extractable leaf-node IDs."""
    leaf_ids = []
    for node in nodes:
        children = node.get('children')
        if not children:
            # ONLY include leaves explicitly intended for extraction
            if node.get('extractable', True):
                leaf_ids.append(node['id'])
        else:
            leaf_ids.extend(_get_leaf_ids_recursive(children))
    return leaf_ids



def load_playbook_leaf_nodes() -> Dict[str, List[str]]:
    """
    Loads the SEBI banking playbook and returns a dictionary mapping each
    statement type to a list of its leaf-node playbook_ids.

    Returns:
        {'pnl': ['id1', 'id2', ...], 'balance_sheet': [...], ...}
    """
    playbook_leaf_nodes = {}
    with open(PLAYBOOK_PATH, 'r') as f:
        playbook_docs = yaml.safe_load_all(f)
        for doc in playbook_docs:
            statement_key = doc.get('statement')
            if statement_key:
                # Map statement variations to a common key
                if 'pnl' in statement_key:
                    normalized_key = 'pnl'
                elif 'balance_sheet' in statement_key:
                    normalized_key = 'balance_sheet'
                elif 'cash_flow' in statement_key:
                    normalized_key = 'cash_flow'
                else:
                    normalized_key = statement_key

                leaf_nodes = _get_leaf_ids_recursive(doc.get('nodes', []))
                
                # For cash flow, both direct and indirect methods have many common leaves
                if normalized_key == 'cash_flow':
                    existing_leaves = set(playbook_leaf_nodes.get(normalized_key, []))
                    existing_leaves.update(leaf_nodes)
                    playbook_leaf_nodes[normalized_key] = sorted(list(existing_leaves))
                else:
                    playbook_leaf_nodes[normalized_key] = leaf_nodes

    return playbook_leaf_nodes