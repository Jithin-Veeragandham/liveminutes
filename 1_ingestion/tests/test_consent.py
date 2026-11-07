"""Offline tests for the consent gate's chat handling: no meeting, no Groq, Attendee mocked.

Run (from 1_ingestion/):  python -m unittest tests.test_consent
In Docker:  see README section 1.7.
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bridge  # noqa: E402
import settings  # noqa: E402
from chat import parse_vouch  # noqa: E402

settings.SESSIONS_DIR = Path(tempfile.mkdtemp(prefix="lm_test_"))  # keep test sessions out of sessions/


class FakeBridge(bridge.Bridge):
    def __init__(self):
        super().__init__(http=None, consent_required=True)
        self.chat, self.calls = [], []
        self.live_url = ""  # no live-view link in chat, so chat[-1] is the consent message (test_live covers it)

    async def attendee(self, s, action, payload=None):
        self.calls.append(action)
        return 200

    async def say(self, s, text):
        s.sent_texts.add(text.strip())
        s.consent_log("bot_chat", text=text)  # like the real say()
        self.chat.append(text)


class ConsentTest(unittest.TestCase):
    def setUp(self):
        self.b = FakeBridge()
        self.s = self.b.session("bot_test")

    def tearDown(self):
        self.s.events.close()
        self.s.piece_log.close()

    def run_(self, coro):
        return asyncio.run(coro)

    def join(self, uuid, name):
        self.run_(self.b.on_join_leave(self.s, {"participant_uuid": uuid, "participant_name": name, "event_type": "join"}))

    def chat(self, uuid, name, text):
        self.run_(self.b.on_chat(self.s, {"text": text, "sender_uuid": uuid, "sender_name": name}))

    def start(self):
        self.run_(self.b.on_bot_state(self.s, {"new_state": "joined_recording"}))

    def meeting(self):
        self.join("u1", "Veeragandham, Jithin")
        self.join("u2", "Conference Room 4B")
        self.join("u3", "Otter.ai Notetaker")
        self.start()

    def test_prompt_lists_everyone_and_explains_vouching(self):
        self.meeting()
        prompt = self.b.chat[-1]
        for name in ("Veeragandham, Jithin", "Conference Room 4B", "Otter.ai Notetaker"):
            self.assertIn(name, prompt)
        self.assertIn('"Conference Room 4B yes"', prompt)
        self.assertFalse(self.s.recording)

    def test_teams_prompt_is_html_one_name_per_line(self):
        self.join("8:orgid:aaa", "Veeragandham, Jithin")
        self.join("8:orgid:bbb", "R&D <Lab>")
        self.start()
        prompt = self.b.chat[-1]
        self.assertNotIn("\n", prompt)  # a newline would press Enter in Teams
        self.assertIn("<br>• Veeragandham, Jithin<br>• R&amp;D &lt;Lab&gt;", prompt)

    def test_meet_prompt_uses_newlines(self):
        self.meeting()
        self.assertIn("\n• Veeragandham, Jithin\n• Conference Room 4B\n• Otter.ai Notetaker", self.b.chat[-1])

    def test_vouching_for_room_and_bot_starts_recording(self):
        self.meeting()
        self.chat("u1", "Veeragandham, Jithin", "yes")
        self.assertFalse(self.s.recording)  # room and bot still waiting
        self.chat("u1", "Veeragandham, Jithin", "Conference Room 4B yes")
        self.assertFalse(self.s.recording)
        self.chat("u1", "Veeragandham, Jithin", "yes for otter")  # partial name, other word order
        self.assertTrue(self.s.recording)
        self.assertEqual(self.s.people["u2"].vouched_by, "Veeragandham, Jithin")
        self.assertIn("Noted: yes for Conference Room 4B, from Veeragandham, Jithin.", self.b.chat)
        self.assertIn("resume_recording", self.b.calls)

    def test_partial_and_comma_names(self):
        names = ["Veeragandham, Jithin", "Conference Room 4B", "Room 12"]
        self.assertEqual(parse_vouch("Jithin yes", names), ("Veeragandham, Jithin", "yes"))
        self.assertEqual(parse_vouch("veeragandham jithin: yes", names), ("Veeragandham, Jithin", "yes"))
        self.assertEqual(parse_vouch("Room 4B yes", names), ("Conference Room 4B", "yes"))
        self.assertEqual(parse_vouch("yes on behalf of Room 12", names), ("Room 12", "yes"))
        self.assertIsNone(parse_vouch("Room yes", names))  # fits two participants: ignored
        self.assertIsNone(parse_vouch("Nobody here yes", names))
        self.assertIsNone(parse_vouch("yes", names))

    def test_no_on_behalf_of_room_stops_and_deletes(self):
        self.meeting()
        self.chat("u1", "Veeragandham, Jithin", "yes")
        self.chat("u1", "Veeragandham, Jithin", "Conference Room 4B no")
        self.assertTrue(self.s.declined)
        self.assertIn("Recording stopped.", self.b.chat)
        self.assertIn("leave", self.b.calls)

    def test_own_no_overrides_earlier_vouch(self):
        self.join("u1", "Ann Lee")
        self.join("u2", "Bob Kim")
        self.start()
        self.chat("u1", "Ann Lee", "yes")
        self.chat("u1", "Ann Lee", "Bob yes")
        self.assertTrue(self.s.recording)
        self.chat("u2", "Bob Kim", "no")
        self.assertTrue(self.s.declined)
        self.assertFalse(self.s.recording)

    def test_unknown_sender_cannot_vouch(self):
        self.meeting()
        self.chat("u9", "Someone Not In Meeting", "Conference Room 4B yes")
        self.assertIsNone(self.s.people["u2"].decision)

    def test_late_joiner_message_explains_vouching(self):
        self.join("u1", "Ann Lee")
        self.start()
        self.chat("u1", "Ann Lee", "yes")
        self.join("u5", "Board Room")
        self.assertFalse(self.s.recording)
        self.assertIn('someone here can reply "Board Room yes"', self.b.chat[-1])
        self.chat("u1", "Ann Lee", "Board Room yes")
        self.assertTrue(self.s.recording)


if __name__ == "__main__":
    unittest.main()
