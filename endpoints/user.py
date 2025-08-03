from fastapi import APIRouter, HTTPException
from models import UserDetails, TokenRequest, SearchChildRequest, LinkChildRequest, HandleParentRequest, DeleteRequest, FetchLinkedChildrenRequest, CheckLinkStatusRequest, AccountTypeRequest
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
        raise HTTPException(status_code=401, detail=str(e))

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
        raise HTTPException(status_code=401, detail=str(e))
    
    
@router.post("/search-child")
def search_child(req: SearchChildRequest):
    """Search for a child by ID."""
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
 
@router.post("/request-child-link")
def request_child_link(req: LinkChildRequest):
    """Send a link request from a parent to a child."""
    try:
        parent_uid = verify_user_token(req.idToken)
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
        child_ref.child(f"pending_parent_requests/{parent_uid}").set({
            "name": parent_data.get("user_details", {}).get("name", ""),
            "email": parent_data.get("user_details", {}).get("email", ""),
            "status": "pending",
            "timestamp": datetime.datetime.now().isoformat()
        })
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

@router.post("/fetch-pending-requests")
def fetch_pending_requests(req: TokenRequest):
    """Fetch pending parent requests for a child."""
    try:
        child_uid = verify_user_token(req.idToken)
        user_ref = db.reference(f"users/{child_uid}")
        user_data = user_ref.get()
        if not user_data or user_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=403, detail="Only child accounts can fetch pending requests")

        pending_requests = user_data.get("pending_parent_requests", {})
        return {"status": "success", "data": pending_requests}
    except Exception as e:
        logger.error(f"Error fetching pending requests: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/handle-parent-request")
def handle_parent_request(req: HandleParentRequest):
    """Handle a parent link request (approve or decline)."""
    try:
        child_uid = verify_user_token(req.idToken)
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

@router.post("/fetch-child-details")
def fetch_child_details(req: TokenRequest):
    """Fetch details of all linked children for a parent."""
    try:
        parent_uid = verify_user_token(req.idToken)
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

@router.post("/fetch-parent-requests")
def fetch_parent_requests(req: TokenRequest):
    """Fetch sent requests for a parent."""
    try:
        parent_uid = verify_user_token(req.idToken)
        ref = db.reference(f"users/{parent_uid}/sent_requests")
        return {"status": "success", "data": ref.get() or {}}
    except Exception as e:
        logger.error(f"Error fetching parent requests: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/delete-request")
def delete_request(req: DeleteRequest):
    """Delete a pending link request from both sides."""
    try:
        child_uid = verify_user_token(req.idToken)
        db.reference(f"users/{child_uid}/pending_parent_requests/{req.target_id}").delete()
        db.reference(f"users/{req.target_id}/sent_requests/{child_uid}").delete()
        return {"status": "success", "message": "Request deleted from both sides"}
    except Exception as e:
        logger.error(f"Error deleting request: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/linked-children")
def linked_children(req: FetchLinkedChildrenRequest):
    """Fetch list of linked children for a parent."""
    try:
        parent_data = fetch_user_data(req.parent_id)
        if parent_data.get("user_details", {}).get("account_type") != "family":
            raise HTTPException(status_code=403, detail="Invalid parent")
        children = parent_data.get("children", {})
        return {"status": "success", "children": list(children.keys())}
    except Exception as e:
        logger.error(f"Error fetching linked children: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

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
        raise HTTPException(status_code=401, detail=str(e))
@router.post("/unlink-child")
def unlink_child(req: DeleteRequest):
    """Remove a child from a parent's linked children and delete all related links."""
    try:
        parent_uid = verify_user_token(req.idToken)
        parent_ref = db.reference(f"users/{parent_uid}")
        parent_data = parent_ref.get()
        if not parent_data or parent_data.get("user_details", {}).get("account_type") != "family":
            raise HTTPException(status_code=403, detail="Only family accounts can unlink children")
        
        child_ref = db.reference(f"users/{req.target_id}")
        child_data = child_ref.get()
        if not child_data or child_data.get("user_details", {}).get("account_type") != "child":
            raise HTTPException(status_code=404, detail="Child ID not found or not a child account")
        
        if req.target_id not in parent_data.get("children", {}):
            raise HTTPException(status_code=400, detail="Child is not linked to this parent")
        
        # Remove child from parent's children
        parent_ref.child(f"children/{req.target_id}").delete()
        
        # Remove parent from child's parents
        child_ref.child(f"parents/{parent_uid}").delete()
        
        # Remove any pending or sent requests
        parent_ref.child(f"sent_requests/{req.target_id}").delete()
        child_ref.child(f"pending_parent_requests/{parent_uid}").delete()
        
        logger.info(f"Unlinked child {req.target_id} from parent {parent_uid}")
        return {"status": "success", "message": f"Child {req.target_id} unlinked successfully"}
    
    except Exception as e:
        logger.error(f"Error unlinking child: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/accounttype")
def save_account_type(req: AccountTypeRequest):
    """Save or update the account type for a user."""
    try:
        custom_uid = verify_user_token(req.idToken)
        user_ref = db.reference(f"users/{custom_uid}")
        # Check if user_details exists
        user_data = user_ref.child("user_details").get() or {}
        # Save or update account_type
        user_ref.child("user_details/account_type").set(req.account_type)
        return {"status": "success", "message": "Account type saved successfully"}
    except Exception as e:
        logger.error(f"Error saving account type: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/fetch-accounttype")
def fetch_account_type(req: TokenRequest):
    """Fetch the account type for a user."""
    try:
        custom_uid = verify_user_token(req.idToken)
        user_ref = db.reference(f"users/{custom_uid}")
        user_details = user_ref.child("user_details").get()
        if not user_details or "account_type" not in user_details:
            raise HTTPException(status_code=404, detail="Account type not found")
        return {"status": "success", "account_type": user_details["account_type"]}
    except Exception as e:
        logger.error(f"Error fetching account type: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

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
        raise HTTPException(status_code=401, detail=str(e))