from typing import List, Optional
from openai import OpenAI
from app.core.config import settings
import json
import logging

logger = logging.getLogger(__name__)

class OpenAIService:
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL
        self.temperature = settings.OPENAI_TEMPERATURE
        self.max_tokens = settings.OPENAI_MAX_TOKENS

    async def analyze_food_entry(self, text: str) -> dict:
        """Analyze food entry text and extract nutritional information."""
        prompt = f"""Analyze this food entry and extract nutritional information:
        {text}
        
        Return ONLY a JSON object with the following fields:
        - foods: list of foods identified
        - calories: total calories (number)
        - protein: grams of protein (number)
        - fats: grams of fat (number)
        - carbs: grams of carbohydrates (number)
        - portion: portion description (string)
        
        Example response:
        {{
            "foods": ["apple"],
            "calories": 95,
            "protein": 0.5,
            "fats": 0.3,
            "carbs": 25,
            "portion": "1 medium apple (182g)"
        }}
        """
        
        try:
            logger.info(f"Sending request to OpenAI for text: {text}")
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a nutrition expert assistant. Always return valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,  # Lower temperature for more consistent output
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"}  # Ensure JSON response
            )
            
            # Get the response content and parse it as JSON
            content = response.choices[0].message.content
            logger.info(f"OpenAI raw response content: {content}")
            
            try:
                result = json.loads(content)
                logger.info(f"Parsed JSON result: {result}")
                
                # Log the type of 'foods' field
                logger.info(f"Type of foods field: {type(result.get('foods'))}")
                logger.info(f"Value of foods field: {result.get('foods')}")
                
                # Ensure foods is always a list
                if isinstance(result.get('foods'), str):
                    logger.info("Converting foods from string to list")
                    result['foods'] = [result['foods']]
                elif not isinstance(result.get('foods'), list):
                    logger.info("Setting foods to empty list as it's neither string nor list")
                    result['foods'] = []
                
                logger.info(f"Final result after processing: {result}")
                return result
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON: {e}. Content: {content}", exc_info=True)
                return {
                    "foods": [],
                    "calories": None,
                    "protein": None,
                    "fats": None,
                    "carbs": None,
                    "portion": None
                }
                
        except Exception as e:
            logger.error(f"Error analyzing food entry: {str(e)}", exc_info=True)
            return {
                "foods": [],
                "calories": None,
                "protein": None,
                "fats": None,
                "carbs": None,
                "portion": None
            }

    async def generate_health_insights(self, user_data: dict) -> str:
        """Generate health insights based on user's nutrition and activity data."""
        # Implementation for health insights

    async def analyze_food_image(self, image_url: str) -> dict:
        """Analyze food image using GPT-4 Vision."""
        # Implementation for image analysis

    async def generate_meal_recommendations(self, user_preferences: dict) -> List[str]:
        """Generate personalized meal recommendations."""
        # Implementation for meal recommendations