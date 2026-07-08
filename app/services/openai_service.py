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


def _has_valid_gs1_check_digit(code: str) -> bool:
    """Validate the GS1 mod-10 check digit for EAN-8 / UPC-A / EAN-13.

    A single misread digit (glare, tilt, smudge) usually invalidates the check
    digit, so this rejects most vision misreads before they hit the product DB
    and return a *different* real product's КБЖУ under a "точно" badge. Only the
    fixed standard lengths carry a mod-10 check digit; other lengths (UPC-E,
    ITF-14, …) are left best-effort and accepted by the caller.
    """
    if len(code) not in (8, 12, 13) or not code.isdigit():
        return False
    digits = [int(c) for c in code]
    check = digits[-1]
    # From the rightmost data digit leftward, weights alternate 3, 1, 3, 1, …
    total = sum(
        d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits[:-1]))
    )
    return (10 - (total % 10)) % 10 == check


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
            # Validate: ASCII digits only, length 6–18 (covers EAN-8 through
            # ITF-14). isascii() guards against non-ASCII "digit" characters
            # (e.g. superscripts) that str.isdigit() accepts but that must never
            # reach the OFF URL path.
            if not (cleaned.isascii() and cleaned.isdigit()) or not (
                6 <= len(cleaned) <= 18
            ):
                logger.warning(
                    "extract_barcode_from_image: invalid barcode value %r", value
                )
                return None
            # For the fixed standard lengths, reject a bad GS1 check digit — it
            # catches most single-digit vision misreads before they resolve to a
            # different real product. Non-standard lengths stay best-effort.
            if len(cleaned) in (8, 12, 13) and not _has_valid_gs1_check_digit(cleaned):
                logger.warning(
                    "extract_barcode_from_image: %r fails GS1 check digit — "
                    "likely a misread, rejecting",
                    cleaned,
                )
                return None
            return cleaned
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.warning(
                "extract_barcode_from_image: failed to parse response %r: %s", raw, exc
            )
            return None

    async def extract_nutrition_label(self, image_url: str) -> str:
        """Extract КБЖУ from the nutrition facts table on a product label.

        Used by LabelOCRStrategy (A10): reads on-pack calorie/protein/fat/carb
        values with their declared serving basis so the pipeline can scale them
        to the vision-estimated portion.  Never cached.

        Returns a JSON string:
        {
            "basis": "per_100g" | "per_serving" | "per_package" | null,
            "serving_grams": float | null,   # gram weight of one serving
            "package_grams": float | null,   # gram weight of whole package
            "calories": float | null,
            "protein":  float | null,
            "fats":     float | null,
            "carbs":    float | null
        }

        ``basis`` is null when the label is absent, illegible, or the serving
        basis cannot be determined; the strategy treats this as a fall-through.
        """
        system_prompt = (
            "You are a nutrition label reader. Find the nutrition facts table on "
            "the product packaging in the image and extract the macronutrient values.\n\n"
            "Return ONLY this JSON:\n"
            "{\n"
            '  "basis": "per_100g" | "per_serving" | "per_package" | null,\n'
            '  "serving_grams": float or null,\n'
            '  "package_grams": float or null,\n'
            '  "calories": float or null,\n'
            '  "protein":  float or null,\n'
            '  "fats":     float or null,\n'
            '  "carbs":    float or null\n'
            "}\n\n"
            "Rules:\n"
            '- "basis" is the column/row heading: "per_100g" for per 100g, '
            '"per_serving" for per serving/portion, "per_package" for per '
            "package/container.\n"
            "- If the table has BOTH per_100g and per_serving columns, use "
            '"per_100g" (standard baseline, more reliable for scaling).\n'
            '- "serving_grams" is the gram weight of one serving '
            '(e.g. 30 from "1 serving (30g)"). Set to null if not given in grams.\n'
            '- "package_grams" is the total gram weight of the package. '
            "Set to null if not shown.\n"
            "- If the nutrition table is absent, illegible, or the basis cannot "
            'be determined, set "basis" to null (other fields may also be null).\n'
            "- Extract numbers as floats. Use the energy value in kcal for "
            '"calories".\n'
            "- Do NOT guess — if a value is unclear, set it to null."
        )

        response = await self._create(
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Extract the nutrition label values from this "
                                "product image."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            # Nutrition label JSON is small — 7 fields with floats.
            max_tokens=256,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    async def web_search_nutrition(self, query_foods: list) -> str:
        """Call the Responses API web_search tool to retrieve product nutrition text.

        Returns the raw text content from the model's response (plain string), so
        analyze_and_log(kind="web_search", parse=...) can consume it unchanged.

        Uses the split-client approach (ADR-0002): only this method goes through
        the Responses API.  All existing methods remain on chat.completions.

        Does NOT catch exceptions — all network / API / parse errors propagate to
        the caller (NameWebSearchStrategy.resolve → try/except → return None).
        Does NOT trigger the TD-005 self-heal mechanism (that path is chat.completions
        only).

        Parameters
        ----------
        query_foods:
            Vision-extracted food name(s) used as the search query.
        """
        query = ", ".join(query_foods)
        response = await self.client.responses.create(
            model=self.model,
            tools=[{"type": "web_search_preview"}],
            input=(
                f"Find the exact КБЖУ (calories, protein, fat, carbohydrates per 100g) "
                f"for: {query}\n\n"
                "Output ONLY a JSON object in this exact format (no prose, no markdown):\n"
                '{"identification": "product name and brand", '
                '"calories_per_100g": number_or_null, '
                '"protein_per_100g": number_or_null, '
                '"fats_per_100g": number_or_null, '
                '"carbs_per_100g": number_or_null}\n'
                'If you cannot identify the specific product, set "identification" to null.'
            ),
        )
        # Return the text content as a plain string — shape matches existing text methods
        # so analyze_and_log(kind="web_search", parse=...) works unchanged.
        return response.output_text

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


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_openai_service_instance: Optional["OpenAIService"] = None


def get_openai_service() -> "OpenAIService":
    """Return the process-wide OpenAIService singleton.

    Both the bot handler layer (telegram.py) and the meal-resolution pipeline
    (product_lookup_service.py) share ONE instance, so a runtime model switch
    (TD-005 self-heal) stays visible everywhere. This factory lives here — not
    on TelegramService — so service-layer code can obtain the client WITHOUT
    importing the handler module, which previously forced a circular import.
    """
    global _openai_service_instance
    if _openai_service_instance is None:
        _openai_service_instance = OpenAIService()
    return _openai_service_instance
