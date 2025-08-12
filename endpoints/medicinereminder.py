from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from database import verify_user_token, fetch_user_data
from helpers import india_tz, generate_random_time, is_valid_three_word_task, is_reminder_in_period
from firebase_admin import db
import os
import datetime
import logging
import json
import random
import re
import uuid
from typing import Optional, List
import calendar
from models import (
    MedicineReminder,
    AddMedicineReminderRequest,
    UpdateMedicineReminderRequest,
    GetLinkedUserTodoListsRequest
)
router = APIRouter()
logger = logging.getLogger(__name__)



def get_accessible_uid(custom_uid: str, target_id: Optional[str], user_data: dict) -> str:
    if target_id:
        linked = user_data.get("linked", {})
        if target_id not in linked:
            raise HTTPException(status_code=403, detail="You are not linked to this user.")
        return target_id
    return custom_uid

@router.post("/add-medicine-reminder")
async def add_medicine_reminder(req: AddMedicineReminderRequest):
    """Add multiple medicine reminders for the user (up to 7), or for a linked user."""
    try:
        custom_uid = verify_user_token(req.idToken)
        if not custom_uid:
            raise HTTPException(status_code=401, detail="Invalid token")

        user_data = fetch_user_data(custom_uid)
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        now = datetime.datetime.now(india_tz).isoformat()

        if not req.reminders or len(req.reminders) > 7:
            raise HTTPException(status_code=400, detail="You must provide between 1 and 7 reminders.")

        saved_reminders = []

        for reminder in req.reminders:
            # Determine start date
            if reminder.start_from_today:
                start_date = datetime.datetime.now(india_tz).date()
            elif reminder.reminder_date:
                start_date = datetime.datetime.strptime(reminder.reminder_date, '%Y-%m-%d').date()
            else:
                raise HTTPException(status_code=400, detail="Must provide reminder_date or set start_from_today to True.")

            # Determine end date
            if reminder.end_date:
                end_date = datetime.datetime.strptime(reminder.end_date, '%Y-%m-%d').date()
            else:
                end_date = start_date + datetime.timedelta(weeks=4)

            # Generate dates
            dates = []
            current_date = start_date
            while current_date <= end_date:
                if reminder.recurring:
                    day_abbr = calendar.day_abbr[current_date.weekday()].lower()[:3]
                    if day_abbr in [d.lower()[:3] for d in reminder.recurring]:
                        dates.append(current_date)
                else:
                    dates.append(current_date)
                    break  # Non-recurring only once
                current_date += datetime.timedelta(days=1)

            recurring_group_id = str(uuid.uuid4()) if reminder.recurring else None

            for date in dates:
                reminder_id = str(uuid.uuid4())
                reminder_data = reminder.dict(exclude_none=True)
                reminder_data["reminder_id"] = reminder_id
                reminder_data["created_at_time"] = reminder_data.get("created_at_time", now)
                reminder_data["updated_at_time"] = reminder_data.get("updated_at_time", now)
                reminder_data["reminder_date"] = date.isoformat()
                if recurring_group_id:
                    reminder_data["recurring_group_id"] = recurring_group_id

                reminder_ref = db.reference(f"users/{effective_uid}/medicine_reminders/{date.isoformat()}/{reminder_id}")
                reminder_ref.set(reminder_data)
                saved_reminders.append(reminder_data)

        return {
            "status": "success",
            "reminders": saved_reminders
        }
    except Exception as e:
        logger.error(f"Error in add-medicine-reminder endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

#No need of this any more
@router.post("/get-all-medicine-reminders")
async def get_all_medicine_reminders(req: GetLinkedUserTodoListsRequest):
    """
    Fetch all medicine reminders for the user (all dates).
    """
    try:
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

        return {
            "status": "success",
            "reminder_lists": result
        }
    except Exception as e:
        logger.error(f"Error in get-all-medicine-reminders endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/update-medicine-reminder")
async def update_medicine_reminder(req: UpdateMedicineReminderRequest):
    """
    Update any field of a specific medicine reminder except reminder_id.
    """
    try:
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        reminder_ref = db.reference(f"users/{effective_uid}/medicine_reminders/{req.date}/{req.reminder_id}")

        reminder_data = reminder_ref.get()
        if not reminder_data:
            raise HTTPException(status_code=404, detail="Reminder not found")

        # Update all fields except reminder_id
        for field in ["medicine_name", "pill_details", "end_date", "amount_per_box", "initial_quantity", "time", "current_quantity", "reminder_date", "start_from_today", "take_medicine_alert", "ring_phone", "send_message", "refill_reminder", "set_refill_date", "set_day_before_refill", "recurring", "status"]:
            value = getattr(req, field, None)
            if value is not None:
                reminder_data[field] = value

        reminder_ref.set(reminder_data)

        return {
            "status": "success",
            "reminder": reminder_data
        }
    except Exception as e:
        logger.error(f"Error in update-medicine-reminder endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/delete-medicine-reminder")
async def delete_medicine_reminder(req: GetLinkedUserTodoListsRequest):
    """
    Delete a specific medicine reminder for the user by date and reminder_id.
    """
    try:
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        reminder_ref = db.reference(f"users/{effective_uid}/medicine_reminders/{req.date}/{req.reminder_id}")
        reminder_data = reminder_ref.get()
        if not reminder_data:
            raise HTTPException(status_code=404, detail="Reminder not found")

        reminder_ref.delete()

        return {
            "status": "success",
            "message": f"Reminder {req.reminder_id} deleted successfully."
        }
    except Exception as e:
        logger.error(f"Error in delete-medicine-reminder endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/get-upcoming-medicine-reminders")
async def get_upcoming_medicine_reminders(req: GetLinkedUserTodoListsRequest):
    """
    Fetch all medicine reminders for the user for today and future dates.
    """
    try:
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

        return {
            "status": "success",
            "reminder_lists": result
        }
    except Exception as e:
        logger.error(f"Error in get-upcoming-medicine-reminders endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/get-completed-medicine-reminders")
async def get_completed_medicine_reminders(req: GetLinkedUserTodoListsRequest):
    """
    Fetch all completed medicine reminders for the user.
    """
    try:
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

        return {
            "status": "success",
            "reminder_lists": result
        }
    except Exception as e:
        logger.error(f"Error in get-completed-medicine-reminders endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/get-missed-medicine-reminders")
async def get_missed_medicine_reminders(req: GetLinkedUserTodoListsRequest):
    """
    Fetch all missed medicine reminders for the user (reminders before today that are not completed).
    """
    try:
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

        return {
            "status": "success",
            "reminder_lists": result
        }
    except Exception as e:
        logger.error(f"Error in get-missed-medicine-reminders endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")