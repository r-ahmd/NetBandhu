"""The model layer: one call site for every provider, with a fallback and a trace.

    from models import build_model, ask_json, stamp

    has_key(spec)        is the key for this provider string in the environment?
    make_model(spec)     one chat model with our timeout, retries and rate limit
    build_model()        the assistant's model: settings.model_id plus the fallback
    ask_json(...)        one structured call you can trust, on any provider
    stamp(name, ...)     the config= keys that label a run with its prompt version

WHY A FALLBACK IS CONFIGURATION, NOT A TRY/EXCEPT
    A provider that 429s on a Saturday evening should not end the demo. LangChain's
    `with_fallbacks` retries the *same request* on the second model, so no feature
    has to remember to wrap its call. The fallback is scored before it is trusted:
    see README, "measured, not assumed", where gemini was chosen over groq because
    groq's gpt-oss-20b returned an empty string for a trivial prompt.

WHY ask_json EXISTS
    `model.with_structured_output(Model)` validates for us, but it has no way to
    pass `context=`, so a schema that needs to check its answer against the ticket
    silently skips that check (session 4, §4.3). So we ask for plain JSON against
    the model's JSON schema and validate ourselves — and on a ValidationError we
    show the model its own answer and what was wrong, once. That single retry is
    the difference between "the model had a bad day" and "the run failed".
"""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.rate_limiters import InMemoryRateLimiter
from pydantic import ValidationError

from core.config import settings
from core.exceptions import ModelReturnedNothing

KEY_NAMES = {
    "openrouter": "OPENROUTER_API_KEY",
    "google_genai": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}


def provider_of(model_id: str) -> str:
    return model_id.split(":", 1)[0]


def has_key(model_id: str) -> bool:
    """Is the key for this provider string in the environment?"""
    name = KEY_NAMES.get(provider_of(model_id))
    return name is None or bool(os.getenv(name))


OPENROUTER_BASE = "https://openrouter.ai/api/v1"


@lru_cache(maxsize=8)
def make_model(model_id: str, stream_tool_steps: bool = True):
    """One chat model with our timeout, retries, output cap and its own rate limit.

    `stream_tool_steps=False` sets LangChain's disable_streaming="tool_calling":
    a call made with tools available is sent unstreamed and arrives whole.

    WHY OPENROUTER GOES THROUGH ChatOpenAI AND NOT `init_chat_model`
        Measured on this machine, 22 Nov: `openrouter:openai/gpt-oss-20b` via
        langchain-openrouter hung past every timeout — its SDK retries internally
        and ignores LangChain's `timeout`, so a stall never surfaced as an error.
        The same endpoint through ChatOpenAI with `base_url` answered in 1.7 s, and
        structured output worked. OpenRouter is OpenAI-compatible; this is the
        shortest path that also honours our timeout and retry budget.
    """
    limiter = InMemoryRateLimiter(requests_per_second=settings.REQUESTS_PER_SECOND)
    common = dict(
        timeout=settings.REQUEST_TIMEOUT_S,
        max_retries=settings.MAX_RETRIES,
        max_tokens=settings.MAX_OUTPUT_TOKENS,
        rate_limiter=limiter,
        temperature=settings.TEMPERATURE,
    )
    if provider_of(model_id) == "openrouter":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_id.split(":", 1)[1],
            base_url=OPENROUTER_BASE,
            api_key=os.getenv("OPENROUTER_API_KEY"),
            **common,
            disable_streaming=False if stream_tool_steps else "tool_calling",
        )
    return init_chat_model(
        model_id, **common,
        disable_streaming=False if stream_tool_steps else "tool_calling",
    )


@lru_cache(maxsize=4)
def build_model(stream_tool_steps: bool = True):
    """The assistant's model: settings.model_id, with the fallback attached.

    If the primary still fails after its own retries — an outage, a spent quota,
    a provider that changed a model id on us — the same request goes to
    settings.FALLBACK. Nobody decides that in the moment.
    """
    primary = make_model(settings.model_id, stream_tool_steps)
    if not settings.FALLBACK:
        return primary
    if not has_key(settings.FALLBACK):
        print(f"(fallback {settings.FALLBACK} is off: no key for it in .env)")
        return primary
    return primary.with_fallbacks([make_model(settings.FALLBACK, stream_tool_steps)])


def ask_json(model, schema, messages, context: dict | None = None):
    """One structured call. Returns (validated object, usage_metadata).

    `messages` is anything `.invoke` accepts: a string, a list of tuples, a prompt.

    THREE ATTEMPTS, AND WHY THREE
        1. the call
        2. a blank or truncated reply gets one more, with a nudge — a reasoning
           model that spent its budget thinking has not given a wrong answer
        3. a ValidationError gets one more, showing the model its own answer and
           the field that was wrong
    After that it raises. Guessing a fourth time is how a program acts on a number
    nobody computed.
    """
    structured = model.with_structured_output(
        schema.model_json_schema(), method="json_schema", include_raw=True)

    last: str = ""
    for attempt in (1, 2, 3):
        out = structured.invoke(messages, config={"run_name": schema.__name__})
        if out["parsed"] is None:
            finish = (out["raw"].response_metadata or {}).get("finish_reason")
            # A reasoning model can spend the whole budget thinking and write
            # nothing. That is worth ONE more attempt — it is a budget accident,
            # not a wrong answer — but not an unbounded loop.
            last = (f"cut off at max_tokens={settings.MAX_OUTPUT_TOKENS}" if finish == "length"
                    else f"no usable answer ({out.get('parsing_error') or 'empty reply'})")
            if attempt < 3:
                messages = [*messages,
                            ("human", "Your previous reply was empty or cut off. "
                                      "Answer again, briefly, in the required JSON.")]
            continue
        try:
            return schema.model_validate(out["parsed"], context=context), out["raw"].usage_metadata
        except ValidationError as err:
            problems = "; ".join(e["msg"] for e in err.errors())
            if attempt == 3:
                raise
            messages = [*messages,
                        ("ai", json.dumps(out["parsed"])),
                        ("human", f"That answer was rejected: {problems}. "
                                  f"Return a corrected answer.")]
    raise ModelReturnedNothing(last)


# ── the prompt stamp: which wording produced this run? (S09) ─────────────────
def prompt_sha(*parts: str) -> str:
    """Twelve hex characters of the prompt's text, as assembled.

    The subscriber's question is never one of the parts: that is data, and a hash
    that changed with every question could not tell you the WORDING moved."""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def stamp(name: str, version: str, *parts: str) -> dict:
    """The config= keys that label a run in LangSmith with its prompt.

    `metadata` is what you filter and group by; the `prompt:` tag puts the two
    arms of an experiment one click apart in the run list."""
    return {
        "metadata": {"prompt": name, "prompt_version": version,
                     "prompt_sha": prompt_sha(*parts)},
        "tags": [f"prompt:{name}:{version}"],
    }


if __name__ == "__main__":
    print(f"primary  {settings.model_id}  key={has_key(settings.model_id)}")
    print(f"fallback {settings.FALLBACK}  key={has_key(settings.FALLBACK)}")
    m = build_model()
    reply = m.invoke("Reply with exactly: NetBandhu ready")
    text = reply.content if isinstance(reply.content, str) else str(reply.content)
    print(f"reply    {text[:60]!r}")
