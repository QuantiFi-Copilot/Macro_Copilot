# earnings_agent/llm/normalizer_client.py
import os
import logging
import json
from typing import List
import anthropic

# --- Load API Key from Environment ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# --- Initialize the Client ---
# It's good practice to initialize the client once at the module level.
try:
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set. Please add it to your .env file.")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
except Exception as e:
    logging.error(f"Failed to initialize Anthropic client: {e}")
    client = None

def get_llm_mapping_suggestion(raw_label: str, standard_names: List[str]) -> str:
    """
    Queries the Anthropic Claude 3 Sonnet model to find the best match for a raw label.

    Args:
        raw_label: The raw label from the source document (e.g., "ProfitLossForPeriod").
        standard_names: The list of valid, clean names from the playbook.

    Returns:
        The suggested standard name, or "N/A" if no confident match is found or an error occurs.
    """
    if not client:
        logging.error("Anthropic client is not initialized. Cannot make API call.")
        return "N/A"

    # The prompt remains the same, as it is well-structured for the task.
    prompt = f"""You are a financial data normalization expert. Given the following raw XBRL label from an Indian financial report, map it to one of the predefined standard names from the list below. The matching should be exact. If there is no confident match, respond with "N/A".

Standard Names List:
{json.dumps(standard_names, indent=2)}

Raw Label: "{raw_label}"

Your one-word response:"""

    try:
        logging.info(f"Querying LLM for raw_label: '{raw_label}'")
        message = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=100,  # We only expect a short response, so this saves cost.
            temperature=0.0, # We want the most deterministic, confident answer.
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )
        
        # Extract the text content from the response object
        suggestion = message.content[0].text.strip()
        logging.info(f"LLM suggested mapping for '{raw_label}' -> '{suggestion}'")
        
        # Final check to ensure the suggestion is actually in our list
        if suggestion in standard_names:
            return suggestion
        else:
            # If the LLM hallucinates or returns something not in the list, treat it as N/A
            logging.warning(f"LLM suggestion '{suggestion}' is not in the provided standard_names list. Defaulting to 'N/A'.")
            return "N/A"

    except Exception as e:
        logging.error(f"An error occurred while calling the LLM API for raw_label '{raw_label}': {e}", exc_info=True)
        # In case of any API error, we fail gracefully and return "N/A"
        return "N/A"