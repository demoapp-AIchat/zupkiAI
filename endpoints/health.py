from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from database import verify_user_token, fetch_user_data
from helpers import india_tz
from firebase_admin import db
from models import AddHealthTrackRequest, GetLinkedUserTodoListsRequest, UpdateMultipleHealthTracksRequest, DeleteHealthTrackRequest
from firebase_admin.exceptions import FirebaseError
import datetime
import logging
import uuid
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

def validate_and_format_number(value: Optional[str], field_name: str) -> Optional[str]:
    """Validate that a string value is numeric and return it as a string."""
    if value is None:
        return None
    try:
        # Convert to float to ensure it's a valid number
        float_value = float(value)
        return str(float_value)
    except (ValueError, TypeError):
        logger.error(f"Invalid {field_name} value: {value}. Must be a numeric string (e.g., '90', '70.5').")
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} value: {value}. Must be a numeric string (e.g., '90', '70.5').")

def append_units(track_data: dict) -> dict:
    """Append units to sugar (mg/dL), weight (kg), and heart_rate (bpm)."""
    updated_data = track_data.copy()
    if updated_data.get('sugar') is not None:
        updated_data['sugar'] = f"{updated_data['sugar']} mg/dL"
    if updated_data.get('weight') is not None:
        updated_data['weight'] = f"{updated_data['weight']} kg"
    if updated_data.get('heart_rate') is not None:
        updated_data['heart_rate'] = f"{updated_data['heart_rate']} bpm"
    return updated_data

@router.post("/add-health-track")
async def add_health_track(req: AddHealthTrackRequest):
    """Add multiple health tracks for the user (up to 7), or for a linked user."""
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

        if not req.tracks or len(req.tracks) > 7:
            logger.error(f"Invalid number of tracks: {len(req.tracks)}")
            raise HTTPException(status_code=400, detail="You must provide between 1 and 7 tracks.")

        # Test Firebase connectivity
        try:
            test_ref = db.reference("test_connectivity")
            test_ref.set({"timestamp": now})
            logger.debug("Firebase connectivity test successful")
        except FirebaseError as e:
            logger.error(f"Firebase connectivity test failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to connect to Firebase: {str(e)}")

        saved_tracks = []

        for track in req.tracks:
            logger.debug(f"Processing health track: bp={track.bp}, sugar={track.sugar}, weight={track.weight}, heart_rate={track.heart_rate}")

            # Validate created_date
            try:
                track_date = datetime.datetime.fromisoformat(track.created_date.replace('Z', '+00:00')).date()
                logger.debug(f"Using created_date {track.created_date} for health track")
            except ValueError as e:
                logger.error(f"Invalid created_date format: {str(e)}")
                raise HTTPException(status_code=400, detail=f"Invalid created_date format. Must be ISO 8601 (e.g., '2025-08-14T01:38:00+05:30').")

            # Validate numeric string fields
            track_data = track.dict(exclude={'health_id'})
            track_data['sugar'] = validate_and_format_number(track.sugar, 'sugar')
            track_data['weight'] = validate_and_format_number(track.weight, 'weight')
            track_data['heart_rate'] = validate_and_format_number(track.heart_rate, 'heart_rate')

            # Append units
            track_data = append_units(track_data)

            health_id = str(uuid.uuid4())
            track_data['health_id'] = health_id
            if not track_data.get('updated_at_time'):
                track_data['updated_at_time'] = now

            try:
                track_ref = db.reference(f"users/{effective_uid}/health_tracks/{track_date.isoformat()}/{health_id}")
                logger.debug(f"Writing health track {health_id} to Firebase path: {track_ref.path}")
                track_ref.set(track_data)
                saved_tracks.append(track_data)
                logger.info(f"Health track {health_id} saved for {track_date.isoformat()}")
            except FirebaseError as e:
                logger.error(f"Firebase write failed for health track {health_id} on {track_date.isoformat()}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Failed to save health track {health_id} on {track_date.isoformat()}: {str(e)}")

        if not saved_tracks:
            logger.warning("No health tracks were saved to the database.")
            raise HTTPException(status_code=500, detail="No health tracks were saved to the database. Check logs for details.")

        logger.debug(f"Returning {len(saved_tracks)} saved health tracks")
        return {
            "status": "success",
            "tracks": saved_tracks
        }
    except Exception as e:
        logger.error(f"Error in add-health-track endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/get-all-health-tracks")
async def get_all_health_tracks(req: GetLinkedUserTodoListsRequest):
    """Fetch all health tracks for the user (all dates)."""
    try:
        logger.debug(f"Received request: {req.dict()}")
        
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        tracks_ref = db.reference(f"users/{effective_uid}/health_tracks")
        all_lists = tracks_ref.get() or {}

        result = []
        for date, tracks in all_lists.items():
            if isinstance(tracks, dict):
                result.append({
                    "date": date,
                    "tracks": list(tracks.values())
                })

        result.sort(key=lambda x: x["date"])
        logger.debug(f"Returning {len(result)} health track lists")
        return {
            "status": "success",
            "health_tracks": result
        }
    except Exception as e:
        logger.error(f"Error in get-all-health-tracks endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/update-health-track")
async def update_health_track(req: UpdateMultipleHealthTracksRequest):
    """Update multiple health tracks for the user or a linked user."""
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

        if not req.tracks or len(req.tracks) > 7:
            logger.error(f"Invalid number of tracks: {len(req.tracks)}")
            raise HTTPException(status_code=400, detail="You must provide between 1 and 7 tracks to update.")

        # Test Firebase connectivity
        try:
            test_ref = db.reference("test_connectivity")
            test_ref.set({"timestamp": now})
            logger.debug("Firebase connectivity test successful")
        except FirebaseError as e:
            logger.error(f"Firebase connectivity test failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to connect to Firebase: {str(e)}")

        updated_tracks = []

        for track in req.tracks:
            logger.debug(f"Processing health track update: health_id={track.health_id}, date={track.date}")

            try:
                # Validate date format
                datetime.datetime.fromisoformat(track.date.replace('Z', '+00:00'))
            except ValueError as e:
                logger.error(f"Invalid date format for health track {track.health_id}: {str(e)}")
                raise HTTPException(status_code=400, detail=f"Invalid date format for health track {track.health_id}. Must be YYYY-MM-DD (e.g., '2025-08-14').")

            track_ref = db.reference(f"users/{effective_uid}/health_tracks/{track.date}/{track.health_id}")
            track_data = track_ref.get()
            if not track_data:
                logger.error(f"Health track not found for ID {track.health_id} on {track.date}")
                raise HTTPException(status_code=404, detail=f"Health track not found for ID {track.health_id} on {track.date}")

            # Validate numeric string fields and append units
            update_data = track.dict(exclude={'health_id', 'date'}, exclude_none=True)
            if 'sugar' in update_data:
                update_data['sugar'] = validate_and_format_number(track.sugar, 'sugar')
            if 'weight' in update_data:
                update_data['weight'] = validate_and_format_number(track.weight, 'weight')
            if 'heart_rate' in update_data:
                update_data['heart_rate'] = validate_and_format_number(track.heart_rate, 'heart_rate')
            update_data = append_units(update_data)

            if not update_data.get('updated_at_time'):
                update_data['updated_at_time'] = now
            track_data.update(update_data)

            try:
                track_ref.set(track_data)
                updated_tracks.append(track_data)
                logger.info(f"Health track {track.health_id} updated on {track.date}")
            except FirebaseError as e:
                logger.error(f"Firebase write failed for health track {track.health_id} on {track.date}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Failed to update health track {track.health_id} on {track.date}: {str(e)}")

        if not updated_tracks:
            logger.warning("No health tracks were updated in the database.")
            raise HTTPException(status_code=500, detail="No health tracks were updated in the database. Check logs for details.")

        logger.debug(f"Returning {len(updated_tracks)} updated health tracks")
        return {
            "status": "success",
            "tracks": updated_tracks
        }
    except Exception as e:
        logger.error(f"Error in update-health-track endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/delete-health-track")
async def delete_health_track(req: DeleteHealthTrackRequest):
    """Delete a specific health track for the user by date and health_id."""
    try:
        logger.debug(f"Received request: {req.dict()}")
        
        custom_uid = verify_user_token(req.idToken)
        user_data = fetch_user_data(custom_uid)
        
        effective_uid = get_accessible_uid(custom_uid, req.target_id, user_data)

        track_ref = db.reference(f"users/{effective_uid}/health_tracks/{req.date}/{req.health_id}")
        track_data = track_ref.get()
        if not track_data:
            logger.error(f"Health track not found for ID {req.health_id} on {req.date}")
            raise HTTPException(status_code=404, detail="Health track not found")

        try:
            track_ref.delete()
            logger.info(f"Health track {req.health_id} deleted on {req.date}")
        except FirebaseError as e:
            logger.error(f"Firebase delete failed for health track {req.health_id} on {req.date}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to delete health track {req.health_id}: {str(e)}")

        return {
            "status": "success",
            "message": f"Health track {req.health_id} deleted successfully."
        }
    except Exception as e:
        logger.error(f"Error in delete-health-track endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
