import openai
import os
from dotenv import load_dotenv
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(dotenv_path=os.path.join(ROOT_DIR, ".env"))
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def load_prompt_template():
    with open("prompts/event_prompt.txt", "r") as file:
        return file.read()

def interpret_event(event_description: str) -> dict:
    prompt_template = load_prompt_template()
    prompt = prompt_template.format(event=event_description)

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=500,
        )
        raw_output = response.choices[0].message.content
        parsed_output = eval(raw_output)
        return parsed_output

    except Exception as e:
        print("Error during interpretation:", e)
        return {"error": str(e)}