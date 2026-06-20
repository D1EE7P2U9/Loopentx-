"""LLM caller — routes ctx.think() to the configured provider."""

from __future__ import annotations

import re
from typing import Optional

import structlog

from loopentx.core.config import get_config

log = structlog.get_logger()


async def call_llm(
    prompt:      str,
    system:      Optional[str] = None,
    choose_from: Optional[list[str]] = None,
) -> str:
    """Call the configured LLM provider and return the response string.

    Automatically routes to OpenAI or Anthropic based on config.
    If choose_from is set, validates the response against the allowed values.
    """
    cfg = get_config()

    if cfg.llm_provider == "anthropic":
        response = await _call_anthropic(prompt, system, cfg)
    elif cfg.llm_provider == "openai":
        response = await _call_openai(prompt, system, cfg)
    else:
        raise ValueError(f"Unsupported LLM provider: {cfg.llm_provider!r}")

    response = response.strip()

    if choose_from:
        response = _extract_choice(response, choose_from)

    log.info("llm.called", provider=cfg.llm_provider,
             prompt_len=len(prompt), response_len=len(response))
    return response


async def _call_anthropic(prompt: str, system: Optional[str], cfg: Any) -> str:
    try:
        import anthropic
    except ImportError:
        raise ImportError("pip install loopentx[anthropic]")

    api_key = cfg.llm_api_key or None
    client  = anthropic.AsyncAnthropic(api_key=api_key)

    kwargs: dict = dict(
        model=cfg.llm_model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = system

    msg = await client.messages.create(**kwargs)
    return msg.content[0].text


async def _call_openai(prompt: str, system: Optional[str], cfg: Any) -> str:
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise ImportError("pip install loopentx[openai]")

    api_key = cfg.llm_api_key or None
    client  = AsyncOpenAI(api_key=api_key)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = await client.chat.completions.create(
        model=cfg.llm_model,
        messages=messages,
        max_tokens=1024,
    )
    return resp.choices[0].message.content or ""


def _extract_choice(response: str, choices: list[str]) -> str:
    """Extract the matching choice from the LLM response."""
    lower = response.lower().strip()
    for choice in choices:
        if choice.lower() in lower:
            return choice
    # Fuzzy: first word
    first_word = re.split(r"\W+", lower)[0] if lower else ""
    for choice in choices:
        if choice.lower().startswith(first_word):
            return choice
    log.warning("llm.choice_not_found", response=response, choices=choices)
    return choices[0]


# Allow Any import for type hints in private functions
from typing import Any
