from fastapi import APIRouter, HTTPException
from models import LinkChildRequest
from typing import Optional, List ,Dict
from database import verify_user_token, fetch_user_data
import firebase_admin
from firebase_admin import credentials, auth, db, messaging
from openai import AsyncOpenAI
import os
import re
import json
import datetime
import logging
from helpers import india_tz, MAX_MESSAGE_LENGTH

router = APIRouter()
logger = logging.getLogger(__name__)

# Initialize OpenAI client
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY not set in .env")
client = AsyncOpenAI(api_key=openai_api_key)

@router.post("/mood-analysis")
async def analyze_mood(req: LinkChildRequest):
    """Analyze the user's mood based on provided text input."""
    try:
      
        parent_uid = verify_user_token(req.idToken)
        logger.info(f"Parent verified: {parent_uid}")

        # Fetch parent data
        parent_ref = db.reference(f"users/{parent_uid}")
        parent_data = parent_ref.get()
        if not parent_data:
            raise HTTPException(status_code=404, detail="Parent not found")
        
        if parent_data.get("user_details", {}).get("account_type") != "family":
            raise HTTPException(status_code=403, detail="Only family accounts can access child mood analysis")

        # Check if child_id exists in parent's children dictionary
        if req.child_id not in (parent_data.get("children") or {}):
            raise HTTPException(status_code=403, detail="Not authorized for this child")

        # Fetch child data
        child_uid = req.child_id
        user_ref = db.reference(f"users/{child_uid}")
        user_data = user_ref.get()
        if not user_data:
            raise HTTPException(status_code=404, detail="Child not found")

        # Fetch child details
        user_details = user_data.get("user_details", {})
        user_name = user_details.get("name", "there")

        # Fetch voice history
        voice_ref = user_ref.child("voice_history")
        voice_data = voice_ref.get() or {"history": []}
        if not isinstance(voice_data, dict):
            logger.error(f"Invalid voice_data for UID: {child_uid}")
            voice_data = {"history": []}
        voice_history = voice_data.get("history", [])

        # Fetch important asked questions
        imp_questions_ref = user_ref.child("imp_ask_question")
        imp_questions_data = imp_questions_ref.get() or {"entries": []}
        if not isinstance(imp_questions_data, dict):
            logger.error(f"Invalid imp_questions_data for UID: {child_uid}")
            imp_questions_data = {"entries": []}
        imp_questions = imp_questions_data.get("entries", [])

        # Current time in IST
        current_time = datetime.datetime.now(india_tz)
        current_date = current_time.date()

        # Analyze mood
        mood_analysis = await analyze_mood(voice_history, imp_questions, user_name, client)

        # Save mood to child's mood_history
        mood_history_ref = user_ref.child("mood_history")
        mood_history = mood_history_ref.get() or []
        if not isinstance(mood_history, list):
            mood_history = []
        
        # Create new mood entry
        new_mood_entry = {
            "overall_mood": mood_analysis["overall_mood"],
            "description": mood_analysis["description"],
            "date": current_date.isoformat(),
            "timestamp": current_time.isoformat()
        }
        mood_history.append(new_mood_entry)

        # Keep only the last 3 mood entries, sorted by timestamp
        mood_history = sorted(
            mood_history,
            key=lambda x: x.get("timestamp", "1970-01-01T00:00:00+05:30"),
            reverse=True
        )[:3]
        mood_history_ref.set(mood_history)

        # Prepare historical moods for response
        historical_moods = [
            {
                "date": detail["date"],
                "overall_mood": detail["overall_mood"],
                "description": detail.get("description", "No description available"),
                "timestamp": detail["timestamp"]
            }
            for detail in mood_history
        ]

        # Compile response
        response = {
            "status": "success",
            "mood_analysis": {
                "overall_mood": mood_analysis["overall_mood"],
                "description": mood_analysis["description"],
                "historical_moods": historical_moods
            },
            "child_id": req.child_id,
            "child_name": user_name,
            "timestamp": current_time.isoformat()
        }

        return response

    except auth.InvalidIdTokenError:
        logger.error("Invalid Firebase ID token")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except auth.ExpiredIdTokenError:
        logger.error("Expired Firebase ID token")
        raise HTTPException(status_code=401, detail="Token has expired")
    except Exception as e:
        logger.error(f"Error in mood-analysis endpoint: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

async def analyze_mood(voice_history: List[dict], imp_questions: List[dict], user_name: str, client: AsyncOpenAI) -> dict:
    # Validate voice history
    if not voice_history or not isinstance(voice_history, list):
        logger.warning("No valid voice history provided for mood analysis")
        return {
            "overall_mood": "Neutral",
            "description": "No valid messages found in voice conversation history for mood analysis."
        }
    
    # Preprocess voice history to include ALL messages with required fields
    formatted_messages = []
    for idx, msg in enumerate(voice_history):
        if not isinstance(msg, dict):
            logger.warning(f"Skipping invalid message at index {idx}: not a dictionary")
            continue
        if "role" not in msg or "content" not in msg:
            logger.warning(f"Skipping message at index {idx}: missing 'role' or 'content'")
            continue
        if not msg["content"] or not isinstance(msg["content"], str):
            logger.warning(f"Skipping message at index {idx}: empty or invalid content")
            continue
        
        # Format message with index, role, content, timestamp, and additional fields
        formatted_msg = {
            "index": idx,
            "role": msg["role"],
            "content": msg["content"].strip(),
            "timestamp": msg.get("timestamp", "unknown"),
            "type": msg.get("type", "unknown"),
            "is_category_question": msg.get("is_category_question", False),
            "category": msg.get("category", None),
            "subcategory": msg.get("subcategory", None)
        }
        formatted_messages.append(formatted_msg)
    
    # Preprocess imp_ask_question entries
    formatted_imp_questions = []
    for idx, entry in enumerate(imp_questions):
        if not isinstance(entry, dict):
            logger.warning(f"Skipping invalid imp_ask_question entry at index {idx}: not a dictionary")
            continue
        if "question" not in entry or "reply" not in entry:
            logger.warning(f"Skipping imp_ask_question entry at index {idx}: missing 'question' or 'reply'")
            continue
        formatted_imp_questions.append({
            "index": idx,
            "question": entry["question"].strip(),
            "reply": entry["reply"].strip(),
            "question_timestamp": entry.get("question_timestamp", "unknown"),
            "reply_timestamp": entry.get("reply_timestamp", "unknown")
        })
    
    if not formatted_messages and not formatted_imp_questions:
        logger.warning("No valid messages or important questions found for mood analysis")
        return {
            "overall_mood": "Neutral",
            "description": "No valid messages or important questions found in voice conversation history for mood analysis."
        }
    
    # Log the formatted voice history and imp_ask_question for debugging
    logger.info(f"Sending voice history to GPT for {user_name}: {json.dumps(formatted_messages, indent=2)}")
    logger.info(f"Sending imp_ask_question to GPT for {user_name}: {json.dumps(formatted_imp_questions, indent=2)}")

    # Construct detailed prompt for mood analysis
    mood_prompt = {
        "role": "system",
        "content": (
            f"You are a mood analysis expert. Analyze the mood of {user_name} based on the ENTIRE voice conversation history and important question entries provided below. "
            f"Consider EVERY user reply in the voice history (role: 'user') and important question replies individually and collectively to determine the overall emotional tone. "
            f"Pay attention to the content, tone, context, sequence of each user message (noted by index), and the categories/subcategories of questions where applicable. "
            f"The voice history includes messages with role ('user' or 'assistant'), content, timestamp, type, and optional category/subcategory fields. "
            f"The important question entries include significant questions asked by the assistant and the user's replies, with timestamps. "
            f"Use the important question replies to gain deeper insight into the user's emotional state, as these reflect responses to personalized, category-based questions. "
            f"Return a VALID JSON object with exactly two fields: "
            f"1. 'overall_mood': A single word or short phrase (e.g., 'Happy', 'Calm', 'Sad'). "
            f"2. 'description': One sentence describing the mood based on the conversation history and important questions. "
            f"The response MUST be enclosed in triple backticks with 'json' language identifier, like this:\n"
            f"```json\n"
            f"{{\"overall_mood\": \"Happy\", \"description\": \"The user expressed excitement.\"}}\n"
            f"```\n"
            f"Voice conversation history:\n"
            f"{json.dumps(formatted_messages, indent=2)}\n"
            f"Important question entries:\n"
            f"{json.dumps(formatted_imp_questions, indent=2)}\n"
            f"Do not make assumptions beyond the provided history and question entries."
        )
    }
    
    # Send only the system prompt with formatted history
    messages = [mood_prompt]
    
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=200,
            temperature=0.5
        )
        raw_response = response.choices[0].message.content
        logger.info(f"Raw GPT response for {user_name}: {raw_response}")

        # Attempt to extract JSON from code block
        json_match = re.search(r'```json\n(.*?)\n```', raw_response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = raw_response  # Fallback to raw response if no code block

        # Parse the JSON response
        result = json.loads(json_str)
        
        # Validate response structure
        if not isinstance(result, dict) or "overall_mood" not in result or "description" not in result:
            logger.error("Invalid GPT response format: missing required fields")
            raise ValueError("Invalid response format: missing required fields 'overall_mood' or 'description'")
        
        # Ensure fields are strings and non-empty
        if not isinstance(result["overall_mood"], str) or not result["overall_mood"]:
            logger.error("Invalid GPT response: 'overall_mood' is empty or not a string")
            raise ValueError("Invalid response: 'overall_mood' must be a non-empty string")
        if not isinstance(result["description"], str) or not result["description"]:
            logger.error("Invalid GPT response: 'description' is empty or not a string")
            raise ValueError("Invalid response: 'description' must be a non-empty string")
        
        logger.info(f"Mood analysis result for {user_name}: {result}")
        return result
    
    except json.JSONDecodeError as jde:
        logger.error(f"Failed to parse GPT response as JSON: {str(jde)}")
        logger.debug(f"Problematic raw response: {raw_response}")
        return {
            "overall_mood": "Unknown",
            "description": "Unable to analyze mood due to invalid JSON response format from GPT."
        }
    except ValueError as ve:
        logger.error(f"Mood analysis validation error: {str(ve)}")
        return {
            "overall_mood": "Unknown",
            "description": f"Unable to analyze mood: {str(ve)}."
        }
    except Exception as e:
        logger.error(f"Error analyzing mood: {str(e)}")
        return {
            "overall_mood": "Unknown",
            "description": f"Unable to analyze mood due to an error: {str(e)}."
        }