from typing import List, Dict, Optional
from openai import AsyncOpenAI
from app.core.config import settings

class OpenAIService:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL
        self.temperature = settings.OPENAI_TEMPERATURE
        self.max_tokens = settings.OPENAI_MAX_TOKENS

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
        
        user_prompt = f"Analyze this food entry and extract nutritional information: {text}"
        
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"}
        )
        return response.choices[0].message.content

    async def analyze_food_image(self, image_url: str) -> Dict:
        """Analyze food image using GPT-4 Vision."""
        system_prompt = """You are a nutrition expert assistant. Analyze food images and extract nutritional information.
        Return data in the following JSON format:
        {
            "foods": list[str],        # list of identified food items
            "calories": float,
            "protein": float,          # in grams
            "fats": float,             # in grams
            "carbs": float,            # in grams
            "portion_estimate": string  # estimated portion size
        }"""
        
        response = await self.client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What food items do you see in this image? Provide nutritional information."},
                        {"type": "image_url", "image_url": image_url}
                    ]
                }
            ],
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"}
        )
        return response.choices[0].message.content

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
        
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens
        )
        return response.choices[0].message.content
