from fastapi import APIRouter, HTTPException
from models import TokenRequest
from database import verify_user_token, fetch_user_data
from helpers import india_tz, generate_random_time, is_valid_three_word_task, is_reminder_in_period
from openai import AsyncOpenAI
from firebase_admin import db
import os
import datetime
import logging
import json
import random
import re

router = APIRouter()
logger = logging.getLogger(__name__)

# Initialize OpenAI client
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY not set in .env")
client = AsyncOpenAI(api_key=openai_api_key)

@router.post("/generate-todo")
async def generate_todo(req: TokenRequest):
    """Generate a personalized to-do list for the user for the current date."""
    try:
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        if not user_data:
            raise HTTPException(status_code=404, detail="User not found")

        if user_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can use generate-todo")

        user_details = user_data.get("user_details", {})
        user_name = user_details.get("name", "there")
        hobbies = user_details.get("hobby", "no hobbies specified")
        age = user_details.get("age", "unknown")
        blood_group = user_details.get("bloodGroup", None)
        medical_history = user_details.get("medicalHistory", None)
        relation = user_details.get("relation", None)
        selected_interests = user_details.get("selectedInterests", [])
        dietary_preference = user_details.get("dietaryPreference", None)
        allergies = user_details.get("allergies", [])
        weight = user_details.get("weight", None)
        height = user_details.get("height", None)
        medicines = user_data.get("health_track", {}).get("medicines", [])
        medicine_reminders = user_data.get("health_track", {}).get("medicine_reminders", [])
        chat_history = user_data.get("chat", {}).get("history", [])

        current_time = datetime.datetime.now(india_tz)
        current_date = current_time.date().isoformat()

        # Prepare user details for the prompt
        medicines_summary = (
            ", ".join([f"{med.get('medicine_name', 'unknown')} ({med.get('dosage', 'unknown')})" 
                       for med in medicines if isinstance(med, dict)]) if medicines else "no medications recorded"
        )
        reminders_summary = (
            ", ".join([f"{rem.get('medicine_name', 'unknown')} at {rem.get('time', 'unknown')}" 
                       for rem in medicine_reminders if isinstance(rem, dict) and rem.get("time")]) 
            if medicine_reminders else "no reminders set"
        )
        medical_context = ""
        if medical_history:
            medical_context += f"Their medical history includes: {medical_history}. "
        if blood_group:
            medical_context += f"Their blood group is: {blood_group}. "
        if relation:
            medical_context += f"They are a {relation} to the primary user. "
        if weight:
            medical_context += f"Their weight is {weight}. "
        if height:
            medical_context += f"Their height is {height}. "
        interests_summary = ", ".join(selected_interests) if selected_interests else "no interests specified"
        dietary_summary = f"Their dietary preference is: {dietary_preference or 'none specified'}. "
        allergies_summary = f"Their allergies include: {', '.join(allergies) if allergies else 'none specified'}. "

        # Initialize to-do lists
        todo_lists = {
            "morning": [],
            "evening": [],
            "night": []
        }
        used_tasks = set()

        # Define time periods
        periods = {
            "morning": (5, 12),
            "evening": (12, 18),
            "night": (18, 24)
        }

        # Process each period
        for period, (start_hour, end_hour) in periods.items():
            # Check for medication reminders in this period
            med_task = None
            med_time = None
            for reminder in medicine_reminders:
                if not isinstance(reminder, dict) or not reminder.get("time"):
                    continue
                reminder_time = reminder.get("time")
                if is_reminder_in_period(reminder_time, start_hour, end_hour):
                    med_task = f"Take {reminder.get('medicine_name', 'medication')} dose"
                    med_time = reminder_time
                    used_tasks.add(med_task)
                    todo_lists[period].append({"to-do-list": med_task, "time": med_time})
                    break

            # Generate remaining tasks (3 total per period)
            tasks_needed = 3 - len(todo_lists[period])
            new_tasks = []
            if tasks_needed > 0:
                prompt = {
                    "role": "system",
                    "content": (
                        f"You are a caring, empathetic best friend for {user_name}, who is {age} years old and enjoys {hobbies}. "
                        f"{medical_context}"
                        f"Their interests include: {interests_summary}. "
                        f"{dietary_summary}{allergies_summary}"
                        f"They are taking: {medicines_summary}. "
                        f"Their reminders are: {reminders_summary}. "
                        f"The current time is {current_time.strftime('%H:%M')}. "
                        f"The recent conversation history is: {json.dumps(chat_history[-5:])}. "
                        f"Generate {tasks_needed} unique to-do tasks for the {period} period (from {start_hour}:00 to {end_hour}:00) to create a personalized to-do list. "
                        f"Each task must: "
                        f"1. Be exactly three words long (e.g., 'Paint small sketch', 'Listen music playlist'). "
                        f"2. Be engaging, positive, and tailored to the user’s hobbies, interests, dietary preferences, allergies, or recent chat history. "
                        f"3. Have a distinct intent/theme from all previously generated tasks: {json.dumps(list(used_tasks))}. "
                        f"4. Be relevant to the time of day (e.g., morning: energizing tasks, evening: relaxing tasks, night: winding down tasks). "
                        f"5. Avoid medication-related tasks, as these are handled separately. "
                        f"6. Consider allergies (e.g., avoid outdoor tasks if pollen allergy is present) and dietary preferences (e.g., suggest vegan tasks if dietary preference is vegan). "
                        f"Examples: "
                        f"- Morning: 'Try yoga routine' "
                        f"- Evening: 'Call friend now' "
                        f"- Night: 'Watch favorite movie' "
                        f"Return a JSON array of {tasks_needed} task strings, each exactly three words."
                    )
                }
                try:
                    response = await client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[prompt]
                    )
                    response_content = response.choices[0].message.content
                    # Strip code block markers if present
                    response_content = re.sub(r'^```json\n|\n```$', '', response_content.strip())
                    new_tasks = json.loads(response_content)
                    # Validate tasks
                    new_tasks = [task for task in new_tasks if is_valid_three_word_task(task) and task not in used_tasks]
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON response from gpt-4o-mini: {response_content}, error: {str(e)}")
                    new_tasks = []
                except Exception as e:
                    logger.error(f"Error generating tasks: {str(e)}")
                    new_tasks = []

                # Assign random times to valid tasks and add to list
                for task_text in new_tasks[:tasks_needed]:
                    time = generate_random_time(start_hour, end_hour)
                    todo_lists[period].append({"to-do-list": task_text, "time": time})
                    used_tasks.add(task_text)

            # Ensure exactly 3 tasks by filling with generic tasks if needed
            while len(todo_lists[period]) < 3:
                generic_tasks = {
                    "morning": ["Eat healthy breakfast", "Read book chapter", "Do light exercise"],
                    "evening": ["Call friend now", "Try new recipe", "Listen music playlist"],
                    "night": ["Watch favorite movie", "Write journal entry", "Relax with tea"]
                }
                available_generics = [t for t in generic_tasks[period] if t not in used_tasks]
                if available_generics:
                    task = random.choice(available_generics)
                    time = generate_random_time(start_hour, end_hour)
                    todo_lists[period].append({"to-do-list": task, "time": time})
                    used_tasks.add(task)
                else:
                    break

        # Store to-do lists in Firebase
        todo_ref = db.reference(f"users/{custom_uid}/todo_lists/{current_date}")
        todo_ref.set({
            "tasks": todo_lists,
            "generated_at": current_time.isoformat()
        })

        return {
            "status": "success",
            "todo_lists": todo_lists,
            "timestamp": current_time.isoformat()
        }

    except Exception as e:
        logger.error(f"Error in generate-todo endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")