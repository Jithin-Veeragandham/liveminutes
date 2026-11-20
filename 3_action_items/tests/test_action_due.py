"""Due-date phrases (due.py): what is found, its key, and the date it resolves to."""
from datetime import date

import pytest

from due import due_key, find_due, resolve

WED = date(2026, 11, 18)   # a Wednesday


@pytest.mark.parametrize("text, key", [
    ("I'll send it today", "today"),
    ("can you do that tonight?", "today"),
    ("by end of day please", "today"),
    ("before the end of the day", "today"),
    ("I'll have it by EOD", "today"),
    ("I'll check tomorrow", "tomorrow"),
    ("let's get it done by the end of the week", "end_of_week"),
    ("sometime next week", "next_week"),
    ("bring it to the next meeting", "next_meeting"),
    ("for next time", "next_meeting"),
    ("by Friday", "weekday:friday"),
    ("on thursday", "weekday:thursday"),
    ("next Monday works", "weekday:monday"),
    ("by November 21", "date:11-21"),
    ("on the 21st of November", "date:11-21"),
    ("due 3 March 2027", "date:2027-03-03"),
    ("by 11/21", "date:11-21"),
    ("deadline 2026-12-01", "date:2026-12-01"),
    ("by 3 pm", "time:15:00"),
    ("by 3:30pm", "time:15:30"),
    ("before 10 am", "time:10:00"),
    ("by 17:00", "time:17:00"),
    ("by noon", "time:12:00"),
])
def test_finds_the_due_date(text, key):
    assert find_due(text)[1] == key


@pytest.mark.parametrize("text", ["I'll send the slides", "we may want three buttons", "I have 3 ideas",
                                  "page 21", "it costs 12.50 euros", "the day after the launch party"])
def test_no_due_date(text):
    assert find_due(text) == ("", "")


def test_the_earliest_phrase_wins_and_the_phrase_is_kept():
    assert find_due("by Friday, or Monday at the latest") == ("by Friday", "weekday:friday")
    assert find_due("tomorrow, before the next meeting") == ("tomorrow", "tomorrow")


def test_due_key_reads_phrases_keys_and_iso_dates():
    assert due_key("Next meeting") == "next_meeting"
    assert due_key("weekday:friday") == "weekday:friday"
    assert due_key("2026-11-20") == "date:2026-11-20"
    assert due_key("") == "" and due_key(None) == ""


def test_resolve_against_the_meeting_date():
    assert resolve("today", WED) == "2026-11-18"
    assert resolve("time:15:00", WED) == "2026-11-18"
    assert resolve("tomorrow", WED) == "2026-11-19"
    assert resolve("weekday:friday", WED) == "2026-11-20"
    assert resolve("weekday:wednesday", WED) == "2026-11-25"      # "by Wednesday" said on a Wednesday: next week
    assert resolve("end_of_week", WED) == "2026-11-20"
    assert resolve("date:11-21", WED) == "2026-11-21"
    assert resolve("date:01-05", WED) == "2027-01-05"              # already past this year: next year
    assert resolve("next_week", WED) == "" and resolve("next_meeting", WED) == ""   # no day named
    assert resolve("tomorrow", None) == ""
