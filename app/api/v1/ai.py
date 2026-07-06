from fastapi import APIRouter, Depends
from app.core.deps import require_api_token
from app.services.openai_service import OpenAIService

# Router-level auth so this stays protected even if mounted without the
# include_router(dependencies=...) the other routers get in main.py.
router = APIRouter(dependencies=[Depends(require_api_token)])

@router.post("/analyze-food")
async def analyze_food(
    text: str,
    service: OpenAIService = Depends()
):
    return await service.analyze_food_entry(text)

@router.post("/analyze-image")
async def analyze_image(
    image_url: str,
    service: OpenAIService = Depends()
):
    return await service.analyze_food_image(image_url)