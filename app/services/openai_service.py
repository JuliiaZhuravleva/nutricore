import json
import logging
from typing import Any, Dict, List, Optional

import openai
from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# Persisted-setting key for the runtime model override (see TD-005 self-heal).
OPENAI_MODEL_SETTING_KEY = "openai_model"

# /v1/models does not tag capabilities, so "suitable for our JSON + vision usage"
# is a maintained heuristic: chat models in these families, minus the non-chat
# variants (audio/realtime/tts/…) that share a prefix.
_SUITABLE_MODEL_PREFIXES = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "o1",
    "o3",
    "o4-mini",
)
_EXCLUDE_SUBSTRINGS = (
    "audio",
    "realtime",
    "transcribe",
    "tts",
    "search",
    "moderation",
    "embedding",
    "image",
)
# Shown when /v1/models can't be reached or matches nothing.
_FALLBACK_MODELS = ("gpt-4o-mini", "gpt-4o")


class ModelUnavailableError(Exception):
    """The configured OpenAI model was rejected as unknown/deprecated.

    Carries the offending model id so the caller can offer a replacement.
    """

    def __init__(self, model: str, original: Exception):
        self.model = model
        self.original = original
        super().__init__(str(original))


def is_model_not_found_error(exc: Exception) -> bool:
    """True if `exc` looks like OpenAI rejecting the model (unknown/deprecated)."""
    if getattr(exc, "code", None) == "model_not_found":
        return True
    msg = str(exc).lower()
    if "model_not_found" in msg:
        return True
    # Require "model" to co-occur so a "does not exist" / "deprecated" message
    # about some other resource isn't misread as a model deprecation.
    return "model" in msg and ("does not exist" in msg or "deprecat" in msg)


class OpenAIService:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            max_retries=settings.OPENAI_MAX_RETRIES,
        )
        self.model = settings.OPENAI_MODEL
        self.temperature = settings.OPENAI_TEMPERATURE
        self.max_tokens = settings.OPENAI_MAX_TOKENS

    def set_model(self, model: str) -> None:
        """Point subsequent calls at `model` (used by the TD-005 self-heal flow)."""
        self.model = model

    async def _create(self, **kwargs) -> Any:
        """chat.completions.create with the current model, translating a
        model-deprecation / 404 into ModelUnavailableError so callers can
        self-heal instead of failing the user's action.
        """
        try:
            return await self.client.chat.completions.create(model=self.model, **kwargs)
        except (openai.NotFoundError, openai.BadRequestError) as exc:
            if is_model_not_found_error(exc):
                raise ModelUnavailableError(self.model, exc) from exc
            raise

    async def list_suitable_models(self) -> List[str]:
        """Current chat/vision-capable models, best-effort, capped for a keyboard.

        Filters the live model list to a maintained family allowlist and drops
        the non-chat variants; falls back to a static list on any fetch failure.
        """
        try:
            resp = await self.client.models.list()
            ids = [m.id for m in resp.data]
        except Exception as exc:  # network / auth — fall back to the static list
            logger.warning("Could not fetch OpenAI model list: %s", exc)
            ids = []
        suitable = sorted(
            {
                m
                for m in ids
                if m.startswith(_SUITABLE_MODEL_PREFIXES)
                and not any(x in m for x in _EXCLUDE_SUBSTRINGS)
            }
        )
        return (suitable or list(_FALLBACK_MODELS))[:6]

    async def analyze_food_entry(self, text: str) -> Dict:
        """Analyze food entry text and extract nutritional information."""
        system_prompt = """You are a nutrition expert assistant. Analyze food entries and extract nutritional information.
        Return data in the following JSON format:
        {
            "calories": float,
            "protein": float,  # in grams
            "fats": float,     # in grams
            "carbs": float,    # in grams
            "portion": string, # e.g. "1 serving (250g)"
            "foods": list[str] # list of identified food items
        }"""

        user_prompt = (
            f"Analyze this food entry and extract nutritional information: {text}"
        )

        response = await self._create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    async def analyze_food_image(self, image_url: str) -> Dict:
        """Analyze food image using the configured vision-capable model."""
        system_prompt = """You are a nutrition expert assistant. Analyze food images and extract nutritional information.
        Return data in the following JSON format:
        {
            "foods": list[str],        # list of identified food items
            "calories": float,
            "protein": float,          # in grams
            "fats": float,             # in grams
            "carbs": float,            # in grams
            "portion": string           # estimated portion size
        }"""

        response = await self._create(
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "What food items do you see in this image? Provide nutritional information.",
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    async def extract_barcode_from_image(self, image_url: str) -> Optional[str]:
        """Extract a barcode (EAN/UPC) from a product image using vision.

        Asks the vision model to read the numeric barcode digits directly from
        the image. Avoids pyzbar/libzbar system-lib dependencies.

        Returns the barcode string (digits only) if one is clearly readable,
        or None if no barcode is visible or the digits cannot be reliably read.
        """
        system_prompt = (
            "You are a barcode reader. Your only job is to find and accurately read "
            "barcode digits from product images. Look for EAN-13 (13 digits), EAN-8 "
            "(8 digits), UPC-A (12 digits), or UPC-E (6–8 digits) barcodes.\n\n"
            'Return ONLY this JSON: {"barcode": "digits_here"}\n'
            'or if no barcode is visible or readable: {"barcode": null}\n\n'
            "Rules:\n"
            "- Return ONLY the numeric digits — no spaces, no dashes.\n"
            "- If you cannot clearly read ALL digits, return null.\n"
            "- Do NOT guess or fill in digits you cannot see.\n"
            "- Ignore QR codes and Data Matrix codes — only linear barcodes."
        )

        response = await self._create(
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Read the barcode from this product image. Return only the numeric digits.",
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            # Barcode is at most ~15 digits + small JSON envelope — 64 tokens is ample.
            max_tokens=64,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        try:
            data = json.loads(raw)
            value = data.get("barcode")
            if not value:
                return None
            # Normalise: strip spaces and dashes the model might include.
            cleaned = str(value).strip().replace(" ", "").replace("-", "")
            # Validate: digits only, length 6–18 (covers EAN-8 through ITF-14).
            if not cleaned.isdigit() or not (6 <= len(cleaned) <= 18):
                logger.warning(
                    "extract_barcode_from_image: invalid barcode value %r", value
                )
                return None
            return cleaned
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.warning(
                "extract_barcode_from_image: failed to parse response %r: %s", raw, exc
            )
            return None

    async def generate_health_insights(self, user_data: Dict) -> str:
        """Generate health insights based on user's nutrition and activity data."""
        system_prompt = """You are a nutrition and health expert assistant. Analyze user's nutrition and activity data
        to provide personalized health insights and recommendations."""

        user_prompt = f"""Analyze this user's data and provide health insights:
        User Data: {user_data}

        Focus on:
        1. Nutritional balance
        2. Caloric intake vs goals
        3. Macro distribution
        4. Areas for improvement
        5. Specific recommendations"""

        response = await self._create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return response.choices[0].message.content
