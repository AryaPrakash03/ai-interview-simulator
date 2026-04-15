"""
llm.py — Failure-proof Groq LLM integration layer.

Architecture:
  1. Primary model:  llama-3.3-70b-versatile  (replacement for decommissioned llama3-70b-8192)
  2. Fallback model: mixtral-8x7b-32768
  3. Each model is retried up to MAX_RETRIES times with exponential backoff.
  4. If both models exhaust retries, a RuntimeError is raised so the caller
     can return a clean 503 to the frontend.
  5. Every attempt is logged with model name, attempt number, and error code.
"""

import os
import re
import json
import asyncio
import httpx
import logging
from dotenv import load_dotenv

# Load .env file from the backend directory
load_dotenv()

logger = logging.getLogger(__name__)

# ── Model Configuration ───────────────────────────────────────────────────────
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Primary model
PRIMARY_MODEL = "llama3-8b-8192"

# Fallback model — smaller, different architecture, high availability
FALLBACK_MODEL = "mixtral-8x7b-32768"

# ── Retry Configuration ───────────────────────────────────────────────────────
MAX_RETRIES = 3
BACKOFF_BASE = 2          # Exponential backoff: 2^(attempt-1) → 1s, 2s, 4s
LLM_TIMEOUT = 25.0        # Per-request timeout in seconds (hard cap)

# ── Constants ─────────────────────────────────────────────────────────────────
FALLBACK_MESSAGE = "LLM temporarily unavailable"
SERVICE_BUSY_MESSAGE = "The AI interviewers are currently busy. Please try again in 60 seconds."

# Status codes that are worth retrying (transient / capacity errors)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _get_api_key() -> str:
    """Read the Groq API key from environment (re-reads on each call for hot-reload)."""
    return os.getenv("GROQ_API_KEY", "").strip()


# ── Low-level request ─────────────────────────────────────────────────────────

async def _send_request(
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    api_key: str,
) -> httpx.Response:
    """Send a single request to the Groq chat completions endpoint."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        response = await client.post(GROQ_API_URL, headers=headers, json=payload)

    return response


# ── Retry wrapper for a single model ──────────────────────────────────────────

async def _call_with_retries(
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    api_key: str,
) -> str:
    """
    Try to get a successful response from `model`, retrying up to MAX_RETRIES
    times with exponential backoff on transient failures.

    Returns:
        The LLM content string on success.

    Raises:
        RuntimeError: If all retries are exhausted or a non-retryable error occurs.
    """
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                f"📤 [{model}] attempt {attempt}/{MAX_RETRIES} "
                f"(temp={temperature})"
            )

            response = await _send_request(model, messages, temperature, max_tokens, api_key)

            # ── Log every response ─────────────────────────────────────────
            logger.info(
                f"📥 [{model}] attempt {attempt} → "
                f"status={response.status_code}"
            )
            logger.debug(f"   Response body: {response.text[:400]}")

            # ── Non-retryable auth error ───────────────────────────────────
            if response.status_code == 401:
                logger.error(
                    f"[MODEL_FAILURE] model={model} status=401 "
                    f"reason=INVALID_API_KEY"
                )
                raise RuntimeError(
                    "Groq API key is invalid or expired. "
                    "Check your key at https://console.groq.com and update backend/.env"
                )

            # ── Model decommissioned (400) — don't retry, move to fallback
            if response.status_code == 400:
                body = response.text[:300]
                logger.error(
                    f"[MODEL_FAILURE] model={model} status=400 "
                    f"reason=MODEL_DECOMMISSIONED_OR_BAD_REQUEST body={body[:120]}"
                )
                raise RuntimeError(f"Model '{model}' returned 400: {body}")

            # ── Retryable errors (429 rate-limit, 5xx / capacity errors) ───
            if response.status_code in RETRYABLE_STATUS_CODES:
                wait = BACKOFF_BASE ** (attempt - 1)  # 1s, 2s, 4s
                logger.warning(
                    f"[MODEL_FAILURE] model={model} status={response.status_code} "
                    f"attempt={attempt}/{MAX_RETRIES} "
                    f"reason=RETRYABLE_ERROR backoff={wait}s"
                )
                last_error = RuntimeError(
                    f"Model '{model}' returned {response.status_code} on attempt {attempt}"
                )
                await asyncio.sleep(wait)
                continue

            # ── Any other unexpected status ────────────────────────────────
            if response.status_code != 200:
                body = response.text[:300]
                logger.error(
                    f"[MODEL_FAILURE] model={model} status={response.status_code} "
                    f"reason=UNEXPECTED body={body[:120]}"
                )
                raise RuntimeError(f"Groq API returned {response.status_code}: {body}")

            # ── Success — extract content ──────────────────────────────────
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            logger.info(f"✅ [{model}] success — {len(content)} chars returned.")
            return content

        except httpx.TimeoutException:
            wait = BACKOFF_BASE ** (attempt - 1)  # 1s, 2s, 4s
            logger.warning(
                f"[MODEL_FAILURE] model={model} status=TIMEOUT "
                f"attempt={attempt}/{MAX_RETRIES} backoff={wait}s"
            )
            last_error = RuntimeError(f"Model '{model}' timed out on attempt {attempt}")
            await asyncio.sleep(wait)
            continue

        except httpx.RequestError as exc:
            wait = BACKOFF_BASE ** (attempt - 1)  # 1s, 2s, 4s
            logger.warning(
                f"[MODEL_FAILURE] model={model} status=NETWORK_ERROR "
                f"attempt={attempt}/{MAX_RETRIES} error={exc} backoff={wait}s"
            )
            last_error = RuntimeError(f"Network error with '{model}': {exc}")
            await asyncio.sleep(wait)
            continue

        except RuntimeError:
            # Non-retryable (401, 400, unexpected status) — bubble up immediately
            raise

        except (KeyError, IndexError) as exc:
            logger.error(
                f"[MODEL_FAILURE] model={model} status=PARSE_ERROR "
                f"reason=UNEXPECTED_RESPONSE_STRUCTURE error={exc}"
            )
            raise RuntimeError(f"Unexpected response from '{model}': {exc}")

    # All retries exhausted for this model
    logger.error(
        f"[MODEL_FAILURE] model={model} status=EXHAUSTED "
        f"reason=ALL_{MAX_RETRIES}_RETRIES_FAILED"
    )
    raise last_error or RuntimeError(f"Model '{model}' failed after {MAX_RETRIES} retries")


# ── Public API ─────────────────────────────────────────────────────────────────

async def call_llm(prompt: str, temperature: float = 0.7, max_tokens: int = 2048) -> str:
    """
    Send a prompt to Groq with dual-model fallback and retry logic.

    Flow:
      1. Try PRIMARY_MODEL with up to 3 retries (exponential backoff).
      2. If primary fails → try FALLBACK_MODEL with up to 3 retries.
      3. If both fail → raise RuntimeError (caller returns 503).

    Args:
        prompt: The user/system prompt to send.
        temperature: Sampling temperature (0.0–2.0).
        max_tokens: Maximum tokens in the response.

    Returns:
        The generated text from the LLM.

    Raises:
        RuntimeError: If the API key is missing, or both models fail.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error("🔑 GROQ_API_KEY is not set!")
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "1) Get a free key at https://console.groq.com  "
            "2) Paste it into backend/.env  "
            "3) Restart the server."
        )

    messages = [
        {
            "role": "system",
            "content": (
                "Return ONLY a valid JSON object. Do not include any introductory text or closing remarks. "
                "Keep explanations brief to avoid token truncation."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    # ── Stage 1: Primary model ─────────────────────────────────────────────
    try:
        logger.info(f"🎯 Trying primary model: {PRIMARY_MODEL}")
        return await _call_with_retries(
            PRIMARY_MODEL, messages, temperature, max_tokens, api_key
        )
    except RuntimeError as primary_err:
        logger.warning(
            f"⚡ Primary model ({PRIMARY_MODEL}) failed: {primary_err}. "
            f"Falling back to {FALLBACK_MODEL}..."
        )

    # ── Stage 2: Fallback model ────────────────────────────────────────────
    try:
        logger.info(f"🔄 Trying fallback model: {FALLBACK_MODEL}")
        return await _call_with_retries(
            FALLBACK_MODEL, messages, temperature, max_tokens, api_key
        )
    except RuntimeError as fallback_err:
        logger.error(
            f"[MODEL_FAILURE] model={FALLBACK_MODEL} status=EXHAUSTED "
            f"reason=FALLBACK_ALSO_FAILED error={fallback_err}"
        )

    # ── Both models exhausted ──────────────────────────────────────────────
    logger.critical(
        "[MODEL_FAILURE] status=ALL_MODELS_EXHAUSTED "
        "reason=PRIMARY_AND_FALLBACK_BOTH_FAILED"
    )
    raise RuntimeError(SERVICE_BUSY_MESSAGE)


# ── JSON Cleaning & Parsing ────────────────────────────────────────────────────

def _clean_llm_text(text: str) -> str:
    """
    Aggressively clean LLM output so it has the best chance of being
    valid JSON. Handles markdown fences, BOM, control chars, etc.
    """
    cleaned = text.strip()

    # Remove UTF-8 BOM if present
    cleaned = cleaned.lstrip("\ufeff")

    # Remove markdown code fences: ```json ... ``` or ``` ... ```
    cleaned = re.sub(r"^```(?:json|JSON)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    # Strip any text before the first { or [ (e.g. "Here is the JSON:")
    first_brace = cleaned.find("{")
    first_bracket = cleaned.find("[")
    candidates = [i for i in (first_brace, first_bracket) if i != -1]
    if candidates:
        cleaned = cleaned[min(candidates):]

    # Strip any text after the last } or ]
    last_brace = cleaned.rfind("}")
    last_bracket = cleaned.rfind("]")
    candidates = [i for i in (last_brace, last_bracket) if i != -1]
    if candidates:
        cleaned = cleaned[: max(candidates) + 1]

    return cleaned.strip()


def parse_json_response(text: str) -> dict | list:
    """
    Extract and parse JSON from an LLM response that may contain
    markdown fences or extra text around the JSON.

    Args:
        text: Raw LLM response text.

    Returns:
        Parsed JSON as a dict or list.

    Raises:
        ValueError: If no valid JSON can be extracted.
    """
    # Log raw response for debugging
    logger.info(f"🔍 Raw LLM response ({len(text)} chars): {text[:300]}")

    # Step 1: Clean the text
    cleaned = _clean_llm_text(text)
    logger.debug(f"   Cleaned text: {cleaned[:300]}")

    # Step 2: Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning(f"⚠️ Direct parse failed: {e}")

    # Step 3: Try to find a JSON object or array within the cleaned text
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = cleaned.find(start_char)
        end = cleaned.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            fragment = cleaned[start : end + 1]
            try:
                return json.loads(fragment)
            except json.JSONDecodeError:
                continue

    # Step 4: Last-ditch — try the original un-cleaned text
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    logger.error(f"❌ Failed to parse JSON from LLM response: {text[:500]}")
    raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}...")
