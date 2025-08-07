# earnings_agent/llm/gemini_client.py

import os
import time
import logging
import re
from typing import Optional

from google import genai
from google.genai import types

# Configuration
GCP_PROJECT_ID = "pdf-extractor-467911"
GCP_LOCATION = "us-central1"

# Retry settings
LLM_MAX_RETRIES = 3
LLM_INITIAL_BACKOFF = 5

# Configure logging
logger = logging.getLogger(__name__)

# Initialize Gemini Client (singleton pattern)
_client = None

def get_gemini_client():
    """Get or create the Gemini client instance."""
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True, 
            project=GCP_PROJECT_ID, 
            location=GCP_LOCATION
        )
    return _client

def clean_json_response(response_text: str) -> str:
    """Clean LLM response to extract only the JSON part."""
    response_text = response_text.strip()
    
    # Remove markdown code blocks
    response_text = re.sub(r'```json\s*', '', response_text)
    response_text = re.sub(r'```\s*', '', response_text)
    
    # Find the first { and last } to extract JSON
    first_brace = response_text.find('{')
    last_brace = response_text.rfind('}')
    
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        json_part = response_text[first_brace:last_brace + 1]
        return json_part.strip()
    
    # Try to find array format
    first_bracket = response_text.find('[')
    last_bracket = response_text.rfind(']')
    
    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        json_part = response_text[first_bracket:last_bracket + 1]
        return json_part.strip()
    
    # If no braces/brackets found, return original (will likely fail JSON parsing)
    return response_text

def call_gemini_with_retry(
    model_name: str, 
    prompt: str, 
    context_text: Optional[str] = None, 
    pdf_bytes: Optional[bytes] = None,
    use_json_mode: bool = True,
    temperature: float = 0.0,
    max_tokens: int = 16384
) -> str:
    """
    Call Gemini API with retry logic.
    
    Args:
        model_name: The model to use (e.g., "gemini-2.5-pro")
        prompt: The main prompt text
        context_text: Optional context to include
        pdf_bytes: Optional PDF data for document analysis
        use_json_mode: Whether to use strict JSON mode
        temperature: Temperature for response generation
        max_tokens: Maximum tokens in response
    
    Returns:
        The response text from the model
    """
    client = get_gemini_client()
    
    for attempt in range(LLM_MAX_RETRIES):
        try:
            # Build parts list
            parts_list = []
            
            # Add prompt
            parts_list.append(types.Part.from_text(text=prompt))
            
            # Add context if provided
            if context_text:
                parts_list.append(types.Part.from_text(text=context_text))
            
            # Add PDF if provided
            if pdf_bytes:
                parts_list.append(types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"))
            
            contents = [types.Content(role="user", parts=parts_list)]
            
            # Configure generation
            if use_json_mode:
                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                )
            else:
                config = types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens
                )
            
            # Add safety settings to be permissive for financial data
            config.safety_settings = [
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
            ]
            
            # Make the API call
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config
            )
            
            if not response.text:
                raise ValueError("LLM returned an empty response.")
            
            # Clean response if using JSON mode
            if use_json_mode:
                return clean_json_response(response.text)
            else:
                return response.text.strip()
            
        except Exception as e:
            logger.warning(f"Gemini API call failed on attempt {attempt + 1}/{LLM_MAX_RETRIES}: {e}")
            if attempt + 1 == LLM_MAX_RETRIES:
                logger.error(f"All retry attempts failed for model {model_name}")
                raise
            
            # Exponential backoff
            wait_time = LLM_INITIAL_BACKOFF * (2 ** attempt)
            logger.info(f"Waiting {wait_time} seconds before retry...")
            time.sleep(wait_time)
    
    raise RuntimeError(f"LLM call to {model_name} failed after all retry attempts.")

def call_gemini_text_only(
    model_name: str,
    prompt: str,
    context_text: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 4096
) -> str:
    """
    Convenience function for text-only Gemini calls without JSON mode.
    """
    return call_gemini_with_retry(
        model_name=model_name,
        prompt=prompt,
        context_text=context_text,
        use_json_mode=False,
        temperature=temperature,
        max_tokens=max_tokens
    )

def call_gemini_with_json(
    model_name: str,
    prompt: str,
    context_text: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 16384
) -> str:
    """
    Convenience function for JSON-mode Gemini calls.
    """
    return call_gemini_with_retry(
        model_name=model_name,
        prompt=prompt,
        context_text=context_text,
        use_json_mode=True,
        temperature=temperature,
        max_tokens=max_tokens
    )

# For backward compatibility with existing PDF parser
def _call_gemini_with_retry(model_name: str, prompt: str, pdf_bytes: bytes, context_text: str = None, use_json_mode: bool = True) -> str:
    """
    Legacy function signature for backward compatibility with PDF parser.
    """
    return call_gemini_with_retry(
        model_name=model_name,
        prompt=prompt,
        context_text=context_text,
        pdf_bytes=pdf_bytes,
        use_json_mode=use_json_mode
    )

# Test function
def test_gemini_connection():
    """
    Test the Gemini connection with a simple call.
    """
    try:
        response = call_gemini_text_only(
            model_name="gemini-2.0-flash-exp",
            prompt="Say 'Hello, I am working!' and nothing else."
        )
        logger.info(f"Gemini test successful: {response}")
        return True
    except Exception as e:
        logger.error(f"Gemini test failed: {e}")
        return False

if __name__ == "__main__":
    # Test the client
    logging.basicConfig(level=logging.INFO)
    test_gemini_connection()