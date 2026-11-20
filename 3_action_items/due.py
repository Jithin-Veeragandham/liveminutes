"""Due dates in what people say: "by Friday", "tomorrow", "end of day", "next week", "November 21", "by 3 pm".

    find_due("I'll send it by Friday")            -> ("by Friday", "weekday:friday")
    due_key("for the next meeting")                -> "next_meeting"
    resolve("weekday:friday", date(2026, 11, 18))  -> "2026-11-20"

A key is how two due dates are compared (the evaluation compares keys):
  today          today, tonight, this afternoon/morning/evening, end of (the) day, EOD
  tomorrow       tomorrow
  end_of_week    this week, end of the week, by the weekend
  next_week      next week
  next_meeting   the next meeting / session, next time, before we meet again
  weekday:<day>  monday ... sunday ("by Thursday", "on Thursday", "next Thursday")
  date:MM-DD     November 21, 21st of November, 11/21 (month first)
  date:YYYY-MM-DD  2026-11-21, or a month and day with a year
  time:HH:MM     by 3 pm, by 15:00, by noon (= today, at that time)
The UI contract wants due as YYYY-MM-DD: resolve() turns a key into a date when the meeting date is known and the
key names a day. "next week" and "next meeting" don't name a day, so they stay as due_text only.
"""
import re
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                          "september", "october", "november", "december"])}
MONTHS.update({m[:3]: n for m, n in list(MONTHS.items())})
MONTHS["sept"] = 9
MONTH = r"(january|february|march|april|may|june|july|august|september|october|november|december|" \
        r"jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)"
DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
LEAD = r"(?:(?:by|before|on|for|until|till|due|at|from)\s+(?:the\s+)?)?"

# (pattern, key or a function of the match). Tried in this order; the earliest match in the text wins.
PATTERNS = [
    (rf"\b(\d{{4}})-(\d{{2}})-(\d{{2}})\b", lambda m: f"date:{m[1]}-{m[2]}-{m[3]}"),
    (rf"\b{LEAD}{MONTH}\.?\s+{DAY}(?:,?\s+(\d{{4}}))?\b", lambda m: month_day(m[1], m[2], m[3])),
    (rf"\b{LEAD}{DAY}\s+(?:of\s+)?{MONTH}\b(?:,?\s+(\d{{4}}))?", lambda m: month_day(m[2], m[1], m[3])),
    (r"\b(?:by|before|on|due)\s+(\d{1,2})/(\d{1,2})\b", lambda m: f"date:{int(m[1]):02d}-{int(m[2]):02d}"),
    (r"\b(?:by|before|until|till)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.|o'?clock)\b", lambda m: clock(m)),
    (r"\b(?:by|before|until|till)\s+(\d{1,2}):(\d{2})\b", lambda m: clock(m)),
    (r"\b(?:by|before|until|till|at)\s+(noon|midday|lunch(?:time)?)\b", "time:12:00"),
    (r"\b(today|tonight|this (?:afternoon|morning|evening)|end of (?:the |today|business )?day|eod|close of business)\b", "today"),
    (r"\b(tomorrow)\b", "tomorrow"),
    (r"\b(end of (?:the |this )?week|this week|by the weekend|before the weekend)\b", "end_of_week"),
    (r"\b(next week)\b", "next_week"),
    (r"\b((?:the )?(?:next|following) (?:meeting|session)|next time we meet|next time|before we meet again|"
     r"by the time we meet)\b", "next_meeting"),
    (rf"\b(?:(?:by|before|on|for|until|till|this|next|coming)\s+)?({'|'.join(WEEKDAYS)})\b",
     lambda m: f"weekday:{m[1].lower()}"),
]
COMPILED = [(re.compile(p, re.IGNORECASE), key) for p, key in PATTERNS]


def month_day(month, day, year):
    m, d = MONTHS[month.lower().rstrip(".")], int(day)
    if not 1 <= d <= 31:
        return None
    return f"date:{int(year):04d}-{m:02d}-{d:02d}" if year else f"date:{m:02d}-{d:02d}"


def clock(m):
    hour, minute = int(m[1]), int(m[2] or 0)
    suffix = (m.group(3) if m.re.groups >= 3 else "") or ""
    suffix = suffix.lower().replace(".", "")
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f"time:{hour:02d}:{minute:02d}"


def find_due(text):
    """(the phrase as said, its key) for the first due date in the text, or ("", "")."""
    best = None
    for pattern, key in COMPILED:
        for m in pattern.finditer(text or ""):
            k = key(m) if callable(key) else key
            if k and (best is None or m.start() < best[0]):
                best = (m.start(), m.group(0).strip(), k)
            if k:
                break
    return (best[1], best[2]) if best else ("", "")


def due_key(text):
    """The key of a due date written any way: a phrase ("by Friday"), a key ("weekday:friday") or an ISO date."""
    text = (text or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"(today|tomorrow|end_of_week|next_week|next_meeting|weekday:\w+|date:[\d-]+|time:\d\d:\d\d)", text):
        return text
    return find_due(text)[1]


def resolve(key, meeting_date):
    """YYYY-MM-DD for a key, counted from the meeting's date. "" if the key names no day (next week, next meeting)
    or there is no meeting date."""
    if not key or meeting_date is None:
        return ""
    if key in ("today",) or key.startswith("time:"):
        return meeting_date.isoformat()
    if key == "tomorrow":
        return (meeting_date + timedelta(days=1)).isoformat()
    if key == "end_of_week":   # the coming Friday (or today, if it is Friday)
        return (meeting_date + timedelta(days=(4 - meeting_date.weekday()) % 7)).isoformat()
    if key.startswith("weekday:"):   # the next such day after today ("by Friday" said on Friday = next Friday)
        days = (WEEKDAYS.index(key.split(":")[1]) - meeting_date.weekday()) % 7 or 7
        return (meeting_date + timedelta(days=days)).isoformat()
    if key.startswith("date:"):
        parts = [int(x) for x in key[5:].split("-")]
        try:
            if len(parts) == 3:
                return date(*parts).isoformat()
            d = date(meeting_date.year, parts[0], parts[1])
            return (d if d >= meeting_date else date(meeting_date.year + 1, parts[0], parts[1])).isoformat()
        except ValueError:
            return ""
    return ""
