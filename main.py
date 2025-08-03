import os
import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pytz import timezone
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, db
import base64
import json
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client
from openai import AsyncOpenAI
import pendulum
import asyncio
from endpoints.auth import router as auth_router
from endpoints.user import router as user_router
from endpoints.health import router as health_router
from endpoints.reminders import router as reminders_router
from endpoints.chat import router as chat_router, schedule_daily_question
from endpoints.todo import router as todo_router
from endpoints.mood import router as mood_router
from endpoints.conversation import router as conversation_router

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

# Debug environment variables
logger.info(f"OPENAI_API_KEY: {os.getenv('OPENAI_API_KEY')[:10]}...")
logger.info(f"FIREBASE_DB_URL: {os.getenv('FIREBASE_DB_URL')}")
logger.info(f"FIREBASE_API_KEY: {os.getenv('FIREBASE_API_KEY')}")
logger.info(f"TWILIO_ACCOUNT_SID: {os.getenv('TWILIO_ACCOUNT_SID')}")
logger.info(f"TWILIO_AUTH_TOKEN: {os.getenv('TWILIO_AUTH_TOKEN')[:10]}...")
logger.info(f"TWILIO_PHONE_NUMBER: {os.getenv('TWILIO_PHONE_NUMBER')}")
logger.info(f"YOUR_PHONE_NUMBER: {os.getenv('YOUR_PHONE_NUMBER')}")
logger.info(f"AWS_BASE_URL: {os.getenv('AWS_BASE_URL')}")

# Verify environment variables
required_vars = ["OPENAI_API_KEY", "FIREBASE_DB_URL", "FIREBASE_CRED_BASE64", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "YOUR_PHONE_NUMBER", "AWS_BASE_URL"]
missing_vars = [var for var in required_vars if not os.getenv(var)]
if missing_vars:
    raise RuntimeError(f"Missing environment variables: {', '.join(missing_vars)}")

# Initialize Firebase
try:
    firebase_dict = json.loads(base64.b64decode(os.getenv("FIREBASE_CRED_BASE64")).decode())
    cred = credentials.Certificate(firebase_dict)
    firebase_admin.initialize_app(cred, {"databaseURL": os.getenv("FIREBASE_DB_URL")})
except Exception as e:
    raise RuntimeError(f"Failed to initialize Firebase: {str(e)}")

# Initialize Twilio
client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

# Initialize OpenAI
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Initialize FastAPI app
app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize AsyncIOScheduler
india_tz = timezone("Asia/Kolkata")
scheduler = AsyncIOScheduler(timezone=india_tz)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the lifecycle of the FastAPI app, starting and stopping the scheduler."""
    logger.info("Starting scheduler")
    scheduler.add_job(schedule_daily_question, 'interval', hours=3)
    scheduler.start()
    yield
    scheduler.shutdown()
    logger.info("Scheduler stopped")       

app = FastAPI(lifespan=lifespan)

# Include routers from endpoint modules
app.include_router(auth_router, prefix="")
app.include_router(user_router, prefix="")
app.include_router(health_router, prefix="")
app.include_router(reminders_router, prefix="")
app.include_router(chat_router, prefix="")
app.include_router(todo_router, prefix="")
app.include_router(mood_router, prefix="")
app.include_router(conversation_router, prefix="")

# Health check endpoint
@app.get("/")
def health_check():
    """Health check endpoint to verify API is running."""
    return {"status": "API is working!"}

# Call automation endpoints
async def get_openai_response(transcription: str, call_sid: str) -> str:
    """Generate AI response using OpenAI."""
    try:
        response = await openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "user",
                    "content": f"Your mom said: '{transcription}'. Respond politely as an AI assistant calling on behalf of her child, acknowledging her response about eating. Keep it short and conversational."
                }
            ],
            max_tokens=50,
            temperature=0.5
        )
        ai_response = response.choices[0].message.content.strip()
        logger.info(f"Call SID: {call_sid} | OpenAI response for transcription '{transcription}': {ai_response}")
        return ai_response or "Thank you, I’ll let your child know."
    except Exception as e:
        logger.error(f"Call SID: {call_sid} | Error fetching OpenAI response: {str(e)}")
        return "Thank you, I’ll let your child know."

async def fetch_transcription(recording_sid: str, call_sid: str) -> str:
    """Fetch transcription from Twilio API."""
    try:
        for _ in range(3):  # Retry up to 3 times
            transcriptions = client.transcriptions.list(recording_sid=recording_sid)
            if transcriptions:
                transcription = transcriptions[0]
                if transcription.status == "completed":
                    logger.info(f"Call SID: {call_sid} | Transcription fetched: {transcription.transcription_text}")
                    return transcription.transcription_text
                elif transcription.status == "in-progress":
                    logger.info(f"Call SID: {call_sid} | Transcription in progress, retrying...")
            await asyncio.sleep(2)  # Wait 2 seconds
        logger.warning(f"Call SID: {call_sid} | Transcription not available after retries")
        return "No transcription available"
    except Exception as e:
        logger.error(f"Call SID: {call_sid} | Error fetching transcription: {str(e)}")
        return "No transcription available"

def initiate_call():
    """Initiate a Twilio call."""
    try:
        call = client.calls.create(
            twiml=f"""
                <Response>
                    <Say voice="Polly.Joanna">Hello, this is your AI assistant calling on behalf of your child. Have you eaten today?</Say>
                    <Record action="{os.getenv('AWS_BASE_URL')}/handle-response" maxLength="30" transcribe="true" transcribeCallback="{os.getenv('AWS_BASE_URL')}/transcription" timeout="5" finishOnKey="#"/>
                </Response>
            """,
            to=os.getenv("YOUR_PHONE_NUMBER"),
            from_=os.getenv("TWILIO_PHONE_NUMBER"),
        )
        logger.info(f"Call initiated: {call.sid} at {pendulum.now('Asia/Kolkata').to_datetime_string()}")
        return {"status": "success", "call_sid": call.sid}
    except Exception as e:
        logger.error(f"Error initiating call: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/schedule-call")
async def schedule_call():
    """Initiate a call to check if the parent has eaten."""
    result = initiate_call()
    if result["status"] == "success":
        logger.info(f"Call triggered immediately | Call SID: {result['call_sid']} at {pendulum.now('Asia/Kolkata').to_datetime_string()}")
        return {"message": "Call initiated successfully", "call_sid": result["call_sid"]}
    else:
        return {"message": "Failed to initiate call", "error": result["message"]}

@app.post("/handle-response")
async def handle_response(request: Request):
    """Handle Twilio call response and generate summary."""
    form_data = await request.form()
    call_sid = form_data.get("CallSid", "Unknown call SID")
    recording_url = form_data.get("RecordingUrl", "No recording URL")
    recording_sid = form_data.get("RecordingSid", "Unknown recording SID")
    recording_duration = form_data.get("RecordingDuration", "Unknown duration")
    transcription = form_data.get("TranscriptionText", None)
    
    logger.info(f"Handle response triggered for Call SID: {call_sid} at {pendulum.now('Asia/Kolkata').to_datetime_string()}")
    logger.info(f"Recording URL: {recording_url}")
    logger.info(f"Recording SID: {recording_sid}")
    logger.info(f"Recording Duration: {recording_duration} seconds")
    
    # Fetch transcription if not provided
    if not transcription:
        transcription = await fetch_transcription(recording_sid, call_sid)
    
    ai_response = await get_openai_response(transcription, call_sid)
    logger.info(f"*** Call Summary *** Call SID: {call_sid}, Transcription: {transcription}, AI Response: {ai_response}, Recording URL: {recording_url}, Recording Duration: {recording_duration} seconds")
    
    # Store in Firebase Realtime Database
    try:
        ref = db.reference("callResponses")
        ref.push({
            "transcription": transcription,
            "aiResponse": ai_response,
            "timestamp": pendulum.now("Asia/Kolkata").to_iso8601_string(),
            "phoneNumber": os.getenv("YOUR_PHONE_NUMBER"),
            "callSid": call_sid,
            "recordingSid": recording_sid,
            "recordingDuration": recording_duration,
        })
        logger.info(f"Call SID: {call_sid} | Call summary stored in Firebase")
    except Exception as e:
        logger.error(f"Call SID: {call_sid} | Error storing call summary: {e}")
    
    twiml = VoiceResponse()
    twiml.say(ai_response, voice="Polly.Joanna")
    twiml.hangup()
    return HTMLResponse(content=str(twiml), media_type="text/xml")

@app.post("/transcription")
async def transcription(request: Request):
    """Handle Twilio transcription callback."""
    form_data = await request.form()
    call_sid = form_data.get("CallSid", "Unknown call SID")
    recording_sid = form_data.get("RecordingSid", "Unknown recording SID")
    transcription_text = form_data.get("TranscriptionText", "No response recorded")
    
    logger.info(f"Transcription callback triggered for Call SID: {call_sid} at {pendulum.now('Asia/Kolkata').to_datetime_string()}")
    logger.info(f"Transcription: {transcription_text}")
    logger.info(f"Recording SID: {recording_sid}")
    
    ai_response = await get_openai_response(transcription_text, call_sid)
    logger.info(f"*** Call Summary *** Call SID: {call_sid}, Transcription: {transcription_text}, AI Response: {ai_response}")
    
    # Store in Firebase Realtime Database
    try:
        ref = db.reference("callResponses")
        ref.push({
            "transcription": transcription_text,
            "aiResponse": ai_response,
            "timestamp": pendulum.now("Asia/Kolkata").to_iso8601_string(),
            "phoneNumber": os.getenv("YOUR_PHONE_NUMBER"),
            "callSid": call_sid,
            "recordingSid": recording_sid,
        })
        logger.info(f"Call SID: {call_sid} | Transcription stored in Firebase")
    except Exception as e:
        logger.error(f"Call SID: {call_sid} | Error storing transcription: {e}")
    
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting FastAPI server at {pendulum.now('Asia/Kolkata').to_datetime_string()}")
    uvicorn.run(app, host="0.0.0.0", port=8000)