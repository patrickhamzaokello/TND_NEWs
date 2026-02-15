"""
Thin wrapper around the OpenAI API client.
Handles retries, token tracking, and cost estimation.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI
from django.conf import settings
from openai import RateLimitError, APIError

logger = logging.getLogger(__name__)

# ── Pricing per 1M tokens (USD) ───────────────────────────────────────────────
# Update pricing as needed
MODEL_PRICING = {
    "gpt-5-nano": {
        "input": 0.05,
        "output": 0.40,
    },
}

# Defaults used by each agent
ENRICHMENT_MODEL = getattr(
    settings, "ENRICHMENT_MODEL", "gpt-5-nano"
)

DIGEST_MODEL = getattr(
    settings, "DIGEST_MODEL", "gpt-5-nano"
)


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, MODEL_PRICING[ENRICHMENT_MODEL])
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)


def call_openai(
    system: str,
    user: str,
    model: str = ENRICHMENT_MODEL,
    max_output_tokens: int = 1024,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> LLMResponse:
    """
    Call the OpenAI Responses API with automatic retry on rate-limit / transient errors.
    Returns an LLMResponse with parsed content and token counts.
    """

    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    for attempt in range(1, max_retries + 1):
        try:
            response = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_output_tokens=max_output_tokens,
            )

            content = response.output_text

            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens

            cost = calculate_cost(model, input_tokens, output_tokens)

            logger.debug(
                "OpenAI call OK | model=%s tokens=%d+%d cost=$%.6f",
                model,
                input_tokens,
                output_tokens,
                cost,
            )

            return LLMResponse(
                content=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=model,
                cost_usd=cost,
            )

        except RateLimitError:
            wait = retry_delay * (2 ** attempt)
            logger.warning(
                "Rate limit hit (attempt %d/%d). Retrying in %.1fs",
                attempt,
                max_retries,
                wait,
            )
            time.sleep(wait)

        except APIError as e:
            if attempt == max_retries:
                raise
            logger.warning(
                "API error (attempt %d/%d): %s. Retrying...",
                attempt,
                max_retries,
                str(e),
            )
            time.sleep(retry_delay)

    raise RuntimeError(f"OpenAI API failed after {max_retries} attempts")


def parse_json_response(raw: str) -> dict:
    """
    Safely parse JSON output from OpenAI.
    Strips accidental markdown fences if present.
    """
    cleaned = raw.strip()

    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(
            lines[1:-1] if lines[-1].startswith("```") else lines[1:]
        )

    return json.loads(cleaned)
