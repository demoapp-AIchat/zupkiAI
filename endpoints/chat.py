from fastapi import APIRouter, HTTPException, Query
from models import ChatRequest, ChatResponse, ProactiveTalkRequest, ProactiveTalkResponse, TokenRequest
from database import verify_user_token, fetch_user_data, fetch_voice_history, fetch_imp_questions
from helpers import get_time_based_greeting, format_reminder_list, is_list_reminders_request, is_within_one_hour, is_exact_reminder_time, is_after_reminder_time, is_refill_date_near, calculate_weights, CATEGORIES_WITH_SUBCATEGORIES, MAX_MESSAGE_LENGTH, MAX_HISTORY_LENGTH, india_tz
from openai import AsyncOpenAI
from firebase_admin import db, messaging
import os
import datetime
import json
import random
import logging
from dotenv import load_dotenv
import aiohttp
import re

router = APIRouter()
logger = logging.getLogger(__name__)

# Load .env file explicitly
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# Initialize OpenAI client
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY not set in .env")
client = AsyncOpenAI(api_key=openai_api_key)

async def get_latest_weather(custom_uid):
    """Fetch the latest weather data for the user from Firebase."""
    weather_ref = db.reference(f"users/{custom_uid}/current_weather")
    weather_data = weather_ref.get()
    if not weather_data:
        logger.warning(f"No weather data found for user {custom_uid}")
    return weather_data if weather_data else None

def is_weather_related(message):
    """Analyze if the message is related to weather."""
    if not message:
        return False
    weather_keywords = ['weather', 'temperature', 'rain', 'sun', 'cloud', 'wind', 'storm', 'forecast', 'humid', 'climate']
    message_lower = message.lower()
    return any(keyword in message_lower for keyword in weather_keywords)

def is_field_related(message):
    """Analyze if the message is related to specific user fields."""
    if not message:
        return False, None
    message_lower = message.lower()
    field_keywords = {
        'blood_group': ['blood group', 'blood type'],
        'medical_history': ['medical history', 'health history', 'past illness', 'medical condition'],
        'relation': ['relation', 'relationship', 'family role'],
        'interests': ['interests', 'hobbies', 'likes', 'favorite activities'],
        'dietary_preference': ['diet', 'dietary preference', 'food preference', 'eating habits'],
        'allergies': ['allergies', 'allergic', 'allergy']
    }
    for field, keywords in field_keywords.items():
        if any(keyword in message_lower for keyword in keywords):
            return True, field
    return False, None

def get_system_prompt(user_name, age, hobbies, medicines_summary, health_metrics_summary, weather_context, blood_group, medical_history, relation, interests_summary, dietary_preference, allergies_summary, message=None, is_proactive=False):
    """Generate system prompt based on message type."""
    base_context = (
        f"You are a caring, empathetic assistant for {user_name}, who is {age} years old and enjoys {hobbies}. "
        f"Their blood group is {blood_group or 'unknown'}. "
        f"Their medical history includes: {medical_history or 'none'}. "
        f"They are a {relation or 'unknown relation'} to the primary user. "
        f"Their interests include: {interests_summary or 'none specified'}. "
        f"Their dietary preference is: {dietary_preference or 'none specified'}. "
        f"Their allergies include: {allergies_summary or 'none specified'}. "
        f"They are taking the following medications: {medicines_summary}. "
        f"Their recent health metrics include: {health_metrics_summary}. {weather_context}"
    )
    is_field_query, field_name = is_field_related(message)
    if is_field_query:
        field_instructions = {
            'blood_group': f"If asked about blood group, respond with: 'Your blood group is {blood_group or 'not specified'}.'",
            'medical_history': f"If asked about medical history, respond with: 'Your medical history includes {medical_history or 'no recorded conditions'}.'",
            'relation': f"If asked about relation, respond with: 'You are a {relation or 'not specified'} to the primary user.'",
            'interests': f"If asked about interests, respond with: 'Your interests include {interests_summary or 'no interests specified'}.'",
            'dietary_preference': f"If asked about dietary preference, respond with: 'Your dietary preference is {dietary_preference or 'not specified'}.'",
            'allergies': f"If asked about allergies, respond with: 'Your allergies include {allergies_summary or 'no allergies specified'}.'"
        }
        return {
            "role": "system",
            "content": (
                f"{base_context} "
                f"The user has asked about their {field_name.replace('_', ' ')}. "
                f"{field_instructions[field_name]} "
                f"Provide a concise, caring response tailored to their profile (e.g., age, hobbies, dietary preferences). "
                f"Do not include greetings unless explicitly asked."
            )
        }
    elif is_weather_related(message):
        return {
            "role": "system",
            "content": (
                f"{base_context} "
                f"Since the user asked about weather, provide a detailed and relevant response based on the current weather data. "
                f"Include suggestions (e.g., clothing, indoor activities if allergies like {allergies_summary} are relevant) tailored to their interests ({interests_summary}) and dietary preferences ({dietary_preference}). "
                f"Keep it caring, personalized, and avoid medical advice unless related to their medications or health metrics. "
                f"Do not include greetings unless explicitly asked."
            )
        }
    else:
        if is_proactive:
            return {
                "role": "system",
                "content": (
                    f"{base_context} "
                    f"Generate a thoughtful response or question based on the context, using their personal details like interests ({interests_summary}), dietary preferences ({dietary_preference}), allergies ({allergies_summary}), medications, or health metrics to make it relevant and personalized. "
                    f"Ensure it’s warm, supportive, and feels like it’s from a best friend, focusing on their needs or interests. "
                    f"Do NOT ask a question unless appropriate for proactive interaction."
                )
            }
        else:
            return {
                "role": "system",
                "content": (
                    f"{base_context} "
                    f"Use this information to make responses relevant and caring, such as commenting on their interests ({interests_summary}), dietary preferences ({dietary_preference}), allergies ({allergies_summary}), medications, or health metrics. "
                    f"Provide weather-related advice only if asked. Do not include greetings unless explicitly asked."
                )
            }

@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Handle chat interactions with personalized responses."""
    try:
        custom_uid = verify_user_token(req.idToken)
       
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        
        if not user_data:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Fetch user details, medicines, and health metrics
        user_data = fetch_user_data(custom_uid)
        user_details = user_data.get("user_details", {})
        user_name = user_details.get("name", "there")
        hobbies = user_details.get("hobby", "unknown")
        age = user_details.get("age", "unknown")
        blood_group = user_details.get("bloodGroup", None)
        medical_history = user_details.get("medicalHistory", None)
        relation = user_details.get("relation", None)
        selected_interests = user_details.get("selectedInterests", [])
        dietary_preference = user_details.get("dietaryPreference", None)
        allergies = user_details.get("allergies", [])
        medicines = user_data.get("health_track", {}).get("medicines", [])
        health_metrics = user_data.get("health_track", {}).get("health_metrics", [])
        medicines_summary = (
            ", ".join([f"{med.get('medicine_name', 'unknown')} ({med.get('dosage', 'unknown')})" 
                       for med in medicines]) if medicines else "no medications recorded"
        )
        health_metrics_summary = (
            ", ".join([f"{metric.get('metric', 'unknown')}: {metric.get('data', 'unknown')}" 
                       for metric in health_metrics]) if health_metrics else "no health metrics recorded"
        )
        interests_summary = ", ".join(selected_interests) if selected_interests else "no interests specified"
        allergies_summary = ", ".join(allergies) if allergies else "no allergies specified"
        
        chat_ref = db.reference(f"users/{custom_uid}/chat")
        chat = chat_ref.get() or {"history": [], "greeted": False}
        chat_history = chat.get("history", [])
        
        # Normalize chat_history to ensure Dict[str, str]
        normalized_history = []
        for entry in chat_history:
            normalized_entry = {
                "role": str(entry.get("role", "")),
                "content": str(entry.get("content", "")),
                "timestamp": str(entry.get("timestamp", ""))
            }
            # Only include valid entries
            if all(k in ["role", "content", "timestamp"] for k in entry.keys()) or all(isinstance(v, str) for v in entry.values()):
                normalized_history.append(normalized_entry)
            else:
                logger.warning(f"Skipping invalid chat entry for {custom_uid}: {entry}")
        
        should_greet = not chat_history or (req.message and req.message.lower() == "hello")
        greeting = get_time_based_greeting(user_name)
        
        if should_greet and not chat.get("greeted", False):
            normalized_history.append({
                "role": "assistant",
                "content": greeting,
                "timestamp": datetime.datetime.now(india_tz).isoformat()
            })
            chat["greeted"] = True
        
        assistant_message = ""
        if req.message:
            if len(req.message) > MAX_MESSAGE_LENGTH:
                raise HTTPException(
                    status_code=400,
                    detail=f"Message exceeds maximum length of {MAX_MESSAGE_LENGTH} characters"
                )
            normalized_history.append({
                "role": "user",
                "content": req.message,
                "timestamp": datetime.datetime.now(india_tz).isoformat()
            })
            
            if len(normalized_history) > MAX_HISTORY_LENGTH:
                normalized_history = normalized_history[-MAX_HISTORY_LENGTH:]
            
            # Fetch latest weather data
            weather_data = await get_latest_weather(custom_uid)
            weather_context = (
                f"The current weather is: temperature {weather_data.get('temperature', 'unknown')}°C, "
                f"windspeed {weather_data.get('windspeed', 'unknown')} km/h, weathercode {weather_data.get('weathercode', 'unknown')} "
                f"at latitude {weather_data.get('latitude', 'unknown')} and longitude {weather_data.get('longitude', 'unknown')}. "
                if weather_data else "No recent weather data available."
            )
            
            system_prompt = get_system_prompt(
                user_name, age, hobbies, medicines_summary, health_metrics_summary, weather_context,
                blood_group, medical_history, relation, interests_summary, dietary_preference, allergies_summary, req.message
            )
            
            messages = [system_prompt] + normalized_history
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages
            )
            assistant_message = response.choices[0].message.content
            normalized_history.append({
                "role": "assistant",
                "content": assistant_message,
                "timestamp": datetime.datetime.now(india_tz).isoformat()
            })
            
            # Save normalized history back to Firebase
            chat["history"] = normalized_history
            chat_ref.set(chat)
        
        response_message = greeting if should_greet and not chat.get("greeted", False) else assistant_message or normalized_history[-1]["content"] if normalized_history else "No conversation history"
        
        return ChatResponse(
            status="success",
            response=response_message,
            chat_history=normalized_history
        )
    
    except Exception as e:
        logger.error(f"Error in chat: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))
@router.post("/proactive-talk", response_model=ProactiveTalkResponse)
async def proactive_talk(req: ProactiveTalkRequest):
    """Handle proactive conversations with users, including reminders and personalized questions."""
    try:
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        user_details = user_data.get("user_details", {})
        if user_details.get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can use proactive talk")
        user_name = user_details.get("name", "there")
        hobbies = user_details.get("hobby", "no hobbies specified")
        age = user_details.get("age", "unknown")
        blood_group = user_details.get("bloodGroup", None)
        medical_history = user_details.get("medicalHistory", None)
        relation = user_details.get("relation", None)
        selected_interests = user_details.get("selectedInterests", [])
        dietary_preference = user_details.get("dietaryPreference", None)
        allergies = user_details.get("allergies", [])
        health_track = user_data.get("health_track", {})
        medicines = health_track.get("medicines", [])
        health_metrics = health_track.get("health_metrics", [])
        medicine_reminders = health_track.get("medicine_reminders", [])
        medicines_summary = (
            ", ".join([f"{med.get('medicine_name', 'unknown')} ({med.get('dosage', 'unknown')})" 
                       for med in medicines if isinstance(med, dict)]) if medicines else "no medications recorded"
        )
        health_metrics_summary = (
            ", ".join([f"{metric.get('metric', 'unknown')}: {metric.get('data', 'unknown')}" 
                       for metric in health_metrics]) if health_metrics else "no health metrics recorded"
        )
        interests_summary = ", ".join(selected_interests) if selected_interests else "no interests specified"
        allergies_summary = ", ".join(allergies) if allergies else "no allergies specified"
        voice_data = fetch_voice_history(custom_uid)
        voice_history = voice_data.get("history", [])
        asked_reminders = voice_data.get("asked_reminders", {})
        category_usage = voice_data.get("category_usage", {})
        subcategory_usage = voice_data.get("subcategory_usage", {})
        imp_questions_data = fetch_imp_questions(custom_uid)
        imp_questions = imp_questions_data.get("entries", [])
        current_time = datetime.datetime.now(india_tz)
        current_date = current_time.date().isoformat()
        # Fetch latest weather data
        weather_data = await get_latest_weather(custom_uid)
        weather_context = (
            f"The current weather is: temperature {weather_data.get('temperature', 'unknown')}°C, "
            f"windspeed {weather_data.get('windspeed', 'unknown')} km/h, weathercode {weather_data.get('weathercode', 'unknown')} "
            f"at latitude {weather_data.get('latitude', 'unknown')} and longitude {weather_data.get('longitude', 'unknown')}. "
            if weather_data else "No recent weather data available."
        )
        response_content = None
        response_key = "response"
        is_category_question = False
        selected_category = None
        selected_subcategory = None
        last_question = voice_history[-1].get("content", "") if voice_history and voice_history[-1].get("role") == "assistant" and voice_history[-1].get("type") == "question" else None
        last_question_is_category = voice_history[-1].get("is_category_question", False) if voice_history and voice_history[-1].get("role") == "assistant" and voice_history[-1].get("type") == "question" else False
        if req.reply and req.reply.strip():
            if len(req.reply) > MAX_MESSAGE_LENGTH:
                raise HTTPException(
                    status_code=400,
                    detail=f"Reply exceeds maximum length of {MAX_MESSAGE_LENGTH} characters"
                )
            voice_history.append({
                "role": "user",
                "content": req.reply,
                "timestamp": current_time.isoformat(),
                "type": "response"
            })
            if len(voice_history) > MAX_HISTORY_LENGTH:
                voice_history = voice_history[-MAX_HISTORY_LENGTH:]
            if last_question and last_question_is_category:
                imp_questions.append({
                    "question": last_question,
                    "reply": req.reply,
                    "question_timestamp": voice_history[-2].get("timestamp", current_time.isoformat()) if len(voice_history) >= 2 else current_time.isoformat(),
                    "reply_timestamp": current_time.isoformat()
                })
                imp_questions_data["entries"] = imp_questions
                db.reference(f"users/{custom_uid}/imp_ask_question").set(imp_questions_data)
            if is_list_reminders_request(req.reply):
                response_content = format_reminder_list(medicine_reminders)
                voice_history.append({
                    "role": "assistant",
                    "content": response_content,
                    "timestamp": current_time.isoformat(),
                    "type": "response"
                })
                voice_data["history"] = voice_history
                db.reference(f"users/{custom_uid}/voice_history").set(voice_data)
                return ProactiveTalkResponse(
                    status="success",
                    response=response_content,
                    timestamp=current_time.isoformat()
                )
            # Generate response for reply
            system_prompt = get_system_prompt(
                user_name, age, hobbies, medicines_summary, health_metrics_summary, weather_context,
                blood_group, medical_history, relation, interests_summary, dietary_preference, allergies_summary, req.reply, is_proactive=True
            )
            messages = [system_prompt] + voice_history[-5:]  # Use recent history
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages
            )
            response_content = response.choices[0].message.content
            response_key = "response"
        else:
            medication_question = None
            for reminder in medicine_reminders:
                if not isinstance(reminder, dict) or not reminder.get("time"):
                    continue
                reminder_id = reminder.get("reminder_id", str(hash(reminder.get("medicine_name", "") + reminder.get("time", ""))))
                reminder_time = reminder.get("time")
                asked_today = asked_reminders.get(reminder_id, {}).get("date") == current_date
                if is_within_one_hour(reminder_time, current_time) and not asked_reminders.get(reminder_id, {}).get("within_hour_asked"):
                    if is_exact_reminder_time(reminder_time, current_time):
                        medication_question = f"Hey {user_name}, it’s time for your {reminder.get('medicine_name', 'medication')}. Have you taken it yet?"
                        asked_reminders[reminder_id] = asked_reminders.get(reminder_id, {})
                        asked_reminders[reminder_id]["within_hour_asked"] = True
                        asked_reminders[reminder_id]["date"] = current_date
                        break
                elif is_after_reminder_time(reminder_time, current_time) and not asked_reminders.get(reminder_id, {}).get("post_reminder_asked"):
                    medication_question = f"Hi {user_name}, did you take your {reminder.get('medicine_name', 'medication')} earlier today at {reminder_time}?"
                    asked_reminders[reminder_id] = asked_reminders.get(reminder_id, {})
                    asked_reminders[reminder_id]["post_reminder_asked"] = True
                    asked_reminders[reminder_id]["date"] = current_date
                    break
            if medication_question:
                response_content = medication_question
                response_key = "question"
            else:
                refill_question = None
                for reminder in medicine_reminders:
                    if not isinstance(reminder, dict) or not reminder.get("set_refill_date"):
                        continue
                    reminder_id = reminder.get("reminder_id", str(hash(reminder.get("medicine_name", "") + reminder.get("time", ""))))
                    if is_refill_date_near(reminder.get("set_refill_date"), current_time) and not asked_reminders.get(reminder_id, {}).get("refill_asked"):
                        refill_question = f"Hey {user_name}, your {reminder.get('medicine_name', 'medication')} is due for a refill soon. Have you planned to get it refilled?"
                        asked_reminders[reminder_id] = asked_reminders.get(reminder_id, {})
                        asked_reminders[reminder_id]["refill_asked"] = True
                        asked_reminders[reminder_id]["date"] = current_date
                        break
                if refill_question:
                    response_content = refill_question
                    response_key = "question"
                else:
                    categories = list(CATEGORIES_WITH_SUBCATEGORIES.keys())
                    category_weights = calculate_weights(categories, category_usage)
                    selected_category = random.choices(categories, weights=category_weights, k=1)[0]
                    subcategories = CATEGORIES_WITH_SUBCATEGORIES[selected_category]
                    subcategory_weights = calculate_weights(subcategories, subcategory_usage)
                    selected_subcategory = random.choices(subcategories, weights=subcategory_weights, k=1)[0]
                    category_usage[selected_category] = category_usage.get(selected_category, 0) + 1
                    subcategory_usage[selected_subcategory] = subcategory_usage.get(selected_subcategory, 0) + 1
                    system_prompt = get_system_prompt(
                        user_name, age, hobbies, medicines_summary, health_metrics_summary, weather_context,
                        blood_group, medical_history, relation, interests_summary, dietary_preference, allergies_summary, is_proactive=True
                    )
                    question_prompt = {
                        "role": "system",
                        "content": (
                            f"{system_prompt['content']} "
                            f"Generate a single, engaging, casual question for the category '{selected_category}' and subcategory '{selected_subcategory}' to interact with {user_name} as a best friend would. "
                            f"Ensure the question: "
                            f"1. Is strictly relevant to the category '{selected_category}' and subcategory '{selected_subcategory}'. "
                            f"2. Is light, friendly, and personal, encouraging them to share about their day, feelings, or experiences. "
                            f"3. Uses their interests (e.g., {interests_summary}), dietary preferences ({dietary_preference}), allergies ({allergies_summary}), hobbies, age, or medical history (if relevant) to personalize the question. "
                            f"4. Is completely unique and distinct from previous questions in the conversation history: {json.dumps(voice_history[-5:])}. "
                            f"Return only the question as a string."
                        )
                    }
                    messages = [question_prompt] + voice_history[-5:]  # Use recent history
                    question_response = await client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=messages
                    )
                    response_content = question_response.choices[0].message.content
                    response_key = "question"
                    is_category_question = True
        voice_data["history"] = voice_history
        voice_data["asked_reminders"] = asked_reminders
        voice_data["category_usage"] = category_usage
        voice_data["subcategory_usage"] = subcategory_usage
        db.reference(f"users/{custom_uid}/voice_history").set(voice_data)
        voice_history.append({
            "role": "assistant",
            "content": response_content,
            "timestamp": current_time.isoformat(),
            "type": response_key,
            "is_category_question": is_category_question,
            "category": selected_category if is_category_question else None,
            "subcategory": selected_subcategory if is_category_question else None
        })
        if len(voice_history) > MAX_HISTORY_LENGTH:
            voice_history = voice_history[-MAX_HISTORY_LENGTH:]
        voice_data["history"] = voice_history
        db.reference(f"users/{custom_uid}/voice_history").set(voice_data)
        push_token = user_data.get("push_token")
        if push_token:
            try:
                message = messaging.Message(
                    notification=messaging.Notification(
                        title="Proactive Talk",
                        body=response_content
                    ),
                    token=push_token
                )
                messaging.send(message)
            except Exception as notify_err:
                logger.error(f"Failed to send notification: {notify_err}")
        return ProactiveTalkResponse(
            status="success",
            response=response_content,
            timestamp=current_time.isoformat()
        )
    except Exception as e:
        logger.error(f"Error in proactive talk: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

async def schedule_daily_question():
    """Schedule a daily question for each child user and send push notifications."""
    try:
        users_ref = db.reference("users")
        users_data = users_ref.get()
        if not users_data:
            logger.info("No users found for scheduling daily questions")
            return
        current_time = datetime.datetime.now(india_tz)
        for custom_uid, user_data in users_data.items():
            try:
                user_details = user_data.get("user_details", {})
                if user_details.get("account_type") != "child":
                    continue
                user_name = user_details.get("name", "there")
                hobbies = user_details.get("hobby", "no hobbies specified")
                age = user_details.get("age", "unknown")
                blood_group = user_details.get("bloodGroup", None)
                medical_history = user_details.get("medicalHistory", None)
                relation = user_details.get("relation", None)
                selected_interests = user_details.get("selectedInterests", [])
                dietary_preference = user_details.get("dietaryPreference", None)
                allergies = user_details.get("allergies", [])
                health_track = user_data.get("health_track", {})
                medicines = health_track.get("medicines", [])
                medicine_reminders = health_track.get("medicine_reminders", [])
                voice_data = fetch_voice_history(custom_uid)
                voice_history = voice_data.get("history", [])
                category_usage = voice_data.get("category_usage", {})
                subcategory_usage = voice_data.get("subcategory_usage", {})
                medicines_summary = (
                    ", ".join([f"{med.get('medicine_name', 'unknown')} ({med.get('dosage', 'unknown')})" 
                               for med in medicines if isinstance(med, dict)]) if medicines else "no medications recorded"
                )
                reminders_summary = (
                    ", ".join([f"{rem.get('medicine_name', 'unknown')} at {rem.get('time', 'unknown')}" 
                               for rem in medicine_reminders if isinstance(rem, dict) and rem.get("time")]) if medicine_reminders else "no reminders set"
                )
                interests_summary = ", ".join(selected_interests) if selected_interests else "no interests specified"
                allergies_summary = ", ".join(allergies) if allergies else "no allergies specified"
                categories = list(CATEGORIES_WITH_SUBCATEGORIES.keys())
                category_weights = calculate_weights(categories, category_usage)
                selected_category = random.choices(categories, weights=category_weights, k=1)[0]
                subcategories = CATEGORIES_WITH_SUBCATEGORIES[selected_category]
                subcategory_weights = calculate_weights(subcategories, subcategory_usage)
                selected_subcategory = random.choices(subcategories, weights=subcategory_weights, k=1)[0]
                category_usage[selected_category] = category_usage.get(selected_category, 0) + 1
                subcategory_usage[selected_subcategory] = subcategory_usage.get(selected_subcategory, 0) + 1
                question_prompt = {
                    "role": "system",
                    "content": (
                        f"You are a caring, empathetic best friend for {user_name}, who is {age} years old and enjoys {hobbies}. "
                        f"Their blood group is {blood_group or 'unknown'}. "
                        f"Their medical history includes: {medical_history or 'none'}. "
                        f"They are a {relation or 'unknown relation'} to the primary user. "
                        f"Their interests include: {interests_summary}. "
                        f"Their dietary preference is: {dietary_preference or 'none specified'}. "
                        f"Their allergies include: {allergies_summary}. "
                        f"They are taking the following medications: {medicines_summary}. "
                        f"Their medicine reminders are: {reminders_summary}. "
                        f"The current time is {current_time.strftime('%H:%M')}. "
                        f"The recent conversation history is: {json.dumps(voice_history[-5:])}. "
                        f"Generate a single, engaging, casual question for the category '{selected_category}' and subcategory '{selected_subcategory}' to interact with {user_name} as a best friend would. "
                        f"Ensure the question: "
                        f"1. Is strictly relevant to the category '{selected_category}' and subcategory '{selected_subcategory}'. "
                        f"2. Is light, friendly, and personal, encouraging them to share about their day, feelings, or experiences. "
                        f"3. Uses their interests (e.g., {interests_summary}), dietary preferences ({dietary_preference}), allergies ({allergies_summary}), hobbies, age, or medical history (if relevant) to personalize the question. "
                        f"4. Is completely unique and distinct from previous questions in the conversation history. "
                        f"Return only the question as a string."
                    )
                }
                question_response = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[question_prompt]
                )
                question = question_response.choices[0].message.content
                voice_history.append({
                    "role": "assistant",
                    "content": question,
                    "timestamp": current_time.isoformat(),
                    "type": "question",
                    "is_category_question": True,
                    "category": selected_category,
                    "subcategory": selected_subcategory
                })
                if len(voice_history) > MAX_HISTORY_LENGTH:
                    voice_history = voice_history[-MAX_HISTORY_LENGTH:]
                voice_data["history"] = voice_history
                voice_data["category_usage"] = category_usage
                voice_data["subcategory_usage"] = subcategory_usage
                db.reference(f"users/{custom_uid}/voice_history").set(voice_data)
                push_token = user_data.get("push_token")
                if push_token:
                    try:
                        message = messaging.Message(
                            notification=messaging.Notification(
                                title="Daily Check-In",
                                body=question
                            ),
                            token=push_token
                        )
                        messaging.send(message)
                    except Exception as notify_err:
                        logger.error(f"Failed to send notification to {custom_uid}: {notify_err}")
            except Exception as user_err:
                logger.error(f"Error processing user {custom_uid} for daily question: {user_err}")
                continue
    except Exception as e:
        logger.error(f"Error in schedule_daily_question: {str(e)}")

@router.post("/weather")
async def get_weather(
    weather_request: dict = None
):
    """Fetch weather data for given latitude and longitude and overwrite existing data."""
    try:
        if not weather_request or not isinstance(weather_request, dict):
            raise HTTPException(status_code=400, detail="Invalid request body")
        
        idToken = weather_request.get("idToken")
        latitude = weather_request.get("latitude")
        longitude = weather_request.get("longitude")

        if not idToken or latitude is None or longitude is None:
            raise HTTPException(status_code=400, detail="idToken, latitude, and longitude are required")

        # Validate token before proceeding
        try:
            custom_uid = verify_user_token(idToken)
        except Exception as e:
            logger.error(f"Token verification failed: {str(e)}")
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        # Construct URL with proper parameters
        url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current_weather=true"
        logger.info(f"Calling weather API with URL: {url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                logger.info(f"API response status: {response.status}")
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"API error response: {error_text}")
                    raise HTTPException(status_code=500, detail=f"Failed to fetch weather data: {error_text}")
                weather_data = await response.json()

        current_weather = weather_data.get("current_weather", {})
        if not current_weather:
            raise HTTPException(status_code=500, detail="No weather data available in response")

        weather_entry = {
            "timestamp": datetime.datetime.now(india_tz).isoformat(),
            "temperature": current_weather.get("temperature"),
            "windspeed": current_weather.get("windspeed"),
            "weathercode": current_weather.get("weathercode"),
            "latitude": latitude,
            "longitude": longitude
        }

        # Overwrite the single weather entry
        weather_ref = db.reference(f"users/{custom_uid}/current_weather")
        weather_ref.set(weather_entry)
        logger.info(f"Weather data updated for user {custom_uid}: {weather_entry}")

        return {
            "status": "success",
            "weather": weather_entry
        }
    except Exception as e:
        logger.error(f"Error in get_weather: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))