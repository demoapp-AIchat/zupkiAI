
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from database import verify_user_token, fetch_user_data
from helpers import india_tz
from models import AddMedicineReminderRequest, GetLinkedUserTodoListsRequest, UpdateMultipleMedicineRemindersRequest,DeleteMedicineRequest
from firebase_admin import db
from firebase_admin.exceptions import FirebaseError
import datetime
import logging
import uuid
import calendar
from typing import Optional, List

router = APIRouter()
logger = logging.getLogger(__name__)

# Configure logging to show detailed messages
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')


def get_accessible_uid(custom_uid: str, target_id: Optional[str], user_data: dict) -> str:
    logger.debug(f"Checking accessible UID: custom_uid={custom_uid}, target_id={target_id}")
    if target_id:
        linked = user_data.get("linked", {})
        if target_id not in linked:
            logger.error(f"User {custom_uid} not linked to target_id {target_id}")
            raise HTTPException(status_code=403, detail="You are not linked to this user.")
        return target_id
    return custom_uid

@router.post("/add-medicine-reminder")
async def add_medicine_reminder(req: AddMedicineReminderRequest):
    """Add multiple medicine reminders for the user (up to 7), or for a linked user."""
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
        current_date = datetime.datetime.now(india_tz).date()
        logger.debug(f"Current date: {current_date.isoformat()}, Current time: {now}")

        if not req.reminders or len(req.reminders) > 7:
            logger.error(f"Invalid number of reminders: {len(req.reminders)}")
            raise HTTPException(status_code=400, detail="You must provide between 1 and 7 reminders.")

        # Test Firebase connectivity
        try:
            test_ref = db.reference("test_connectivity")
            test_ref.set({"timestamp": now})
            logger.debug("Firebase connectivity test successful")
        except FirebaseError as e:
            logger.error(f"Firebase connectivity test failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to connect to Firebase: {str(e)}")

        saved_reminders = []

        def get_reminder_dates(reminder, start_date, end_date):
            """Generate dates for reminders based on recurring field."""
            weekday_map = {
                "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6
            }
            if not reminder.recurring:
                return [start_date]
            weekdays_idx = [weekday_map.get(d[:3].lower()) for d in reminder.recurring if d[:3].lower() in weekday_map]
            if not weekdays_idx:
                logger.warning(f"Invalid weekdays provided for reminder {reminder.medicine_name}: {reminder.recurring}. Using start_date: {start_date.isoformat()}")
                return [start_date]
            dates = []
            current_date_iter = start_date
            while current_date_iter <= end_date:
                if current_date_iter.weekday() in weekdays_idx:
                    dates.append(current_date_iter)
                current_date_iter += datetime.timedelta(days=1)
            return dates if dates else [start_date]

        for reminder in req.reminders:
            logger.debug(f"Processing reminder: {reminder.medicine_name}")

            # Determine start date
            start_date = current_date
            if reminder.start_from_today:
                start_date = current_date
                logger.debug(f"Using start_from_today for reminder {reminder.medicine_name}")
            elif reminder.reminder_date:
                try:
                    start_date = datetime.datetime.fromisoformat(reminder.reminder_date).date()
                    logger.debug(f"Using reminder_date {reminder.reminder_date} for reminder {reminder.medicine_name}")
                except ValueError as e:
                    logger.error(f"Invalid reminder_date format for reminder {reminder.medicine_name}: {str(e)}")
                    raise HTTPException(status_code=400, detail=f"Invalid reminder_date format for reminder {reminder.medicine_name}. Must be ISO 8601 (e.g., '2025-08-13T09:00:00+05:30').")

            # Determine end date
            end_date = start_date + datetime.timedelta(weeks=4)
            if reminder.end_date:
                try:
                    end_date = datetime.datetime.fromisoformat(reminder.end_date).date()
                    logger.debug(f"Using end_date {reminder.end_date} for reminder {reminder.medicine_name}")
                except ValueError as e:
                    logger.error(f"Invalid end_date format for reminder {reminder.medicine_name}: {str(e)}")
                    raise HTTPException(status_code=400, detail=f"Invalid end_date format for reminder {reminder.medicine_name}. Must be ISO 8601 (e.g., '2025-08-20T00:00:00+05:30').")

            # Generate dates for reminders
            reminder_dates = get_reminder_dates(reminder, start_date, end_date)
            logger.info(f"Reminder {reminder.medicine_name} scheduled for dates: {[d.isoformat() for d in reminder_dates]}")

            recurring_group_id = str(uuid.uuid4()) if reminder.recurring else None

            for date in reminder_dates:
                reminder_id = str(uuid.uuid4())
                # Use payload directly with minimal modifications
                reminder_data = reminder.dict(exclude={'reminder_id'})
                reminder_data['reminder_id'] = reminder_id
                if recurring_group_id:
                    reminder_data['recurring_group_id'] = recurring_group_id
                if not reminder_data.get('updated_at_time'):
                    reminder_data['updated_at_time'] = now

                try:
                    reminder_ref = db.reference(f"users/{effective_uid}/medicine_reminders/{date.isoformat()}/{reminder_id}")
                    logger.debug(f"Writing reminder {reminder_id} to Firebase path: {reminder_ref.path}")
                    reminder_ref.set(reminder_data)
                    saved_reminders.append(reminder_data)
                    logger.info(f"Reminder {reminder_id} saved for {date.isoformat()}")
                except FirebaseError as e:
                    logger.error(f"Firebase write failed for reminder {reminder_id} on {date.isoformat()}: {str(e)}")
                    raise HTTPException(status_code=500, detail=f"Failed to save reminder {reminder_id} on {date.isoformat()}: {str(e)}")

        if not saved_reminders:
            logger.warning("No reminders were saved to the database.")
            raise HTTPException(status_code=500, detail="No reminders were saved to the database. Check logs for details.")

        logger.debug(f"Returning {len(saved_reminders)} saved reminders")
        return {
            "status": "success",
            "reminders": saved_reminders
        }
    except Exception as e:
        logger.error(f"Error in add-medicine-reminder endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/get-all-medicine-reminders")
async def get_all_medicine_reminders(req: GetLinkedUserTodoListsRequest):
    """Fetch all medicine reminders for the user (all dates)."""
    try:
        logger.debug(f"Received request: {req.dict()}")
        
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        reminders_ref = db.reference(f"users/{effective_uid}/medicine_reminders")
        all_lists = reminders_ref.get() or {}

        result = []
        for date, reminders in all_lists.items():
            if isinstance(reminders, dict):
                result.append({
                    "date": date,
                    "reminders": list(reminders.values())
                })

        result.sort(key=lambda x: x["date"])
        logger.debug(f"Returning {len(result)} reminder lists")
        return {
            "status": "success",
            "reminder_lists": result
        }
    except Exception as e:
        logger.error(f"Error in get-all-medicine-reminders endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/update-medicine-reminder")
async def update_medicine_reminder(req: UpdateMultipleMedicineRemindersRequest):
    """Update multiple medicine reminders for the user or a linked user."""
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

        if not req.reminders or len(req.reminders) > 7:
            logger.error(f"Invalid number of reminders: {len(req.reminders)}")
            raise HTTPException(status_code=400, detail="You must provide between 1 and 7 reminders to update.")

        # Test Firebase connectivity
        try:
            test_ref = db.reference("test_connectivity")
            test_ref.set({"timestamp": now})
            logger.debug("Firebase connectivity test successful")
        except FirebaseError as e:
            logger.error(f"Firebase connectivity test failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to connect to Firebase: {str(e)}")

        updated_reminders = []

        for reminder in req.reminders:
            logger.debug(f"Processing reminder update: reminder_id={reminder.reminder_id}, date={reminder.date}")

            try:
                # Validate date format
                datetime.datetime.fromisoformat(reminder.date.replace('Z', '+00:00'))
            except ValueError as e:
                logger.error(f"Invalid date format for reminder {reminder.reminder_id}: {str(e)}")
                raise HTTPException(status_code=400, detail=f"Invalid date format for reminder {reminder.reminder_id}. Must be YYYY-MM-DD (e.g., '2025-08-13').")

            reminder_ref = db.reference(f"users/{effective_uid}/medicine_reminders/{reminder.date}/{reminder.reminder_id}")
            reminder_data = reminder_ref.get()
            if not reminder_data:
                logger.error(f"Reminder not found for ID {reminder.reminder_id} on {reminder.date}")
                raise HTTPException(status_code=404, detail=f"Reminder not found for ID {reminder.reminder_id} on {reminder.date}")

            # Update fields using payload directly
            update_data = reminder.dict(exclude={'reminder_id', 'date'}, exclude_none=True)
            if not update_data.get('updated_at_time'):
                update_data['updated_at_time'] = now
            reminder_data.update(update_data)

            try:
                reminder_ref.set(reminder_data)
                updated_reminders.append(reminder_data)
                logger.info(f"Reminder {reminder.reminder_id} updated on {reminder.date}")
            except FirebaseError as e:
                logger.error(f"Firebase write failed for reminder {reminder.reminder_id} on {reminder.date}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Failed to update reminder {reminder.reminder_id} on {reminder.date}: {str(e)}")

        if not updated_reminders:
            logger.warning("No reminders were updated in the database.")
            raise HTTPException(status_code=500, detail="No reminders were updated in the database. Check logs for details.")

        logger.debug(f"Returning {len(updated_reminders)} updated reminders")
        return {
            "status": "success",
            "reminders": updated_reminders
        }
    except Exception as e:
        logger.error(f"Error in update-medicine-reminder endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/delete-medicine-reminder")
async def delete_medicine_reminder(req: DeleteMedicineRequest):
    """Delete a specific medicine reminder for the user by date and reminder_id."""
    try:
        logger.debug(f"Received request: {req.dict()}")
        
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        reminder_ref = db.reference(f"users/{effective_uid}/medicine_reminders/{req.date}/{req.reminder_id}")
        reminder_data = reminder_ref.get()
        if not reminder_data:
            logger.error(f"Reminder not found for ID {req.reminder_id} on {req.date}")
            raise HTTPException(status_code=404, detail="Reminder not found")

        try:
            reminder_ref.delete()
            logger.info(f"Reminder {req.reminder_id} deleted on {req.date}")
        except FirebaseError as e:
            logger.error(f"Firebase delete failed for reminder {req.reminder_id} on {req.date}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to delete reminder {req.reminder_id}: {str(e)}")

        return {
            "status": "success",
            "message": f"Reminder {req.reminder_id} deleted successfully."
        }
    except Exception as e:
        logger.error(f"Error in delete-medicine-reminder endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/get-upcoming-medicine-reminders")
async def get_upcoming_medicine_reminders(req: GetLinkedUserTodoListsRequest):
    """Fetch all medicine reminders for the user for today and future dates."""
    try:
        logger.debug(f"Received request: {req.dict()}")
        
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        reminders_ref = db.reference(f"users/{effective_uid}/medicine_reminders")
        all_lists = reminders_ref.get() or {}

        current_date = datetime.datetime.now(india_tz).date().isoformat()
        result = []

        for date, reminders in all_lists.items():
            if isinstance(reminders, dict) and date >= current_date:
                result.append({
                    "date": date,
                    "reminders": list(reminders.values())
                })

        result.sort(key=lambda x: x["date"])
        logger.debug(f"Returning {len(result)} upcoming reminder lists")
        return {
            "status": "success",
            "reminder_lists": result
        }
    except Exception as e:
        logger.error(f"Error in get-upcoming-medicine-reminders endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/get-completed-medicine-reminders")
async def get_completed_medicine_reminders(req: GetLinkedUserTodoListsRequest):
    """Fetch all completed medicine reminders for the user."""
    try:
        logger.debug(f"Received request: {req.dict()}")
        
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        reminders_ref = db.reference(f"users/{effective_uid}/medicine_reminders")
        all_lists = reminders_ref.get() or {}

        result = []
        for date, reminders in all_lists.items():
            if isinstance(reminders, dict):
                completed_reminders = [reminder for reminder in reminders.values() if reminder.get("status") == "completed"]
                if completed_reminders:
                    result.append({
                        "date": date,
                        "reminders": completed_reminders
                    })

        result.sort(key=lambda x: x["date"])
        logger.debug(f"Returning {len(result)} completed reminder lists")
        return {
            "status": "success",
            "reminder_lists": result
        }
    except Exception as e:
        logger.error(f"Error in get-completed-medicine-reminders endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/get-missed-medicine-reminders")
async def get_missed_medicine_reminders(req: GetLinkedUserTodoListsRequest):
    """Fetch all missed medicine reminders for the user (reminders before today that are not completed)."""
    try:
        logger.debug(f"Received request: {req.dict()}")
        
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        reminders_ref = db.reference(f"users/{effective_uid}/medicine_reminders")
        all_lists = reminders_ref.get() or {}

        current_date = datetime.datetime.now(india_tz).date().isoformat()
        result = []

        for date, reminders in all_lists.items():
            if isinstance(reminders, dict) and date < current_date:
                missed_reminders = [reminder for reminder in reminders.values() if reminder.get("status") != "completed"]
                if missed_reminders:
                    result.append({
                        "date": date,
                        "reminders": missed_reminders
                    })

        result.sort(key=lambda x: x["date"])
        logger.debug(f"Returning {len(result)} missed reminder lists")
        return {
            "status": "success",
            "reminder_lists": result
        }
    except Exception as e:
        logger.error(f"Error in get-missed-medicine-reminders endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
