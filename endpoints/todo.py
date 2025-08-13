from fastapi import APIRouter, HTTPException,Body
from models import TokenRequest,AddMultipleTodoTasksRequest,UpdateLinkedUserTodoTaskRequest,GetLinkedUserTodoListsRequest,UpdateMultipleTodoTasksRequest
from database import verify_user_token, fetch_user_data
from helpers import india_tz, generate_random_time, is_valid_three_word_task, is_reminder_in_period
from openai import AsyncOpenAI   
from firebase_admin.exceptions import FirebaseError
from firebase_admin import db
import os
import datetime
import logging
import json
import random
import re
import uuid
from typing import Optional
router = APIRouter()
logger = logging.getLogger(__name__)


# Initialize OpenAI client
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY not set in .env")
client = AsyncOpenAI(api_key=openai_api_key)



def get_accessible_uid(custom_uid: str, target_id: Optional[str], user_data: dict) -> str:
    if target_id:
        linked = user_data.get("linked", {})
        if target_id not in linked:
            raise HTTPException(status_code=403, detail="You are not linked to this user.")
        return target_id
    return custom_uid

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
    

@router.post("/add-todo-task")
async def add_todo_task(req: AddMultipleTodoTasksRequest):
    """Add multiple custom to-do tasks for the user (up to 7), or for a linked user."""
    try:
        logger.debug(f"Received request: {req.dict()}")
        
        # Verify user token
        custom_uid = verify_user_token(req.idToken)
        if not custom_uid:
            logger.error("Invalid token provided")
            raise HTTPException(status_code=401, detail="Invalid token")

        # Fetch user data
        user_data = fetch_user_data(custom_uid)
        if not user_data:
            logger.error(f"User data not found for UID: {custom_uid}")
            raise HTTPException(status_code=404, detail="User data not found")

        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)
        logger.debug(f"Effective UID: {effective_uid}")

        now = datetime.datetime.now(india_tz).isoformat()
        current_date = datetime.datetime.now(india_tz).date()
        logger.debug(f"Current date: {current_date.isoformat()}, Current time: {now}")

        if not req.tasks or len(req.tasks) > 7:
            logger.error(f"Invalid number of tasks: {len(req.tasks)}")
            raise HTTPException(status_code=400, detail="You must provide between 1 and 7 tasks.")

        # Test Firebase connectivity
        try:
            test_ref = db.reference("test_connectivity")
            test_ref.set({"timestamp": now})
            logger.debug("Firebase connectivity test successful")
        except FirebaseError as e:
            logger.error(f"Firebase connectivity test failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to connect to Firebase: {str(e)}")

        saved_tasks = []

        def get_next_dates_for_weekdays(weekdays, start_date, num_weeks=4):
            weekday_map = {
                "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6
            }
            if not weekdays:
                logger.warning(f"No weekdays provided for recurring task. Using start_date: {start_date.isoformat()}")
                return [start_date]
            weekdays_idx = [weekday_map.get(d[:3].lower()) for d in weekdays if d[:3].lower() in weekday_map]
            if not weekdays_idx:
                logger.warning(f"Invalid weekdays provided: {weekdays}. Using start_date: {start_date.isoformat()}")
                return [start_date]
            dates = []
            for i in range(num_weeks * 7):
                date = start_date + datetime.timedelta(days=i)
                if date.weekday() in weekdays_idx:
                    dates.append(date)
            return dates if dates else [start_date]

        for task in req.tasks:
            logger.debug(f"Processing task: {task.title}")
            
            # Validate created_at_time
            task_date = current_date
            if task.created_at_time:
                try:
                    task_date = datetime.datetime.fromisoformat(task.created_at_time).date()
                    logger.debug(f"Using created_at_time {task.created_at_time} for task {task.title}")
                except ValueError as e:
                    logger.error(f"Invalid created_at_time format for task {task.title}: {str(e)}")
                    raise HTTPException(status_code=400, detail=f"Invalid created_at_time format for task {task.title}. Must be ISO 8601 (e.g., '2025-08-12T09:00:00+05:30').")

            if task.recurring:
                recurring_group_id = str(uuid.uuid4())
                recurring_dates = get_next_dates_for_weekdays(task.recurring, task_date)
                logger.info(f"Recurring task {task.title} scheduled for dates: {[d.isoformat() for d in recurring_dates]}")
                for date in recurring_dates:
                    task_id = str(uuid.uuid4())
                    task_data = {
                        "title": task.title,
                        "description": task.description,
                        "status": task.status or "pending",
                        "created_at_time": task.created_at_time ,
                        "updated_at_time": task.updated_at_time ,
                        "completed_at_time": task.completed_at_time,
                        "priority": task.priority or "medium",
                        "task_id": task_id,
                        "time": task.time,
                        "catagory": task.catagory,
                        "recurring": task.recurring,
                        "recurring_group_id": recurring_group_id
                    }
                    try:
                        todo_ref = db.reference(f"users/{effective_uid}/custom_todo_lists/{date.isoformat()}/{task_id}")
                        logger.debug(f"Writing task {task_id} to Firebase path: {todo_ref.path}")
                        todo_ref.set(task_data)
                        saved_tasks.append(task_data)
                        logger.info(f"Task {task_id} saved for {date.isoformat()}")
                    except FirebaseError as e:
                        logger.error(f"Firebase write failed for task {task_id} on {date.isoformat()}: {str(e)}")
                        raise HTTPException(status_code=500, detail=f"Failed to save task {task_id} on {date.isoformat()}: {str(e)}")
            else:
                task_id = task.task_id or str(uuid.uuid4())
                task_data = {
                    "title": task.title,
                    "description": task.description,
                    "status": task.status or "pending",
                    "created_at_time": task.created_at_time ,
                    "updated_at_time": task.updated_at_time ,
                    "completed_at_time": task.completed_at_time,
                    "priority": task.priority or "medium",
                    "task_id": task_id,
                    "time": task.time,
                    "catagory": task.catagory,
                    "recurring": task.recurring
                }
                try:
                    todo_ref = db.reference(f"users/{effective_uid}/custom_todo_lists/{task_date.isoformat()}/{task_id}")
                    logger.debug(f"Writing task {task_id} to Firebase path: {todo_ref.path}")
                    todo_ref.set(task_data)
                    saved_tasks.append(task_data)
                    logger.info(f"Task {task_id} saved for {task_date.isoformat()}")
                except FirebaseError as e:
                    logger.error(f"Firebase write failed for task {task_id} on {task_date.isoformat()}: {str(e)}")
                    raise HTTPException(status_code=500, detail=f"Failed to save task {task_id} on {task_date.isoformat()}: {str(e)}")

        if not saved_tasks:
            logger.warning("No tasks were saved to the database.")
            raise HTTPException(status_code=500, detail="No tasks were saved to the database. Check logs for details.")

        logger.debug(f"Returning {len(saved_tasks)} saved tasks")
        return {
            "status": "success",
            "tasks": saved_tasks
        }
    except Exception as e:
        logger.error(f"Error in add-todo-task endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    
#No need of this any more
@router.post("/get-all-todo-lists")
async def get_all_todo_lists(req: GetLinkedUserTodoListsRequest):
    """
    Fetch all custom to-do lists for the user (all dates).
    """
    try:
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        # This will return the UID for which data should be fetched (either self or target_id)
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        todo_lists_ref = db.reference(f"users/{effective_uid}/custom_todo_lists")

        all_lists = todo_lists_ref.get() or {}

        # Format: [{ "date": date, "tasks": [ ... ] }, ...]
        result = []
        for date, tasks in all_lists.items():
            if isinstance(tasks, dict):
                result.append({
                    "date": date,
                    "tasks": list(tasks.values())
                })

        return {
            "status": "success",
            "todo_lists": result
        }
    except Exception as e:
        logger.error(f"Error in get-all-todo-lists endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
@router.post("/update-todo-task")
async def update_todo_task(req: UpdateMultipleTodoTasksRequest):
    """Update multiple to-do tasks for the user or a linked user."""
    try:
        logger.debug(f"Received request: {req.dict()}")
        
        custom_uid = verify_user_token(req.idToken)
        if not custom_uid:
            logger.error("Invalid token provided")
            raise HTTPException(status_code=401, detail="Invalid token")

        user_data = fetch_user_data(custom_uid)
        if not user_data:
            logger.error(f"User data not found for UID: {custom_uid}")
            raise HTTPException(status_code=404, detail="User data not found")

        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)
        logger.debug(f"Effective UID: {effective_uid}")

        now = datetime.datetime.now(india_tz).isoformat()
        logger.debug(f"Current time: {now}")

        if not req.tasks or len(req.tasks) > 7:
            logger.error(f"Invalid number of tasks: {len(req.tasks)}")
            raise HTTPException(status_code=400, detail="You must provide between 1 and 7 tasks to update.")

        # Test Firebase connectivity
        try:
            test_ref = db.reference("test_connectivity")
            test_ref.set({"timestamp": now})
            logger.debug("Firebase connectivity test successful")
        except FirebaseError as e:
            logger.error(f"Firebase connectivity test failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to connect to Firebase: {str(e)}")

        updated_tasks = []

        for task in req.tasks:
            logger.debug(f"Processing task update: task_id={task.task_id}, date={task.date}")

            try:
                # Validate date format
                datetime.datetime.fromisoformat(task.date.replace('Z', '+00:00'))
            except ValueError as e:
                logger.error(f"Invalid date format for task {task.task_id}: {str(e)}")
                raise HTTPException(status_code=400, detail=f"Invalid date format for task {task.task_id}. Must be YYYY-MM-DD (e.g., '2025-08-13').")

            task_ref = db.reference(f"users/{effective_uid}/custom_todo_lists/{task.date}/{task.task_id}")
            task_data = task_ref.get()
            if not task_data:
                logger.error(f"Task not found for ID {task.task_id} on {task.date}")
                raise HTTPException(status_code=404, detail=f"Task not found for ID {task.task_id} on {task.date}")

            # Update fields using payload directly
            update_data = task.dict(exclude={'task_id', 'date'}, exclude_none=True)
            if not update_data.get('updated_at_time'):
                update_data['updated_at_time'] = now
            task_data.update(update_data)

            try:
                task_ref.set(task_data)
                updated_tasks.append(task_data)
                logger.info(f"Task {task.task_id} updated on {task.date}")
            except FirebaseError as e:
                logger.error(f"Firebase write failed for task {task.task_id} on {task.date}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Failed to update task {task.task_id} on {task.date}: {str(e)}")

        if not updated_tasks:
            logger.warning("No tasks were updated in the database.")
            raise HTTPException(status_code=500, detail="No tasks were updated in the database. Check logs for details.")

        logger.debug(f"Returning {len(updated_tasks)} updated tasks")
        return {
            "status": "success",
            "tasks": updated_tasks
        }
    except Exception as e:
        logger.error(f"Error in update-todo-task endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

async def delete_todo_task(req: GetLinkedUserTodoListsRequest):
    """
    Delete a specific to-do task for the user by date and task_id.
    """
    try:
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        # This will return the UID for which data should be fetched (either self or target_id)
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        task_ref = db.reference(f"users/{effective_uid}/custom_todo_lists/{req.date}/{req.task_id}")
        task_data = task_ref.get()
        if not task_data:
            raise HTTPException(status_code=404, detail="Task not found")

        task_ref.delete()

        return {
            "status": "success",
            "message": f"Task {req.task_id} deleted successfully."
        }
    except Exception as e:
        logger.error(f"Error in delete-todo-task endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
@router.post("/get-upcoming-todo-tasks")
async def get_upcoming_todo_tasks(req: GetLinkedUserTodoListsRequest):
    """
    Fetch all to-do tasks for the user for today and future dates.
    """
    try:
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        # This will return the UID for which data should be fetched (either self or target_id)
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        todo_lists_ref = db.reference(f"users/{effective_uid}/custom_todo_lists")
        all_lists = todo_lists_ref.get() or {}

        current_date = datetime.datetime.now(india_tz).date().isoformat()
        result = []

        for date, tasks in all_lists.items():
            # Include tasks from today and future dates
            if isinstance(tasks, dict) and date >= current_date:
                result.append({
                    "date": date,
                    "tasks": list(tasks.values())
                })

        # Sort by date for chronological order
        result.sort(key=lambda x: x["date"])

        return {
            "status": "success",
            "todo_lists": result
        }
    except Exception as e:
        logger.error(f"Error in get-upcoming-todo-tasks endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/get-completed-todo-tasks")
async def get_completed_todo_tasks(req: GetLinkedUserTodoListsRequest):
    """
    Fetch all completed to-do tasks for the user.
    """
    try:
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        # This will return the UID for which data should be fetched (either self or target_id)
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        todo_lists_ref = db.reference(f"users/{effective_uid}/custom_todo_lists")
        all_lists = todo_lists_ref.get() or {}

        result = []
        for date, tasks in all_lists.items():
            if isinstance(tasks, dict):
                completed_tasks = [task for task in tasks.values() if task.get("status") == "completed"]
                if completed_tasks:
                    result.append({
                        "date": date,
                        "tasks": completed_tasks
                    })

        # Sort by date for chronological order
        result.sort(key=lambda x: x["date"])

        return {
            "status": "success",
            "todo_lists": result
        }
    except Exception as e:
        logger.error(f"Error in get-completed-todo-tasks endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/get-missed-todo-tasks")
async def get_missed_todo_tasks(req: GetLinkedUserTodoListsRequest):
    """
    Fetch all missed to-do tasks for the user (tasks before today that are not completed).
    """
    try:
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        # This will return the UID for which data should be fetched (either self or target_id)
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        todo_lists_ref = db.reference(f"users/{effective_uid}/custom_todo_lists")
        all_lists = todo_lists_ref.get() or {}

        current_date = datetime.datetime.now(india_tz).date().isoformat()
        result = []

        for date, tasks in all_lists.items():
            # Consider tasks from past dates only
            if isinstance(tasks, dict) and date < current_date:
                # Missed tasks are those not marked as completed
                missed_tasks = [task for task in tasks.values() if task.get("status") != "completed"]
                if missed_tasks:
                    result.append({
                        "date": date,
                        "tasks": missed_tasks
                    })

        # Sort by date for chronological order
        result.sort(key=lambda x: x["date"])

        return {
            "status": "success",
            "todo_lists": result
        }
    except Exception as e:
        logger.error(f"Error in get-missed-todo-tasks endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
