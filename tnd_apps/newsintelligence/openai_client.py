"""
OpenAI API client for the enrichment pipeline.
Uses the standard Chat Completions API (client.chat.completions.create).

Pricing reference (per 1M tokens):
  gpt-4o-mini : input $0.15  / output $0.60   ← bulk article enrichment
  gpt-4o      : input $2.50  / output $10.00  ← daily digest synthesis
"""

import json
import logging
import time
from dataclasses import dataclass

import openai
from django.conf import settings

logger = logging.getLogger(__name__)

# ── Model config ──────────────────────────────────────────────────────────────

MODEL_PRICING = {
    'gpt-4o-mini': {'input': 0.15,  'output': 0.60},
    'gpt-4o':      {'input': 2.50,  'output': 10.00},
    'gpt-4o-mini-2024-07-18': {'input': 0.15, 'output': 0.60},
    'gpt-4o-2024-08-06':      {'input': 2.50, 'output': 10.00},
}

ENRICHMENT_MODEL = getattr(settings, 'ENRICHMENT_MODEL', 'gpt-4o-mini')
DIGEST_MODEL     = getattr(settings, 'DIGEST_MODEL',     'gpt-4o-mini')


# ── Response wrapper ──────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, MODEL_PRICING['gpt-4o-mini'])
    return round(
        (input_tokens  / 1_000_000) * pricing['input'] +
        (output_tokens / 1_000_000) * pricing['output'],
        6
    )


# ── Main client call ──────────────────────────────────────────────────────────

def call_openai(
    system: str,
    user: str,
    model: str = ENRICHMENT_MODEL,
    max_tokens: int = 1024,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    timeout: float = 60.0,
) -> LLMResponse:
    """
    Call the OpenAI Chat Completions API.
    Uses client.chat.completions.create — NOT client.responses.create.

    Includes:
      - Automatic retry on rate limits and transient errors
      - Request timeout (default 60s)
      - Detailed logging on failure
    """
    client = openai.OpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=timeout,
    )

    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {'role': 'system', 'content': system},
                    {'role': 'user',   'content': user},
                ],
                temperature=0.1,  # low temperature = consistent JSON output
            )

            content       = response.choices[0].message.content or ''
            input_tokens  = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
            actual_model  = response.model

            if not content.strip():
                raise ValueError(
                    f"OpenAI returned empty content (model={actual_model}, "
                    f"finish_reason={response.choices[0].finish_reason})"
                )

            cost = calculate_cost(actual_model, input_tokens, output_tokens)
            logger.debug(
                "OpenAI OK | model=%s tokens=%d+%d cost=$%.5f",
                actual_model, input_tokens, output_tokens, cost
            )
            return LLMResponse(
                content=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=actual_model,
                cost_usd=cost,
            )

        except openai.RateLimitError:
            wait = retry_delay * (2 ** attempt)
            logger.warning(
                "OpenAI rate limit (attempt %d/%d). Retrying in %.1fs",
                attempt, max_retries, wait
            )
            time.sleep(wait)

        except openai.APITimeoutError:
            logger.warning(
                "OpenAI timeout after %.1fs (attempt %d/%d).",
                timeout, attempt, max_retries
            )
            if attempt == max_retries:
                raise RuntimeError(
                    f"OpenAI timed out after {max_retries} attempts ({timeout}s each). "
                    "Check your network/firewall — the container may not have access to api.openai.com."
                )
            time.sleep(retry_delay)

        except openai.AuthenticationError:
            raise RuntimeError(
                "OpenAI authentication failed. "
                "Check that OPENAI_API_KEY is set correctly in your settings/env."
            )

        except openai.APIConnectionError as e:
            if attempt == max_retries:
                raise RuntimeError(
                    f"OpenAI connection error after {max_retries} attempts: {e}\n"
                    "The container may not have outbound internet access to api.openai.com."
                ) from e
            logger.warning("Connection error (attempt %d/%d): %s", attempt, max_retries, e)
            time.sleep(retry_delay)

        except openai.APIStatusError as e:
            if attempt == max_retries:
                raise
            logger.warning(
                "API status error %s (attempt %d/%d): %s",
                e.status_code, attempt, max_retries, e.message
            )
            time.sleep(retry_delay)

    raise RuntimeError(f"OpenAI API failed after {max_retries} attempts")


# ── JSON parser ───────────────────────────────────────────────────────────────

def parse_json_response(raw: str) -> dict:
    """
    Safely parse JSON from the LLM response.
    Strips markdown fences (```json ... ```) if present.
    Raises a clear error showing the raw response on failure.
    """
    if not raw or not raw.strip():
        raise ValueError("LLM returned empty response — cannot parse JSON.")

    cleaned = raw.strip()

    # Strip markdown code fences
    if cleaned.startswith('```'):
        lines = cleaned.split('\n')
        # Drop first line (```json or ```) and last line (```)
        inner = lines[1:] if len(lines) > 1 else lines
        if inner and inner[-1].strip() == '```':
            inner = inner[:-1]
        cleaned = '\n'.join(inner).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        # Log the first 500 chars of the raw response to help debug
        preview = raw[:500].replace('\n', ' ')
        raise ValueError(
            f"JSON parse failed: {e}\n"
            f"Raw response preview: {preview}"
        ) from e
