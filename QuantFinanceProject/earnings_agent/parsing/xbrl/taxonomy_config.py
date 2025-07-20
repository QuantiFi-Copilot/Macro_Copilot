# earnings_agent/parsing/xbrl/taxonomy_config.py
from pathlib import Path

# Define the root path to the taxonomies directory
TAXONOMY_ROOT = Path(__file__).parent / "taxonomies"

# The Registry: Maps the entry point XSD file to its containing directory and URL prefix
TAXONOMY_REGISTRY = {

    # --- Ind AS Taxonomy ---
    "Ind-AS_Financial_Results_2020-03-31.xsd": {
        "path": TAXONOMY_ROOT / "IND AS" / "Financial Results_Ind AS Taxonomy" / "Ind AS Taxonomy 2020-03-31" / "in-bse-fin-2020-03-31.xsd",
        "url_prefix": "http://www.bseindia.com/xbrl/fin/2020-03-31/"
    },

    # --- Banking Taxonomy ---
    "banking_entry_point_2019-09-30.xsd": {
        "path": TAXONOMY_ROOT / "Banking" / "Financial Results_Banking Taxonomy" / "Banking Taxonomy-2019-09-30",
        "url_prefix": "http://www.bseindia.com/xbrl/fin/2019-09-30/"
    },

    # --- NBFC Taxonomy ---
    "in-bse-fin-2020-03-31.xsd": {
        "path": TAXONOMY_ROOT / "NBFC" / "NBFCTaxonomy" / "NBFC Taxonomy 2020-03-31" / "in-bse-fin-2020-03-31.xsd",
        "url_prefix": "http://www.bseindia.com/xbrl/fin/2020-03-31/"
    },

    # --- General Insurance Taxonomy ---
    "in-capmkt-ent-2020-03-31.xsd": {
        "path": TAXONOMY_ROOT / "General Insurance" / "General_Insurance_Taxonomy" / "General Insurance 2020-03-31" / "General_Insurance" / "in-capmkt-ent-2020-03-31.xsd",
        "url_prefix": "http://www.bseindia.com/xbrl/fin/2020-03-31/"
    },

}

