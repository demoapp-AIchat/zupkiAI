
from pydantic import BaseModel
from typing import Optional, List, Dict

class AuthRequest(BaseModel):
    """Model for user authentication request."""
    email: str
    password: str
    account_type: str  # "child" or "family"

class AccountTypeRequest(BaseModel):
    idToken: str
    account_type: str
    
class TokenRequest(BaseModel):
    """Model for token verification request."""
    idToken: str

class ProactiveRequest(BaseModel):
    """Model for proactive talk request."""
    idToken: str
    reply: Optional[str] = None

class ReminderResponseRequest(BaseModel):
    """Model for medicine reminder response."""
    idToken: str
    medicine_name: Optional[str] = None
    reminder_id: Optional[str] = None
    response: Optional[str] = None

class ProactiveTalkResponse(BaseModel):
    """Model for proactive talk response."""
    status: str
    response: str  # Can be a question or a response
    timestamp: str

class UserDetails(BaseModel):
    """Model for user details."""
    idToken: str
    uid: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    emergencyContact: Optional[str] = None
    medication: Optional[str] = None
    age: Optional[str] = None
    habbitsToSkip: Optional[str] = None
    languagePreference: Optional[str] = None
    bloodGroup: Optional[str] = None
    medicalHistory: Optional[str] = None
    relation: Optional[str] = None
    selectedInterests: Optional[List[str]] = None
    allergies: Optional[List[str]]=None

class HealthInfo(BaseModel):
    """Model for user health information."""
    idToken: str
    hobbies: Optional[List[str]] = None
    medicines: Optional[List[str]] = None
    medical_history: Optional[str] = None

class Medicine(BaseModel):
    """Model for medicine details."""
    id: Optional[str] = None
    medicine_name: Optional[str] = None
    dosage: Optional[str] = None
    initial_quantity: Optional[int] = None
    daily_intake: Optional[int] = None
    timestamp: Optional[str] = None

class HealthMetric(BaseModel):
    """Model for health metric details."""
    id: Optional[str] = None
    timestamp: Optional[str] = None
    metric: Optional[str] = None
    data: Optional[float] = None

class DeleteReminderRequest(BaseModel):
    """Model for deleting a medicine reminder."""
    idToken: str
    reminder_id: str


class MedicineReminder(BaseModel):
    """Model for single medicine reminder."""
    medicine_name: str
    pill_details: Optional[str] = None
    end_date: Optional[str] = None
    amount_per_box: Optional[str] = None
    initial_quantity: Optional[str] = None
    time: Optional[str] = None
    current_quantity: Optional[str] = None
    reminder_date: Optional[str] = None
    start_from_today: Optional[bool] = None
    take_medicine_alert: Optional[bool] = None
    ring_phone: Optional[bool] = None
    send_message: Optional[bool] = None
    refill_reminder: Optional[bool] = None
    set_refill_date: Optional[str] = None
    set_day_before_refill: Optional[int] = None
    reminder_id: Optional[str] = None
    recurring: Optional[List[str]] = None  # e.g., ["sun", "mon", "tue"]
    status: Optional[str] = "pending"
    updated_at_time: Optional[str] = None

class AddMedicineReminderRequest(BaseModel):
    """Model for adding medicine reminders."""
    idToken: str
    target_id: Optional[str] = None
    reminders: List[MedicineReminder]

class UpdateMedicineReminder(BaseModel):
    date: str
    reminder_id: str
    medicine_name: Optional[str] = None
    pill_details: Optional[str] = None
    reminder_date: Optional[str] = None
    end_date: Optional[str] = None
    time: Optional[str] = None
    catagory: Optional[str] = None
    status: Optional[str] = None
    recurring: Optional[List[str]] = None
    start_from_today: Optional[bool] = None
    amount_per_box: Optional[str] = None
    initial_quantity: Optional[str] = None
    current_quantity: Optional[str] = None
    take_medicine_alert: Optional[bool] = None
    ring_phone: Optional[bool] = None
    send_message: Optional[bool] = None
    refill_reminder: Optional[bool] = None
    set_refill_date: Optional[str] = None
    set_day_before_refill: Optional[int] = None
    updated_at_time: Optional[str] = None

class UpdateMultipleMedicineRemindersRequest(BaseModel):
    idToken: str
    target_id: Optional[str] = None
    reminders: List[UpdateMedicineReminder]


class TodoTask(BaseModel):
    title: str
    description: Optional[str] = None
    status: Optional[str] = "pending"
    created_at_time: Optional[str] = None
    updated_at_time: Optional[str] = None
    completed_at_time: Optional[str] = None
    time: Optional[str] = None
    catagory: Optional[str] = None
    priority: Optional[str] = "medium"
    task_id: Optional[str] = None
    recurring: Optional[List[str]] = None

class AddMultipleTodoTasksRequest(BaseModel):
    idToken: str
    target_id: Optional[str] = None
    tasks: List[TodoTask]

class DeleteHealthTrackRequest(BaseModel):
    """Model for deleting health track data."""
    idToken: str
    delete_type: Optional[str] = None

class ProactiveTalkRequest(BaseModel):
    """Model for proactive talk request."""
    idToken: str
    reply: Optional[str] = None

class ChatRequest(BaseModel):
    """Model for chat request."""
    idToken: str
    message: Optional[str] = None

class PushTokenRequest(BaseModel):
    """Model for saving push token."""
    idToken: str
    push_token: Optional[str] = None

class SearchChildRequest(BaseModel):
    """Model for searching a child by ID."""
    idToken: str
    target_id: str

class LinkChildRequest(BaseModel):
    """Model for linking a child to a parent."""
    idToken: str
    target_id: str

class PushTokenRequest(BaseModel):
    idToken: str
    push_token: Optional[str] = None

class ChatResponse(BaseModel):
    """Model for chat response."""
    status: str
    response: str
    chat_history: List[Dict[str, str]]

class HandleRequest(BaseModel):
    """Model for handling  link request."""
    idToken: str
    target_id: str
    action: str  # "allow" or "decline"

class MedicineTrack(BaseModel):
    """Model for medicine tracking data."""
    idToken: str
    medicines: Optional[List[Medicine]] = None

class CheckLinkStatusRequest(BaseModel):
    """Model for checking link status."""
    idToken: str
    target_id: str

class HealthMetricTrack(BaseModel):
    """Model for health metric tracking data."""
    idToken: str
    health_metrics: Optional[List[HealthMetric]] = None

class DeleteMedicineRequest(BaseModel):
    """Model for deleting a medicine."""
    idToken: str
    reminder: str

class DeleteHealthMetricRequest(BaseModel):
    """Model for deleting a health metric."""
    idToken: str
    metric_id: str

class DeleteRequest(BaseModel):
    """Model for deleting a request."""
    idToken: str
    target_id: str

class FetchLinkedChildrenRequest(BaseModel):
    """Model for fetching linked children."""
    target_id: str

class RefreshRequest(BaseModel):
    """Model for refreshing authentication token."""
    refreshToken: str

class PasswordResetRequest(BaseModel):
    email: str

class GetCustomUidRequest(BaseModel):
    firebase_uid: str

class GetLinkedUserTodoListsRequest(BaseModel):
    idToken: str
    target_id: Optional[str] = None

class UpdateLinkedUserTodoTaskRequest(BaseModel):
    idToken: str
    linked_uid: str
    date: str
    target_id: Optional[str] = None
    task_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    time: Optional[str] = None
    created_at_time: Optional[str] = None
    updated_at_time: Optional[str] = None
    completed_at_time: Optional[str] = None
    priority: Optional[str] = None
    recurring: Optional[list] = None
class UpdateTodoTask(BaseModel):
    date: str
    task_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    reminder_date: Optional[str] = None
    updated_at_time: Optional[str] = None
    completed_at_time: Optional[str] = None
    time: Optional[str] = None
    catagory: Optional[str] = None
    priority: Optional[str] = None
    recurring: Optional[List[str]] = None

class UpdateMultipleTodoTasksRequest(BaseModel):
    idToken: str
    target_id: Optional[str] = None
    tasks: List[UpdateTodoTask]

class HealthTrack(BaseModel):
    """Model for single health track record."""
    health_id: Optional[str] = None
    bp: Optional[str] = None
    sugar: Optional[str] = None
    weight: Optional[str] = None
    heart_rate: Optional[str] = None
    created_date: str  # ISO 8601 format, required for storage
    updated_at_time: Optional[str] = None

class AddHealthTrackRequest(BaseModel):
    """Model for adding health tracks."""
    idToken: str
    target_id: Optional[str] = None
    tracks: List[HealthTrack]

class UpdateHealthTrack(BaseModel):
    date: str
    health_id: str
    bp: Optional[str] = None
    sugar: Optional[str] = None
    weight: Optional[str] = None
    heart_rate: Optional[str] = None
    created_date: Optional[str] = None
    updated_at_time: Optional[str] = None

class UpdateMultipleHealthTracksRequest(BaseModel):
    idToken: str
    target_id: Optional[str] = None
    tracks: List[UpdateHealthTrack]
class DeleteHealthTrackRequest(BaseModel):
    idToken: str
    target_id: Optional[str] = None
    date: str
    health_id: str
class DeleteMedicineRequest(BaseModel):
    idToken: str
    target_id: Optional[str] = None
    date: str
    reminder_id: str
class DeleteTaskRequest(BaseModel):
    idToken: str
    target_id: Optional[str] = None
    date: str
    task_id: str
