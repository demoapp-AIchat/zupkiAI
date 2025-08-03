from fastapi import APIRouter, HTTPException
from models import MedicineReminder, DeleteReminderRequest, ReminderResponseRequest, LinkChildRequest,TokenRequest
from database import verify_user_token, fetch_user_data
from firebase_admin import db, messaging
from helpers import format_reminder_list
import datetime
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/save-medicine-reminder")
def save_medicine_reminder(req: MedicineReminder):
    """Save a medicine reminder for a child account."""
    try:
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        if user_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can save reminders")
        user_ref = db.reference(f"users/{custom_uid}")
        reminder_ref = user_ref.child("health_track/medicine_reminders")
        existing = reminder_ref.get()
        current_length = len(existing) if existing else 0
        clean_data = {k: v for k, v in req.dict().items() if v is not None and k != "idToken"}
        next_index = str(current_length)
        reminder_ref.child(next_index).set(clean_data)
        return {"status": "success", "message": "Medicine reminder saved successfully"}
    except Exception as e:
        logger.error(f"Error saving medicine reminder: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/get-medicine-reminders")
def get_medicine_reminders(req: TokenRequest):
    """Fetch medicine reminders for a child account."""
    try:
        custom_uid = verify_user_token(req.idToken)
        user_ref = db.reference(f"users/{custom_uid}")

        # Check if user is a child
        user_data = user_ref.get()
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can save medicines")
        # Reference to medicine reminders node
        reminder_ref = user_ref.child("health_track/medicine_reminders")
        reminders = reminder_ref.get()

        # Return empty list if no reminders exist
        if not reminders:
            return {"status": "success", "reminders": []}

        # Handle both dict and list cases
        reminders_list = []
        if isinstance(reminders, dict):
         reminders_list = [reminders[key] for key in reminders if reminders[key] is not None]
        elif isinstance(reminders, list):
            reminders_list = [r for r in reminders if r is not None]
        else:
            logger.warning(f"Unexpected data type for reminders: {type(reminders)}")
            return {"status": "success", "reminders": []}

        return {"status": "success", "reminders": reminders_list}

    except Exception as e:
        logger.error(f"Error retrieving medicine reminders: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/get-child-reminders")
def get_child_medicine_reminders(req: LinkChildRequest):
    """Fetch medicine reminders for a linked child."""
    try:
        parent_uid = verify_user_token(req.idToken)
      
        # Step 2: Check parent account type
        parent_data = db.reference(f"users/{parent_uid}").get()
        if not parent_data or parent_data.get("user_details", {}).get("account_type") != "family":
            raise HTTPException(status_code=403, detail="Only family accounts can access this")

        # Step 3: Check if request was approved
        status_ref = db.reference(f"users/{parent_uid}/sent_requests/{req.child_id}/status")
        status = status_ref.get()
        if status != "approved":
            raise HTTPException(status_code=403, detail="Child link request not approved or not sent")

        # Step 4: Validate child exists and is a child account
        child_ref = db.reference(f"users/{req.child_id}")
        child_data = child_ref.get()
        if not child_data or child_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=404, detail="Child ID not found or not a child account")

        # Step 5: Get reminders
        reminders = child_data.get("health_track", {}).get("medicine_reminders", {})
        reminder_list = []
        if isinstance(reminders, dict):
            reminder_list = [reminders[k] for k in reminders]
        elif isinstance(reminders, list):
            reminder_list = reminders

        return {
            "status": "success",
            "child_name": child_data.get("user_details", {}).get("name", "Unknown"),
            "reminders": reminder_list
        }

    except Exception as e:
        logger.error(f"Error fetching child reminders: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
@router.delete("/delete-medicine-reminder")
def delete_medicine_reminder(req: DeleteReminderRequest):
    """Delete a medicine reminder by ID."""
    try:
        custom_uid = verify_user_token(req.idToken)
        
        # Get user data
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()

        # Ensure user is a child
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can delete medicine reminders")

        # Reference to medicine reminders
        reminder_ref = user_ref.child("health_track/medicine_reminders")
        reminders = reminder_ref.get() or []

        # Handle both list and dict cases
        if isinstance(reminders, dict):
            reminders_list = [reminders[k] for k in reminders if reminders[k] is not None]
        else:
            reminders_list = [r for r in reminders if r is not None]

        # Try to remove the reminder with matching ID
        found = False
        updated_reminders = []
        for reminder in reminders_list:
            if reminder and reminder.get("reminder_id") != req.reminder_id:
                updated_reminders.append(reminder)
            elif reminder and reminder.get("reminder_id") == req.reminder_id:
                found = True  # Mark as found

        if not found:
            raise HTTPException(status_code=404, detail="Reminder not found")

        # Overwrite the full reminders list with compacted one
        reminder_ref.set(updated_reminders)

        return {"status": "success", "message": f"Reminder '{req.reminder_id}' deleted and list adjusted successfully"}

    except Exception as e:
        logger.error(f"Error deleting medicine reminder: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/get-child-reminders-with-status")
def get_child_medicine_reminders_with_status(req: LinkChildRequest):
    """Fetch medicine reminders with response status for a linked child."""
    try:
        parent_uid = verify_user_token(req.idToken)
       
        # Step 2: Check parent account type
        parent_data = db.reference(f"users/{parent_uid}").get()
        if not parent_data or parent_data.get("user_details", {}).get("account_type") != "family":
            raise HTTPException(status_code=403, detail="Only family accounts can access this")

        # Step 3: Get child data
        child_ref = db.reference(f"users/{req.child_id}")
        child_data = child_ref.get()
        if not child_data or child_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=404, detail="Child not found or not a child account")

        # Step 4: Get reminders and responses
        reminders_data = child_data.get("health_track", {}).get("medicine_reminders", {})
        responses_data = child_data.get("health_track", {}).get("medicine_responses", {})

        reminder_list = []

        # Step 5: Normalize reminders
        if isinstance(reminders_data, dict):
            normalized_reminders = [reminders_data[k] for k in reminders_data if reminders_data[k] is not None]
        elif isinstance(reminders_data, list):
            normalized_reminders = [r for r in reminders_data if r is not None]
        else:
            normalized_reminders = []

        # Step 6: Attach latest response
        for reminder in normalized_reminders:
            reminder_id = reminder.get("reminder_id", "unknown")
            latest_response = "no response"

            # Try to fetch latest response from response history
            if reminder_id in responses_data and isinstance(responses_data[reminder_id], list):
                resp_list = responses_data[reminder_id]
                if resp_list:
                    latest_response = resp_list[-1].get("response", "no response")

            reminder["response"] = latest_response
            reminder_list.append(reminder)

        return {
            "status": "success",
            "child_name": child_data.get("user_details", {}).get("name", "Unknown"),
            "reminders": reminder_list
        }

    except Exception as e:
        logger.error(f"Error in get-child-reminders-with-status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/save-reminder-response")
def save_reminder_response(req: ReminderResponseRequest):
    """Save a response to a medicine reminder and notify parents."""
    try:
        custom_uid = verify_user_token(req.idToken)
       
        # Step 2: Confirm user is child
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can respond")

        # Step 3: Save response under child's record
        response_ref = user_ref.child(f"health_track/medicine_responses/{req.reminder_id}")
        existing_responses = response_ref.get() or []

        new_entry = {
            "medicine_name": req.medicine_name,
            "response": req.response,
            "timestamp": datetime.datetime.now().isoformat()
        }

        existing_responses.append(new_entry)
        response_ref.set(existing_responses)

        # Step 4: Notify parents if push_token exists
        parents_ref = user_ref.child("parents")
        parent_ids = parents_ref.get() or {}

        for parent_uid in parent_ids:
            parent_data = db.reference(f"users/{parent_uid}").get()
            push_token = parent_data.get("push_token")

            if push_token:
                try:
                    message = messaging.Message(
                        notification=messaging.Notification(
                            title="Medicine Reminder Update",
                            body=f"{user_data['user_details'].get('name', 'Your child')} responded: {req.response} to {req.medicine_name}"
                        ),
                        token=push_token
                    )
                    messaging.send(message)
                except Exception as notify_err:
                    logger.error(f"Failed to send notification to {parent_uid}: {notify_err}")

        return {"status": "success", "message": "Response saved and parents notified"}

    except Exception as e:
        logger.error(f"Error saving reminder response: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/medication-adherence-summary")
def get_medication_adherence_summary(req: LinkChildRequest):
    """Generate a medication adherence summary for a linked child."""
    try:
        parent_uid = verify_user_token(req.idToken)
        parent_data = fetch_user_data(parent_uid)
        if parent_data.get("user_details", {}).get("account_type") != "family":
            raise HTTPException(status_code=403, detail="Only family accounts can access this")
        link_status = db.reference(f"users/{parent_uid}/sent_requests/{req.child_id}/status").get()
        if link_status != "approved":
            raise HTTPException(status_code=403, detail="Child link not approved")
        child_data = fetch_user_data(req.child_id)
        if child_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=404, detail="Child not found or not a child account")
        reminders = child_data.get("health_track", {}).get("medicine_reminders", {})
        responses = child_data.get("health_track", {}).get("medicine_responses", {})
        today = datetime.datetime.now().date()
        all_taken_today = True
        missed_doses = 0
        total_reminders_last_7_days = 0
        taken_count = 0
        next_dose = None
        if isinstance(reminders, list):
            reminders = {str(i): v for i, v in enumerate(reminders)}
        for reminder_id, reminder in reminders.items():
            reminder_time = reminder.get("time", "")
            medicine_name = reminder.get("medicine_name", "Unknown")
            response_list = responses.get(reminder_id, [])
            if not isinstance(response_list, list):
                continue
            taken_today = False
            for r in response_list:
                timestamp = r.get("timestamp")
                response = r.get("response")
                if timestamp and response:
                    try:
                        ts_date = datetime.datetime.fromisoformat(timestamp).date()
                        diff_days = (today - ts_date).days
                        if 0 <= diff_days < 7:
                            total_reminders_last_7_days += 1
                            if response == "yes":
                                taken_count += 1
                        if ts_date == today and response == "yes":
                            taken_today = True
                    except Exception as parse_err:
                        logger.warning(f"Invalid timestamp format: {timestamp} — {parse_err}")
            if not taken_today:
                all_taken_today = False
                missed_doses += 1
            if not next_dose or reminder_time < next_dose:
                next_dose = f"{reminder_time} - {medicine_name}"
        adherence_rate = round((taken_count / total_reminders_last_7_days) * 100, 2) if total_reminders_last_7_days else 0.0
        return {
            "status": "success",
            "child_name": child_data.get("user_details", {}).get("name", "Unknown"),
            "all_taken_today": all_taken_today,
            "missed_doses": missed_doses,
            "adherence_rate": f"{adherence_rate}%",
            "next_dose": next_dose or "No upcoming dose"
        }
    except Exception as e:
        logger.error(f"Error calculating adherence stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))