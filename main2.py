import os
import string
import re
import random
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List ,Dict
import datetime
import logging
import firebase_admin
from firebase_admin import credentials, auth, db, messaging
from openai import AsyncOpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pytz import timezone
import datetime
import requests
import base64
import json
import asyncio
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
from fastapi import Body


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Firebase
firebase_base64 = os.getenv("FIREBASE_CRED_BASE64")
if not firebase_base64:
    raise RuntimeError("FIREBASE_CRED_BASE64 not set in .env")

try:
    firebase_dict = json.loads(base64.b64decode(firebase_base64).decode())
    cred = credentials.Certificate(firebase_dict)
    firebase_admin.initialize_app(cred, {
        "databaseURL": os.getenv("FIREBASE_DB_URL")
    })
except Exception as e:
    raise RuntimeError(f"Failed to initialize Firebase: {str(e)}")

# Initialize OpenAI client
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY not set in .env")

client = AsyncOpenAI(api_key=openai_api_key)

# Initialize FastAPI app
app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Constants
MAX_MESSAGE_LENGTH = 1000  # Maximum length of a single message
MAX_HISTORY_LENGTH = 50  # Maximum number of messages to keep in history
india_tz = timezone("Asia/Kolkata")
SAMPLE_QUESTIONS = [
    "How are you feeling today?",
    "Did you remember to take your medicine?",
    "What did you enjoy doing yesterday?",
    "Would you like to share a happy memory?",
    "Would you like some good news today?"
]
CATEGORIES_WITH_SUBCATEGORIES = {
    "Health and Medicine": [
        "Medication intake",
        "Doctor appointment reminders",
        "Symptoms or discomfort",
        "Chronic illness check-in"
    ],
    "Emotional State and Well-being": [
        "Mood tracking",
        "Stress or anxiety check",
        "Loneliness check",
        "Positive reinforcement"
    ],
    "Companion and Social Interaction": [
        "Family interactions",
        "Friendship talk",
        "Daily social activity",
        "Memory sharing with loved ones"
    ],
    "Reminders Questions": [
        "Medication reminders",
        "Hydration check",
        "Meal time check",
        "Appointment reminders"
    ],
    "Practical Support and Request": [
        "Help with devices",
        "Need groceries or essentials",
        "Household tasks",
        "Emergency check"
    ],
    "Health Motion": [
        "Exercise tracking",
        "Walking reminders",
        "Mobility check",
        "Stretching or movement prompts"
    ],
    "Best Caring": [
        "Comfort level",
        "Care quality feedback",
        "Suggestions for better care",
        "Is anything bothering you?"
    ],
    "Current Time Based Questions (e.g., morning, evening, night)": [
        "Morning greetings and check-in",
        "Evening mood and activities",
        "Night sleep preparation",
        "Time-specific medicine check"
    ],
    "Memory and Life Reflection": [
        "Childhood memories",
        "Career achievements",
        "Important life lessons",
        "Family stories"
    ],
    "Daily Routine Check-ins": [
        "Wake-up time",
        "Meals taken",
        "Activities done",
        "Nap or rest"
    ],
    "Nutrition and Meal-related": [
        "Breakfast/lunch/dinner check",
        "Diet preferences",
        "Did you enjoy your meal?",
        "Water intake"
    ],
    "Sleep and Rest": [
        "Sleep quality",
        "Nap check",
        "Bedtime routine",
        "Any sleep difficulties"
    ],
    "Mental Stimulation and Cognitive Exercises": [
        "Memory games",
        "Trivia or puzzles",
        "Storytelling prompts",
        "What day is it today?"
    ],
    "Safety and Security Concerns": [
        "Door locked check",
        "Feeling safe?",
        "Stranger alert",
        "Emergency preparedness"
    ],
    "Festivals and Cultural Engagement": [
        "Festival greetings",
        "Special traditions",
        "Religious practices",
        "Celebration plans"
    ],
    "Weather-related Questions": [
        "Weather comfort check",
        "Dress suggestions",
        "Outdoor plan suitability",
        "Cold/heat-related discomfort"
    ],
    "Motivational and Encouraging Conversations": [
        "Words of encouragement",
        "Proud of you messages",
        "Goal setting",
        "Daily affirmations"
    ],
    "Celebration and Special Days": [
        "Birthday wishes",
        "Anniversary reminders",
        "Milestones celebration",
        "Family event questions"
    ],
    "Spiritual and Faith-based Reflections": [
        "Prayer time",
        "Faith talk",
        "Spiritual comfort check",
        "Religious holiday wishes"
    ],
    "Entertainment and Leisure Activities": [
        "TV/music preference",
        "Movie recommendations",
        "Hobby discussion",
        "Crafts or games ideas"
    ],
    "Technology Help or Guidance": [
        "Phone help",
        "Video call assistance",
        "Settings guidance",
        "Online safety tips"
    ],
    "Personal Hygiene and Grooming": [
        "Bathing check",
        "Brushing teeth",
        "Hair grooming",
        "Clothing comfort"
    ],
    "Pain or Discomfort Tracking": [
        "Pain scale rating",
        "Body part check",
        "Relief methods",
        "Medical follow-up needs"
    ],
    "Exercise and Physical Activity Encouragement": [
        "Stretch prompt",
        "Breathing exercises",
        "Balance check",
        "Short walk suggestion"
    ],
    "Custom Questions based on User Preferences or Habits": [
        "Favorite routine check",
        "User-defined goals",
        "Unique memory triggers",
        "Personal hobbies"
    ]
}
# Initialize AsyncIOScheduler
scheduler = AsyncIOScheduler(timezone=india_tz)

# Generate a 7-character UID with 2 or 3 digits
def generate_custom_uid():
    characters = string.ascii_letters
    digits = string.digits
    while True:
        num_digits = random.choice([2, 3])
        num_letters = 7 - num_digits
        letters = ''.join(random.choices(characters, k=num_letters))
        numbers = ''.join(random.choices(digits, k=num_digits))
        custom_uid = ''.join(random.sample(letters + numbers, 7))
        if not db.reference(f"users/{custom_uid}").get():
            return custom_uid

# Get time-based greeting
def get_time_based_greeting(user_name: str) -> str:
    current_time = datetime.datetime.now(india_tz)
    hour = current_time.hour
    if 0 <= hour < 12:
        return f"Good morning, {user_name}!"
    elif 12 <= hour < 18:
        return f"Good evening, {user_name}!"
    else:
        return f"Good night, {user_name}!"
# Map Firebase UID to custom UID
def get_custom_uid(firebase_uid: str) -> str:
    user_ref = db.reference(f"uid_mapping/{firebase_uid}").get()
    if user_ref:
        return user_ref["custom_uid"]
    raise HTTPException(status_code=404, detail="Custom UID not found for this user")

# Models
class AuthRequest(BaseModel):
    email: str
    password: str
    account_type: str  # "child" or "family"

class TokenRequest(BaseModel):
    idToken: str
class ProactiveRequest(BaseModel):
    idToken: str
    reply:Optional[str] = None

class ReminderResponseRequest(BaseModel):
    idToken: str
    medicine_name: Optional[str] = None
    reminder_id: Optional[str] = None
    response: Optional[str] = None 

# Response model
class ProactiveTalkResponse(BaseModel):
    status: str
    response: str  # Can be a question or a response
    timestamp: str

class PasswordResetRequest(BaseModel):
    email: str

class EmailVerificationRequest(BaseModel):
    idToken: str

class UserDetails(BaseModel):
    idToken: str
    uid: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    hobby: Optional[str] = None
    emergencyContact: Optional[str] = None
    medication: Optional[str] = None
    dob: Optional[str] = None
    height: Optional[str] = None
    weight: Optional[str] = None
    bloodGroup: Optional[str] = None
    medicalHistory: Optional[str] = None
    relation: Optional[str] = None
    selectedInterests: Optional[List[str]] = None
    dietaryPreference: Optional[str] = None
    allergies: Optional[List[str]] = None
class HealthInfo(BaseModel):
    idToken: str
    hobbies: Optional[List[str]] = None
    medicines: Optional[List[str]] = None
    medical_history: Optional[str] = None

class Medicine(BaseModel):
    id: Optional[str] = None
    medicine_name: Optional[str] = None
    dosage: Optional[str] = None
    initial_quantity: Optional[int] = None
    daily_intake: Optional[int] = None
    timestamp: Optional[str] = None
class HealthMetric(BaseModel):
    id: Optional[str] = None
    timestamp: Optional[str] = None
    metric: Optional[str] = None
    data: Optional[float] = None
class DeleteReminderRequest(BaseModel):
    idToken: str
    reminder_id: str
class HealthTrack(BaseModel):
    idToken: str
    medicines: Optional[List[Medicine]] = None
    health_metrics: Optional[List[HealthMetric]] = None
class MedicineReminder(BaseModel):
    idToken: str
    reminder_id:Optional[str] = None
    medicine_name: Optional[str] = None
    pill_details: Optional[str] = None
    end_date: Optional[str] = None  # Expected format: YYYY-MM-DD
    amount_per_box: Optional[str] = None
    initial_quantity: Optional[str] = None
    time: Optional[str] = None
    current_quantity: Optional[str] = None
    reminder_date: Optional[str] = None  # Expected format: YYYY-MM-DD
    start_from_today: Optional[str] = None
    take_medicine_alert: Optional[str] = None
    ring_phone: Optional[str] = None
    send_message: Optional[str] = None
    refill_reminder: Optional[str] = None
    set_refill_date:Optional[str] = None
    set_day_before_refill:Optional[str] = None
class DeleteHealthTrackRequest(BaseModel):
    idToken: str
    delete_type: Optional[str] = None
class ProactiveTalkRequest(BaseModel):
    idToken: str
    reply: Optional[str] = None
class ChatRequest(BaseModel):
    idToken: str
    message: Optional[str] = None

class PushTokenRequest(BaseModel):
    idToken: str
    push_token: Optional[str] = None

class SearchChildRequest(BaseModel):
    child_id: str

class LinkChildRequest(BaseModel):
    idToken: str
    child_id: str
# Response model
class ChatResponse(BaseModel):
    status: str
    response: str
    chat_history: List[Dict[str, str]]

class HandleParentRequest(BaseModel):
    idToken: str
    parent_id: str
    action: str  # "allow" or "decline"
class MedicineTrack(BaseModel):
    idToken: str
    medicines: Optional[List[Medicine]] = None
class CheckLinkStatusRequest(BaseModel):
    idToken: str
    child_id: str

class HealthMetricTrack(BaseModel):
    idToken: str
    health_metrics: Optional[List[HealthMetric]] = None
class DeleteMedicineRequest(BaseModel):
    idToken: str
    medicine_id: str 
class DeleteHealthMetricRequest(BaseModel):
    idToken: str
    metric_id: str 
class HandleParentRequest(BaseModel):
    idToken: str
    parent_id: str
    action: str  # "allow" or "decline"
# FastAPI lifespan for scheduler
class DeleteRequest(BaseModel):
    idToken: str
    target_id: str
class FetchLinkedChildrenRequest(BaseModel):
    parent_id: str
class RefreshRequest(BaseModel):
    refreshToken: str
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting scheduler")
    scheduler.add_job(schedule_daily_question, 'interval', hours=3)  # Run every 3 hours
    scheduler.start()
    yield
    scheduler.shutdown()
    logger.info("Scheduler stopped")

# Reinitialize FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# Routes
@app.get("/")
def health_check():
    return {"status": "API is working!"}

@app.post("/create-user")
def create_user(req: AuthRequest):
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

@app.post("/login")
def login_user(req: AuthRequest):
    try:
        api_key = os.getenv("FIREBASE_API_KEY")
        if not api_key:
            raise RuntimeError("FIREBASE_API_KEY not set in .env")

        # Step 1: Firebase sign-in
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
        payload = {
            "email": req.email,
            "password": req.password,
            "returnSecureToken": True
        }
        response = requests.post(url, json=payload)
        result = response.json()

        # Step 2: Validate success
        if "idToken" in result:
            decoded = auth.verify_id_token(result["idToken"])
            firebase_uid = decoded["uid"]
            logger.info(f"Firebase UID: {firebase_uid}")

            # Step 3: Get custom UID
            user_mapping = db.reference(f"uid_mapping/{firebase_uid}").get()
            if not user_mapping or "custom_uid" not in user_mapping:
                raise HTTPException(status_code=404, detail="Custom UID not found")

            custom_uid = user_mapping["custom_uid"]
            logger.info(f"Custom UID: {custom_uid}")

            # Step 4: Fetch account type from DB
            db_account_type = db.reference(f"users/{custom_uid}/user_details/account_type").get()
            logger.info(f"Account type from DB: {db_account_type}")

            if not db_account_type:
                raise HTTPException(status_code=404, detail="Account type not found in database.")

            # Step 5: Strict match
            if db_account_type.strip().lower() != req.account_type.strip().lower():
                raise HTTPException(
                    status_code=403,
                    detail=f"Account type mismatch. You are registered as '{db_account_type}'."
                )

            # Step 6: Return login response
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
@app.post("/refresh-token")
def refresh_token(req: RefreshRequest):
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
        logger.info(f"Refresh token response: {result}")

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
@app.post("/verify-token")
def verify_token(req: TokenRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        return {"status": "verified", "uid": custom_uid}
    except Exception as e:
        logger.error(f"Error verifying token: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))
@app.post("/forgot-password")
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

@app.post("/verify-email")
async def verify_email(req: EmailVerificationRequest):
        """
        Sends an email verification link to the user's email address.
        """
        try:
            # Get Firebase API key from environment
            api_key = os.getenv("FIREBASE_API_KEY")
            if not api_key:
                raise RuntimeError("FIREBASE_API_KEY not set in .env")

            # Verify the ID token
            decoded = auth.verify_id_token(req.idToken)
            user_email = decoded.get("email")
            if not user_email:
                raise HTTPException(status_code=400, detail="Email not found in token")

            # Check if email is already verified
            user = auth.get_user(decoded["uid"])
            if user.email_verified:
                return {
                    "status": "success",
                    "message": "Email is already verified",
                    "emailVerified": True
                }

            # Firebase REST API endpoint for sending verification email
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={api_key}"
            payload = {
                "requestType": "VERIFY_EMAIL",
                "idToken": req.idToken
            }

            # Send request to Firebase
            response = requests.post(url, json=payload)
            result = response.json()

            if response.status_code == 200:
                logger.info(f"Verification email sent to {user_email}")
                return {
                    "status": "success",
                    "message": f"Verification email sent to {user_email}. Please check your email and follow the link to verify.",
                    "emailVerified": False
                }
            else:
                error_message = result.get("error", {}).get("message", "Unknown error")
                logger.error(f"Email verification failed for {user_email}: {error_message}")
                raise HTTPException(status_code=400, detail=error_message)

        except auth.InvalidIdTokenError:
            logger.error("Invalid Firebase ID token")
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        except auth.ExpiredIdTokenError:
            logger.error("Expired Firebase ID token")
            raise HTTPException(status_code=401, detail="Token has expired")
        except Exception as e:
            logger.error(f"Error in verify-email endpoint: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
@app.post("/user-details")
def save_user_details(req: UserDetails):
    try:
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        req.uid = custom_uid
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get() or {}
        user_details = {k: v for k, v in req.dict(exclude={"idToken"}).items() if v is not None}
        user_ref.child("user_details").update(user_details)
        return {"status": "success", "message": "User details saved successfully"}
    except Exception as e:
        logger.error(f"Error saving user details: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

# 0. Fetch user details (for both child and family)
@app.post("/user-detail")
def fetch_user_details(req: TokenRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        if not user_data or not user_data.get("user_details"):
            raise HTTPException(status_code=404, detail="User details not found")

        # Allow both child and family to fetch their own details
        return {"status": "success", "data": user_data["user_details"]}
    except Exception as e:
        logger.error(f"Error fetching user details: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

@app.post("/user-health")
def save_user_health(req: HealthInfo):
    try:
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can save health info")
        health_info = {k: v for k, v in req.dict(exclude={"idToken"}).items() if v is not None}
        if health_info:
            user_ref.child("health_info").update(health_info)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error saving user health: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

@app.get("/user-health")
def fetch_user_health(req: TokenRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        if not user_data or not user_data.get("health_info"):
            raise HTTPException(status_code=404, detail="Health info not found")
        if (user_data.get("user_details", {}).get("account_type") == "child" or 
            custom_uid in user_data.get("user_details", {}).get("children", {})):
            return {"status": "success", "data": user_data["health_info"]}
        raise HTTPException(status_code=403, detail="Not authorized to access this data")
    except Exception as e:
        logger.error(f"Error fetching user health: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/save-medicines")
def save_medicines(req: MedicineTrack):
    try:
        # Verify Firebase ID token
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        user_ref = db.reference(f"users/{custom_uid}")

        # Check if user is a child
        user_data = user_ref.get()
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can save medicines")

        # Reference to medicines node
        med_ref = user_ref.child("health_track/medicines")
        existing = med_ref.get()
        current_length = len(existing) if existing else 0

        # Save each medicine with next numeric key
        for i, med in enumerate(req.medicines):
            clean_data = {k: v for k, v in med.dict().items() if v is not None}
            next_index = str(current_length + i)
            med_ref.child(next_index).set(clean_data)

        return {"status": "success", "message": "Medicines saved successfully"}

    except Exception as e:
        logger.error(f"Error saving medicines: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

# Updated /get-medicines endpoint
@app.post("/get-medicines", response_model=List[Medicine])
async def get_medicines(req: TokenRequest):
    try:
        logger.info(f"Received request for /get-medicines with idToken: {req.idToken[:10]}...")
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            logger.warning(f"Access denied for UID: {custom_uid}, account_type: {user_data.get('user_details', {}).get('account_type')}")
            raise HTTPException(status_code=403, detail="Only child accounts can access medicines")
        
        medicines_data = user_ref.child("health_track/medicines").get() or []
        logger.info(f"Retrieved medicines data: {medicines_data}")
        medicines = [
            Medicine(
                id=med.get("id"),
                timestamp=med.get("timestamp"),
                medicine_name=med.get("medicine_name"),
                dosage=str(med.get("dosage")),
                initial_quantity=med.get("initial_quantity"),
                daily_intake=med.get("daily_intake")
            )
            for med in medicines_data
            if med
        ]
        logger.info(f"Returning {len(medicines)} medicines")
        return medicines
    except auth.InvalidIdTokenError:
        logger.error("Invalid Firebase ID token")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except auth.ExpiredIdTokenError:
        logger.error("Expired Firebase ID token")
        raise HTTPException(status_code=401, detail="Token has expired")
    except Exception as e:
        logger.error(f"Error retrieving medicines: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
#Delete medicine from databse 
@app.delete("/delete-medicine")
async def delete_medicine(req: DeleteMedicineRequest):
    try:
        logger.info(f"Received request for /delete-medicine with idToken: {req.idToken[:10]}... and medicine_id: {req.medicine_id}")
        
        # Verify the token and extract custom_uid
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        
        # Reference to the user's medicines in the database
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        
        # Check if the user is a child account
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            logger.warning(f"Access denied for UID: {custom_uid}, account_type: {user_data.get('user_details', {}).get('account_type')}")
            raise HTTPException(status_code=403, detail="Only child accounts can access medicines")

        medicines_ref = user_ref.child("health_track/medicines")
        medicines_data = medicines_ref.get() or []
        
        # Find and remove the medicine with the matching id
        updated_medicines = [med for med in medicines_data if med.get("id") != req.medicine_id]
        
        # Check if the medicine was found and deleted
        if len(updated_medicines) == len(medicines_data):
            logger.warning(f"Medicine with id {req.medicine_id} not found for UID: {custom_uid}")
            raise HTTPException(status_code=404, detail="Medicine not found")

        # Update the database with the new list
        medicines_ref.set(updated_medicines)
        logger.info(f"Successfully deleted medicine with id: {req.medicine_id} for UID: {custom_uid}")
        return {"message": f"Medicine with id {req.medicine_id} deleted successfully"}
    
    except auth.InvalidIdTokenError:
        logger.error("Invalid Firebase ID token")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except auth.ExpiredIdTokenError:
        logger.error("Expired Firebase ID token")
        raise HTTPException(status_code=401, detail="Token has expired")
    except Exception as e:
        logger.error(f"Error deleting medicine: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    try:
        # Verify Firebase token
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        med_ref = db.reference(f"users/{custom_uid}/health_track/medicines")

        existing_meds = med_ref.get()
        if not existing_meds:
            raise HTTPException(status_code=404, detail="No medicines found")

        # Search for entry with matching internal id
        for key, med in existing_meds.items():
            if med and med.get("id") == req.medicine_id:
                med_ref.child(key).delete()
                return {"status": "success", "message": f"Medicine with id '{req.medicine_id}' deleted"}

        raise HTTPException(status_code=404, detail=f"Medicine with id '{req.medicine_id}' not found")

    except auth.InvalidIdTokenError:
        raise HTTPException(status_code=401, detail="Invalid Firebase token")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
# Updated /get-health-metric endpoint
@app.post("/save-health-metrics")
def save_health_metrics(req: HealthMetricTrack):
    try:
        # Verify Firebase ID token
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        user_ref = db.reference(f"users/{custom_uid}")

        # Check if user is a child
        user_data = user_ref.get()
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can save health metrics")

        # Reference to health_metrics node
        metrics_ref = user_ref.child("health_track/health_metrics")
        existing = metrics_ref.get()
        current_length = len(existing) if existing else 0

        # Append each metric with next numeric key
        for i, metric in enumerate(req.health_metrics):
            clean_data = {k: v for k, v in metric.dict().items() if v is not None}
            next_index = str(current_length + i)
            metrics_ref.child(next_index).set(clean_data)

        return {"status": "success", "message": "Health metrics saved successfully"}

    except Exception as e:
        logger.error(f"Error saving health metrics: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

# Updated /get-health-metric endpoint
@app.post("/get-health-metric", response_model=List[HealthMetric])
async def get_health_metrics(req: TokenRequest):
    try:
        logger.info(f"Received request for /get-health-metric with idToken: {req.idToken[:10]}...")
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            logger.warning(f"Access denied for UID: {custom_uid}, account_type: {user_data.get('user_details', {}).get('account_type')}")
            raise HTTPException(status_code=403, detail="Only child accounts can access health metrics")
        
        health_metrics_data = user_ref.child("health_track/health_metrics").get() or []
        logger.info(f"Retrieved health metrics data: {health_metrics_data}")
        health_metrics = [
            HealthMetric(
                id=metric.get("id"),
                timestamp=metric.get("timestamp"),
                metric=metric.get("metric"),
                data=metric.get("data")
            )
            for metric in health_metrics_data
            if metric
        ]
        logger.info(f"Returning {len(health_metrics)} health metrics")
        return health_metrics
    except auth.InvalidIdTokenError:
        logger.error("Invalid Firebase ID token")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except auth.ExpiredIdTokenError:
        logger.error("Expired Firebase ID token")
        raise HTTPException(status_code=401, detail="Token has expired")
    except Exception as e:
        logger.error(f"Error retrieving health metrics: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

#delete metric id
@app.delete("/delete-health-metric")
async def delete_health_metric(req: DeleteHealthMetricRequest):
    try:
        logger.info(f"Received request for /delete-health-metric with idToken: {req.idToken[:10]}... and metric_id: {req.metric_id}")
        
        # Verify the token and extract custom_uid
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        
        # Reference to the user's health metrics in the database
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        
        # Check if the user is a child account
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            logger.warning(f"Access denied for UID: {custom_uid}, account_type: {user_data.get('user_details', {}).get('account_type')}")
            raise HTTPException(status_code=403, detail="Only child accounts can access health metrics")

        health_metrics_ref = user_ref.child("health_track/health_metrics")
        health_metrics_data = health_metrics_ref.get() or []
        
        # Find and remove the health metric with the matching id
        updated_health_metrics = [metric for metric in health_metrics_data if metric.get("id") != req.metric_id]
        
        # Check if the health metric was found and deleted
        if len(updated_health_metrics) == len(health_metrics_data):
            logger.warning(f"Health metric with id {req.metric_id} not found for UID: {custom_uid}")
            raise HTTPException(status_code=404, detail="Health metric not found")

        # Update the database with the new list
        health_metrics_ref.set(updated_health_metrics)
        logger.info(f"Successfully deleted health metric with id: {req.metric_id} for UID: {custom_uid}")
        return {"message": f"Health metric with id {req.metric_id} deleted successfully"}
    
    except auth.InvalidIdTokenError:
        logger.error("Invalid Firebase ID token")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except auth.ExpiredIdTokenError:
        logger.error("Expired Firebase ID token")
        raise HTTPException(status_code=401, detail="Token has expired")
    except Exception as e:
        logger.error(f"Error deleting health metric: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
##################################################

@app.post("/save-medicine-reminder")
def save_medicine_reminder(req: MedicineReminder):
    try:
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        
        # Reference to the user's health metrics in the database
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        
        # Check if the user is a child account
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            logger.warning(f"Access denied for UID: {custom_uid}, account_type: {user_data.get('user_details', {}).get('account_type')}")
            raise HTTPException(status_code=403, detail="Only child accounts can access health metrics")

        # Reference to medicine reminders node
        reminder_ref = user_ref.child("health_track/medicine_reminders")
        existing = reminder_ref.get()
        current_length = len(existing) if existing else 0

        # Save the reminder with next numeric key
        clean_data = {k: v for k, v in req.dict().items() if v is not None and k != "idToken"}
        next_index = str(current_length)
        reminder_ref.child(next_index).set(clean_data)

        return {"status": "success", "message": "Medicine reminder saved successfully"}

    except Exception as e:
        logger.error(f"Error saving medicine reminder: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))
#######################################
@app.post("/get-medicine-reminders")
def get_medicine_reminders(req: TokenRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
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
@app.post("/get-child-reminders")
def get_child_medicine_reminders(req: LinkChildRequest):
    try:
        # Step 1: Decode parent token
        decoded = auth.verify_id_token(req.idToken)
        parent_uid = get_custom_uid(decoded["uid"])

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
@app.delete("/delete-medicine-reminder")
def delete_medicine_reminder(req: DeleteReminderRequest):
    try:
        # Verify ID token
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])

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

@app.post("/get-child-reminders-with-status")
def get_child_medicine_reminders_with_status(req: LinkChildRequest):
    try:
        # Step 1: Decode parent token
        decoded = auth.verify_id_token(req.idToken)
        parent_uid = get_custom_uid(decoded["uid"])

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

@app.post("/save-reminder-response")
def save_reminder_response(req: ReminderResponseRequest):
    try:
        # Step 1: Verify token and UID
        decoded = auth.verify_id_token(req.idToken)
        uid = decoded["uid"]
        custom_uid = get_custom_uid(uid)

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

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        # Verify user authentication
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        
        if not user_data:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Fetch user details, medicines, and health metrics
        user_details = user_data.get("user_details", {})
        user_name = user_details.get("name", "there")
        hobbies = user_details.get("hobby", "unknown")
        age = user_details.get("age", "unknown")
        medicines = user_data.get("health_track", {}).get("medicines", [])
        health_metrics = user_data.get("health_track", {}).get("health_metrics", [])
        
        # Format user context for personalization
        medicines_summary = (
            ", ".join([f"{med.get('medicine_name', 'unknown')} ({med.get('dosage', 'unknown')})" 
                       for med in medicines]) if medicines else "no medications recorded"
        )
        health_metrics_summary = (
            ", ".join([f"{metric.get('metric', 'unknown')}: {metric.get('data', 'unknown')}" 
                       for metric in health_metrics]) if health_metrics else "no health metrics recorded"
        )
        
        # Initialize chat history
        chat_ref = user_ref.child("chat_history")
        chat = chat_ref.get() or {"history": [], "greeted": False}
        chat_history = chat.get("history", [])
        
        # Determine if greeting is needed
        should_greet = False
        greeting = get_time_based_greeting(user_name)
        
        if not chat_history:  # New conversation
            should_greet = True
        elif req.message and req.message.lower() == "hello":  # User says "hello"
            should_greet = True
        
        # Add greeting if needed
        if should_greet and not chat.get("greeted", False):
            chat_history.append({
                "role": "assistant",
                "content": greeting,
                "timestamp": datetime.datetime.now().isoformat()
            })
            chat["greeted"] = True
            chat["history"] = chat_history
            chat_ref.set(chat)
        
        # Handle user message
        assistant_message = ""
        if req.message:
            if len(req.message) > MAX_MESSAGE_LENGTH:
                raise HTTPException(
                    status_code=400,
                    detail=f"Message exceeds maximum length of {MAX_MESSAGE_LENGTH} characters"
                )
            
            # Append user message to history
            chat_history.append({
                "role": "user",
                "content": req.message,  # Fixed: Changed req_message to req.message
                "timestamp": datetime.datetime.now().isoformat()
            })
            
            # Trim history if it exceeds limit
            if len(chat_history) > MAX_HISTORY_LENGTH:
                chat_history = chat_history[-MAX_HISTORY_LENGTH:]
            
            # Create personalized system prompt
            system_prompt = {
                "role": "system",
                "content": (
                    f"You are a friendly and supportive assistant for {user_name}, who is {age} years old "
                    f"and enjoys {hobbies}. They are taking the following medications: {medicines_summary}. "
                    f"Their recent health metrics include: {health_metrics_summary}. Use this information to make "
                    f"responses relevant and caring, such as reminding them about medications or commenting on their "
                    f"health metrics. Do not include greetings unless explicitly asked."
                )
            }
            
            # Prepare messages for OpenAI
            messages = [system_prompt] + chat_history
            
            # Get response from OpenAI
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages
            )
            assistant_message = response.choices[0].message.content
            
            # Append assistant response to history
            chat_history.append({
                "role": "assistant",
                "content": assistant_message,
                "timestamp": datetime.datetime.now().isoformat()
            })
            
            # Update chat history in database
            chat["history"] = chat_history
            chat_ref.set(chat)
        
        # Prepare response
        if should_greet and not chat.get("greeted", False):
            response_message = greeting
        elif chat_history:
            response_message = assistant_message or chat_history[-1]["content"]
        else:
            response_message = "No conversation history"
        
        # Return response with full chat history
        return ChatResponse(
            status="success",
            response=response_message,
            chat_history=chat_history
        )
    
    except Exception as e:
        logger.error(f"Error in chat: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))
# Helper function to check if a medication reminder falls within a time period
def is_reminder_in_period(reminder_time: str, period_start_hour: int, period_end_hour: int) -> bool:
    try:
        if not reminder_time:
            return False
        reminder_hour, _ = map(int, reminder_time.split(":"))
        return period_start_hour <= reminder_hour < period_end_hour
    except Exception as e:
        logger.error(f"Error parsing reminder time {reminder_time}: {str(e)}")
        return False

# Helper function to generate a random time within a period
def generate_random_time(period_start_hour: int, period_end_hour: int) -> str:
    hour = random.randint(period_start_hour, period_end_hour - 1)
    minute = random.choice([0, 15, 30, 45])
    return f"{hour:02d}:{minute:02d}"

# Helper function to validate if a task is exactly three words
def is_valid_three_word_task(task: str) -> bool:
    return len(task.strip().split()) == 3
# Generate To-Do List API endpoint
#######################################################################################3
@app.post("/generate-todo")
async def generate_todo(req: TokenRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        if not user_data:
            raise HTTPException(status_code=404, detail="User not found")

        if user_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can use generate-todo")

        user_details = user_data.get("user_details", {})
        user_name = user_details.get("name", "there")
        hobbies = user_details.get("hobbies", "no hobbies specified")
        age = user_details.get("age", "unknown")
        medical_history = user_details.get("medical_history", None)
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
                       for med in medicines]) if medicines else "no medications recorded"
        )
        reminders_summary = (
            ", ".join([f"{rem.get('medicine_name', 'unknown')} at {rem.get('time', 'unknown')}" 
                       for rem in medicine_reminders if rem.get("time")]) if medicine_reminders else "no reminders set"
        )
        medical_context = ""
        if medical_history:
            medical_context += f"Their medical history includes: {medical_history}. "
        if weight:
            medical_context += f"Their weight is {weight}. "
        if height:
            medical_context += f"Their height is {height}. "

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
                    med_task = f"Take medicine {reminder.get('medicine_name', 'medication')}"
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
                        f"They are taking: {medicines_summary}. "
                        f"Their reminders are: {reminders_summary}. "
                        f"The current time is {current_time.strftime('%H:%M')}. "
                        f"The recent conversation history is: {json.dumps(chat_history[-5:])}. "
                        f"Generate {tasks_needed} unique to-do tasks for the {period} period (from {start_hour}:00 to {end_hour}:00) to create a personalized to-do list. "
                        f"Each task must: "
                        f"1. Be exactly three words long (e.g., 'Paint small sketch', 'Listen music playlist'). "
                        f"2. Be engaging, positive, and tailored to the user’s hobbies, medical needs (excluding taking medication), or recent chat history. "
                        f"3. Have a distinct intent/theme from all previously generated tasks: {json.dumps(list(used_tasks))}. "
                        f"4. Be relevant to the time of day (e.g., morning: energizing tasks, evening: relaxing tasks, night: winding down tasks). "
                        f"5. Avoid medication-related tasks, as these are handled separately. "
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
                    "morning": ["Eat healthy breakfast", "Read book chapter"],
                    "evening": ["Call friend now", "Try new recipe"],
                    "night": ["Watch favorite movie", "Write journal entry"]
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
        todo_ref = user_ref.child(f"todo_lists/{current_date}")
        todo_ref.set(todo_lists)

        return {
            "status": "success",
            "todo_lists": todo_lists,
            "timestamp": current_time.isoformat()
        }

    except auth.InvalidIdTokenError:
        logger.error("Invalid Firebase ID token")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except auth.ExpiredIdTokenError:
        logger.error("Expired Firebase ID token")
        raise HTTPException(status_code=401, detail="Token has expired")
    except Exception as e:
        logger.error(f"Error in generate-todo endpoint: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
@app.post("/save-push-token")
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
@app.post("/medication-adherence-summary")
def get_medication_adherence_summary(req: LinkChildRequest):
    try:
        # Step 1: Decode parent token
        decoded = auth.verify_id_token(req.idToken)
        custom_parent_uid = get_custom_uid(decoded["uid"])

        # Step 2: Check parent account type
        parent_data = db.reference(f"users/{custom_parent_uid}").get()
        if not parent_data or parent_data.get("user_details", {}).get("account_type") != "family":
            raise HTTPException(status_code=403, detail="Only family accounts can access this")
 # Step 3: Check link status
        link_status = db.reference(f"users/{custom_parent_uid}/sent_requests/{req.child_id}/status").get()
        if link_status != "approved":
            raise HTTPException(status_code=403, detail="Child link not approved")

        # Step 4: Fetch child data
        child_ref = db.reference(f"users/{req.child_id}")
        child_data = child_ref.get()
        if not child_data or child_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=404, detail="Child not found or not a child account")

        # Step 5: Collect reminders and responses
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

            # Ensure response_list is a list
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

            # Track next dose
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

##################################################################################
@app.post("/search-child")
def search_child(req: SearchChildRequest):
    try:
        child_ref = db.reference(f"users/{req.child_id}")
        child_data = child_ref.get()
        if not child_data or child_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=404, detail="Child ID not found or not a child account")
        child_details = child_data.get("user_details", {})
        search_result = {
            "name": child_details.get("name", ""),
            "age": child_details.get("age", None)
        }
        return {"status": "success", "data": search_result}
    except Exception as e:
        logger.error(f"Error searching child: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

# 2. Send link request
@app.post("/request-child-link")
def request_child_link(req: LinkChildRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        parent_uid = get_custom_uid(decoded["uid"])

        parent_ref = db.reference(f"users/{parent_uid}")
        parent_data = parent_ref.get()
        if not parent_data or parent_data.get("user_details", {}).get("account_type") != "family":
            raise HTTPException(status_code=403, detail="Only family accounts can request to link children")

        child_ref = db.reference(f"users/{req.child_id}")
        child_data = child_ref.get()
        if not child_data or child_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=404, detail="Child ID not found or not a child account")

        if parent_uid in child_data.get("parents", {}):
            raise HTTPException(status_code=400, detail="Already linked as parent")

        if parent_uid in child_data.get("pending_parent_requests", {}):
            raise HTTPException(status_code=400, detail="Request already sent")

        # Save request in child
        child_ref.child(f"pending_parent_requests/{parent_uid}").set({
            "name": parent_data.get("user_details", {}).get("name", ""),
            "email": parent_data.get("user_details", {}).get("email", ""),
            "status": "pending",
            "timestamp": datetime.datetime.now().isoformat()
        })

        # Save request in parent
        parent_ref.child(f"sent_requests/{req.child_id}").set({
            "name": child_data.get("user_details", {}).get("name", ""),
            "email": child_data.get("user_details", {}).get("email", ""),
            "status": "pending",
            "timestamp": datetime.datetime.now().isoformat()
        })

        return {"status": "success", "message": f"Link request sent to child {req.child_id}"}
    except Exception as e:
        logger.error(f"Error requesting child link: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

# 3. Fetch pending requests for child
@app.post("/fetch-pending-requests")
def fetch_pending_requests(req: TokenRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        child_uid = get_custom_uid(decoded["uid"])

        user_ref = db.reference(f"users/{child_uid}")
        user_data = user_ref.get()
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can fetch pending requests")

        pending_requests = user_data.get("pending_parent_requests", {})
        return {"status": "success", "data": pending_requests}
    except Exception as e:
        logger.error(f"Error fetching pending requests: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

# 4. Handle parent request
@app.post("/handle-parent-request")
def handle_parent_request(req: HandleParentRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        child_uid = get_custom_uid(decoded["uid"])
        child_ref = db.reference(f"users/{child_uid}")
        child_data = child_ref.get()

        if not child_data or child_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can handle parent requests")

        if req.parent_id not in child_data.get("pending_parent_requests", {}):
            raise HTTPException(status_code=404, detail="Parent request not found")

        parent_ref = db.reference(f"users/{req.parent_id}")
        parent_data = parent_ref.get()
        if not parent_data:
            raise HTTPException(status_code=404, detail="Parent not found")

        if req.action.lower() == "allow":
            child_ref.child(f"parents/{req.parent_id}").set(True)
            parent_ref.child(f"children/{child_uid}").set(True)

            # Update statuses
            child_ref.child(f"pending_parent_requests/{req.parent_id}/status").set("approved")
            parent_ref.child(f"sent_requests/{child_uid}/status").set("approved")

            return {"status": "success", "message": "Parent approved successfully"}

        elif req.action.lower() == "decline":
            child_ref.child(f"pending_parent_requests/{req.parent_id}/status").set("declined")
            parent_ref.child(f"sent_requests/{child_uid}/status").set("declined")
            return {"status": "success", "message": "Parent request declined"}

        else:
            raise HTTPException(status_code=400, detail="Invalid action")
    except Exception as e:
        logger.error(f"Error handling parent request: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

# 5. Fetch child details for parent
@app.post("/fetch-child-details")
def fetch_child_details(req: TokenRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        parent_uid = get_custom_uid(decoded["uid"])

        parent_ref = db.reference(f"users/{parent_uid}")
        parent_data = parent_ref.get()
        if not parent_data or parent_data.get("user_details", {}).get("account_type") != "family":
            raise HTTPException(status_code=403, detail="Only family accounts can fetch child details")

        children = parent_data.get("children", {})
        result = {}
        for child_id in children:
            child_ref = db.reference(f"users/{child_id}")
            child_data = child_ref.get()
            result[child_id] = {
                "user_details": child_data.get("user_details", {}),
                "health_info": child_data.get("health_info", {}),
                "health_track": child_data.get("health_track", {})
            }
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"Error fetching child details: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

# 6. Fetch sent requests for parent
@app.post("/fetch-parent-requests")
def fetch_parent_requests(req: TokenRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        parent_uid = get_custom_uid(decoded["uid"])

        ref = db.reference(f"users/{parent_uid}/sent_requests")
        return {"status": "success", "data": ref.get() or {}}
    except Exception as e:
        logger.error(f"Error fetching parent requests: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

# 7. Delete a request (child side)
@app.post("/delete-request")
def delete_request(req: DeleteRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        child_uid = get_custom_uid(decoded["uid"])

        db.reference(f"users/{child_uid}/pending_parent_requests/{req.target_id}").delete()
        db.reference(f"users/{req.target_id}/sent_requests/{child_uid}").delete()

        return {"status": "success", "message": "Request deleted from both sides"}
    except Exception as e:
        logger.error(f"Error deleting request: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

# 8. Fetch children linked to parent (send parent_id)
@app.post("/linked-children")
def linked_children(req: FetchLinkedChildrenRequest):
    try:
        parent_ref = db.reference(f"users/{req.parent_id}")
        parent_data = parent_ref.get()
        if not parent_data or parent_data.get("user_details", {}).get("account_type") != "family":
            raise HTTPException(status_code=403, detail="Invalid parent")

        children = parent_data.get("children", {})
        return {"status": "success", "children": list(children.keys())}
    except Exception as e:
        logger.error(f"Error fetching linked children: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

# 9. Check link status
@app.post("/check-link-status")
def check_link_status(req: CheckLinkStatusRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        parent_uid = get_custom_uid(decoded["uid"])
        status_ref = db.reference(f"users/{parent_uid}/sent_requests/{req.child_id}/status")
        status = status_ref.get()
        return {"status": "success", "link_status": status or "not_requested"}
    except Exception as e:
        logger.error(f"Error checking link status: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

@app.get("/fetch-child-details")
def fetch_child_details(req: TokenRequest):
    try:
        decoded = auth.verify_id_token(req.idToken)
        parent_uid = get_custom_uid(decoded["uid"])
        parent_ref = db.reference(f"users/{parent_uid}")
        parent_data = parent_ref.get()
        if not parent_data or parent_data.get("user_details", {}).get("account_type") != "family":
            raise HTTPException(status_code=403, detail="Only family accounts can fetch child details")
        children = parent_data.get("children", {})
        child_details = {}
        for child_id in children:
            child_ref = db.reference(f"users/{child_id}")
            child_data = child_ref.get()
            if child_data:
                child_details[child_id] = {
                    "user_details": child_data.get("user_details", {}),
                    "health_info": child_data.get("health_info", {}),
                    "health_track": child_data.get("health_track", {})
                }
        return {"status": "success", "data": child_details}
    except Exception as e:
        logger.error(f"Error fetching child details: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))
@app.post("/mood-analysis")
async def mood_analysis(req: LinkChildRequest):
    try:
        # Verify parent's token
        decoded = auth.verify_id_token(req.idToken)
        parent_uid = get_custom_uid(decoded["uid"])
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
@app.post("/conversation-summary")
async def conversation_summary(req: LinkChildRequest):
    try:
        # Verify parent's token
        decoded = auth.verify_id_token(req.idToken)
        parent_uid = get_custom_uid(decoded["uid"])
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
def calculate_weights(items: List[str], usage_counts: Dict[str, int], default_weight: float = 1.0) -> List[float]:
    try:
        weights = []
        max_count = max(usage_counts.values(), default=1) if usage_counts else 1
        for item in items:
            count = usage_counts.get(item, 0)
            # Inverse frequency: less recently used items get higher weight
            weight = default_weight / (1 + count / max_count)
            weights.append(weight)
        return weights
    except Exception as e:
        logger.error(f"Error calculating weights: {str(e)}")
        return [default_weight] * len(items)

# Helper function to check if current time is within 1-hour window of reminder time
def is_within_one_hour(reminder_time: str, current_time: datetime, threshold_minutes: int = 60) -> bool:
    try:
        if not reminder_time:
            return False
        # Extract time part from ISO 8601 or HH:MM format
        if 'T' in reminder_time:
            reminder_dt = datetime.fromisoformat(reminder_time.replace('Z', '+00:00'))
            reminder_hour, reminder_minute = reminder_dt.hour, reminder_dt.minute
        else:
            reminder_hour, reminder_minute = map(int, reminder_time.split(":"))
        current_hour, current_minute = current_time.hour, current_time.minute
        reminder_minutes = reminder_hour * 60 + reminder_minute
        current_minutes = current_hour * 60 + current_minute
        return abs(reminder_minutes - current_minutes) <= threshold_minutes
    except Exception as e:
        logger.error(f"Error parsing reminder time {reminder_time}: {str(e)}")
        return False

# Helper function to check if current time exactly matches reminder time (1-minute window)
def is_exact_reminder_time(reminder_time: str, current_time: datetime, threshold_minutes: int = 1) -> bool:
    try:
        if not reminder_time:
            return False
        # Extract time part from ISO 8601 or HH:MM format
        if 'T' in reminder_time:
            reminder_dt = datetime.fromisoformat(reminder_time.replace('Z', '+00:00'))
            reminder_hour, reminder_minute = reminder_dt.hour, reminder_dt.minute
        else:
            reminder_hour, reminder_minute = map(int, reminder_time.split(":"))
        current_hour, current_minute = current_time.hour, current_time.minute
        reminder_minutes = reminder_hour * 60 + reminder_minute
        current_minutes = current_hour * 60 + current_minute
        return abs(reminder_minutes - current_minutes) <= threshold_minutes
    except Exception as e:
        logger.error(f"Error parsing reminder time {reminder_time}: {str(e)}")
        return False

# Helper function to check if current time is after the reminder time
def is_after_reminder_time(reminder_time: str, current_time: datetime) -> bool:
    try:
        if not reminder_time:
            return False
        # Extract time part from ISO 8601 or HH:MM format
        if 'T' in reminder_time:
            reminder_dt = datetime.fromisoformat(reminder_time.replace('Z', '+00:00'))
            reminder_hour, reminder_minute = reminder_dt.hour, reminder_dt.minute
        else:
            reminder_hour, reminder_minute = map(int, reminder_time.split(":"))
        current_hour, current_minute = current_time.hour, current_time.minute
        reminder_minutes = reminder_hour * 60 + reminder_minute
        current_minutes = current_hour * 60 + current_minute
        return current_minutes > reminder_minutes
    except Exception as e:
        logger.error(f"Error parsing reminder time {reminder_time}: {str(e)}")
        return False

# Helper function to check if refill date is near
def is_refill_date_near(refill_date: str, current_date: datetime, threshold_days: int = 3) -> bool:
    try:
        if not refill_date:
            return False
        # Parse ISO 8601 format (e.g., 2025-06-16T20:06:41.452Z)
        refill_dt = datetime.fromisoformat(refill_date.replace('Z', '+00:00'))
        refill = refill_dt.date()
        current = current_date.date()
        delta = (refill - current).days
        return 0 <= delta <= threshold_days
    except Exception as e:
        logger.error(f"Error parsing refill date {refill_date}: {str(e)}")
        return False

# Helper function to detect if user is asking to list reminders
def is_list_reminders_request(reply: str) -> bool:
    if not reply:
        return False
    reply_lower = reply.lower()
    keywords = ["list all reminders", "show my reminders", "what are my reminders", 
                "medicine schedule", "reminder list", "all medicine times"]
    return any(keyword in reply_lower for keyword in keywords)

# Helper function to format reminder list
def format_reminder_list(medicine_reminders: List[dict]) -> str:
    if not medicine_reminders:
        return "You have no medicine reminders set."
    reminder_list = "Here are your medicine reminders:\n"
    for reminder in medicine_reminders:
        if not isinstance(reminder, dict):
            continue
        med_name = reminder.get("medicine_name", "Unknown")
        time = reminder.get("time", "No time set")
        # Extract time part if in ISO 8601 format
        if 'T' in time:
            try:
                time_dt = datetime.fromisoformat(time.replace('Z', '+00:00'))
                time = time_dt.strftime("%H:%M")
            except:
                time = "Invalid time format"
        refill_date = reminder.get("set_refill_date", "No refill date set")
        # Extract date part if in ISO 8601 format
        if 'T' in refill_date:
            try:
                refill_dt = datetime.fromisoformat(refill_date.replace('Z', '+00:00'))
                refill_date = refill_dt.strftime("%Y-%m-%d")
            except:
                refill_date = "Invalid refill date"
        reminder_list += f"- {med_name} at {time}, Refill due: {refill_date}\n"
    return reminder_list



@app.post("/proactive-talk", response_model=ProactiveTalkResponse)
async def proactive_talk(req: ProactiveTalkRequest):
    try:
        # Verify user authentication
        decoded = auth.verify_id_token(req.idToken)
        custom_uid = get_custom_uid(decoded["uid"])
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        if not user_data or not isinstance(user_data, dict):
            logger.error(f"No user data found for UID: {custom_uid}")
            raise HTTPException(status_code=404, detail="User data not found or invalid")

        user_details = user_data.get("user_details", {})
        if user_details.get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can use proactive talk")

        # Fetch user details with fallback for None
        user_name = user_details.get("name", "there") if isinstance(user_details, dict) else "there"
        hobbies = user_details.get("hobbies", "no hobbies specified") if isinstance(user_details, dict) else "no hobbies specified"
        age = user_details.get("age", "unknown") if isinstance(user_details, dict) else "unknown"
        medical_history = user_details.get("medical_history", None) if isinstance(user_details, dict) else None
        weight = user_details.get("weight", None) if isinstance(user_details, dict) else None
        height = user_details.get("height", None) if isinstance(user_details, dict) else None
        health_track = user_data.get("health_track", {}) if isinstance(user_data, dict) else {}
        medicines = health_track.get("medicines", []) if isinstance(health_track, dict) else []
        medicine_reminders = health_track.get("medicine_reminders", []) if isinstance(health_track, dict) else []

        current_time = datetime.datetime.now(india_tz)
        current_date = current_time.date().isoformat()

        # Initialize voice history
        voice_ref = user_ref.child("voice_history")
        voice_data = voice_ref.get() or {"history": [], "asked_reminders": {}, "category_usage": {}, "subcategory_usage": {}}
        if not isinstance(voice_data, dict):
            logger.error(f"Invalid voice_data for UID: {custom_uid}")
            voice_data = {"history": [], "asked_reminders": {}, "category_usage": {}, "subcategory_usage": {}}
        voice_history = voice_data.get("history", [])
        asked_reminders = voice_data.get("asked_reminders", {})
        category_usage = voice_data.get("category_usage", {})
        subcategory_usage = voice_data.get("subcategory_usage", {})

        # Initialize important asked questions
        imp_questions_ref = user_ref.child("imp_ask_question")
        imp_questions_data = imp_questions_ref.get() or {"entries": []}
        if not isinstance(imp_questions_data, dict):
            logger.error(f"Invalid imp_questions_data for UID: {custom_uid}")
            imp_questions_data = {"entries": []}
        imp_questions = imp_questions_data.get("entries", [])

        # Format user context for personalization
        medicines_summary = (
            ", ".join([f"{med.get('medicine_name', 'unknown')} ({med.get('dosage', 'unknown')})" 
                       for med in medicines if isinstance(med, dict)]) if medicines else "no medications recorded"
        )
        reminders_summary = (
            ", ".join([f"{rem.get('medicine_name', 'unknown')} at {rem.get('time', 'unknown')}" 
                       for rem in medicine_reminders if isinstance(rem, dict) and rem.get("time")]) if medicine_reminders else "no reminders set"
        )
        medical_context = ""
        if medical_history:
            medical_context += f"Their medical history includes: {medical_history}. "
        if weight:
            medical_context += f"Their weight is {weight}. "
        if height:
            medical_context += f"Their height is {height}. "

        response_content = None
        response_key = "response"
        is_category_question = False
        selected_category = None
        selected_subcategory = None

        # Store the last assistant question for reference (to associate with user reply)
        last_question = voice_history[-1].get("content", "") if voice_history and isinstance(voice_history[-1], dict) and voice_history[-1].get("role") == "assistant" and voice_history[-1].get("type") == "question" else None
        last_question_is_category = voice_history[-1].get("is_category_question", False) if voice_history and isinstance(voice_history[-1], dict) and voice_history[-1].get("role") == "assistant" and voice_history[-1].get("type") == "question" else False

        # If the user provides a reply, save it to voice history and imp_ask_question if applicable
        if req.reply and req.reply.strip():
            if len(req.reply) > MAX_MESSAGE_LENGTH:
                raise HTTPException(
                    status_code=400,
                    detail=f"Reply exceeds maximum length of {MAX_MESSAGE_LENGTH} characters"
                )

            # Save reply to voice history
            voice_history.append({
                "role": "user",
                "content": req.reply,
                "timestamp": current_time.isoformat(),
                "type": "response"
            })
            if len(voice_history) > MAX_HISTORY_LENGTH:
                voice_history = voice_history[-MAX_HISTORY_LENGTH:]

            # If replying to a category-based question, save to imp_ask_question
            if last_question and last_question_is_category:
                imp_questions.append({
                    "question": last_question,
                    "reply": req.reply,
                    "question_timestamp": voice_history[-2].get("timestamp", current_time.isoformat()) if len(voice_history) >= 2 else current_time.isoformat(),
                    "reply_timestamp": current_time.isoformat()
                })
                imp_questions_data["entries"] = imp_questions
                imp_questions_ref.set(imp_questions_data)

            # Check if the user is asking to list reminders
            if is_list_reminders_request(req.reply):
                response_content = format_reminder_list(medicine_reminders)
                voice_history.append({
                    "role": "assistant",
                    "content": response_content,
                    "timestamp": current_time.isoformat(),
                    "type": "response"
                })
                voice_data["history"] = voice_history
                voice_ref.set(voice_data)
                return ProactiveTalkResponse(
                    status="success",
                    response=response_content,
                    timestamp=current_time.isoformat()
                )

        # Check for medication reminders (within 1-hour window or post-reminder)
        medication_question = None
        for reminder in medicine_reminders:
            if not isinstance(reminder, dict) or not reminder.get("time"):
                continue
            reminder_id = reminder.get("reminder_id", str(hash(reminder.get("medicine_name", "") + reminder.get("time", ""))))
            reminder_time = reminder.get("time")
            asked_today = asked_reminders.get(reminder_id, {}).get("date") == current_date

            # Question 1: Within 1-hour window
            if is_within_one_hour(reminder_time, current_time) and not asked_reminders.get(reminder_id, {}).get("within_hour_asked"):
                if is_exact_reminder_time(reminder_time, current_time):
                    medication_question = f"Hey {user_name}, it’s time for your {reminder.get('medicine_name', 'medication')}. Have you taken it yet?"
                    asked_reminders[reminder_id] = asked_reminders.get(reminder_id, {})
                    asked_reminders[reminder_id]["within_hour_asked"] = True
                    asked_reminders[reminder_id]["date"] = current_date
                    break
            # Question 2: After reminder time
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
            # Check for refill date questions
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
                # Generate a response to the user's reply or a general personalized question
                if req.reply and req.reply.strip():
                    response_prompt = {
                        "role": "system",
                        "content": (
                            f"You are a caring, empathetic best friend for {user_name}, who is {age} years old and enjoys {hobbies}. "
                            f"{medical_context}"
                            f"They are taking the following medications: {medicines_summary}. "
                            f"Their medicine reminders are: {reminders_summary}. "
                            f"The current time is {current_time.strftime('%H:%M')}. "
                            f"The recent conversation history is: {json.dumps(voice_history[-5:])}. "
                            f"The user's latest reply is: '{req.reply}'. "
                            f"Generate a thoughtful response (not a question) to the user's reply, using their personal details like medicines, reminders, medical history, or hobbies to make the response relevant and personalized. "
                            f"Ensure the response is warm, supportive, and feels like it’s from someone who genuinely cares, focusing on the user's needs or interests mentioned in their reply. "
                            f"Do NOT ask a question unless the reply explicitly requires clarification. "
                            f"Return only the response as a string."
                        )
                    }
                    response_response = await client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[response_prompt]
                    )
                    response_content = response_response.choices[0].message.content
                    response_key = "response"
                else:
                    # Select a category and subcategory with weighted random selection
                    categories = list(CATEGORIES_WITH_SUBCATEGORIES.keys())
                    category_weights = calculate_weights(categories, category_usage)
                    selected_category = random.choices(categories, weights=category_weights, k=1)[0]
                    subcategories = CATEGORIES_WITH_SUBCATEGORIES[selected_category]
                    subcategory_weights = calculate_weights(subcategories, subcategory_usage)
                    selected_subcategory = random.choices(subcategories, weights=subcategory_weights, k=1)[0]

                    # Update usage counts
                    category_usage[selected_category] = category_usage.get(selected_category, 0) + 1
                    subcategory_usage[selected_subcategory] = subcategory_usage.get(selected_subcategory, 0) + 1

                    question_prompt = {
                        "role": "system",
                        "content": (
                            f"You are a caring, empathetic best friend for {user_name}, who is {age} years old and enjoys {hobbies}. "
                            f"{medical_context}"
                            f"They are taking the following medications: {medicines_summary}. "
                            f"Their medicine reminders are: {reminders_summary}. "
                            f"The current time is {current_time.strftime('%H:%M')}. "
                            f"The recent conversation history is: {json.dumps(voice_history[-5:])}. "
                            f"Generate a single, engaging, casual question for the category '{selected_category}' and subcategory '{selected_subcategory}' to interact with {user_name} as a best friend would. "
                            f"Ensure the question: "
                            f"1. Is strictly relevant to the category '{selected_category}' and subcategory '{selected_subcategory}'. "
                            f"2. Is light, friendly, and personal, encouraging them to share about their day, feelings, or experiences. "
                            f"3. Uses their hobbies (e.g., {hobbies}), age, or medical history (if relevant) to personalize the question. "
                            f"4. Is completely unique and distinct from previous questions in the conversation history, avoiding any repetition in content, tone, or semantics (e.g., avoid rephrasing questions like 'What made you smile today?'). "
                            f"5. For subcategory examples: "
                            f"   - Health and Medicine > Medication intake: 'Have you found a good way to keep track of your meds, {user_name}?'. "
                            f"   - Emotional State and Well-being > Mood tracking: 'What’s been lifting your spirits today, {user_name}?'. "
                            f"   - Companion and Social Interaction > Friendship talk: 'Have you caught up with a friend recently, {user_name}?'. "
                            f"   - Reminders Questions > Hydration check: 'Have you had enough water today, {user_name}?'. "
                            f"   - Practical Support and Request > Help with devices: 'Need any help with your phone or tablet today, {user_name}?'. "
                            f"   - Health Motion > Stretching or movement prompts: 'Have you tried any gentle stretches today, {user_name}?'. "
                            f"   - Best Caring > Comfort level: 'Are you feeling cozy and comfortable today, {user_name}?'. "
                            f"   - Memory and Life Reflection > Childhood memories: 'What’s a fun childhood memory that popped into your head today, {user_name}?'. "
                            f"   - Daily Routine Check-ins > Meals taken: 'What did you have for lunch today, {user_name}?'. "
                            f"   - Nutrition and Meal-related > Water intake: 'Have you been sipping water throughout the day, {user_name}?'. "
                            f"6. Do NOT ask about taking medication or refilling medication, as these are handled separately. "
                            f"Return only the question as a string."
                        )
                    }
                    question_response = await client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[question_prompt]
                    )
                    response_content = question_response.choices[0].message.content
                    response_key = "question"
                    is_category_question = True

        # Append assistant response/question to voice history
        voice_history.append({
            "role": "assistant",
            "content": response_content,
            "timestamp": current_time.isoformat(),
            "type": response_key,
            "is_category_question": is_category_question,
            "category": selected_category,
            "subcategory": selected_subcategory
        })
        if len(voice_history) > MAX_HISTORY_LENGTH:
            voice_history = voice_history[-MAX_HISTORY_LENGTH:]

        # Update voice history, asked reminders, and usage counts in database
        voice_data["history"] = voice_history
        voice_data["asked_reminders"] = asked_reminders
        voice_data["category_usage"] = category_usage
        voice_data["subcategory_usage"] = subcategory_usage
        voice_ref.set(voice_data)

        # Return response
        return ProactiveTalkResponse(
            status="success",
            response=response_content,
            timestamp=current_time.isoformat()
        )

    except auth.InvalidIdTokenError:
        logger.error("Invalid Firebase ID token")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except auth.ExpiredIdTokenError:
        logger.error("Expired Firebase ID token")
        raise HTTPException(status_code=401, detail="Token has expired")
    except Exception as e:
        logger.error(f"Error in proactive-talk endpoint: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
async def generate_random_question_for_user(custom_uid: str, user_name: str, chat_ref):
    try:
        # Fetch user data for personalization
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get() or {}
        user_details = user_data.get("user_details", {})
        hobbies = user_details.get("hobby", "no hobbies specified")
        age = user_details.get("age", "unknown")
        medicines = user_data.get("health_track", {}).get("medicines", [])
        health_metrics = user_data.get("health_track", {}).get("health_metrics", [])

        # Format user context
        medicines_summary = (
            ", ".join([f"{med.get('medicine_name', 'unknown')} ({med.get('dosage', 'unknown')})" 
                       for med in medicines]) if medicines else "no medications recorded"
        )
        health_metrics_summary = (
            ", ".join([f"{metric.get('metric', 'unknown')}: {metric.get('data', 'unknown')}" 
                       for metric in health_metrics]) if health_metrics else "no health metrics recorded"
        )

        # Create system prompt for personalized question
        system_prompt = {
            "role": "system",
            "content": (
                f"You are a friendly assistant creating a personalized question for {user_name}, who is {age} years old "
                f"and enjoys {hobbies}. They are taking the following medications: {medicines_summary}. "
                f"Their recent health metrics include: {health_metrics_summary}. Generate a simple, friendly, and relevant "
                f"question based on this information. For example, ask about their hobbies, remind them about medications, "
                f"or inquire about their health metrics. Ensure the question is engaging and easy to reply to."
            )
        }
        prompt_text = (
            "Generate a simple, friendly question for the user based on their profile and the following examples:\n"
            + "\n".join(f"- {q}" for q in SAMPLE_QUESTIONS)
            + "\nMake it personalized, relevant, and easy to reply to."
        )
        messages = [system_prompt, {"role": "user", "content": prompt_text}]
        
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages
        )
        question = response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error generating question for {custom_uid}: {str(e)}")
        question = random.choice(SAMPLE_QUESTIONS)
    
    chat = chat_ref.get() or {"history": [], "greeted": False}
    chat["history"].append({"role": "assistant", "content": question, "system_generated": True})
    chat_ref.set(chat)
    
    user_ref = db.reference(f"users/{custom_uid}")
    push_token = user_ref.child("push_token").get()
    if push_token:
        message = messaging.Message(
            notification=messaging.Notification(
                title="New Question",
                body=question
            ),
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    channel_id="default_channel",
                    sound="default"
                )
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        sound="default"
                    )
                )
            ),
            token=push_token
        )
        try:
            response = messaging.send(message)
            logger.info(f"Notification sent to user {custom_uid}: {question}, Response: {response}")
        except Exception as e:
            logger.error(f"Failed to send notification to user {custom_uid}: {str(e)}")
    else:
        logger.info(f"No push token found for user {custom_uid}")
    return question

async def schedule_daily_question():
    try:
        logger.info("Running schedule_daily_question")
        users_ref = db.reference("users")
        users = users_ref.get()
        if not users:
            logger.info("No users found in Firebase")
            return
        for custom_uid, user_data in users.items():
            user_name = user_data.get("user_details", {}).get("name", "there")
            chat_ref = db.reference(f"users/{custom_uid}/chat")
            await generate_random_question_for_user(custom_uid, user_name, chat_ref)
    except Exception as e:
        logger.error(f"Error in schedule_daily_question: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
    