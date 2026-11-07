"""User-facing text for the message codes passed in redirect URLs.

Only codes are put in URLs; the text always comes from here, so a crafted
link can't inject arbitrary content into the page.
"""

ERRORS = {
    "doctor_not_found": "Please choose a doctor from the list.",
    "slot_not_found": "That time slot no longer exists. It may have been removed.",
    "slot_already_booked": "Sorry, that slot has already been booked. Please pick another one.",
    "patient_already_booked": (
        "This patient already has a booking with this doctor on that day, "
        "or another booking at the same time."
    ),
    "slot_not_booked": "That slot isn't booked, so there is nothing to cancel.",
    "slot_in_past": "That slot has already started or passed and can no longer be booked.",
    "invalid_duration": "Slot duration must be a whole number between 1 and 240 minutes.",
    "invalid_time_range": "End time must be after start time.",
    "duration_too_long": "The slot duration is longer than the time between start and end.",
    "invalid_time": "Please enter valid start and end times.",
    "invalid_date": "Please enter a valid date.",
    "past_date": "You can't create a schedule for a date in the past.",
    "invalid_name": "Please enter the patient's name (up to 100 characters).",
    "invalid_input": "Some values were invalid. Please check the form and try again.",
}

GENERIC_ERROR = "Something went wrong. Please try again."


def error_message(code: str | None) -> str | None:
    if not code:
        return None
    return ERRORS.get(code, GENERIC_ERROR)


def success_message(code: str | None, created: int = 0, skipped: int = 0) -> str | None:
    if code == "generated":
        if created == 0:
            return f"No new slots were created: all {skipped} already exist."
        text = f"Created {created} new slot{'s' if created != 1 else ''}."
        if skipped:
            text += f" Skipped {skipped} that already existed."
        return text
    return {"booked": "Appointment booked.", "cancelled": "Booking cancelled."}.get(code or "")
