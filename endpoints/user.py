from fastapi import APIRouter, HTTPException
from models import UserDetails, TokenRequest, SearchChildRequest, LinkChildRequest, HandleRequest, DeleteRequest, FetchLinkedChildrenRequest, CheckLinkStatusRequest, AccountTypeRequest
from database import verify_user_token, fetch_user_data
from firebase_admin import db
import logging
import datetime

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/user-details")
def save_user_details(req: UserDetails):
    """Save user details to Firebase."""
    try:
        custom_uid = verify_user_token(req.idToken)
        user_ref = db.reference(f"users/{custom_uid}")
        user_details = {k: v for k, v in req.dict(exclude={"idToken"}).items() if v is not None}
        user_ref.child("user_details").update(user_details)
        return {"status": "success", "message": "User details saved successfully"}
    except Exception as e:
        logger.error(f"Error saving user details: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=401, detail={"error": "Failed to save user details", "details": error_message})

@router.post("/user-detail")
def fetch_user_details(req: TokenRequest):
    try:
        custom_uid = verify_user_token(req.idToken)
        user_ref = db.reference(f"users/{custom_uid}")
        user_data = user_ref.get()
        if not user_data or not user_data.get("user_details"):
            raise HTTPException(status_code=404, detail="User details not found")
        # Include UID in the response data
        response_data = {
            "uid": custom_uid,
            **user_data["user_details"]
        }
        return {"status": "success", "data": response_data}
    except Exception as e:
        logger.error(f"Error fetching user details: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=401, detail={"error": "Failed to fetch user details", "details": error_message})
    
    
@router.post("/search-user")
def search_child(req: SearchChildRequest):
    """Search for user by ID."""
    try:
        child_ref = db.reference(f"users/{req.child_id}")
        child_data = child_ref.get()
        
        # Check if user exists
        if not child_data:
            raise HTTPException(status_code=404, detail="Child ID not found")
        
        # Check if user has user_details (assuming child accounts must have user_details)
        child_details = child_data.get("user_details", {})
        if not child_details:
            raise HTTPException(status_code=404, detail="Child account details not found")
        
        # Optionally, add logic to verify if the account is a "child account"
        # Example: Check if the account has a specific flag or lacks certain attributes
        # For instance, child accounts might not have linked users
        if child_data.get("linked"):
            raise HTTPException(status_code=400, detail="User is not a child account")
        
        search_result = {
            "name": child_details.get("name", ""),
            "age": child_details.get("age", None)
        }
        return {"status": "success", "data": search_result}
    except Exception as e:
        logger.error(f"Error searching child: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=400, detail={"error": "Failed to search user", "details": error_message})
    
@router.post("/request-uid-link")
def request_child_link(req: LinkChildRequest):
    """Send a link request from a parent to a child."""
    try:
        sender_uid = verify_user_token(req.idToken)
        sender_ref = db.reference(f"users/{sender_uid}")
        sender_data = sender_ref.get()
        receiver_ref = db.reference(f"users/{req.child_id}")
        receiver_data = receiver_ref.get()
        if not sender_data:
            raise HTTPException(status_code=404, detail="Sender UID not found")
        if not receiver_data:
            raise HTTPException(status_code=404, detail="Receiver UID not found")
        # Prevent duplicate requests
        if sender_uid in receiver_data.get("pending_link_requests", {}):
            raise HTTPException(status_code=400, detail="Request already sent")
        # Save request in receiver's pending_link_requests
        receiver_ref.child(f"pending_link_requests/{sender_uid}").set({
            "name": sender_data.get("user_details", {}).get("name", ""),
            "email": sender_data.get("user_details", {}).get("email", ""),
            "status": "pending",
            "timestamp": datetime.datetime.now().isoformat()
        })
        # Save request in sender's sent_link_requests
        sender_ref.child(f"sent_link_requests/{req.child_id}").set({
            "name": receiver_data.get("user_details", {}).get("name", ""),
            "email": receiver_data.get("user_details", {}).get("email", ""),
            "status": "pending",
            "timestamp": datetime.datetime.now().isoformat()
        })
        return {"status": "success", "message": f"Link request sent to UID {req.child_id}"}
    except Exception as e:
        logger.error(f"Error requesting link: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=401, detail={"error": "Failed to request link", "details": error_message})

@router.post("/fetch-pending-requests")
def fetch_pending_requests(req: TokenRequest):
    """Fetch pending parent requests for a child."""
    try:
        uid = verify_user_token(req.idToken)
        user_ref = db.reference(f"users/{uid}")
        user_data = user_ref.get()
        if not user_data:
            raise HTTPException(status_code=404, detail="User not found")

        pending_requests = user_data.get("pending_link_requests", {})
        return {"status": "success", "data": pending_requests}
    except Exception as e:
        logger.error(f"Error fetching pending requests: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=401, detail={"error": "Failed to fetch pending requests", "details": error_message})

@router.post("/handle-request")
def handle_request(req: HandleRequest):
    """Handle a  link request (approve or decline)."""
    try:
        receiver_uid = verify_user_token(req.idToken)
        receiver_ref = db.reference(f"users/{receiver_uid}")
        receiver_data = receiver_ref.get()
        if not receiver_data:
            raise HTTPException(status_code=404, detail="Receiver not found")
        # Check if sender_id exists in receiver's pending_link_requests
        if req.user_id not in receiver_data.get("pending_link_requests", {}):
            raise HTTPException(status_code=404, detail="Request not found")
        sender_ref = db.reference(f"users/{req.user_id}")
        sender_data = sender_ref.get()
        if not sender_data:
            raise HTTPException(status_code=404, detail="Sender not found")
        if req.action.lower() == "allow":
            # Mark as linked for both users
            receiver_ref.child(f"linked/{req.user_id}").set(True)
            sender_ref.child(f"linked/{receiver_uid}").set(True)
            receiver_ref.child(f"pending_link_requests/{req.user_id}/status").set("approved")
            sender_ref.child(f"sent_link_requests/{receiver_uid}/status").set("approved")
            return {"status": "success", "message": "Link request approved successfully"}
        elif req.action.lower() == "decline":
            receiver_ref.child(f"pending_link_requests/{req.user_id}/status").set("declined")
            sender_ref.child(f"sent_link_requests/{receiver_uid}/status").set("declined")
            return {"status": "success", "message": "Link request declined"}
        else:
            raise HTTPException(status_code=400, detail="Invalid action")
    except Exception as e:
        logger.error(f"Error handling link request: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=401, detail={"error": "Failed to handle link request", "details": error_message})

@router.post("/fetch-user-details")
def fetch_child_details(req: TokenRequest):
    """Fetch details of all linked children for a parent."""
    try:
        uid = verify_user_token(req.idToken)
        user_ref = db.reference(f"users/{uid}")
        user_data = user_ref.get()
        if not user_data:
            raise HTTPException(status_code=404, detail="User not found")
        linked = user_data.get("linked", {})
        result = {}
        for linked_uid in linked:
            linked_ref = db.reference(f"users/{linked_uid}")
            linked_data = linked_ref.get()
            result[linked_uid] = {
                "user_details": linked_data.get("user_details", {}),
                "health_info": linked_data.get("health_info", {}),
                "health_track": linked_data.get("health_track", {})
            }
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"Error fetching linked user details: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=401, detail={"error": "Failed to fetch linked user details", "details": error_message})

@router.post("/fetch-user-requests")
def fetch_parent_requests(req: TokenRequest):
    """Fetch sent requests for a user."""
    try:
        uid = verify_user_token(req.idToken)
        ref = db.reference(f"users/{uid}/sent_link_requests")
        return {"status": "success", "data": ref.get() or {}}
    except Exception as e:
        logger.error(f"Error fetching sent requests: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=401, detail={"error": "Failed to fetch sent requests", "details": error_message})

@router.post("/delete-request")
def delete_request(req: DeleteRequest):
    """Delete a pending link request from both sides."""
    try:
        uid = verify_user_token(req.idToken)
        db.reference(f"users/{uid}/pending_link_requests/{req.target_id}").delete()
        db.reference(f"users/{req.target_id}/sent_link_requests/{uid}").delete()
        return {"status": "success", "message": "Request deleted from both sides"}
    except Exception as e:
        logger.error(f"Error deleting request: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=401, detail={"error": "Failed to delete request", "details": error_message})

@router.post("/linked-user")
def linked_children(req: FetchLinkedChildrenRequest):
    """Fetch list of linked user."""
    try:
        uid = verify_user_token(req.idToken)
        user_ref = db.reference(f"users/{uid}")
        user_data = user_ref.get()
        if not user_data:
            raise HTTPException(status_code=404, detail="User not found")
        linked = user_data.get("linked", {})
        return {"status": "success", "linked_uids": list(linked.keys())}
    except Exception as e:
        logger.error(f"Error fetching linked uids: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=401, detail={"error": "Failed to fetch linked users", "details": error_message})

@router.post("/check-link-status")
def check_link_status(req: CheckLinkStatusRequest):
    """Check the status of a link request."""
    try:
        parent_uid = verify_user_token(req.idToken)
        status_ref = db.reference(f"users/{parent_uid}/sent_requests/{req.child_id}/status")
        status = status_ref.get()
        return {"status": "success", "link_status": status or "not_requested"}
    except Exception as e:
        logger.error(f"Error checking link status: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=401, detail={"error": "Failed to check link status", "details": error_message})
@router.post("/unlink-child")
def unlink_child(req: DeleteRequest):
    """Unlink any user from the authenticated user's linked list and delete all related links."""
    try:
        uid = verify_user_token(req.idToken)
        user_ref = db.reference(f"users/{uid}")
        target_ref = db.reference(f"users/{req.target_id}")
        user_data = user_ref.get()
        target_data = target_ref.get()
        if not user_data:
            raise HTTPException(status_code=404, detail="User not found")
        if not target_data:
            raise HTTPException(status_code=404, detail="Target user not found")
        # Remove link from both users
        user_ref.child(f"linked/{req.target_id}").delete()
        target_ref.child(f"linked/{uid}").delete()
        # Remove any pending or sent requests
        user_ref.child(f"sent_link_requests/{req.target_id}").delete()
        target_ref.child(f"pending_link_requests/{uid}").delete()
        logger.info(f"Unlinked user {req.target_id} from user {uid}")
        return {"status": "success", "message": f"User {req.target_id} unlinked successfully"}
    except Exception as e:
        logger.error(f"Error unlinking user: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=401, detail={"error": "Failed to unlink user", "details": error_message})

@router.post("/update-user-details")
def update_user_details(req: UserDetails):
    """
    Update (upsert) specific user detail fields.
    Only provided fields are updated; existing data is preserved for others.
    """
    try:
        custom_uid = verify_user_token(req.idToken)
        user_ref = db.reference(f"users/{custom_uid}/user_details")
        existing_details = user_ref.get() or {}

        # Only update fields that are not None in the request
        update_fields = {k: v for k, v in req.dict(exclude={"idToken"}).items() if v is not None}
        updated_details = {**existing_details, **update_fields}

        user_ref.set(updated_details)
        return {"status": "success", "message": "User details updated successfully"}
    except Exception as e:
        logger.error(f"Error updating user details: {str(e)}")
        error_message = str(e)
        if hasattr(e, 'detail'):
            error_message = getattr(e, 'detail', error_message)
        raise HTTPException(status_code=401, detail={"error": "Failed to update user details", "details": error_message})