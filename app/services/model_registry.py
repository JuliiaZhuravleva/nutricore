"""Which model runs where, and what each one accepts (ADR-0004).

A model is named by a *ref*: ``"<route>:<model>"``, where the route is the API
the request goes to (``openai`` directly, or ``openrouter`` for Claude, Gemini,
Qwen and OpenAI models behind one key). A bare id means ``openai`` — so the
production ``OPENAI_MODEL=gpt-4o-mini``, the persisted ``"openai_model"``
override and every existing test keep working unchanged.

Models differ in the parameters they take. gpt-4o-mini wants ``temperature`` and
``max_tokens``; the reasoning models that replace it reject or ignore
``temperature`` unless reasoning is off, count reasoning tokens against
``max_completion_tokens``, and can come back EMPTY (and still billed) when a
small budget — barcode 64, label 256 — is spent on thinking. ``ModelSpec``
records those differences, ``build_chat_params`` turns one call's intent into
the keyword arguments that model accepts. No I/O here: the service owns the
clients, ``model_watch`` owns the lifecycle checks.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ROUTES = ("openai", "openrouter")
# The chat tasks a model can be chosen for. Web search keeps its own pin
# (settings.OPENAI_WEB_SEARCH_MODEL); embeddings stay on OpenAI directly, their
# dimension is migration-locked.
TASKS = ("vision", "text", "barcode", "label_ocr")
VISION_TASKS = ("vision", "barcode", "label_ocr")

# The cheapest efforts, cheapest first. Reading a barcode or a label needs no
# thinking, so those tasks ask for the first one a model supports.
_LOW_EFFORTS = ("none", "minimal", "low")
_LOW_EFFORT_TASKS = ("barcode", "label_ocr")

# Budget floor while a model reasons: the limit covers reasoning AND the visible
# answer. Billing is for tokens used, not for the cap, so a roomy floor is free
# unless the model actually thinks that long.
REASONING_TOKEN_FLOOR = 4096

# OpenRouter routing for every request: only providers that honour every
# parameter we send (otherwise unknown ones are silently dropped), and never a
# provider that keeps prompts for training — food photos are personal data.
OPENROUTER_PROVIDER_PREFS = {"require_parameters": True, "data_collection": "deny"}


@dataclass(frozen=True)
class ModelRef:
    route: str
    model: str

    def __str__(self) -> str:
        return f"{self.route}:{self.model}"


@dataclass(frozen=True)
class ModelSpec:
    """What one model accepts. The defaults are the cautious guess for an
    unknown model: it may reason, so no temperature and a budget floor."""

    vision: bool = True
    # "any" | "only_if_effort_none" | "omit"
    temperature: str = "omit"
    # "max_tokens" | "max_completion_tokens"
    token_param: str = "max_completion_tokens"
    reasoning: bool = True
    efforts: tuple = ()
    default_effort: str | None = None
    web_search: bool = False
    # Whose lifecycle to ask (model_watch): (provider, id at that provider);
    # None = only the route's own model list.
    first_party: tuple | None = None
    # A dated snapshot behind an alias: it can be shut down before the alias is.
    snapshot: str | None = None
    known: bool = True


_GPT_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")

_LEGACY_OPENAI_CHAT = ModelSpec(
    temperature="any",
    token_param="max_tokens",
    reasoning=False,
    web_search=True,
)


def _openai_reasoning(model: str) -> ModelSpec:
    return ModelSpec(
        temperature="only_if_effort_none",
        token_param="max_completion_tokens",
        efforts=_GPT_EFFORTS,
        default_effort="medium",
        web_search=True,
        first_party=("openai", model),
    )


# Flags come from the providers' docs and OpenRouter's supported_parameters
# (2026-10-27). OpenRouter rows that list no `temperature` must not get one:
# with require_parameters it would leave no eligible provider.
REGISTRY: dict[str, ModelSpec] = {
    "openai:gpt-4o-mini": ModelSpec(
        temperature="any",
        token_param="max_tokens",
        reasoning=False,
        web_search=True,
        first_party=("openai", "gpt-4o-mini"),
        snapshot="gpt-4o-mini-2024-07-18",
    ),
    "openai:gpt-6-luna": _openai_reasoning("gpt-6-luna"),
    "openai:gpt-5.6-luna": _openai_reasoning("gpt-5.6-luna"),
    "openai:gpt-6.1-terra": _openai_reasoning("gpt-6.1-terra"),
    "openrouter:openai/gpt-6-luna": ModelSpec(
        temperature="omit",
        token_param="max_completion_tokens",
        efforts=_GPT_EFFORTS,
        default_effort="medium",
        web_search=True,
        first_party=("openai", "gpt-6-luna"),
    ),
    "openrouter:anthropic/claude-haiku-5.5": ModelSpec(
        temperature="omit",
        token_param="max_completion_tokens",
        efforts=_GPT_EFFORTS,
        default_effort="medium",
        web_search=True,
        first_party=("anthropic", "claude-haiku-5-5"),
    ),
    "openrouter:google/gemini-3.8-flash": ModelSpec(
        temperature="any",
        token_param="max_tokens",
        efforts=("low", "medium", "high"),
        default_effort="medium",
        web_search=True,
        first_party=("google", "gemini-3.8-flash"),
    ),
    "openrouter:google/gemini-3.5-flash-lite": ModelSpec(
        temperature="any",
        token_param="max_tokens",
        efforts=("minimal", "low", "medium", "high"),
        default_effort="minimal",
        web_search=True,
        first_party=("google", "gemini-3.5-flash-lite"),
    ),
    # Reasoning is on by default and budget-controlled: no effort knob to send.
    "openrouter:qwen/qwen3.8-flash": ModelSpec(
        temperature="any",
        token_param="max_tokens",
        web_search=True,
    ),
}

# The older OpenAI chat families the self-heal list used to offer: plain chat
# models that take temperature and max_tokens like gpt-4o-mini does.
_LEGACY_OPENAI_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-4-turbo")


def parse_model_ref(ref: str) -> ModelRef:
    """``"openrouter:google/gemini-3.8-flash"`` → (openrouter, google/…);
    a bare id, or one whose prefix is not a route (``ft:gpt-4o-mini:…``), is
    an OpenAI model."""
    ref = ref.strip()
    route, sep, model = ref.partition(":")
    if sep and route in ROUTES and model:
        return ModelRef(route, model)
    return ModelRef("openai", ref)


def spec_for(ref: ModelRef | str) -> ModelSpec:
    """The registry row for ``ref``, or a cautious default for an unknown model."""
    if isinstance(ref, str):
        ref = parse_model_ref(ref)
    spec = REGISTRY.get(str(ref))
    if spec is not None:
        return spec
    if ref.route == "openai" and ref.model.startswith(_LEGACY_OPENAI_PREFIXES):
        return _LEGACY_OPENAI_CHAT
    # Gemini endpoints on OpenRouter list only max_tokens, and OpenRouter gives
    # both names the same meaning — so max_tokens is the safe one there.
    token_param = "max_tokens" if ref.route == "openrouter" else "max_completion_tokens"
    return ModelSpec(token_param=token_param, known=False)


def effort_for(spec: ModelSpec, task: str, configured: str | None = None) -> str | None:
    """The effort to send for ``task``, or None to leave the model's default.

    A configured effort the model doesn't list is dropped with a warning rather
    than sent: the provider would reject it (or, on OpenRouter with
    require_parameters, find no provider at all).
    """
    if not spec.efforts:
        if configured:
            logger.warning("Model takes no reasoning effort; ignoring %r", configured)
        return None
    if configured:
        if configured in spec.efforts:
            return configured
        logger.warning(
            "Effort %r is not one of %s; using the default", configured, spec.efforts
        )
    if task in _LOW_EFFORT_TASKS:
        return next((e for e in _LOW_EFFORTS if e in spec.efforts), None)
    return None


def build_chat_params(
    ref: ModelRef | str,
    task: str,
    *,
    max_tokens: int,
    temperature: float | None = None,
    effort: str | None = None,
) -> dict:
    """chat.completions keyword arguments for one call of ``task`` on ``ref``.

    ``max_tokens`` and ``temperature`` are the call's intent (what gpt-4o-mini
    got before ADR-0004); this translates them into what the model accepts.
    ``effort`` is the configured override, if any.
    """
    if isinstance(ref, str):
        ref = parse_model_ref(ref)
    spec = spec_for(ref)
    chosen = effort_for(spec, task, effort)
    reasoning = spec.reasoning and (chosen or spec.default_effort) != "none"

    params: dict = {"model": ref.model}
    if temperature is not None and (
        spec.temperature == "any"
        or (spec.temperature == "only_if_effort_none" and not reasoning)
    ):
        params["temperature"] = temperature
    params[spec.token_param] = (
        max(max_tokens, REASONING_TOKEN_FLOOR) if reasoning else max_tokens
    )

    extra_body: dict = {}
    if chosen is not None:
        if ref.route == "openrouter":
            extra_body["reasoning"] = {"effort": chosen}
        else:
            params["reasoning_effort"] = chosen
    if ref.route == "openrouter":
        extra_body["provider"] = dict(OPENROUTER_PROVIDER_PREFS)
    if extra_body:
        params["extra_body"] = extra_body
    return params
