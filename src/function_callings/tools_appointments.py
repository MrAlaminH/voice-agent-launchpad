import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
from dateutil.parser import isoparse, parse
from livekit.agents import RunContext
from livekit.agents.llm import function_tool


def _validate_email(email: str) -> bool:
    pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
    return re.match(pattern, email) is not None


class AppointmentTools:
    async def _post_json_with_redirects(
        self,
        session: aiohttp.ClientSession,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        max_redirects: int = 3,
    ) -> aiohttp.ClientResponse:
        """POST JSON while handling redirects without downgrading to GET.

        Some webhook providers 301/302/307/308 to a canonical URL. aiohttp may
        switch to GET on redirect; we disable auto-redirects and re-POST to the
        Location to preserve method and body.
        """
        current_url = url
        for _ in range(max_redirects + 1):
            resp = await session.post(
                current_url, json=payload, headers=headers, allow_redirects=False
            )
            if resp.status in {301, 302, 303, 307, 308}:
                location = resp.headers.get("Location")
                if not location:
                    return resp
                current_url = location
                await resp.release()
                continue
            return resp
        return resp

    """Mixin that provides appointment-related tool calls.

    Exposes a single tool `schedule_appointment` that collects user-provided
    details and posts them to a configurable webhook.
    """

    def _parse_and_normalize_datetime(self, text: str) -> Optional[str]:
        """Parse a date/time string and return ISO-8601 in UTC, or None."""
        try:
            # Provide current-year default so spoken dates without a year resolve sensibly
            now = datetime.now(timezone.utc)
            default_dt = now.replace(
                month=now.month, day=now.day, hour=9, minute=0, second=0, microsecond=0
            )
            dt = (
                isoparse(text)
                if "T" in text or ":" in text
                else parse(text, default=default_dt)
            )
            if dt.tzinfo is None:
                # Assume user's local time; treat as naive -> convert to UTC by assuming it's UTC
                # For stricter handling, collect timezone from the user.
                dt = dt.replace(tzinfo=timezone.utc)
            dt_utc = dt.astimezone(timezone.utc)
            return dt_utc.isoformat()
        except Exception:
            return None

    def _normalize_spoken_numbers(self, text: str) -> str:
        """Convert simple spoken numbers to digits (e.g., 'one two three' -> '123', 'triple nine' -> '999')."""
        num_map = {
            "zero": "0",
            "oh": "0",
            "one": "1",
            "two": "2",
            "three": "3",
            "four": "4",
            "for": "4",
            "five": "5",
            "six": "6",
            "seven": "7",
            "eight": "8",
            "ate": "8",
            "nine": "9",
        }
        tokens: list[str] = text.lower().split()
        out: list[str] = []
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token in {"double", "triple"} and i + 1 < len(tokens):
                repeat = 2 if token == "double" else 3
                nxt = tokens[i + 1]
                digit = num_map.get(nxt, nxt if nxt.isdigit() else "")
                if digit:
                    out.append(digit * repeat)
                    i += 2
                    continue
            digit = num_map.get(token)
            if digit is not None:
                out.append(digit)
            else:
                out.append(token)
            i += 1
        return " ".join(out)

    def _normalize_spoken_email(self, email_text: str) -> str:
        """Convert spoken email like 'john dot doe at gmail dot com' to 'john.doe@gmail.com'."""
        s = email_text.strip().lower()
        s = s.replace(" at ", " @ ").replace(" at@", " @")
        s = s.replace(" dot ", ".")
        s = s.replace(" underscore ", "_")
        s = s.replace(" dash ", "-")
        s = self._normalize_spoken_numbers(s)
        s = s.replace(" ", "")
        # common domains spoken
        s = (
            s.replace("gmailcom", "gmail.com")
            .replace("yahoocom", "yahoo.com")
            .replace("outlookcom", "outlook.com")
        )
        s = s.replace("protonmailcom", "protonmail.com")
        # ensure single '@'
        parts = s.split("@")
        if len(parts) > 2:
            s = parts[0] + "@" + "".join(parts[1:])
        return s

    def _normalize_spoken_datetime_phrase(self, phrase: str) -> str:
        """Make time phrases easier to parse: 'three thirty pm' -> '3:30 pm', 'four pm' -> '4 pm'."""
        p = phrase.lower().strip()
        p = self._normalize_spoken_numbers(p)
        # map common minute words after normalization
        p = p.replace(" 30 ", " 3 0 ")
        # convert patterns like '3 30 pm' -> '3:30 pm'
        tokens = p.split()
        out: list[str] = []
        i = 0
        while i < len(tokens):
            if i + 1 < len(tokens) and tokens[i].isdigit() and tokens[i + 1].isdigit():
                out.append(f"{tokens[i]}:{tokens[i + 1].zfill(2)}")
                i += 2
                continue
            out.append(tokens[i])
            i += 1
        p2 = " ".join(out)
        # clean duplicated spaces
        return " ".join(p2.split())

    @function_tool(
        name="prepare_appointment_details",
        description="Validate and normalize name, email, and requested date/time make sure capture the date and time as digits not as words. Returns normalized payload and a friendly confirmation message.",
    )
    async def prepare_appointment_details(
        self,
        context: RunContext,
        name: str,
        email: str,
        appointment_datetime: str,
    ) -> dict[str, Any]:
        """Validate and normalize appointment details before sending.

        - Ensures `email` is valid.
        - Parses `appointment_datetime` in natural language and normalizes to ISO-8601 UTC (defaults to current year if omitted).
        - Returns a dict with `status`, `message`, and `normalized_payload` (name, email, appointment_datetime only).
        """

        if not name or len(name.strip()) < 2:
            return {
                "status": "error",
                "message": "Please provide your full name.",
            }

        email_norm = self._normalize_spoken_email(email)
        if not _validate_email(email_norm):
            return {
                "status": "error",
                "message": "I might have misheard your email. Could you say it again clearly?",
            }

        dt_phrase = self._normalize_spoken_datetime_phrase(appointment_datetime)
        normalized_iso = self._parse_and_normalize_datetime(dt_phrase)
        if not normalized_iso:
            return {
                "status": "error",
                "message": "I didn't quite catch the date and time. Could you say it again naturally, like 'next Tuesday at 3:30pm' or 'August 29th at 4pm'?",
            }

        payload = {
            "name": name.strip(),
            "email": email_norm.strip(),
            "appointment_datetime": normalized_iso,
            "requestType": "tool-calling",
        }

        try:
            # Create a simple, human-friendly confirmation in 12-hour time
            dt_obj = datetime.fromisoformat(
                payload["appointment_datetime"].replace("Z", "+00:00")
            )
            dt_friendly = dt_obj.strftime("%A, %b %d at %I:%M %p")
            # Remove leading zero from hour and lowercase am/pm for natural speech
            dt_friendly = (
                dt_friendly.replace(" at 0", " at ", 1)
                .replace("AM", "am")
                .replace("PM", "pm")
            )
        except Exception:
            dt_friendly = "the requested time"
        confirm_text = f"Just to confirm, is this correct: {payload['name']} at {payload['email']}, and the time is {dt_friendly}?"

        return {
            "status": "ok",
            "message": confirm_text,
            "normalized_payload": payload,
        }

    @function_tool(
        name="confirm_and_send_appointment",
        description="Send a previously prepared appointment payload (name, email, appointment_datetime) to the configured webhook with retries.",
    )
    async def confirm_and_send_appointment(
        self,
        context: RunContext,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Send the normalized payload to the webhook with basic retry policy."""

        logger = logging.getLogger("agent")
        webhook_url = os.getenv("APPOINTMENT_WEBHOOK_URL")
        if not webhook_url:
            return {
                "status": "error",
                "message": "Configuration error: APPOINTMENT_WEBHOOK_URL is not set.",
            }

        headers = {"Content-Type": "application/json"}
        max_retries = 3
        backoff_seconds = 0.75

        for attempt in range(1, max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    resp = await self._post_json_with_redirects(
                        session, webhook_url, payload, headers
                    )
                    async with resp:
                        if 200 <= resp.status < 300:
                            logger.info(
                                "appointment webhook succeeded",
                                extra={"status": resp.status},
                            )
                            return {
                                "status": "ok",
                                "message": "Your appointment details have been submitted. We will confirm shortly.",
                            }
                        text = await resp.text()
                        logger.warning(
                            "appointment webhook failed",
                            extra={"status": resp.status, "body": text[:500]},
                        )
                        # Retry on 5xx, otherwise fail fast
                        if 500 <= resp.status < 600 and attempt < max_retries:
                            await asyncio.sleep(backoff_seconds * attempt)
                            continue
                        return {
                            "status": "error",
                            "message": f"Failed to submit appointment (status {resp.status}). Please try again later.",
                        }
            except Exception as exc:
                logger.exception("appointment webhook exception", exc_info=exc)
                if attempt < max_retries:
                    await asyncio.sleep(backoff_seconds * attempt)
                    continue
                return {
                    "status": "error",
                    "message": "There was a network error submitting your appointment. Please try again later.",
                }

    @function_tool(
        name="schedule_appointment",
        description="Single-step scheduling that validates and sends to the webhook. Prefer the two-step flow for confirmations.",
    )
    async def schedule_appointment(
        self,
        context: RunContext,
        name: str,
        email: str,
        appointment_datetime: str,
    ) -> str:
        """Schedule an appointment by sending details to a webhook.

        Args:
            name: Full name of the caller.
            email: Email address of the caller.
            appointment_datetime: Requested date and time in natural language; it will be normalized server-side.

        Behavior:
            - Validates the provided email format. If invalid, instruct the model to collect a valid email.
            - Sends a JSON payload to the webhook URL defined in env `APPOINTMENT_WEBHOOK_URL`.
            - Returns a short status message suitable for the assistant to read back to the user.
        """

        webhook_url = os.getenv("APPOINTMENT_WEBHOOK_URL")
        if not webhook_url:
            return "Configuration error: APPOINTMENT_WEBHOOK_URL is not set. Please set it and try again."

        if not _validate_email(email):
            return "The email provided seems invalid. Please provide a valid email address."

        payload = {
            "name": name,
            "email": email,
            "appointment_datetime": appointment_datetime,
            "requestType": "tool-calling",
        }

        logger = logging.getLogger("agent")
        headers = {"Content-Type": "application/json"}
        max_retries = 3
        backoff_seconds = 0.75

        for attempt in range(1, max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    resp = await self._post_json_with_redirects(
                        session, webhook_url, payload, headers
                    )
                    async with resp:
                        if 200 <= resp.status < 300:
                            logger.info(
                                "appointment webhook succeeded",
                                extra={"status": resp.status},
                            )
                            return "Your appointment details have been submitted. We will confirm shortly."
                        text = await resp.text()
                        logger.warning(
                            "appointment webhook failed",
                            extra={"status": resp.status, "body": text[:500]},
                        )
                        if 500 <= resp.status < 600 and attempt < max_retries:
                            await asyncio.sleep(backoff_seconds * attempt)
                            continue
                        return f"Failed to submit appointment (status {resp.status}). Please try again later."
            except Exception as exc:
                logger.exception("appointment webhook exception", exc_info=exc)
                if attempt < max_retries:
                    await asyncio.sleep(backoff_seconds * attempt)
                    continue
                return "There was a network error submitting your appointment. Please try again later."
