import string
import random
import datetime
from pytz import timezone
import re
import json
from firebase_admin import db, messaging
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

# Constants
MAX_MESSAGE_LENGTH = 1000
MAX_HISTORY_LENGTH = 50
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

def generate_custom_uid():
    """Generate a unique 7-character UID with 2 or 3 digits."""
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

def get_time_based_greeting(user_name: str) -> str:
    """Generate a time-based greeting for the user."""
    current_time = datetime.datetime.now(india_tz)
    hour = current_time.hour
    if 0 <= hour < 12:
        return f"Good morning, {user_name}!"
    elif 12 <= hour < 18:
        return f"Good evening, {user_name}!"
    else:
        return f"Good night, {user_name}!"

def calculate_weights(items: List[str], usage_counts: Dict[str, int], default_weight: float = 1.0) -> List[float]:
    """Calculate weights for items based on usage counts."""
    try:
        weights = []
        max_count = max(usage_counts.values(), default=1) if usage_counts else 1
        for item in items:
            count = usage_counts.get(item, 0)
            weight = default_weight / (1 + count / max_count)
            weights.append(weight)
        return weights
    except Exception as e:
        logger.error(f"Error calculating weights: {str(e)}")
        return [default_weight] * len(items)

def is_within_one_hour(reminder_time: str, current_time: datetime.datetime, threshold_minutes: int = 60) -> bool:
    """Check if current time is within 1-hour window of reminder time."""
    try:
        if not reminder_time:
            return False
        if 'T' in reminder_time:
            reminder_dt = datetime.datetime.fromisoformat(reminder_time.replace('Z', '+00:00'))
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

def is_exact_reminder_time(reminder_time: str, current_time: datetime.datetime, threshold_minutes: int = 1) -> bool:
    """Check if current time exactly matches reminder time (1-minute window)."""
    try:
        if not reminder_time:
            return False
        if 'T' in reminder_time:
            reminder_dt = datetime.datetime.fromisoformat(reminder_time.replace('Z', '+00:00'))
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

def is_after_reminder_time(reminder_time: str, current_time: datetime.datetime) -> bool:
    """Check if current time is after the reminder time."""
    try:
        if not reminder_time:
            return False
        if 'T' in reminder_time:
            reminder_dt = datetime.datetime.fromisoformat(reminder_time.replace('Z', '+00:00'))
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

def is_refill_date_near(refill_date: str, current_date: datetime.datetime, threshold_days: int = 3) -> bool:
    """Check if refill date is within the threshold days."""
    try:
        if not refill_date:
            return False
        refill_dt = datetime.datetime.fromisoformat(refill_date.replace('Z', '+00:00'))
        refill = refill_dt.date()
        current = current_date.date()
        delta = (refill - current).days
        return 0 <= delta <= threshold_days
    except Exception as e:
        logger.error(f"Error parsing refill date {refill_date}: {str(e)}")
        return False

def is_list_reminders_request(reply: str) -> bool:
    """Check if user reply is requesting to list reminders."""
    if not reply:
        return False
    reply_lower = reply.lower()
    keywords = ["list all reminders", "show my reminders", "what are my reminders", 
                "medicine schedule", "reminder list", "all medicine times"]
    return any(keyword in reply_lower for keyword in keywords)

def format_reminder_list(medicine_reminders: List[dict]) -> str:
    """Format the list of medicine reminders for display."""
    if not medicine_reminders:
        return "You have no medicine reminders set."
    reminder_list = "Here are your medicine reminders:\n"
    for reminder in medicine_reminders:
        if not isinstance(reminder, dict):
            continue
        med_name = reminder.get("medicine_name", "Unknown")
        time = reminder.get("time", "No time set")
        if 'T' in time:
            try:
                time_dt = datetime.datetime.fromisoformat(time.replace('Z', '+00:00'))
                time = time_dt.strftime("%H:%M")
            except:
                time = "Invalid time format"
        refill_date = reminder.get("set_refill_date", "No refill date set")
        if 'T' in refill_date:
            try:
                refill_dt = datetime.datetime.fromisoformat(refill_date.replace('Z', '+00:00'))
                refill_date = refill_dt.strftime("%Y-%m-%d")
            except:
                refill_date = "Invalid refill date"
        reminder_list += f"- {med_name} at {time}, Refill due: {refill_date}\n"
    return reminder_list

def is_valid_three_word_task(task: str) -> bool:
    """Validate if a task is exactly three words."""
    return len(task.strip().split()) == 3

def generate_random_time(period_start_hour: int, period_end_hour: int) -> str:
    """Generate a random time within a period."""
    hour = random.randint(period_start_hour, period_end_hour - 1)
    minute = random.choice([0, 15, 30, 45])
    return f"{hour:02d}:{minute:02d}"

def is_reminder_in_period(reminder_time: str, period_start_hour: int, period_end_hour: int) -> bool:
    """Check if a reminder time falls within the specified period hours."""
    try:
        if not reminder_time:
            return False
        if 'T' in reminder_time:
            reminder_dt = datetime.datetime.fromisoformat(reminder_time.replace('Z', '+00:00'))
            reminder_hour = reminder_dt.hour
        else:
            reminder_hour, _ = map(int, reminder_time.split(":"))
        return period_start_hour <= reminder_hour < period_end_hour
    except Exception as e:
        logger.error(f"Error parsing reminder time {reminder_time}: {str(e)}")
        return False