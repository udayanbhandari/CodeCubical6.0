import re
import json
import time
import logging
import asyncio
from typing import Type, TypeVar, Optional
from pydantic import BaseModel, ValidationError
from app.config import settings

logger = logging.getLogger("proofdata.llm")
T = TypeVar("T", bound=BaseModel)


def _clean_json_text(text: str) -> str:
    """Strip markdown code fences and extraneous whitespace."""
    text = text.strip()
    # Match ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text


async def _call_llm_raw(system: str, user: str) -> str:
    """Route LLM call to configured provider (Anthropic, OpenAI, or Fallback)."""
    provider = settings.LLM_PROVIDER.lower()
    api_key = settings.LLM_API_KEY
    model = settings.LLM_MODEL

    if not api_key or api_key.startswith("your_"):
        logger.warning(f"LLM_API_KEY not configured. Provider={provider}. Mock response fallback triggered.")
        # If model_cls parsing is expected, we raise error so retry can occur or fallback logic can run
        raise ValueError("LLM_API_KEY is not set or invalid in environment.")

    if provider == "anthropic":
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await asyncio.wait_for(
            client.messages.create(
                model=model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}]
            ),
            timeout=60.0
        )
        return response.content[0].text

    elif provider in ("openai", "azure"):
        import openai
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ],
                temperature=0.1
            ),
            timeout=60.0
        )
        return response.choices[0].message.content or ""

    elif provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        
        generation_config = {
            "temperature": 0.1,
            "response_mime_type": "application/json",
        }
        model_instance = genai.GenerativeModel(
            model_name=model,
            system_instruction=system,
            generation_config=generation_config
        )
        response = await model_instance.generate_content_async(user)
        return response.text

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


async def generate_json(system: str, user: str, model_cls: Type[T], max_retries: int = 2) -> T:
    """Generate structured JSON from LLM and validate against Pydantic model_cls.

    Retries up to max_retries on invalid JSON or Pydantic ValidationError,
    feeding error feedback back to the LLM.
    """
    current_user_prompt = user
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        start_time = time.time()
        try:
            raw_response = await _call_llm_raw(system, current_user_prompt)
            cleaned_text = _clean_json_text(raw_response)
            parsed_json = json.loads(cleaned_text)
            validated_obj = model_cls.model_validate(parsed_json)

            latency = time.time() - start_time
            logger.debug(f"[LLM SUCCESS] Attempt={attempt} Latency={latency:.2f}s Model={settings.LLM_MODEL}")
            return validated_obj

        except (json.JSONDecodeError, ValidationError) as e:
            latency = time.time() - start_time
            logger.warning(f"[LLM VALIDATION ERROR] Attempt={attempt} Latency={latency:.2f}s Error={str(e)}")
            last_error = e

            if attempt < max_retries:
                error_details = str(e)
                current_user_prompt = (
                    f"{user}\n\n"
                    f"[SYSTEM ERROR FEEDBACK - ATTEMPT {attempt + 1}]\n"
                    f"Your previous JSON output failed validation with the following error:\n"
                    f"{error_details}\n\n"
                    f"Please output ONLY valid JSON adhering strictly to the required schema."
                )
            else:
                break
        except Exception as e:
            logger.error(f"[LLM EXECUTION ERROR] {str(e)}")
            last_error = e
            break

    raise RuntimeError(f"Failed to generate valid JSON after {max_retries + 1} attempts: {last_error}")
