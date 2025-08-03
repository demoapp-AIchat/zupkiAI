import os
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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
from twilio.twiml.voice_response import VoiceResponse, Connect, Say
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

# Include routers
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
@app.post("/schedule-call")
async def schedule_call():
    """Initiate a call to the user with AI interaction."""
    try:
        response = VoiceResponse()
        response.say("Hello, this is your AI assistant. Please speak, and I’ll respond to you.", voice="Polly.Joanna")
        connect = Connect()
        connect.stream(url=f"wss://{os.getenv('AWS_BASE_URL').replace('https://', '')}/media-stream")
        response.append(connect)
        
        twiml = str(response)
        logger.info(f"TwiML Response: {twiml}")
        call = client.calls.create(
            twiml=twiml,
            to=os.getenv("YOUR_PHONE_NUMBER"),
            from_=os.getenv("TWILIO_PHONE_NUMBER"),
        )
        logger.info(f"Call initiated: {call.sid} at {pendulum.now('Asia/Kolkata').to_datetime_string()}")
        return {"message": "Call initiated successfully", "call_sid": call.sid}
    except Exception as e:
        logger.error(f"Error initiating call: {e}")
        return {"message": "Failed to initiate call", "error": str(e)}

@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI Realtime API."""
    await websocket.accept()
    logger.info(f"Twilio WebSocket connected from {websocket.client} at {pendulum.now('Asia/Kolkata').to_datetime_string()}")

    try:
        async with openai_client.beta.realtime.connect(model="gpt-4o-realtime-preview") as connection:
            logger.info("Connected to OpenAI Realtime API")
            # Update session configuration
            await connection.session.update(session={
                "turn_detection": {"type": "server_vad"},
                "input_audio_format": "g711_ulaw",
                "output_audio_format": "g711_ulaw",
                "voice": "alloy",
                "instructions": "You are a friendly AI assistant. Listen to the user’s response and reply back politely and conversationally.",
                "modalities": ["text", "audio"],
            })
            logger.info("OpenAI session initialized")

            stream_sid = None
            first_audio_received = False
            stop_received = False

            async def receive_from_twilio():
                nonlocal stream_sid, first_audio_received, stop_received
                try:
                    async for message in websocket.iter_text():
                        data = json.loads(message)
                        logger.info(f"Received from Twilio: {data['event']} | Data: {data}")
                        if data["event"] == "media":
                            await connection.send({
                                "type": "input_audio_buffer.append",
                                "audio": data["media"]["payload"]
                            })
                            logger.info("Sent audio to OpenAI")
                            if not first_audio_received:
                                await connection.conversation.item.create(
                                    item={
                                        "type": "message",
                                        "role": "user",
                                        "content": [{"type": "input_text", "text": "Please respond to my voice input."}]
                                    }
                                )
                                await connection.response.create()
                                logger.info("Initialized conversation and triggered OpenAI response")
                                first_audio_received = True
                        elif data["event"] == "start":
                            stream_sid = data["start"]["streamSid"]
                            logger.info(f"Incoming stream started: {stream_sid}")
                        elif data["event"] == "stop":
                            logger.info("Twilio sent stop event, ending receive loop.")
                            stop_received = True
                            break
                except WebSocketDisconnect:
                    logger.info("Twilio WebSocket disconnected")
                    await connection.disconnect()
                except Exception as e:
                    logger.error(f"Error in receive_from_twilio: {e}")

            async def send_to_twilio():
                nonlocal stream_sid, stop_received
                try:
                    async for event in connection:
                        logger.info(f"Received from OpenAI: {event.type}")
                        if event.type == "response.audio.delta":
                            audio_payload = event.delta
                            await websocket.send_json({
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": audio_payload}
                            })
                            logger.info(f"Sent audio delta to Twilio for streamSid: {stream_sid}")
                        elif event.type == "response.done":
                            logger.info("OpenAI response completed")
                            break
                        elif event.type == "error":
                            logger.error(f"OpenAI error: {event.error.message}")
                        # Optionally handle other event types for debugging
                except Exception as e:
                    logger.error(f"Error in send_to_twilio: {e}")

            await asyncio.gather(receive_from_twilio(), send_to_twilio())
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception as e:
            logger.error(f"Error closing websocket: {e}")
        logger.info("WebSocket closed")

# Remove legacy recording endpoints
def initiate_call():
    pass

async def get_openai_response(transcription: str, call_sid: str) -> str:
    pass

async def fetch_transcription(recording_sid: str, call_sid: str) -> str:
    pass