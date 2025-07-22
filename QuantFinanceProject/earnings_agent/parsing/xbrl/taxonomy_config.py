# earnings_agent/parsing/xbrl/taxonomy_config.py
from pathlib import Path

# Define the root path to the taxonomies directory within the project
TAXONOMY_ROOT = Path(__file__).parent / "taxonomies"

# The Definitive Registry
# This dictionary maps a requested XSD filename to its correct local path.
# It uses a nested dictionary for context when a filename is not unique.
TAXONOMY_REGISTRY = {

    # =========================================================================
    # == AMBIGUOUS FILENAMES (Require context to resolve)
    # =========================================================================

    # This filename is used by both the standard Ind AS and the NBFC taxonomies.
    # We use the company's industry to pick the right one.
    "in-bse-fin-2020-03-31.xsd": {
        "Non Banking Financial Company (NBFC)": TAXONOMY_ROOT / "NBFC"/ "NBFCTaxonomy" / "NBFC_Taxonomy_2020-03-31",
        # For any other industry requesting this file, we fall back to the standard Ind AS version.
        "_default_": TAXONOMY_ROOT / "IND_AS" / "Financial Results_Ind AS Taxonomy" / "Ind AS Taxonomy 2020-03-31"
    },

    # This filename is used by both General and Life Insurance taxonomies.
    "in-capmkt-ent-2020-03-31.xsd": {
        "General Insurance": TAXONOMY_ROOT / "General_Insurance" / "General_Insurance_Taxonomy" / "General Insurance 2020-03-31" / "General_Insurance",
        "Life Insurance": TAXONOMY_ROOT / "Life_Insurance" / "Life_Insurance_Taxonomy" / "Life Insurance Taxonomy 2020-03-31" / "Insurance"
    },

    # =========================================================================
    # == UNIQUE FILENAMES (Only need a default path)
    # =========================================================================

    # The primary entry point for most standard companies.
    "Ind-AS_entry_point_2020-03-31.xsd": {
        "_default_": TAXONOMY_ROOT / "IND_AS" / "Financial Results_Ind AS Taxonomy" / "Ind AS Taxonomy 2020-03-31"
    },

    # The unique entry point for the Banking taxonomy.
    "banking_entry_point_2019-09-30.xsd": {
        "_default_": TAXONOMY_ROOT / "Banking" / "Financial Results_Banking Taxonomy" / "Banking Taxonomy-2019-09-30"
    },

    # The unique entry point for REITs and InvITs.
    "in-capmkt-ent-2021-03-31.xsd": {
        "_default_": TAXONOMY_ROOT / "REITs_InvITs" / "Financial Results"
    },

    # The unique entry point for the "Other than Banks" taxonomy.
    "other_than_banks_entry_point_2019-09-30.xsd": {
        "_default_": TAXONOMY_ROOT / "Other than Banks" / "Financial Results_Other Than Banks" / "Main Taxonomy-2019-09-30"
    }
}