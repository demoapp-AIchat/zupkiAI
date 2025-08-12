from fastapi import APIRouter, HTTPException
from models import AuthRequest, RefreshRequest, TokenRequest,PasswordResetRequest, GetCustomUidRequest,PushTokenRequest
from database import get_custom_uid
from firebase_admin import auth, db
import requests
from helpers import generate_custom_uid
from database import verify_user_token
import os
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/create-user")
def create_user(req: AuthRequest):
    """Create a new user with email, password, and account type."""
    try:
        if req.account_type not in ["child", "family"]:
            raise HTTPException(status_code=400, detail="Account type must be 'child' or 'family'")
        user = auth.create_user(email=req.email, password=req.password)
        custom_uid = generate_custom_uid()
        db.reference(f"uid_mapping/{user.uid}").set({"custom_uid": custom_uid})
        user_data = {
            "email": req.email,
            "account_type": req.account_type
        }
        if req.account_type == "family":
            user_data["children"] = {}
        if req.account_type == "child":
            user_data["parents"] = {}
            user_data["pending_parent_requests"] = {}
        db.reference(f"users/{custom_uid}/user_details").set(user_data)
        return {"status": "success", "uid": custom_uid}
    except Exception as e:
        logger.error(f"Error creating user: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login")
def login_user(req: AuthRequest):
    """Authenticate a user and return tokens."""
    try:
        api_key = os.getenv("FIREBASE_API_KEY")
        if not api_key:
            raise RuntimeError("FIREBASE_API_KEY not set in .env")
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
        payload = {
            "email": req.email,
            "password": req.password,
            "returnSecureToken": True
        }
        response = requests.post(url, json=payload)
        result = response.json()
        if "idToken" in result:
            decoded = auth.verify_id_token(result["idToken"])
            firebase_uid = decoded["uid"]
            custom_uid = get_custom_uid(firebase_uid)
            db_account_type = db.reference(f"users/{custom_uid}/user_details/account_type").get()
            if not db_account_type:
                raise HTTPException(status_code=404, detail="Account type not found in database.")
            if db_account_type.strip().lower() != req.account_type.strip().lower():
                raise HTTPException(
                    status_code=403,
                    detail=f"Account type mismatch. You are registered as '{db_account_type}'."
                )
            return {
                "status": "success",
                "idToken": result["idToken"],
                "refreshToken": result["refreshToken"],
                "expiresIn": result["expiresIn"],
                "uid": custom_uid
            }
        else:
            error_message = result.get("error", {}).get("message", "Unknown error")
            raise HTTPException(status_code=401, detail=error_message)
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/refresh-token")
def refresh_token(req: RefreshRequest):
    """Refresh authentication token."""
    try:
        api_key = os.getenv("FIREBASE_API_KEY")
        if not api_key:
            raise RuntimeError("FIREBASE_API_KEY not set in environment")
        url = f"https://securetoken.googleapis.com/v1/token?key={api_key}"
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": req.refreshToken
        }
        response = requests.post(url, data=payload)
        result = response.json()
        if "id_token" in result:
            return {
                "status": "success",
                "idToken": result["id_token"],
                "refreshToken": result["refresh_token"],
                "expiresIn": result["expires_in"],
                "uid": result["user_id"]
            }
        else:
            error = result.get("error", {}).get("message", "Unknown error")
            raise HTTPException(status_code=401, detail=error)
    except Exception as e:
        logger.error(f"Error refreshing token: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/verify-token")
def verify_token(req: TokenRequest):
    """Verify Firebase ID token."""
    custom_uid = verify_user_token(req.idToken)
    return {"status": "verified", "uid": custom_uid}
@router.post("/forgot-password")
async def forgot_password(req: PasswordResetRequest):
        """
        Sends a password reset email with a Firebase reset link.
        """
        try:
            # Get Firebase API key from environment
            api_key = os.getenv("FIREBASE_API_KEY")
            if not api_key:
                raise RuntimeError("FIREBASE_API_KEY not set in .env")

            # Check if email exists
            try:
                auth.get_user_by_email(req.email)
            except auth.UserNotFoundError:
                logger.warning(f"Password reset requested for non-existent email: {req.email}")
                raise HTTPException(status_code=404, detail="Email not found")

            # Firebase REST API endpoint for sending password reset email
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={api_key}"
            payload = {
                "requestType": "PASSWORD_RESET",
                "email": req.email
            }

            # Send request to Firebase
            response = requests.post(url, json=payload)
            result = response.json()

            if response.status_code == 200:
                logger.info(f"Password reset email sent to {req.email}")
                return {
                    "status": "success",
                    "message": f"Password reset email sent to {req.email}. Please check your email and follow the link to reset your password."
                }
            else:
                error_message = result.get("error", {}).get("message", "Unknown error")
                logger.error(f"Password reset failed for {req.email}: {error_message}")
                raise HTTPException(status_code=400, detail=error_message)

        except Exception as e:
            logger.error(f"Error in forgot-password endpoint: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
        
@router.post("/save-push-token")
def save_push_token(req: PushTokenRequest):
    try:
        logger.info(f"Received push token request for user: {req.idToken}")
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        if not user_data:
            raise HTTPException(status_code=404, detail="User not found")
        if req.push_token:
            user_ref.child("push_token").set(req.push_token)
            return {"status": "success", "message": "Push token saved successfully"}
        return {"status": "success", "message": "No push token provided"}
    except Exception as e:
        logger.error(f"Error saving push token: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))
    
@router.post("/get-custom-uid")
def get_custom_uid_endpoint(req: GetCustomUidRequest):
    """Get custom UID for a given Firebase UID."""
    try:
        # Verify if the Firebase UID exists
        try:
            auth.get_user(req.firebase_uid)
        except auth.UserNotFoundError:
            logger.warning(f"User not found for Firebase UID: {req.firebase_uid}")
            raise HTTPException(status_code=404, detail="User not found")

        # Check if custom UID exists in the database
        custom_uid_ref = db.reference(f"uid_mapping/{req.firebase_uid}")
        custom_uid_data = custom_uid_ref.get()
        
        if custom_uid_data and "custom_uid" in custom_uid_data:
            custom_uid = custom_uid_data["custom_uid"]
        else:
            # Generate new custom UID if it doesn't exist
            custom_uid = generate_custom_uid()
            custom_uid_ref.set({"custom_uid": custom_uid})
            
        return {"status": "success", "custom_uid": custom_uid}
    except Exception as e:
        logger.error(f"Error getting custom UID: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))