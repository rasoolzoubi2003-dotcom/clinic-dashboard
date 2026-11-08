"""Tools the assistant model can call.

Every tool:
- validates its arguments with Pydantic and returns a clear error the model can act on,
- re-checks slot IDs against the database (and against what this chat was actually shown),
- calls `app.services`, so the dashboard and the assistant share the same booking rules,
- never returns another patient's name or phone number.

Booking and cancelling are two-step: the first call only returns the details for the
patient to confirm. The change happens on a later call, after the patient has replied
(or when they press the Confirm button), never in the same turn.
"""

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app import services
from app.assistant.dates import resolve_date
from app.assistant.prompts import date_label
from app.assistant.providers.base import ToolCall, ToolResult, ToolSpec
from app.clock import today
from app.models import Appointment, Doctor
from app.schemas import normalize_name, normalize_phone

MAX_SLOTS_SHOWN = 8

TIME_WINDOWS: dict[str, tuple[dt.time | None, dt.time | None]] = {
    "morning": (dt.time(6), dt.time(12)),
    "afternoon": (dt.time(12), dt.time(17)),
    "evening": (dt.time(17), None),
    "any": (None, None),
}

TIME_OF_DAY_WORDS = {
    "morning": ["morning", "am", "a.m.", "early"],
    "afternoon": ["afternoon", "noon", "midday"],
    "evening": ["evening", "pm", "p.m.", "night", "late"],
}

# Extra words that identify a specialty.
SPECIALTY_ALIASES = {
    "cardiology": ["cardiologist", "cardiac", "heart"],
    "dermatology": ["dermatologist", "derma", "skin"],
    "pediatrics": ["pediatrician", "paediatrics", "children", "child", "kids", "baby"],
    "general medicine": ["general", "gp", "family", "general practitioner"],
}
_TITLES = ("dr.", "dr ", "doctor ")


# --- Per-chat state ---------------------------------------------------------


@dataclass
class PendingAction:
    """A booking or cancellation waiting for the patient's confirmation."""

    kind: Literal["book", "cancel"]
    slot_id: int
    patient_name: str
    phone_number: str
    requested_on_turn: int
    details: dict

    def matches(self, kind: str, slot_id: int, patient_name: str, phone_number: str) -> bool:
        return (
            self.kind == kind
            and self.slot_id == slot_id
            and self.patient_name.casefold() == patient_name.casefold()
            and services.phone_key(self.phone_number) == services.phone_key(phone_number)
        )

    def to_public(self) -> dict:
        return {"action": self.kind, **self.details}


@dataclass
class AssistantState:
    """What the tools need to remember during one chat (independent of the LLM provider)."""

    user_turn: int = 0
    offered_slot_ids: set[int] = field(default_factory=set)
    pending: PendingAction | None = None


# --- Argument models ----------------------------------------------------------


class _Args(BaseModel):
    # Small models sometimes add extra keys; ignore them rather than failing.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class _PatientArgs(_Args):
    patient_name: str = Field(max_length=200)
    phone_number: str = Field(max_length=40)

    @field_validator("patient_name")
    @classmethod
    def _name(cls, value: str) -> str:
        return normalize_name(value)

    @field_validator("phone_number", mode="before")
    @classmethod
    def _phone(cls, value: Any) -> str:
        return normalize_phone(str(value))


class ListDoctorsArgs(_Args):
    pass


class FindAvailableSlotsArgs(_Args):
    doctor: str = Field(min_length=1, max_length=100)
    date: dt.date
    time_of_day: Literal["morning", "afternoon", "evening", "any"] = "any"

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, value: Any) -> Any:
        return resolve_date(value, today())

    @field_validator("time_of_day", mode="before")
    @classmethod
    def _time_of_day(cls, value: Any) -> str:
        # Lenient on purpose: an unrecognised value just means "any time".
        text = str(value or "").strip().casefold()
        for period, words in TIME_OF_DAY_WORDS.items():
            if text in words:
                return period
        return "any"


class BookSlotArgs(_PatientArgs):
    slot_id: int = Field(gt=0)


class FindBookingsArgs(_PatientArgs):
    pass


class CancelBookingArgs(_PatientArgs):
    slot_id: int = Field(gt=0)


# --- Tool definitions shown to the model --------------------------------------

_PATIENT_PROPS = {
    "patient_name": {"type": "string", "description": "Patient's full name, as the patient wrote it."},
    "phone_number": {
        "type": "string",
        "description": "Patient's phone number exactly as they wrote it, as a string. Keep any leading 0 or +.",
    },
}

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="list_doctors",
        description="List the clinic's doctors and their specialties.",
        parameters={"type": "object", "properties": {}, "required": []},
    ),
    ToolSpec(
        name="find_available_slots",
        description=(
            "Find free appointment slots for a doctor on one date. "
            "Returns slot_id values to use with book_slot."
        ),
        parameters={
            "type": "object",
            "properties": {
                "doctor": {
                    "type": "string",
                    "description": "Doctor name or specialty in English, e.g. 'Dr. Ali' or 'Dermatology'.",
                },
                "date": {
                    "type": "string",
                    "description": (
                        "YYYY-MM-DD, or the patient's word: 'today', 'tomorrow', or a weekday like 'Sunday'. "
                        "Words are converted to the right date for you."
                    ),
                },
                "time_of_day": {
                    "type": "string",
                    "enum": ["morning", "afternoon", "evening", "any"],
                    "description": "Part of the day the patient prefers. Use 'any' if not mentioned.",
                },
            },
            "required": ["doctor", "date"],
        },
    ),
    ToolSpec(
        name="book_slot",
        description=(
            "Book a slot for a patient. The first call does NOT book: it returns the details for the "
            "patient to confirm. After the patient says yes, call it again with the same arguments."
        ),
        parameters={
            "type": "object",
            "properties": {
                "slot_id": {"type": "integer", "description": "A slot_id returned by find_available_slots."},
                **_PATIENT_PROPS,
            },
            "required": ["slot_id", "patient_name", "phone_number"],
        },
    ),
    ToolSpec(
        name="find_bookings",
        description="Find a patient's upcoming bookings. Needs the name AND phone number used when booking.",
        parameters={"type": "object", "properties": dict(_PATIENT_PROPS), "required": ["patient_name", "phone_number"]},
    ),
    ToolSpec(
        name="cancel_booking",
        description=(
            "Cancel a patient's booking. Needs the name and phone number used when booking. "
            "The first call only returns details to confirm; call again after the patient says yes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "slot_id": {"type": "integer", "description": "A slot_id returned by find_bookings."},
                **_PATIENT_PROPS,
            },
            "required": ["slot_id", "patient_name", "phone_number"],
        },
    ),
]
TOOL_NAMES = frozenset(spec.name for spec in TOOL_SPECS)

class SlotNotOffered(services.ServiceError):
    """The model used a slot_id that this chat was never shown (likely made up)."""

    code = "slot_not_offered"


# Messages for service errors, written for the model.
_SERVICE_ERRORS = {
    "slot_not_offered": (
        "That slot_id was not offered in this conversation. Call find_available_slots and use one of the "
        "slot_id values it returns."
    ),
    "slot_not_found": "That slot_id does not exist. Use a slot_id returned by find_available_slots.",
    "slot_already_booked": "That slot was just booked by someone else. Search again and offer other times.",
    "slot_in_past": "That slot has already started. Search again and offer later times.",
    "patient_already_booked": (
        "This patient already has a booking with this doctor on that day, or another booking at the same "
        "time. Tell the patient; they can cancel the other booking first."
    ),
    "booking_not_found": (
        "No booking matches this slot, name and phone number. Ask the patient to check their name and phone "
        "number, or call find_bookings."
    ),
    "doctor_not_found": "Unknown doctor. Call list_doctors to see the available doctors.",
}


# --- Helpers -----------------------------------------------------------------


def _doctor_info(doctor: Doctor) -> dict:
    return {"doctor_id": doctor.id, "name": doctor.name, "specialty": doctor.specialty}


def slot_details(slot: Appointment) -> dict:
    return {
        "slot_id": slot.id,
        "doctor": slot.doctor.name,
        "specialty": slot.doctor.specialty,
        "date": slot.date.isoformat(),
        "date_label": date_label(slot.date),
        "weekday": slot.date.strftime("%A"),
        "start": slot.start_time.strftime("%H:%M"),
        "end": slot.end_time.strftime("%H:%M"),
    }


def _clean(text: str) -> str:
    text = " ".join(text.casefold().split())
    for title in _TITLES:
        if text.startswith(title):
            text = text[len(title):].strip()
    return text


def resolve_doctors(doctors: list[Doctor], query: str) -> list[Doctor]:
    """Match a doctor by id, name, specialty, or specialty alias."""
    q = _clean(query)
    if q.isdigit():
        return [d for d in doctors if d.id == int(q)]

    exact = [d for d in doctors if _clean(d.name) == q or _clean(d.display_name) == q]
    if exact:
        return exact
    by_name = [d for d in doctors if q in _clean(d.name) or _clean(d.name) in q]
    if by_name:
        return by_name

    def specialty_terms(d: Doctor) -> list[str]:
        specialty = d.specialty.casefold()
        return [specialty, *SPECIALTY_ALIASES.get(specialty, [])]

    return [d for d in doctors if any(term == q or term in q or q in term for term in specialty_terms(d))]


def _format_validation_error(exc: ValidationError) -> list[str]:
    hints = {
        "date": "date must be YYYY-MM-DD, e.g. " + today().isoformat(),
        "time_of_day": "time_of_day must be one of: morning, afternoon, evening, any",
        "slot_id": "slot_id must be a positive integer from find_available_slots or find_bookings",
        "phone_number": "phone_number must contain 7-15 digits; ask the patient for it if unknown",
        "patient_name": "patient_name must be the patient's full name; ask the patient for it if unknown",
    }
    messages = []
    for err in exc.errors():
        field_name = str(err["loc"][0]) if err["loc"] else "arguments"
        if err["type"] == "missing":
            messages.append(f"{field_name} is required")
        else:
            messages.append(hints.get(field_name, f"{field_name}: {err['msg']}"))
    return messages


# --- Executor ----------------------------------------------------------------


class ToolExecutor:
    """Runs tool calls from the model against the database for one chat."""

    def __init__(self, db: Session, state: AssistantState):
        self.db = db
        self.state = state
        self._tools: dict[str, tuple[type[_Args], Callable[[Any], dict]]] = {
            "list_doctors": (ListDoctorsArgs, self._list_doctors),
            "find_available_slots": (FindAvailableSlotsArgs, self._find_available_slots),
            "book_slot": (BookSlotArgs, self._book_slot),
            "find_bookings": (FindBookingsArgs, self._find_bookings),
            "cancel_booking": (CancelBookingArgs, self._cancel_booking),
        }

    def execute(self, call: ToolCall) -> ToolResult:
        if call.name not in self._tools:
            return self._error(
                call, "unknown_tool", f"There is no tool named '{call.name}'. Use one of: {', '.join(self._tools)}."
            )
        model, handler = self._tools[call.name]

        arguments = call.arguments
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                return self._error(call, "invalid_arguments", "Arguments must be a JSON object.")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return self._error(call, "invalid_arguments", "Arguments must be a JSON object.")

        try:
            args = model.model_validate(arguments)
        except ValidationError as exc:
            return self._error(
                call,
                "invalid_arguments",
                "The arguments are invalid. Fix them and call the tool again, or ask the patient.",
                problems=_format_validation_error(exc),
            )

        try:
            data = handler(args)
        except services.ServiceError as exc:
            message = _SERVICE_ERRORS.get(exc.code, "The request couldn't be completed.")
            return self._error(call, exc.code, message)
        return ToolResult(call=call, content=json.dumps(data, ensure_ascii=False))

    @staticmethod
    def _error(call: ToolCall, code: str, message: str, **extra) -> ToolResult:
        payload = {"error": code, "message": message, **extra}
        return ToolResult(call=call, content=json.dumps(payload, ensure_ascii=False), error_code=code)

    # -- tools -----------------------------------------------------------

    def _list_doctors(self, _: ListDoctorsArgs) -> dict:
        return {"doctors": [_doctor_info(d) for d in services.list_doctors(self.db)]}

    def _find_available_slots(self, args: FindAvailableSlotsArgs) -> dict:
        doctors = services.list_doctors(self.db)
        matches = resolve_doctors(doctors, args.doctor)
        if not matches:
            raise services.DoctorNotFound()
        if len(matches) > 1:
            return {
                "status": "ambiguous_doctor",
                "message": f"'{args.doctor}' matches more than one doctor. Ask the patient which one they want.",
                "candidates": [_doctor_info(d) for d in matches],
            }
        doctor = matches[0]
        if args.date < today():
            return {
                "status": "past_date",
                "message": f"{args.date.isoformat()} is in the past. Today is {today().isoformat()}.",
            }

        start_from, start_before = TIME_WINDOWS[args.time_of_day]
        slots = services.list_available_slots(self.db, doctor.id, args.date, start_from, start_before)
        result = {
            "doctor": _doctor_info(doctor),
            "date": args.date.isoformat(),
            "date_label": date_label(args.date),
            "time_of_day": args.time_of_day,
        }
        if not slots:
            next_dates = services.next_available_dates(self.db, doctor.id, args.date)
            result.update(
                available_slots=[],
                message="No free slots for this doctor at that date and time. Offer the next available dates.",
                next_available_dates=[d.isoformat() for d in next_dates],
            )
            return result

        shown = slots[:MAX_SLOTS_SHOWN]
        self.state.offered_slot_ids.update(s.id for s in shown)
        result["available_slots"] = [
            {"slot_id": s.id, "start": s.start_time.strftime("%H:%M"), "end": s.end_time.strftime("%H:%M")}
            for s in shown
        ]
        if len(slots) > len(shown):
            result["more_available"] = len(slots) - len(shown)
        return result

    def _book_slot(self, args: BookSlotArgs) -> dict:
        # Never trust the model's slot_id: it must exist and must be one this chat was shown.
        slot = services.get_slot(self.db, args.slot_id)
        if slot.id not in self.state.offered_slot_ids:
            raise SlotNotOffered()
        services.check_can_book(self.db, slot, args.patient_name)

        details = {**slot_details(slot), "patient_name": args.patient_name, "phone_number": args.phone_number}
        if self._confirmed("book", args, details):
            return self.run_pending()
        return self._confirmation_required(details)

    def _find_bookings(self, args: FindBookingsArgs) -> dict:
        bookings = services.find_patient_bookings(self.db, args.patient_name, args.phone_number)
        if not bookings:
            return {"bookings": [], "message": "No upcoming bookings found for this name and phone number."}
        return {"bookings": [slot_details(s) for s in bookings]}

    def _cancel_booking(self, args: CancelBookingArgs) -> dict:
        slot = services.get_patient_booking(self.db, args.slot_id, args.patient_name, args.phone_number)
        details = {**slot_details(slot), "patient_name": slot.patient_name, "phone_number": slot.phone_number}
        if self._confirmed("cancel", args, details):
            return self.run_pending()
        return self._confirmation_required(details)

    # -- confirmation guard -----------------------------------------------

    def _confirmed(self, kind: str, args: BookSlotArgs | CancelBookingArgs, details: dict) -> bool:
        """True only if the same action was proposed in an EARLIER turn (the patient has replied since).

        Otherwise this call (re)creates the pending action and the patient must confirm it.
        """
        pending = self.state.pending
        if (
            pending is not None
            and pending.matches(kind, args.slot_id, args.patient_name, args.phone_number)
            and pending.requested_on_turn < self.state.user_turn
        ):
            return True
        if pending is None or not pending.matches(kind, args.slot_id, args.patient_name, args.phone_number):
            self.state.pending = PendingAction(
                kind=kind,
                slot_id=args.slot_id,
                patient_name=args.patient_name,
                phone_number=args.phone_number,
                requested_on_turn=self.state.user_turn,
                details=details,
            )
        return False

    def _confirmation_required(self, details: dict) -> dict:
        action = "booked" if self.state.pending.kind == "book" else "cancelled"
        return {
            "status": "confirmation_required",
            "details": details,
            "message": (
                f"Nothing has been {action} yet. Show these details to the patient and ask them to confirm. "
                "Only after the patient replies yes, call the same tool again with the same arguments."
            ),
        }

    def run_pending(self) -> dict:
        """Carry out the pending action. Called after confirmation (by the tool or the Confirm button)."""
        pending = self.state.pending
        if pending is None:
            return {"status": "nothing_pending"}
        self.state.pending = None  # cleared even if it fails, so a stale action can't be retried by accident
        if pending.kind == "book":
            slot = services.book_slot(self.db, pending.slot_id, pending.patient_name, pending.phone_number)
            return {"status": "booked", "booking": slot_details(slot), "message": "Booked. Tell the patient."}
        slot = services.cancel_patient_booking(self.db, pending.slot_id, pending.patient_name, pending.phone_number)
        return {"status": "cancelled", "booking": slot_details(slot), "message": "Cancelled. Tell the patient."}
