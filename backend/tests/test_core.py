import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pearmsg import bmsg, contacts, store  # noqa: E402
from pearmsg import bluebubbles as BB  # noqa: E402

INCOMING = (
    "BEGIN:BMSG\r\nVERSION:1.0\r\nSTATUS:UNREAD\r\nTYPE:SMS_GSM\r\nFOLDER:telecom/msg/inbox\r\n"
    "BEGIN:VCARD\r\nVERSION:2.1\r\nN:Doe;Jane\r\nTEL:+44 7700 900123\r\nEND:VCARD\r\n"
    "BEGIN:BENV\r\nBEGIN:VCARD\r\nVERSION:2.1\r\nN:\r\nTEL:\r\nEND:VCARD\r\n"
    "BEGIN:BBODY\r\nCHARSET:UTF-8\r\nLENGTH:40\r\nBEGIN:MSG\r\nhello\r\nsecond line\r\nEND:MSG\r\n"
    "END:BBODY\r\nEND:BENV\r\nEND:BMSG\r\n"
)


class BMessageTest(unittest.TestCase):
    def test_parse_incoming(self):
        m = bmsg.parse(INCOMING)
        self.assertEqual(m.type, "SMS_GSM")
        self.assertEqual(m.originator.name, "Jane Doe")
        self.assertEqual(m.originator.address, "+44 7700 900123")
        self.assertEqual(m.text, "hello\nsecond line")

    def test_build_roundtrip(self):
        raw = bmsg.build("+447700900123", "hi there\nline two ✓", name="Jane")
        m = bmsg.parse(raw)
        self.assertEqual(m.recipients[0].address, "+447700900123")
        self.assertEqual(m.text, "hi there\nline two ✓")
        body = raw.split("BEGIN:MSG", 1)[1]
        length = int(raw.split("LENGTH:", 1)[1].split("\r\n", 1)[0])
        self.assertEqual(length, len(("BEGIN:MSG" + body.split("END:MSG", 1)[0] + "END:MSG\r\n").encode()))

    def test_email_recipient(self):
        self.assertIn("EMAIL:someone@icloud.com", bmsg.build("someone@icloud.com", "x"))

    def test_msg_body_with_end_lookalike(self):
        raw = INCOMING.replace("second line", "END:BBODY in text")
        self.assertEqual(bmsg.parse(raw).text, "hello\nEND:BBODY in text")


class ContactsTest(unittest.TestCase):
    def test_key(self):
        self.assertEqual(contacts.key("+44 7700 900123"), contacts.key("07700900123"))
        self.assertEqual(contacts.key("Someone@iCloud.com"), "someone@icloud.com")

    def test_vcards(self):
        raw = ("BEGIN:VCARD\nVERSION:3.0\nN:Doe;Jane;;;\nFN:Jane Doe\nTEL;TYPE=CELL:+1 (555) 010\n"
               " 0123\nEND:VCARD\nBEGIN:VCARD\nVERSION:3.0\nFN:No Number\nEND:VCARD\n")
        cards = contacts.parse_vcards(raw)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["tels"], ["+1 (555) 0100123"])


class DedupTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.s = store.Store(os.path.join(self.dir, "m.db"))
        self.t = time.time() - 30

    def _map(self, handle, text, ts, addr="+447700900123"):
        return {"map_handle": handle, "thread": store.addr_thread(addr), "from_me": False,
                "sender_addr": addr, "text": text, "ts": ts, "unread": True}

    def _bb(self, guid, text, ts, addr="+447700900123", from_me=False, chat=None):
        chat = chat or {"guid": f"iMessage;-;{addr}", "style": 45, "participants": [{"address": addr}]}
        return BB.to_message({"guid": guid, "text": text, "isFromMe": from_me,
                              "handle": None if from_me else {"address": addr},
                              "dateCreated": int(ts * 1000), "chats": [chat]})

    def test_map_then_bb_merges(self):
        a, id1 = self.s.ingest(self._map("h1", "See you at 5", self.t), "iphone")
        b, id2 = self.s.ingest(self._bb("G1", "See you at 5", self.t + 1.4, addr="07700900123"), "bluebubbles")
        self.assertEqual((a, b), ("new", "merged"))
        self.assertEqual(id1, id2)
        m = self.s.message(id1)
        self.assertEqual(m["via"], "both")
        self.assertAlmostEqual(m["ts"], self.t + 1.4, places=2)  # BlueBubbles' exact time wins

    def test_bb_then_map_merges(self):
        self.s.ingest(self._bb("G1", "Hi’", self.t), "bluebubbles")
        what, _ = self.s.ingest(self._map("h1", "Hi'", self.t + 50), "iphone")
        self.assertEqual(what, "merged")
        self.assertEqual(len(self.s.messages(store.addr_thread("+447700900123"))), 1)

    def test_same_text_twice_stays_two(self):
        self.s.ingest(self._map("h1", "ok", self.t), "iphone")
        self.s.ingest(self._map("h2", "ok", self.t + 20), "iphone")
        self.s.ingest(self._bb("G1", "ok", self.t), "bluebubbles")
        self.s.ingest(self._bb("G2", "ok", self.t + 20), "bluebubbles")
        msgs = self.s.messages(store.addr_thread("+447700900123"))
        self.assertEqual(len(msgs), 2)
        self.assertTrue(all(m["via"] == "both" for m in msgs))

    def test_truncated_copy_merges_and_full_text_wins(self):
        full = "Here is the link to the thing we talked about earlier today, have a look when you can https://example.com/a/b"
        _, rid = self.s.ingest(self._map("h1", full[:100], self.t), "iphone")
        what, rid2 = self.s.ingest(self._bb("G1", full, self.t + 1), "bluebubbles")
        self.assertEqual((what, rid2), ("merged", rid))
        self.assertEqual(self.s.message(rid)["text"], full)

    def test_short_prefix_does_not_merge(self):
        self.s.ingest(self._map("h1", "ok", self.t), "iphone")
        self.s.ingest(self._bb("G1", "ok see you", self.t + 1), "bluebubbles")
        self.assertEqual(len(self.s.messages(store.addr_thread("+447700900123"))), 2)

    def test_far_apart_not_merged(self):
        self.s.ingest(self._map("h1", "ok", self.t - 3600), "iphone")
        self.s.ingest(self._bb("G1", "ok", self.t), "bluebubbles")
        self.assertEqual(len(self.s.messages(store.addr_thread("+447700900123"))), 2)

    def test_repeat_is_update(self):
        self.s.ingest(self._bb("G1", "ok", self.t), "bluebubbles")
        what, _ = self.s.ingest(self._bb("G1", "ok", self.t), "bluebubbles")
        self.assertEqual(what, "updated")

    def test_group_message_moves_to_group(self):
        chat = {"guid": "iMessage;+;chat99", "style": 43, "displayName": "Family",
                "participants": [{"address": "+447700900123"}, {"address": "+447700900456"}]}
        _, rid = self.s.ingest(self._map("h1", "dinner?", self.t), "iphone")
        self.s.ingest(self._bb("G1", "dinner?", self.t + 2, chat=chat), "bluebubbles")
        m = self.s.message(rid)
        self.assertEqual(m["thread"], "chat:iMessage;+;chat99")
        self.assertEqual(self.s.thread(m["thread"])["title"], "Family")

    def test_sent_via_phone_then_seen_by_bb(self):
        _, rid = self.s.ingest({"temp_id": "t1", "thread": store.addr_thread("+447700900123"),
                                "from_me": True, "text": "on my way", "ts": self.t, "status": "sending"}, "local")
        self.s.set_status(rid, "sent", via="iphone")
        what, rid2 = self.s.ingest(self._bb("G9", "on my way", self.t + 4, from_me=True), "bluebubbles")
        self.assertEqual((what, rid2), ("merged", rid))
        self.assertEqual(self.s.message(rid)["status"], "sent")

    def test_sent_via_bb_matches_temp_guid(self):
        _, rid = self.s.ingest({"temp_id": "temp-abc", "thread": store.addr_thread("+447700900123"),
                                "from_me": True, "text": "x", "ts": self.t, "status": "sending"}, "local")
        n = self._bb("G5", "x", self.t + 400, from_me=True)  # far outside the window
        n["temp_id"] = "temp-abc"
        what, rid2 = self.s.ingest(n, "bluebubbles")
        self.assertEqual((what, rid2), ("merged", rid))

    def test_status_never_goes_backwards(self):
        _, rid = self.s.ingest(self._bb("G1", "x", self.t, from_me=True), "bluebubbles")
        self.s.set_status(rid, "read")
        n = self._bb("G1", "x", self.t, from_me=True)
        n["status"] = "delivered"
        self.s.ingest(n, "bluebubbles")
        self.assertEqual(self.s.message(rid)["status"], "read")

    def test_reaction_before_message(self):
        self.s.react("G1", "+447700900123", "love")
        _, rid = self.s.ingest(self._bb("G1", "x", self.t), "bluebubbles")
        self.assertEqual(self.s.message(rid)["reactions"], {"+447700900123": "love"})

    def test_sms_and_imessage_chats_are_one_thread(self):
        self.s.ingest(self._bb("G1", "a", self.t, chat={"guid": "SMS;-;+447700900123", "style": 45,
                                                          "participants": [{"address": "+447700900123"}]}), "bluebubbles")
        self.s.ingest(self._bb("G2", "b", self.t + 1), "bluebubbles")
        threads = self.s.threads()
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["bbChat"], "iMessage;-;+447700900123")

    def test_tapback_text(self):
        self.assertTrue(self.s.is_tapback_text("Loved “see you”"))
        self.assertFalse(self.s.is_tapback_text("Loved the film"))


class BBTest(unittest.TestCase):
    def test_reaction_parse(self):
        r = BB.to_message({"guid": "R", "associatedMessageGuid": "p:0/ABC", "associatedMessageType": 2001,
                           "isFromMe": False, "handle": {"address": "+1"}})
        self.assertEqual((r["kind"], r["target"], r["reaction"]), ("reaction", "ABC", "like"))
        r = BB.to_message({"guid": "R", "associatedMessageGuid": "ABC", "associatedMessageType": 3001,
                           "isFromMe": True})
        self.assertEqual(r["reaction"], "")


if __name__ == "__main__":
    unittest.main()
