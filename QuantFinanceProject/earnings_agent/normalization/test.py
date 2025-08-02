#!/usr/bin/env python3
"""
Debug script to investigate why facts are being dropped in unit normalization.
"""
import json
import logging
from sqlalchemy.orm import Session
from earnings_agent.storage.database import get_session
from earnings_agent.storage.models import ParsedDocument

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def debug_parsed_document(doc_id: int):
    """Debug what's actually in a parsed document."""
    session = get_session()
    try:
        pd = session.get(ParsedDocument, doc_id)
        if not pd:
            print(f"❌ Document {doc_id} not found!")
            return
        
        content = pd.content or {}
        print(f"📋 Document {doc_id} - Parser Version: {pd.parser_version}")
        print(f"📋 Parse Status: {pd.parse_status}")
        print(f"📋 Total keys in content: {len(content)}")
        
        # Categorize the content
        fact_keys = []
        string_keys = []
        none_keys = []
        meta_keys = []
        
        for key, val in content.items():
            if key.startswith('source_') or key in ['presentation_currency', 'rounding_level', 'rounding_confidence']:
                meta_keys.append(key)
            elif val is None:
                none_keys.append(key)
            elif isinstance(val, dict) and "value" in val:
                fact_keys.append(key)
            elif isinstance(val, str):
                string_keys.append(key)
            else:
                print(f"🤔 Unusual type for {key}: {type(val)} = {val}")
        
        print(f"\n📊 Content breakdown:")
        print(f"  - Facts (dict with 'value'): {len(fact_keys)}")
        print(f"  - Strings: {len(string_keys)}")
        print(f"  - None values: {len(none_keys)}")
        print(f"  - Metadata: {len(meta_keys)}")
        
        print(f"\n🔍 Fact keys found:")
        for key in fact_keys:
            val = content[key]
            print(f"  ✅ {key}: value='{val.get('value')}', unitRef='{val.get('unitRef')}', repr='{val.get('representation')}'")
        
        print(f"\n📝 String keys (should these be facts?):")
        for key in string_keys[:10]:  # Show first 10
            print(f"  📄 {key}: '{content[key]}'")
        if len(string_keys) > 10:
            print(f"  ... and {len(string_keys) - 10} more")
        
        # Check specifically for re_net_sale
        if 're_net_sale' in content:
            net_sale = content['re_net_sale']
            print(f"\n🎯 re_net_sale analysis:")
            print(f"  Type: {type(net_sale)}")
            print(f"  Value: {net_sale}")
            if isinstance(net_sale, dict):
                print(f"  Has 'value' key: {'value' in net_sale}")
                print(f"  Keys: {list(net_sale.keys())}")
        else:
            print(f"\n❌ re_net_sale not found in content!")
        
        # Pretty print a sample fact
        if fact_keys:
            sample_key = fact_keys[0]
            print(f"\n📖 Sample fact ({sample_key}):")
            print(json.dumps(content[sample_key], indent=2))
            
    finally:
        session.close()

def test_unit_normalizer_logic(doc_id: int):
    """Test the unit normalizer logic step by step."""
    session = get_session()
    try:
        pd = session.get(ParsedDocument, doc_id)
        if not pd:
            return
        
        content = pd.content or {}
        print(f"\n🧪 Testing unit normalizer logic on doc {doc_id}:")
        
        # Replicate the exact logic from unit_normalize_document
        doc_meta = {
            "presentation_currency": content.get("presentation_currency"),
            "rounding_level": content.get("rounding_level")
        }
        
        facts_found = 0
        for key, val in content.items():
            print(f"  Checking key '{key}': type={type(val)}")
            
            # The exact condition from your code
            if not isinstance(val, dict) or "value" not in val:
                print(f"    ❌ Skipped: not dict ({not isinstance(val, dict)}) or no 'value' key ({'value' not in val if isinstance(val, dict) else 'N/A'})")
                continue
            
            print(f"    ✅ Passed filter - this is a fact!")
            facts_found += 1
            
        print(f"\n📈 Total facts that would be processed: {facts_found}")
        
    finally:
        session.close()

if __name__ == '__main__':
    # Replace with your actual doc_id
    DOC_ID = 1657  # From your debug output
    
    print("=" * 60)
    print("🐛 DEBUGGING UNIT NORMALIZER ISSUE")
    print("=" * 60)
    
    debug_parsed_document(DOC_ID)
    test_unit_normalizer_logic(DOC_ID)