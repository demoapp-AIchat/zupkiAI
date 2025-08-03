from fastapi import APIRouter, HTTPException
from models import HealthInfo, MedicineTrack, HealthMetricTrack, DeleteMedicineRequest, DeleteHealthMetricRequest,TokenRequest
from database import verify_user_token, fetch_user_data
from firebase_admin import db
import logging
from typing import List
from models import Medicine, HealthMetric

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/user-health")
def save_user_health(req: HealthInfo):
    """Save health information for a child account."""
    try:
        custom_uid = verify_user_token(req.idToken)
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

@router.get("/user-health")
def fetch_user_health(req: TokenRequest):
    """Fetch health information for a child or authorized parent."""
    try:
        custom_uid = verify_user_token(req.idToken)
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


@router.post("/save-medicines")
def save_medicines(req: MedicineTrack):
    """Save medicine data for a child account."""
    try:
        custom_uid = verify_user_token(req.idToken)
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

@router.post("/get-medicines", response_model=List[Medicine])
async def get_medicines(req: TokenRequest):
    """Fetch medicine data for a child account."""
    try:
        custom_uid = verify_user_token(req.idToken)
        
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
   
    except Exception as e:
        logger.error(f"Error retrieving medicines: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.delete("/delete-medicine")
async def delete_medicine(req: DeleteMedicineRequest):
    """Delete a medicine by ID for a child account."""
    try:
        custom_uid = verify_user_token(req.idToken)
         
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
    
    
    except Exception as e:
        logger.error(f"Error deleting medicine: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
   
@router.post("/save-health-metrics")
def save_health_metrics(req: HealthMetricTrack):
    """Save health metrics for a child account."""
    try:
        custom_uid = verify_user_token(req.idToken)
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

@router.post("/get-health-metric", response_model=List[HealthMetric])
async def get_health_metrics(req: TokenRequest):
    """Fetch health metrics for a child account."""
    try:
        custom_uid = verify_user_token(req.idToken)
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
    except Exception as e:
        logger.error(f"Error retrieving health metrics: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.delete("/delete-health-metric")
async def delete_health_metric(req: DeleteHealthMetricRequest):
    """Delete a health metric by ID for a child account."""
    try:
        custom_uid = verify_user_token(req.idToken)
       
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
    

    except Exception as e:
        logger.error(f"Error deleting health metric: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")