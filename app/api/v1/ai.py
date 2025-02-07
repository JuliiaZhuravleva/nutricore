from fastapi import APIRouter, Depends, HTTPException
from app.services.openai_service import OpenAIService

router = APIRouter()

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