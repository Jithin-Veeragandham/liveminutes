"""The rule-based baseline (rules.py) on small, hand-written meetings where the right answer is known."""
from datetime import date

import pytest

import rules

PEOPLE = ["Helen Park", "Tom", "Priya", "Marcus"]


def meeting(*rows):
    return [{"id": f"t{i * 1000}", "speaker": who, "start": float(i), "end": float(i), "text": text}
            for i, (who, text) in enumerate(rows)]


def extract(*rows, people=PEOPLE, when=None):
    return rules.extract(meeting(*rows), people, when)


def one(*rows, **kw):
    items = extract(*rows, **kw)
    assert len(items) == 1, items
    return items[0]


# ================= commitments: the speaker owns them =================

@pytest.mark.parametrize("text", [
    "I'll send the deck round after this.",
    "I will check the battery costs.",
    "I can look into the supplier prices.",
    "I'm going to write up the minutes.",
    "I'm gonna email the client.",
    "Let me find out what the display costs.",
    "Um, I I'll draft the survey questions.",
    "I need to contact the factory about the casing.",
    "Should I send you the cost sheet?",
])
def test_first_person_commitment(text):
    item = one(("Tom", text))
    assert item["owner"] == "Tom" and item["kind"] == "first_person"


def test_text_starts_at_the_task_verb():
    assert one(("Tom", "Okay so I'll send the deck round after this."))["text"] == "Send the deck round after this"


# ================= requests: who is asked owns them =================

def test_request_with_a_name():
    item = one(("Helen Park", "Priya, can you send me the screen sketches?"))
    assert item["owner"] == "Priya" and item["kind"] == "request"


def test_request_with_the_name_at_the_end_and_could_you():
    assert one(("Helen Park", "Could you check the strap supplier, Marcus?"))["owner"] == "Marcus"


def test_request_owner_is_who_answers():
    item = one(("Helen Park", "Can someone, can you send me the file?"), ("Tom", "Sure, I can do that."))
    assert item["owner"] == "Tom" and item["source_line_ids"] == ["t0", "t1000"]


def test_unnamed_request_goes_to_the_next_speaker():
    assert one(("Helen Park", "Could you check the budget numbers?"), ("Marcus", "The ones from last week?"))["owner"] == "Marcus"


def test_vocative_assignment_with_youll():
    item = one(("Helen Park", "Tom, you'll look at the working design, so the components."), ("Tom", "Yeah."))
    assert item["owner"] == "Tom"


def test_imperative_with_a_name():
    assert one(("Helen Park", "Priya, finish the display for the tilted front."))["owner"] == "Priya"


def test_third_person_assignment():
    assert one(("Helen Park", "Marcus will contact the factory about the casing."))["owner"] == "Marcus"
    assert one(("Helen Park", "Let's ask Priya to review the leaflet text."))["owner"] == "Priya"


def test_someone_else_takes_over_a_named_task():
    item = one(("Helen Park", "Priya, could you get the usage numbers from the library?"), ("Priya", "Uh, okay, I can try."),
               ("Marcus", "Oh, I can take that, actually."))
    assert item["owner"] == "Marcus"


def test_task_stated_as_someones():
    assert one(("Helen Park", "So Marcus gets the usage numbers from the library."))["owner"] == "Marcus"
    assert one(("Helen Park", "Tom is doing the user flow and the screens."))["owner"] == "Tom"
    assert extract(("Helen Park", "Tom is doing well.")) == []


def test_put_it_on_someone():
    assert one(("Helen Park", "I'll put that on Tom, he can ask management about the branding."))["owner"] == "Tom"


def test_his_list_means_the_person_just_named():
    item = one(("Helen Park", "Marcus isn't here today."), ("Tom", "The printer then."),
               ("Helen Park", "I'll add the receipt printer to his list."))
    assert item["owner"] == "Marcus"


def test_his_list_means_the_absent_person_when_nobody_was_named():
    class P:
        def __init__(self, name, present):
            self.name, self.mentions, self.present = name, (name.lower(),), present
    people = [P("Helen", True), P("Tom", True), P("Rafael", False)]
    assert one(("Helen", "I'll add the receipt printer to his list."), people=people)["owner"] == "Rafael"


# ================= "we" and "let's": nobody owns them =================

@pytest.mark.parametrize("text", ["We need to check the cost of the straps.", "Let's send the client the concept.",
                                  "Someone should probably check the costs.", "We should email the supplier."])
def test_group_task_is_unassigned(text):
    item = one(("Helen Park", text))
    assert item["owner"] == "" and item["kind"] == "group"


def test_group_task_taken_by_someone():
    item = one(("Helen Park", "We need to check the strap costs."), ("Priya", "I'll take that."))
    assert item["owner"] == "Priya"


def test_offer_taken_by_someone_else():
    item = one(("Tom", "I could look into the battery options."), ("Priya", "Actually I'll take that, it's market research."))
    assert item["owner"] == "Priya" and item["text"] == "Look into the battery options"


# ================= due dates on items =================

def test_due_from_the_line():
    item = one(("Tom", "I'll send the cost sheet by Friday."))
    assert item["due_text"] == "by Friday" and item["due"] == ""


def test_due_resolved_with_the_meeting_date():
    item = one(("Tom", "I'll send the cost sheet by Friday."), when=date(2026, 11, 18))
    assert item["due"] == "2026-11-20"


def test_due_from_the_answer():
    item = one(("Helen Park", "Priya, can you send the mock-ups?"), ("Priya", "Sure, I can do that by tomorrow."))
    assert item["due_text"] == "by tomorrow" and item["owner"] == "Priya"


# ================= the same task twice: one item =================

def test_request_then_the_owner_confirming_is_one_item():
    items = extract(("Helen Park", "Tom, you'll do the interface design then, the command list and the lights?"),
                    ("Tom", "Yeah, I'll write out the commands and sketch the display."))
    assert len(items) == 1 and items[0]["owner"] == "Tom" and len(items[0]["source_line_ids"]) == 2


def test_restated_at_the_end_is_one_item():
    items = extract(("Tom", "I'll check the battery supplier prices."), ("Priya", "The screen is fine."),
                    ("Helen Park", "Okay, colours next."), ("Marcus", "Blue."),
                    ("Tom", "So I'll check the battery supplier prices then."))
    assert len(items) == 1


def test_two_different_tasks_stay_two():
    items = extract(("Tom", "I'll check the battery supplier prices."), ("Tom", "And I'll email the client the logo."))
    assert len(items) == 2


def test_same_owner_two_lines_apart_but_different_tasks_stay_two():
    items = extract(("Tom", "I could look into the battery."), ("Priya", "Actually I'll take that."),
                    ("Helen Park", "Can you send me the file by tomorrow?"), ("Priya", "Sure."))
    assert [i["text"] for i in items] == ["Look into the battery", "Send me the file by tomorrow"]


def test_dropped_task_is_removed():
    assert extract(("Tom", "I'll send Priya the notes."), ("Helen Park", "Actually, forget that, no need to send the notes.")) == []


# ================= looks like a task, isn't =================

@pytest.mark.parametrize("text", [
    "I'll be honest, I don't like the colour.",
    "Can you hear me?",
    "Can everyone see my screen?",
    "Could you repeat that?",
    "Let's move on to the budget.",
    "Let's start.",
    "Let's see what we've got.",
    "As you can see here, the sales went up.",
    "You'll get an email from the coach.",
    "Do you want to go first?",
    "I can see that working.",
    "I'll tell you what, it's too big.",
    "We have to make the buttons bigger.",
    "You can make them red.",
    "I'll show you the slides.",
    "I'll make this quick.",
    "Yeah, I think so.",
    "I can't make it on Friday.",
])
def test_not_a_task(text):
    assert extract(("Tom", text)) == []


def test_in_meeting_verbs_need_a_later_time():
    assert extract(("Tom", "I'll present the results.")) == []
    assert one(("Tom", "I'll present the results at the next meeting."))["due_text"] == "at the next meeting"


# ================= the output is the UI contract =================

def test_contract_fields():
    item = one(("Tom", "I'll send the deck round."))
    assert set(item) >= {"id", "text", "owner", "due", "status", "source_line_ids", "version"}
    assert item["id"] == "a1" and item["status"] == "pending" and item["version"] == 1
    assert item["source_line_ids"] == ["t0"]


def test_clean():
    assert rules.clean("Um, I I'll uh send the the deck") == "i'll send the deck"
    assert rules.clean("I'm gonna do it") == "i'm going to do it"
