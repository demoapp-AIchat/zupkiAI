import logging
from firebase_admin import db, auth
from fastapi import HTTPException

logger = logging.getLogger(__name__)

def get_custom_uid(firebase_uid: str) -> str:
    """Retrieve custom UID from Firebase UID mapping."""
    user_ref = db.reference(f"uid_mapping/{firebase_uid}").get()
    if user_ref and "custom_uid" in user_ref:
        return user_ref["custom_uid"]
    raise HTTPException(status_code=404, detail="Custom UID not found for this user")

def fetch_user_data(custom_uid: str) -> dict:
    """Fetch user data from Firebase by custom UID."""
    user_ref = db.reference(f"users/{custom_uid}")
    user_data = user_ref.get()
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    return user_data

def fetch_user_details(custom_uid: str) -> dict:
    """Fetch user details from Firebase."""
    user_data = fetch_user_data(custom_uid)
    return user_data.get("user_details", {})

def fetch_health_data(custom_uid: str) -> dict:
    """Fetch health data (medicines, health metrics, reminders) from Firebase."""
    user_data = fetch_user_data(custom_uid)
    return user_data.get("health_track", {})

def fetch_voice_history(custom_uid: str) -> dict:
    """Fetch voice history and related data from Firebase."""
    user_ref = db.reference(f"users/{custom_uid}/voice_history")
    voice_data = user_ref.get() or {"history": [], "asked_reminders": {}, "category_usage": {}, "subcategory_usage": {}}
    if not isinstance(voice_data, dict):
        logger.error(f"Invalid voice_data for UID: {custom_uid}")
        return {"history": [], "asked_reminders": {}, "category_usage": {}, "subcategory_usage": {}}
    return voice_data

def fetch_imp_questions(custom_uid: str) -> dict:
    """Fetch important asked questions from Firebase."""
    imp_questions_ref = db.reference(f"users/{custom_uid}/imp_ask_question")
    imp_questions_data = imp_questions_ref.get() or {"entries": []}
    if not isinstance(imp_questions_data, dict):
        logger.error(f"Invalid imp_questions_data for UID: {custom_uid}")
        return {"entries": []}
    return imp_questions_data

def verify_user_token(id_token: str) -> str:
    """Verify Firebase ID token and return custom UID."""
    try:
        decoded = auth.verify_id_token(id_token)
        return get_custom_uid(decoded["uid"])
    except Exception as e:
        logger.error(f"Error verifying token: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))