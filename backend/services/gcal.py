"""A small Google Calendar client for booking appointments on a clinic's or office's calendar.

It uses a Google *service account*: a robot identity that has a JSON key file. Nobody signs in with a browser, so it works on a
laptop and in a server. The calendar's owner shares the calendar with the service account's e-mail address ("Make changes
to events"), and this module then reads when the calendar is busy and creates or deletes events on it.

Settings (backend/.env):
    GOOGLE_CALENDAR_ID            the calendar's id (Calendar settings -> Integrate calendar), e.g. abc123@group.calendar.google.com
    GOOGLE_SERVICE_ACCOUNT_FILE   the key file, default backend/data/google_calendar.json (never committed)
    VAKYA_TIMEZONE              default Asia/Kolkata

Nothing here talks to Google unless both the id and the key file exist, so without them the booking tool simply works from
its own list. `_http` is the one place a request leaves the program; tests replace it.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

try:
    from core import offline
except ImportError:  # pragma: no cover - running from a different cwd
    from backend.core import offline

log = logging.getLogger("vakya.gcal")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCOPE = "https://www.googleapis.com/auth/calendar"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"
TIMEOUT_S = 8.0
BUSY_TIMEOUT_S = 2.0       # reading the owner's events while a caller waits: never longer than this
BUSY_CACHE_S = 60.0        # how long the events of a day are remembered (a booking made through Vakya forgets its day at once)
BUSY_WINDOW_DAYS = 14      # one request reads this many days, so a search for the nearest free time costs a few requests
BUSY_FAIL_COOLDOWN_S = 15.0  # after a failed read, do not try again for this long: every caller would otherwise wait the timeout


class CalendarError(RuntimeError):
    """The calendar could not be read or written (not configured, refused, unreachable). `status` is Google's HTTP status
    when Google answered."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def calendar_id() -> str:
    return os.getenv("GOOGLE_CALENDAR_ID", "").strip()


def key_path() -> str:
    return os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip() or os.path.join(BACKEND_DIR, "data", "google_calendar.json")


def timezone_name() -> str:
    return os.getenv("VAKYA_TIMEZONE", "Asia/Kolkata").strip() or "Asia/Kolkata"


def tz() -> ZoneInfo:
    return ZoneInfo(timezone_name())


def configured() -> bool:
    if offline.enabled():
        return False
    return bool(calendar_id()) and os.path.isfile(key_path())


def status() -> dict:
    """What the settings page may say: never the key itself."""
    email = None
    try:
        with open(key_path(), "r", encoding="utf-8") as f:
            email = json.load(f).get("client_email")
    except (OSError, ValueError):
        pass
    return {"configured": configured(), "calendar_id_set": bool(calendar_id()), "key_file_found": os.path.isfile(key_path()),
            "service_account_email": email, "timezone": timezone_name()}


# -------------------------------------------------------------------------------------------------- transport

def _http(method: str, url: str, *, headers: Optional[dict] = None, params: Optional[dict] = None,
          json_body: Optional[dict] = None, data: Optional[dict] = None, timeout: Optional[float] = None) -> Tuple[int, dict]:
    import httpx  # noqa: PLC0415
    try:
        r = httpx.request(method, url, headers=headers, params=params, json=json_body, data=data,
                          timeout=TIMEOUT_S if timeout is None else timeout)
    except httpx.HTTPError as e:
        raise CalendarError(f"Could not reach Google Calendar ({e.__class__.__name__}).") from e
    try:
        body = r.json() if r.content else {}
    except ValueError:
        body = {"raw": r.text[:200]}
    return r.status_code, body


_token_lock = threading.Lock()
_token: dict = {"value": None, "expires": 0.0}


def _access_token(timeout: Optional[float] = None) -> str:
    with _token_lock:
        if _token["value"] and _token["expires"] - 60 > time.time():
            return _token["value"]
        try:
            with open(key_path(), "r", encoding="utf-8") as f:
                key = json.load(f)
        except (OSError, ValueError) as e:
            raise CalendarError("The Google service account key file is missing or unreadable.") from e
        import jwt  # noqa: PLC0415  (PyJWT with the cryptography package)
        now = int(time.time())
        assertion = jwt.encode({"iss": key["client_email"], "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 3000},
                               key["private_key"], algorithm="RS256")
        status_code, body = _http("POST", TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                           "assertion": assertion}, **_t(timeout))
        if status_code != 200 or "access_token" not in body:
            raise CalendarError(f"Google refused the service account key ({status_code}): {body.get('error_description') or body.get('error')}")
        _token.update(value=body["access_token"], expires=time.time() + int(body.get("expires_in", 3000)))
        return _token["value"]


def _t(timeout: Optional[float]) -> dict:
    return {} if timeout is None else {"timeout": timeout}


def _call(method: str, path: str, *, params: Optional[dict] = None, json_body: Optional[dict] = None,
          timeout: Optional[float] = None) -> dict:
    if not configured():
        raise CalendarError("Google Calendar is not set up.")
    code, body = _http(method, f"{API}{path}", headers={"Authorization": f"Bearer {_access_token(timeout)}"}, params=params,
                       json_body=json_body, **_t(timeout))
    if code >= 300:
        message = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
        if code in (403, 404):
            message = f"{message}. Share the calendar with the service account and check GOOGLE_CALENDAR_ID."
        raise CalendarError(f"Google Calendar said {code}: {message}", code)
    return body


# ---------------------------------------------------------------------------------------------------- calls

def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=tz())


def busy(start: datetime, end: datetime) -> List[Tuple[datetime, datetime]]:
    """When the calendar is busy between two local times, as (start, end) pairs in local time."""
    start, end = _aware(start), _aware(end)
    body = _call("POST", "/freeBusy", json_body={"timeMin": _rfc3339(start), "timeMax": _rfc3339(end),
                                                 "timeZone": timezone_name(), "items": [{"id": calendar_id()}]})
    cal = (body.get("calendars") or {}).get(calendar_id()) or {}
    if cal.get("errors"):
        raise CalendarError(f"Google Calendar could not read this calendar: {cal['errors'][0].get('reason')}")
    out = []
    for b in cal.get("busy") or []:
        s = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(tz())
        e = datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(tz())
        out.append((s.replace(tzinfo=None), e.replace(tzinfo=None)))
    return out


def create_event(summary: str, description: str, start: datetime, minutes: int, location: str = "",
                 attendees: Optional[List[str]] = None, request_id: str = "") -> str:
    """Create an event and return its id. `request_id` is stored with the event so it can be traced back."""
    start = _aware(start)
    body = {"summary": summary[:200], "description": description[:2000], "location": location[:200],
            "start": {"dateTime": start.isoformat(), "timeZone": timezone_name()},
            "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat(), "timeZone": timezone_name()},
            "extendedProperties": {"private": {"vakyaRequest": request_id}}}
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    return _call("POST", f"/calendars/{_quote(calendar_id())}/events", json_body=body)["id"]


def move_event(event_id: str, start: datetime, minutes: int, description: Optional[str] = None) -> None:
    """Change the start and end of an existing event (events.patch), and its description when one is given. Raises
    CalendarError; `status` is 404 or 410 when the event is gone (the owner deleted it)."""
    start = _aware(start)
    body: dict = {"start": {"dateTime": start.isoformat(), "timeZone": timezone_name()},
                  "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat(), "timeZone": timezone_name()}}
    if description is not None:
        body["description"] = description[:2000]
    _call("PATCH", f"/calendars/{_quote(calendar_id())}/events/{_quote(event_id)}", json_body=body)


def delete_event(event_id: str) -> None:
    code, _ = _http("DELETE", f"{API}/calendars/{_quote(calendar_id())}/events/{_quote(event_id)}",
                    headers={"Authorization": f"Bearer {_access_token()}"})
    if code not in (200, 204, 404, 410):
        raise CalendarError(f"Google Calendar said {code} when removing the event.")


def _quote(text: str) -> str:
    from urllib.parse import quote  # noqa: PLC0415
    return quote(text, safe="")


# ------------------------------------------------------------------------------------------ the owner's own events
# The owner puts a lunch, a holiday or a meeting on the same calendar; those times must not be offered to callers. Vakya's
# own events are bookings already (they carry extendedProperties.private.vakyaRequest) and are left out here.

_busy_lock = threading.Lock()
_busy_cache: Dict[tuple, Tuple[float, List[Tuple[datetime, datetime]]]] = {}
_busy_failed_until = 0.0
_clock = time.monotonic     # tests move it


def _zone(tzname: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(tzname) if tzname else tz()
    except Exception:  # noqa: BLE001 - an unknown zone name: the desk's own
        return tz()


def _event_busy(ev: dict, zone: ZoneInfo) -> Optional[Tuple[datetime, datetime]]:
    """The (start, end) in local naive time that a Google event blocks, or None when it blocks nothing: cancelled, shown as
    available (transparent), declined by the calendar's owner, one of Vakya's own bookings, or without readable times. An
    all-day event blocks its whole days only when it is shown as busy."""
    if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
        return None
    if "vakyaRequest" in ((ev.get("extendedProperties") or {}).get("private") or {}):
        return None
    if any(a.get("self") and a.get("responseStatus") == "declined" for a in ev.get("attendees") or []):
        return None
    start, end = ev.get("start") or {}, ev.get("end") or {}
    try:
        if start.get("dateTime") and end.get("dateTime"):
            s = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
            e = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
            if s.tzinfo is None:
                s, e = s.replace(tzinfo=zone), e.replace(tzinfo=zone)
            return s.astimezone(zone).replace(tzinfo=None), e.astimezone(zone).replace(tzinfo=None)
        if start.get("date") and end.get("date"):
            return (datetime.combine(date.fromisoformat(start["date"]), datetime.min.time()),
                    datetime.combine(date.fromisoformat(end["date"]), datetime.min.time()))
    except (ValueError, TypeError, AttributeError):
        return None
    return None


def _read_window(first: date, last: date, tzname: Optional[str]) -> Dict[date, List[Tuple[datetime, datetime]]]:
    """Read the events of first..last (inclusive) in one go: {day: [(start, end)]} for every day of the window, an event
    that spans midnight cut at the days. Raises CalendarError."""
    zone = _zone(tzname)
    lo = datetime.combine(first, datetime.min.time(), tzinfo=zone)
    hi = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=zone)
    out: Dict[date, List[Tuple[datetime, datetime]]] = {first + timedelta(days=i): [] for i in range((last - first).days + 1)}
    token = None
    for _page in range(6):
        params = {"timeMin": _rfc3339(lo), "timeMax": _rfc3339(hi), "singleEvents": "true", "orderBy": "startTime",
                  "maxResults": 2500, "timeZone": zone.key}
        if token:
            params["pageToken"] = token
        body = _call("GET", f"/calendars/{_quote(calendar_id())}/events", params=params, timeout=BUSY_TIMEOUT_S)
        for ev in body.get("items") or []:
            span = _event_busy(ev, zone)
            if span is None or span[1] <= span[0]:
                continue
            d = span[0].date()
            while d <= span[1].date() and d <= last:
                if d in out:
                    a = max(span[0], datetime.combine(d, datetime.min.time()))
                    b = min(span[1], datetime.combine(d + timedelta(days=1), datetime.min.time()))
                    if b > a:
                        out[d].append((a, b))
                d += timedelta(days=1)
        token = body.get("nextPageToken")
        if not token:
            return out
    raise CalendarError("The calendar has too many events in this window to read.")


def day_busy(day: date, tzname: Optional[str] = None) -> List[Tuple[datetime, datetime]]:
    """When the owner's own events make `day` busy, as (start, end) pairs in local naive time of `tzname` (default the desk's
    zone). Remembered for BUSY_CACHE_S per (calendar, zone, day); a read covers BUSY_WINDOW_DAYS days. Raises CalendarError
    when Google cannot be read in time (the caller falls back to Vakya's own list)."""
    global _busy_failed_until
    cal = calendar_id()
    key = (cal, _zone(tzname).key, day)
    with _busy_lock:
        hit = _busy_cache.get(key)
        if hit and hit[0] > _clock():
            return list(hit[1])
        if _clock() < _busy_failed_until:
            raise CalendarError("Google Calendar could not be read a moment ago; not trying again yet.")
    try:
        window = _read_window(day, day + timedelta(days=BUSY_WINDOW_DAYS - 1), tzname)
    except Exception as e:
        with _busy_lock:
            _busy_failed_until = _clock() + BUSY_FAIL_COOLDOWN_S
        if isinstance(e, CalendarError):
            raise
        raise CalendarError(f"Could not read Google Calendar ({e.__class__.__name__}).") from e
    expires = _clock() + BUSY_CACHE_S
    with _busy_lock:
        for d, spans in window.items():
            _busy_cache[(cal, key[1], d)] = (expires, spans)
        _busy_failed_until = 0.0
    return list(window[day])


def invalidate(day: Optional[date] = None) -> None:
    """Forget what was read for `day` (or every day): a booking, move or cancellation through Vakya changed it."""
    with _busy_lock:
        for k in [k for k in _busy_cache if day is None or k[2] == day]:
            _busy_cache.pop(k, None)


def reset_busy() -> None:
    """Tests: forget everything remembered about the calendar's busy times."""
    global _busy_failed_until
    invalidate()
    _busy_failed_until = 0.0
