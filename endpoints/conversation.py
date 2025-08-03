from fastapi import APIRouter, HTTPException
from models import LinkChildRequest
from typing import Optional, List ,Dict
from database import verify_user_token, fetch_user_data, fetch_voice_history
from openai import AsyncOpenAI
import firebase_admin
from firebase_admin import credentials, auth, db, messaging
import os
import re
import json
import datetime
import logging
from helpers import india_tz, MAX_HISTORY_LENGTH

router = APIRouter()
logger = logging.getLogger(__name__)

# Initialize OpenAI client
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY not set in .env")
client = AsyncOpenAI(api_key=openai_api_key)

@router.post("/conversation-summary")
async def conversation_summary(req: LinkChildRequest):
    """Generate a summary of the user's conversation history."""
    try:
        
        parent_uid = verify_user_token(req.idToken)
        logger.info(f"Parent verified: {parent_uid}")

        # Fetch parent data
        parent_ref = db.reference(f"users/{parent_uid}")
        parent_data = parent_ref.get()
        if not parent_data:
            raise HTTPException(status_code=404, detail="Parent not found")
        
        if parent_data.get("user_details", {}).get("account_type") != "family":
            raise HTTPException(status_code=403, detail="Only family accounts can access child conversation summary")

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

        # Generate conversation summary
        summary = await generate_conversation_summary(voice_history, imp_questions, user_name, client)

        # Compile response
        response = {
            "status": "success",
            "conversation_summary": summary,
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
        logger.error(f"Error in conversation-summary endpoint: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

async def generate_conversation_summary(voice_history: List[dict], imp_questions: List[dict], user_name: str, client: AsyncOpenAI, max_retries: int = 2) -> str:
    # Validate voice history
    if not voice_history or not isinstance(voice_history, list):
        logger.warning("No valid voice history provided for conversation summary")
        return f"In the conversation with {user_name}, no valid conversation history was available to summarize. Topic: None"
    
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
        logger.warning("No valid messages or important questions found for conversation summary")
        return f"In the conversation with {user_name}, no valid messages or important questions were found to summarize. Topic: None"
    
    # Log the formatted voice history and imp_ask_question for debugging
    logger.info(f"Sending voice history to GPT for {user_name}: {json.dumps(formatted_messages, indent=2)}")
    logger.info(f"Sending imp_ask_question to GPT for {user_name}: {json.dumps(formatted_imp_questions, indent=2)}")

    # Construct detailed prompt for conversation summary
    summary_prompt = {
        "role": "system",
        "content": (
            f"You are an expert in summarizing conversations. Summarize the ENTIRE voice conversation history for {user_name} provided below, ensuring ALL messages and important question entries are considered. "
            f"Consider EVERY message in the voice history and important question replies individually and collectively to create a comprehensive summary. "
            f"The voice history includes messages with role ('user' or 'assistant'), content, timestamp, type, and optional category/subcategory fields. "
            f"The important question entries include significant questions asked by the assistant and the user's replies, with timestamps, which reflect responses to personalized, category-based questions. "
            f"The summary MUST start with 'In the conversation with {user_name}, the main topics discussed included' followed by a comma-separated list of up to three key topics discussed, integrated into the sentence. "
            f"Continue with two to three additional sentences (total 3-4 sentences) describing the main points, tone, context, and key interactions, emphasizing health, well-being, and emotional support where relevant. "
            f"Use the important question replies to highlight significant emotional or contextual insights. "
            f"After the summary, append a section with 'Topic: ' followed by a comma-separated list of up to three topics (e.g., 'Topic: 1. Topic 1, 2. Topic 2, 3. Topic 3'). "
            f"If no clear topics are identified, use 'None' in the topic list. "
            f"Return the summary as a plain string, enclosed in triple backticks with 'text' language identifier, like this:\n"
            f"```text\n"
            f"In the conversation with {user_name}, the main topics discussed included medication management, playing with a child, and health reminders. The tone was warm and encouraging, with the assistant offering support. The conversation emphasized health and well-being. Topic: 1. Medication management, 2. Playing with a child, 3. Health reminders\n"
            f"```\n"
            f"Voice conversation history:\n"
            f"{json.dumps(formatted_messages, indent=2)}\n"
            f"Important question entries:\n"
            f"{json.dumps(formatted_imp_questions, indent=2)}\n"
            f"Do not make assumptions beyond the provided history and question entries."
        )
    }
    
    # Send only the system prompt with formatted history
    messages = [summary_prompt]
    
    for attempt in range(max_retries + 1):
        try:
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=250,
                temperature=0.5
            )
            raw_response = response.choices[0].message.content
            logger.info(f"Raw GPT response for {user_name} (attempt {attempt + 1}): {raw_response}")

            # Attempt to extract string from code block
            text_match = re.search(r'```text\n(.*?)\n```', raw_response, re.DOTALL)
            if text_match:
                summary = text_match.group(1).strip()
                logger.info(f"Extracted summary for {user_name}: {summary}")
            else:
                summary = raw_response.strip()  # Fallback to raw response
                logger.warning(f"No text code block found in GPT response for {user_name}, using raw response")

            # Validate summary
            if not summary or not summary.startswith(f"In the conversation with {user_name}"):
                logger.error("Invalid GPT response: summary is empty or does not start with expected format")
                raise ValueError("Invalid response: summary must start with 'In the conversation with {user_name}'")
            
            logger.info(f"Conversation summary result for {user_name}: {summary}")
            return summary
        
        except ValueError as ve:
            logger.error(f"Summary validation error (attempt {attempt + 1}): {str(ve)}")
            if attempt == max_retries:
                return f"In the conversation with {user_name}, unable to summarize: {str(ve)}. Topic: None"
        except Exception as e:
            logger.error(f"Error generating summary (attempt {attempt + 1}): {str(e)}")
            if attempt == max_retries:
                return f"In the conversation with {user_name}, unable to summarize due to an error: {str(e)}. Topic: None"
# Helper function to calculate weights based on recent usage