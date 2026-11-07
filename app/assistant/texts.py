"""Fixed replies the agent sends without the model."""

TEXTS = {
    "empty_reply": "Sorry, I didn't catch that. Could you say it another way?",
    "too_many_errors": (
        "Sorry, I'm having trouble with that request. Could you rephrase it? "
        "You can also book from the dashboard."
    ),
    "too_many_steps": "Sorry, that took too many steps. Could you tell me again what you need, in one message?",
    "conversation_too_long": "This conversation is getting long. Please start a new conversation.",
    "nothing_pending": "There's nothing waiting for confirmation.",
    "declined": "Okay, I haven't changed anything. How else can I help?",
    "confirm_user": "Yes, confirm.",
    "decline_user": "No, don't do it.",
    "booked": "Your appointment is booked: {doctor} ({specialty}) on {date_label}, {start}-{end}.",
    "cancelled": "Your appointment with {doctor} on {date_label} at {start} has been cancelled.",
    # Shown when the Confirm button fails, keyed by service error code.
    "slot_already_booked": "Sorry, that time was just booked by someone else. Would you like me to find another time?",
    "slot_in_past": "Sorry, that time has already passed. Would you like me to find a later time?",
    "patient_already_booked": (
        "You already have a booking with this doctor that day, or another booking at the same time."
    ),
    "booking_not_found": "I couldn't find that booking any more. It may have already been cancelled.",
    "action_failed": "Sorry, that couldn't be completed. Please try again.",
}


def text(key: str, **values) -> str:
    return TEXTS[key].format(**values)
