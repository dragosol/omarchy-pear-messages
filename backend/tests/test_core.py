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

    def test_email_vs_phone_handle_merges(self):
        # the iPhone names the sender by Apple Account email, the Mac by phone number
        _, rid = self.s.ingest(self._map("h1", "cool", self.t, addr="someone@icloud.com"), "iphone")
        what, rid2 = self.s.ingest(self._bb("G1", "cool", self.t - 2), "bluebubbles")
        self.assertEqual((what, rid2), ("merged", rid))
        self.assertEqual(self.s.message(rid)["thread"], store.addr_thread("+447700900123"))

    def test_other_handle_far_apart_stays_separate(self):
        self.s.ingest(self._map("h1", "cool", self.t, addr="someone@icloud.com"), "iphone")
        self.s.ingest(self._bb("G1", "cool", self.t - 60), "bluebubbles")
        self.assertEqual(self.s.db.execute("select count(*) from messages").fetchone()[0], 2)

    def test_merge_orphans_cleans_old_duplicates(self):
        self.s.ingest(self._bb("G1", "?", self.t), "bluebubbles")
        # stored by an older version, which didn't match across handles
        self.s.db.execute("INSERT INTO messages(thread,from_me,sender,sender_addr,text,norm,ts,via,map_handle) "
                          "VALUES('addr:x',0,'someone@icloud.com','someone@icloud.com','?','?',?,'iphone','h9')", (self.t + 2,))
        self.assertEqual(self.s.merge_orphans(), 1)
        rows = self.s.db.execute("select via, map_handle, bb_guid from messages").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("both", "h9", "G1")])

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


class EffectsTest(unittest.TestCase):
    def setUp(self):
        self.s = store.Store(os.path.join(tempfile.mkdtemp(), "m.db"))

    def test_effect_kept_and_upgraded_on_merge(self):
        t = time.time() - 10
        _, rid = self.s.ingest({"map_handle": "h1", "thread": store.addr_thread("+15550100"), "from_me": False,
                                "sender_addr": "+15550100", "text": "boom", "ts": t}, "iphone")
        self.assertEqual(self.s.message(rid)["effect"], "")      # Bluetooth never carries effects
        n = BB.to_message({"guid": "G1", "text": "boom", "isFromMe": False, "handle": {"address": "+15550100"},
                           "dateCreated": int((t + 1) * 1000), "expressiveSendStyleId": "com.apple.MobileSMS.expressivesend.impact",
                           "chats": [{"guid": "iMessage;-;+15550100", "style": 45, "participants": [{"address": "+15550100"}]}]})
        self.s.ingest(n, "bluebubbles")
        self.assertEqual(self.s.message(rid)["effect"], "com.apple.MobileSMS.expressivesend.impact")
        self.assertEqual(self.s.message(rid)["guid"], "G1")

    def test_my_reaction_roundtrip(self):
        n = BB.to_message({"guid": "G1", "text": "hi", "isFromMe": False, "handle": {"address": "+1"},
                           "dateCreated": int(time.time() * 1000), "chats": []})
        _, rid = self.s.ingest(n, "bluebubbles")
        self.s.react("G1", "me", "love")
        self.assertEqual(self.s.message(rid)["reactions"], {"me": "love"})
        # BlueBubbles echoes our own reaction back as a from-me reaction message
        r = BB.to_message({"guid": "R", "associatedMessageGuid": "p:0/G1", "associatedMessageType": 3000, "isFromMe": True})
        self.s.react(r["target"], r["sender"], r["reaction"])
        self.assertEqual(self.s.message(rid)["reactions"], {})

    def test_old_database_gets_effect_column(self):
        import sqlite3
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        db = sqlite3.connect(path)
        db.executescript(store.SCHEMA.replace(",\n    effect      TEXT NOT NULL DEFAULT ''     -- iMessage bubble/screen effect id", ""))
        db.close()
        s2 = store.Store(path)
        cols = {r[1] for r in s2.db.execute("PRAGMA table_info(messages)")}
        self.assertIn("effect", cols)


class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.s = store.Store(os.path.join(tempfile.mkdtemp(), "m.db"))
        self.t = time.time() - 100

    def _bb(self, guid, text, ts, addr, from_me=False):
        return BB.to_message({"guid": guid, "text": text, "isFromMe": from_me,
                              "handle": None if from_me else {"address": addr}, "dateCreated": int(ts * 1000),
                              "chats": [{"guid": f"iMessage;-;{addr}", "style": 45, "participants": [{"address": addr}]}]})

    def test_messaging_yourself_is_one_thread_shown_once(self):
        # you text yourself at your number and at your email; Messages keeps a sent and a received copy
        for i, (addr, text) in enumerate([("+16135550100", "Wow"), ("me@icloud.com", "Happy birthday!")]):
            self.s.ingest(self._bb(f"O{i}", text, self.t + i * 10, addr, from_me=True), "bluebubbles")
            self.s.ingest(self._bb(f"I{i}", text, self.t + i * 10, addr), "bluebubbles")
        self.assertEqual(len(self.s.threads()), 2)
        self.assertTrue(self.s.learn_self())
        self.s.rethread()
        threads = self.s.threads()
        self.assertEqual([t["id"] for t in threads], ["addr:self"])
        msgs = self.s.messages("addr:self")
        self.assertEqual([(m["text"], m["fromMe"]) for m in msgs], [("Wow", True), ("Happy birthday!", True)])
        # later self-messages go straight there, once
        self.s.ingest(self._bb("I9", "again", self.t + 50, "me@icloud.com"), "bluebubbles")
        self.s.ingest(self._bb("O9", "again", self.t + 50, "me@icloud.com", from_me=True), "bluebubbles")
        self.assertEqual([m["text"] for m in self.s.messages("addr:self")][-1:], ["again"])
        self.assertEqual(len(self.s.messages("addr:self")), 3)

    def test_one_card_two_numbers_one_thread(self):
        self.s.set_contacts([{"name": "Sam", "tels": ["+447700900123", "+447700900999"], "emails": []}], "iphone")
        self.s.ingest(self._bb("A", "hi", self.t, "+447700900123"), "bluebubbles")
        self.s.ingest(self._bb("B", "yo", self.t + 5, "+447700900999"), "bluebubbles")
        self.assertEqual(len(self.s.threads()), 1)
        self.assertEqual(self.s.threads()[0]["title"], "Sam")

    def test_shared_landline_does_not_fuse_people(self):
        self.s.set_contacts([{"name": "Ana", "tels": ["+15550001111", "+15550002222"], "emails": []},
                             {"name": "Bo", "tels": ["+15550001111", "+15550003333"], "emails": []}], "iphone")
        self.s.ingest(self._bb("A", "a", self.t, "+15550002222"), "bluebubbles")
        self.s.ingest(self._bb("B", "b", self.t + 1, "+15550003333"), "bluebubbles")
        self.s.ingest(self._bb("C", "c", self.t + 2, "+15550001111"), "bluebubbles")
        self.assertEqual(len(self.s.threads()), 3)


class TextEffectsTest(unittest.TestCase):
    def test_runs_and_segments(self):
        ab = [{"string": "Wow! 🤯 so big", "runs": [
            {"range": [0, 8], "attributes": {"__kIMTextEffectAttributeName": 12, "__kIMMessagePartAttributeName": 0}},
            {"range": [8, 3], "attributes": {"__kIMMessagePartAttributeName": 0}},
            {"range": [11, 3], "attributes": {"__kIMTextBoldAttributeName": 1}}]}]
        runs = BB.text_runs(ab)
        self.assertEqual(runs, [{"start": 0, "length": 8, "styles": [], "effect": "explode"},
                                {"start": 11, "length": 3, "styles": ["bold"], "effect": ""}])
        segs = store._segments("Wow! 🤯 so big", runs)
        self.assertEqual(segs, [{"text": "Wow! 🤯 ", "styles": [], "effect": "explode"},
                                {"text": "so ", "styles": [], "effect": ""},
                                {"text": "big", "styles": ["bold"], "effect": ""}])

    def test_plain_text_has_no_segments(self):
        self.assertEqual(store._segments("hello", []), [])


class BBTest(unittest.TestCase):
    def test_reaction_parse(self):
        r = BB.to_message({"guid": "R", "associatedMessageGuid": "p:0/ABC", "associatedMessageType": 2001,
                           "isFromMe": False, "handle": {"address": "+1"}})
        self.assertEqual((r["kind"], r["target"], r["reaction"]), ("reaction", "ABC", "like"))
        r = BB.to_message({"guid": "R", "associatedMessageGuid": "ABC", "associatedMessageType": 3001,
                           "isFromMe": True})
        self.assertEqual(r["reaction"], "")

    def test_string_and_emoji_reactions(self):
        r = BB.to_message({"associatedMessageGuid": "p:0/A", "associatedMessageType": "2006", "isFromMe": False,
                           "handle": {"address": "+1"}, "text": "Reacted 😂 to “hello there”"})
        self.assertEqual(r["reaction"], "😂")
        r = BB.to_message({"associatedMessageGuid": "p:0/A", "associatedMessageType": "3006", "isFromMe": False,
                           "handle": {"address": "+1"}, "text": "Removed a 😂 from “hello there”"})
        self.assertEqual(r["reaction"], "")
        r = BB.to_message({"associatedMessageGuid": "A", "associatedMessageType": "laugh", "isFromMe": True})
        self.assertEqual((r["reaction"], r["sender"]), ("laugh", "me"))


if __name__ == "__main__":
    unittest.main()
