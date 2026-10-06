import os
import re
import stat
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
        self.assertEqual(what, "same")  # nothing new: no broadcast

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
        self.assertEqual(self.s.message(rid)["reactions"], {store.C.key("+447700900123"): "love"})

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


class TapbackTextTest(unittest.TestCase):
    def test_languages(self):
        from pearmsg import tapback
        cases = {
            "Loved “see you at 7”": ("love", "see you at 7"),
            "a attribué la mention « Adore » à « nice im right about to start! »": ("love", "nice im right about to start!"),
            "a attribué la mention « J’aime » à « ok »": ("like", "ok"),
            "a attribué la mention « Je n’aime pas » à « ok »": ("dislike", "ok"),
            "Laughed at “lol”": ("laugh", "lol"),
            "Emphasized “really”": ("emphasize", "really"),
            "Questioned “what”": ("question", "what"),
            "Reacted 😂 to “that meme”": ("😂", "that meme"),
            "a réagi avec 🔥 à « la photo »": ("🔥", "la photo"),
            "Removed a heart from “hi”": ("", "hi"),
            "Le encantó “hola”": ("love", "hola"),
        }
        for text, (kind, quoted) in cases.items():
            tb = tapback.parse(text)
            self.assertIsNotNone(tb, text)
            self.assertEqual((tb.kind, tb.quoted), (kind, quoted), text)

    def test_ordinary_messages_are_not_reactions(self):
        from pearmsg import tapback
        for text in ["He said “I loved it” and then left, can you believe that he actually did that?!",
                     "see you at 7", "“quote only”", "I adore this"]:
            self.assertIsNone(tapback.parse(text), text)

    def test_cleanup_turns_stored_text_into_reaction(self):
        s = store.Store(os.path.join(tempfile.mkdtemp(), "m.db"))
        t = time.time() - 100
        th = store.addr_thread("+16135550100")
        _, target = s.ingest({"bb_guid": "G1", "thread": th, "from_me": True, "text": "nice im right about to start!",
                              "ts": t}, "bluebubbles")
        s.ingest({"map_handle": "h1", "thread": th, "from_me": False, "sender_addr": "+16135550100",
                  "text": "a attribué la mention « Adore » à « nice im right about to start! »", "ts": t + 30}, "iphone")
        s.cleanup_reaction_texts()
        msgs = s.messages(th)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(list(msgs[0]["reactions"].values()), ["love"])


class EnrichmentTest(unittest.TestCase):
    def test_bluetooth_message_gets_mac_details_later(self):
        s = store.Store(os.path.join(tempfile.mkdtemp(), "m.db"))
        t = time.time() - 300
        th = store.addr_thread("+16135550100")
        # while BlueBubbles was offline: plain text from the iPhone, and its reaction as text
        _, rid = s.ingest({"map_handle": "h1", "thread": th, "from_me": False, "sender_addr": "+16135550100",
                           "text": "Wow! huge news", "ts": t}, "iphone")
        s.react_row(rid, "+1 (613) 555-0100", "love")
        # the Mac catches up: the same message with its text effect, a photo and the real reaction
        n = BB.to_message({"guid": "G1", "text": "Wow! huge news", "isFromMe": False, "handle": {"address": "+16135550100"},
                           "dateCreated": int((t + 2) * 1000), "expressiveSendStyleId": "com.apple.MobileSMS.expressivesend.impact",
                           "attributedBody": [{"string": "Wow! huge news", "runs": [{"range": [0, 4], "attributes": {"__kIMTextEffectAttributeName": 12}}]}],
                           "attachments": [{"guid": "A1", "mimeType": "image/jpeg", "transferName": "p.jpg", "totalBytes": 10}],
                           "chats": [{"guid": "iMessage;-;+16135550100", "style": 45, "participants": [{"address": "+16135550100"}]}]})
        self.assertEqual(s.ingest(n, "bluebubbles")[0], "merged")
        r = BB.to_message({"guid": "R1", "associatedMessageGuid": "p:0/G1", "associatedMessageType": "love",
                           "isFromMe": False, "handle": {"address": "+16135550100"}})
        s.react(r["target"], r["sender"], r["reaction"])
        m = s.messages(th)[0]
        self.assertEqual(len(s.messages(th)), 1)
        self.assertEqual(m["via"], "both")
        self.assertEqual(m["effect"], "com.apple.MobileSMS.expressivesend.impact")
        self.assertEqual(m["segments"][0]["effect"], "explode")
        self.assertEqual([a["guid"] for a in m["attachments"]], ["A1"])
        self.assertEqual(m["reactions"], {store.C.key("+16135550100"): "love"})   # one heart, not two


class PruneTest(unittest.TestCase):
    def test_limits_keep_unread_and_unsent(self):
        s = store.Store(os.path.join(tempfile.mkdtemp(), "m.db"))
        th = store.addr_thread("+16135550100")
        t = time.time() - 1000
        for i in range(10):
            s.ingest({"bb_guid": f"G{i}", "thread": th, "from_me": False, "sender_addr": "+16135550100",
                      "text": f"m{i}", "ts": t + i}, "bluebubbles")
        s.ingest({"temp_id": "q", "thread": th, "from_me": True, "text": "waiting", "ts": t - 5, "status": "queued"}, "local")
        s.db.execute("UPDATE messages SET unread=1 WHERE bb_guid='G0'")
        self.assertEqual(s.prune(per_chat=4, total=0), 5)          # G1..G5 go; G0 is unread, the queued one stays
        left = {m["text"] for m in s.messages(th)}
        self.assertEqual(left, {"m0", "waiting", "m6", "m7", "m8", "m9"})


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


class QmlTextFormatTest(unittest.TestCase):
    """Received message text, contact names and group names reach QML labels. A label left on
    the default Text.AutoText parses them as rich text, so a sender could include an <img> and
    make Qt fetch any URL, including a loopback or private-network one that never passes the
    link previewer's public-IP checks. Every label must therefore declare PlainText, and the
    one deliberate RichText element must only ever be fed escaped text."""

    APP = os.path.join(os.path.dirname(__file__), "..", "..", "app")
    # A word boundary, not a line anchor: `delegate: Text {` and `component Foo: Text {` are
    # elements too. A ^ anchor skipped them, this test still passed, and the reviewer then
    # found one of them. TextInput has no textFormat property, so it is excluded by name.
    ELEMENT = re.compile(r"(?<![A-Za-z0-9_])(TextEdit|TextArea|TextInput|Text)\s*\{")

    def _qml(self):
        for name in sorted(os.listdir(self.APP)):
            if name.endswith(".qml"):
                with open(os.path.join(self.APP, name)) as fh:
                    yield name, fh.read().split("\n")

    def test_every_text_element_declares_a_format(self):
        missing = []
        for name, lines in self._qml():
            for i, line in enumerate(lines):
                found = self.ELEMENT.search(line)
                if (found and found.group(1) != "TextInput"
                        and "textFormat" not in " ".join(lines[i:i + 16])):
                    missing.append(f"{name}:{i + 1} {line.strip()[:60]}")
        self.assertEqual(missing, [], "text elements on the AutoText default:\n" + "\n".join(missing))

    def test_only_the_message_bubble_uses_rich_text(self):
        rich = []
        for name, lines in self._qml():
            for i, line in enumerate(lines):
                if re.search(r"textFormat:\s*\w+\.(RichText|StyledText|AutoText)", line):
                    rich.append((name, i + 1, line.strip()))
        self.assertEqual(len(rich), 1, f"expected exactly one rich-text element, got {rich}")
        name, lineno, _ = rich[0]
        self.assertEqual(name, "shell.qml")
        # it must take its text from linkify()/styledHtml(), both of which run escapeHtml()
        block = "\n".join(dict(self._qml())[name][lineno - 6:lineno + 6])
        self.assertTrue("root.linkify(" in block or "root.styledHtml(" in block,
                        f"the rich-text element at {name}:{lineno} is not fed through linkify()")

    def test_escapehtml_neutralises_an_img_tag(self):
        """Mirror of app/shell.qml's escapeHtml, which is what keeps the bubble safe."""
        def escape_html(s):
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        evil = '<img src="http://127.0.0.1:9/x.png">'
        self.assertNotIn("<img", escape_html(evil))
        self.assertIn("&lt;img", escape_html(evil))


class StandardInstallationTest(unittest.TestCase):
    """The marketplace only offers a plain copy-paste install for a listing whose baseline has
    no capabilities beyond `installer`. `privilege`, `package-manager` and `service-management`
    are all triggered by prose and scripts rather than by behaviour, so they come back easily.
    These pin the three that were removed in 1.1.0."""

    ROOT = os.path.join(os.path.dirname(__file__), "..", "..")

    def _text(self, *names):
        out = {}
        for name in names:
            path = os.path.join(self.ROOT, name)
            if os.path.exists(path):
                with open(path, errors="replace") as fh:
                    out[name] = fh.read()
        return out

    SKIP_DIRS = {".git", "__pycache__", "node_modules"}

    def _repo_files(self):
        """Every tracked text file. The first version of this test looked at three files by
        name and so could not see the three systemctl calls that survived in app/launch.sh,
        backend/pearmsg/__main__.py and backend/pearmsg/iphone.py. A guard that names its own
        haystack only proves what it was already looking at."""
        for base, dirs, names in os.walk(self.ROOT):
            dirs[:] = [d for d in dirs if d not in self.SKIP_DIRS]
            for name in names:
                if name.endswith((".png", ".jpg", ".webp", ".svg", ".mp4", ".db")):
                    continue
                path = os.path.join(base, name)
                rel = os.path.relpath(path, self.ROOT)
                if rel.startswith("backend/tests"):
                    continue
                try:
                    with open(path, errors="replace") as fh:
                        yield rel, fh.read()
                except OSError:
                    continue

    def test_no_service_manager_anywhere(self):
        """A systemd unit, or any systemctl call anywhere in the repository, reinstates
        `service-management`. The marketplace cites prose and scripts alike."""
        self.assertFalse(os.path.exists(os.path.join(self.ROOT, "systemd")),
                         "the systemd/ folder is back")
        hits = []
        for rel, body in self._repo_files():
            for n, line in enumerate(body.split("\n"), 1):
                if "systemctl" in line:
                    hits.append(f"{rel}:{n} {line.strip()[:70]}")
        self.assertEqual(hits, [], "systemctl appears in:\n" + "\n".join(hits))

    def test_no_package_manager_or_privilege_prose(self):
        """`sudo pacman -S ...` in a README is enough for both capabilities; Omarchy already
        ships every dependency, so there is nothing to tell anyone to install."""
        bad = []
        for rel, body in self._repo_files():
            for n, line in enumerate(body.split("\n"), 1):
                if "pacman" in line:
                    bad.append(f"{rel}:{n} pacman: {line.strip()[:60]}")
                if ("sudo" in line or "pkexec" in line) and not (
                        "no elevated" in line or "never" in line):
                    bad.append(f"{rel}:{n} privilege: {line.strip()[:60]}")
        self.assertEqual(bad, [], "package-manager/privilege triggers:\n" + "\n".join(bad))

    def test_the_plugin_runs_the_daemon(self):
        """With no unit file, the service plugin is what keeps pear-messagesd alive."""
        with open(os.path.join(self.ROOT, "plugin", "Service.qml")) as fh:
            qml = fh.read()
        self.assertIn('"pearmsg", "daemon"', qml, "Service.qml no longer starts the daemon")
        self.assertIn("workingDirectory", qml)
        self.assertIn("Timer", qml, "nothing restarts the daemon after it exits")

    def test_daemon_sets_its_own_umask(self):
        """UMask=0077 came from the unit file. Losing it silently would undo the journal and
        cache permission fixes' backstop."""
        with open(os.path.join(self.ROOT, "backend", "pearmsg", "daemon.py")) as fh:
            body = fh.read()
        self.assertIn("os.umask(0o077)", body)


class LinkPreviewAddressTest(unittest.TestCase):
    """A sender picks the hostname in a link, so a sender picks what the previewer resolves to.

    `ipaddress` compares an address to a network of the other version as False rather than
    raising, so `ip in CGNAT` never fired for IPv6 and `::ffff:100.64.1.2` passed as public
    while the socket reached 100.64.1.2 on somebody's tailnet. Every IPv6 form that embeds an
    IPv4 address is checked as that address now, not only the mapped one."""

    PRIVATE = [
        "100.64.1.2", "::ffff:100.64.1.2",        # CGNAT, plain and v4-mapped
        "::ffff:127.0.0.1", "::ffff:192.168.1.5", "::ffff:10.0.0.1", "::ffff:169.254.1.1",
        "2002:6440:0102::1",                      # 6to4 around 100.64.1.2
        "64:ff9b::6440:102",                      # NAT64 around 100.64.1.2
        "127.0.0.1", "192.168.1.1", "10.0.0.1", "169.254.1.1", "::1", "0.0.0.0",
        "224.0.0.1", "fe80::1", "fc00::1",
    ]
    PUBLIC = ["8.8.8.8", "1.1.1.1", "::ffff:8.8.8.8", "2001:4860:4860::8888", "2606:4700::1111"]

    def test_private_and_embedded_private_are_refused(self):
        from pearmsg.linkpreview import _is_public
        import ipaddress
        for text in self.PRIVATE:
            with self.subTest(address=text):
                self.assertFalse(_is_public(ipaddress.ip_address(text)),
                                 f"{text} was treated as a public address")

    def test_public_addresses_still_work(self):
        from pearmsg.linkpreview import _is_public
        import ipaddress
        for text in self.PUBLIC:
            with self.subTest(address=text):
                self.assertTrue(_is_public(ipaddress.ip_address(text)),
                                f"{text} was refused but is public")

    def test_version_mismatch_cannot_silently_pass(self):
        """The shape of the original bug: a v6 address tested against a v4 network."""
        import ipaddress
        from pearmsg.linkpreview import CGNAT
        self.assertFalse(ipaddress.ip_address("::ffff:100.64.1.2") in CGNAT,
                         "ipaddress now raises instead of returning False; revisit _is_public")


class LinkifyEscapingTest(unittest.TestCase):
    """linkify() drops the matched URL into href="...", and its URL pattern allows a quote in
    the middle, so escaping only < > & let a sender close the attribute and add their own.
    style="background-image:url(...)" then fetches with no click and without the link
    previewer's address checks running at all."""

    import re as _re
    ESCAPE = _re.compile(r"function escapeHtml\(s\) \{(.+?)\n    \}", _re.S)

    def _escape_body(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "app", "shell.qml")
        with open(path) as fh:
            found = self.ESCAPE.search(fh.read())
        self.assertIsNotNone(found, "escapeHtml() is gone or was renamed")
        return found.group(1)

    def test_quotes_are_escaped(self):
        body = self._escape_body()
        for pattern, entity in (('/"/g', "&quot;"), ("/'/g", "&#39;")):
            self.assertIn(pattern, body, f"escapeHtml no longer escapes {pattern}")
            self.assertIn(entity, body)

    def test_ampersand_is_escaped_first(self):
        """&quot; and &#39; introduce an ampersand, so & has to be replaced before them or the
        entities get double-escaped."""
        body = self._escape_body()
        self.assertLess(body.index("/&/g"), body.index('/"/g'),
                        "& must be escaped before the entity replacements")

    def test_the_injection_payload_cannot_escape_the_attribute(self):
        """Mirror of linkify in Python, run against the payload the review described."""
        def escape(s):
            return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                     .replace('"', "&quot;").replace("'", "&#39;"))
        payload = 'look http://a/x"style="background-image:url(http://127.0.0.1/x.png)"y end'
        escaped = escape(payload)
        self.assertNotIn('"', escaped, "a bare quote survived escaping")
        href = f'<a href="{escaped}">'
        self.assertEqual(href.count('"'), 2, "the href attribute is no longer a single value")


class TapbackLanguageTest(unittest.TestCase):
    """iOS sends a reaction over Bluetooth as a sentence in the phone's language, and it names
    the reaction in its own quotes: "a attribué la mention « Aime » à « … »". The bare word is
    what arrives, without the verb, so a pattern needing "j'aime" missed it and the whole
    sentence was shown as an ordinary message instead of a reaction."""

    REAL = ("a attribué la mention « Aime » à "
            "« good emphasis on the being open to other opportunities »")

    def test_the_reported_french_message_is_a_reaction(self):
        from pearmsg.tapback import parse
        got = parse(self.REAL)
        self.assertIsNotNone(got, "the French 'Aime' form is still read as a plain message")
        self.assertEqual(got.kind, "like")
        self.assertEqual(got.quoted, "good emphasis on the being open to other opportunities")

    def test_named_reactions_across_languages(self):
        from pearmsg.tapback import parse
        for text, kind in [
            ("a attribué la mention « Aime » à « hi »", "like"),
            ("a attribué la mention « Adore » à « hi »", "love"),
            ("a attribué la mention « Haha » à « hi »", "laugh"),
            ("a attribué la mention « Je n’aime pas » à « hi »", "dislike"),
            ("Liked “hi”", "like"),
            ("Le gusta «hi»", "like"),
        ]:
            with self.subTest(text=text):
                got = parse(text)
                self.assertIsNotNone(got, f"not recognised: {text}")
                self.assertEqual(got.kind, kind, f"wrong reaction for: {text}")

    def test_dislike_is_still_checked_before_like(self):
        """'Je n'aime pas' contains 'aime'; the order in KINDS is what keeps it a dislike."""
        from pearmsg.tapback import parse
        self.assertEqual(parse("a attribué la mention « Je n’aime pas » à « hi »").kind, "dislike")

    def test_an_ordinary_sentence_is_not_a_reaction(self):
        from pearmsg.tapback import parse
        self.assertIsNone(parse("I really aime this restaurant"))


class ReactionChipLayoutTest(unittest.TestCase):
    """The chip is pinned to the bubble's top edge and is 34 tall. The row has to reserve that
    height above the bubble, or the chip hangs out of its own row and is drawn over the message
    above it. 18 reserved against 26 needed left 8px of it on the previous bubble."""

    CHIP_HEIGHT = 34
    OVERLAP = 8          # how much of the chip should sit over its own bubble

    def _shell(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "app", "shell.qml")
        with open(path) as fh:
            return fh.read()

    def test_the_row_reserves_the_whole_chip(self):
        body = self._shell()
        found = re.search(r"height: bubble\.height \+ \(msgItem\.reacts\.length \? (\d+) : 0\)", body)
        self.assertIsNotNone(found, "the reacted-row height rule is gone or was rewritten")
        reserve = int(found.group(1))
        self.assertGreaterEqual(reserve, self.CHIP_HEIGHT - self.OVERLAP,
                                f"{reserve}px reserved for a {self.CHIP_HEIGHT}px chip lets it "
                                f"protrude onto the message above")

    def test_the_chip_is_not_anchored_above_its_row(self):
        body = self._shell()
        for found in re.finditer(r"anchors\.topMargin: (-\d+)", body):
            self.fail(f"a negative topMargin ({found.group(1)}) puts an element outside its row")


class SideTimeClearanceTest(unittest.TestCase):
    """The react button appears on hover on the same side of the bubble as the timestamp, at
    `bubble.x - width - 6` with width 32, while the timestamp sat at a fixed 10px margin. They
    overlapped by 28px; the button is simply invisible until you hover, which is why it looked
    like the timestamp was being covered by the reaction popup."""

    def _shell(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "app", "shell.qml")
        with open(path) as fh:
            return fh.read()

    def test_side_time_takes_a_clearance(self):
        body = self._shell()
        self.assertIn("property int clearance", body, "SideTime no longer accepts a clearance")
        self.assertIn("anchors.leftMargin: 10 + clearance", body)
        self.assertIn("anchors.rightMargin: 10 + clearance", body)

    def test_the_bubble_time_clears_the_react_button(self):
        body = self._shell()
        self.assertIn("clearance: reactBtn.visible", body,
                      "the timestamp no longer moves aside for the react button")
        found = re.search(r"clearance: reactBtn\.visible \? \(reactBtn\.width \+ (\d+)\)", body)
        self.assertIsNotNone(found, "the clearance rule was rewritten")
        # button occupies 6 + 32 from the bubble edge; base margin is 10
        pad = int(found.group(1))
        self.assertGreaterEqual(10 + 32 + pad, 6 + 32,
                                "the clearance is too small for the button to fit beside it")

    def test_the_react_button_is_identifiable(self):
        self.assertIn("id: reactBtn", self._shell(), "reactBtn lost its id")


class RedirectBodyLimitTest(unittest.TestCase):
    """Only the final response was capped at MAX_BYTES. urllib's redirect handler drains each
    hop with a bare fp.read() first, which has no limit, so a sender's link answering 302 with
    a huge body held all of it in the daemon."""

    def test_a_huge_redirect_body_is_not_read(self):
        import http.server, threading
        from pearmsg import linkpreview

        chunk = b"A" * (1024 * 1024)
        total = 8                                  # MB offered on the hop
        sent = []

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                if self.path == "/final":
                    body = b"<html><head><title>ok</title></head></html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(302)
                self.send_header("Location",
                                 f"http://127.0.0.1:{self.server.server_port}/final")
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(total * 1024 * 1024))
                self.end_headers()
                try:
                    for _ in range(total):
                        self.wfile.write(chunk)
                        sent.append(len(chunk))
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)

        # the address rules are tested in LinkPreviewAddressTest; this is about the body size
        original = linkpreview._is_public
        linkpreview._is_public = lambda ip: True
        self.addCleanup(lambda: setattr(linkpreview, "_is_public", original))

        # Only that the hop is still followed. How many bytes the *server* manages to push into
        # the socket before the client stops reading depends on kernel buffer sizes and timing,
        # so asserting on it is flaky; the cap itself is checked deterministically below.
        out = linkpreview.fetch(f"http://127.0.0.1:{server.server_port}/start")
        self.assertEqual(out.get("title"), "ok", "the redirect was not followed")

    def test_the_drain_is_capped(self):
        """The mechanism, with no sockets involved: urllib calls fp.read() with no argument on
        a redirect, and that must not be able to return an unbounded amount."""
        from pearmsg import linkpreview

        class Fake:
            def __init__(self):
                self.asked = []
                self.body = b"B" * (32 * 1024 * 1024)

            def read(self, amt=None):
                self.asked.append(amt)
                return self.body if amt is None else self.body[:amt]

        fp = Fake()
        linkpreview._bound_body(fp)
        got = fp.read()                                  # exactly what urllib does
        self.assertLessEqual(len(got), linkpreview.MAX_REDIRECT_BYTES,
                             "an unbounded read got through")
        self.assertEqual(fp.asked[-1], linkpreview.MAX_REDIRECT_BYTES)
        self.assertLessEqual(len(fp.read(64 * 1024 * 1024)), linkpreview.MAX_REDIRECT_BYTES,
                             "an explicit oversized read got through")

    def test_every_redirect_status_is_covered(self):
        from pearmsg import linkpreview
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                self.assertIn(f"http_error_{code}", linkpreview._Redirects.__dict__,
                              f"{code} still uses urllib's unbounded drain")


if __name__ == "__main__":
    unittest.main()


class PrivacyOnDiskTest(unittest.TestCase):
    """The database is not the only file your messages land in.

    SQLite gives the write-ahead log and shared-memory files the permissions the main database
    had when it created them, so chmodding the database after connecting leaves messages.db-wal
    world-readable with message text in it while the database itself looks private. These check
    the files on disk rather than the code that writes them, so they keep holding whatever the
    implementation does next.
    """

    def test_database_and_journal_are_private_under_a_permissive_umask(self):
        old = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder = os.path.join(tmp, "pear-messages")
                path = os.path.join(folder, "messages.db")
                st = store.Store(path)
                st.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('probe','PrivateText')")
                st.db.commit()
                for f, want in ((folder, 0o700), (path, 0o600),
                                (path + "-wal", 0o600), (path + "-shm", 0o600)):
                    if not os.path.exists(f):
                        continue
                    mode = stat.S_IMODE(os.stat(f).st_mode)
                    self.assertEqual(mode & 0o077, 0,
                                     f"{os.path.basename(f)} is {oct(mode)}, readable by others")
        finally:
            os.umask(old)

    def test_the_journal_really_does_hold_message_text(self):
        """Guards the test above from passing because the journal happened to be empty."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pear-messages", "messages.db")
            st = store.Store(path)
            st.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('probe','PrivateText')")
            st.db.commit()
            wal = path + "-wal"
            if os.path.exists(wal):
                with open(wal, "rb") as fh:
                    self.assertIn(b"PrivateText", fh.read())


class MediaPrivacyTest(unittest.TestCase):
    """Photos and videos out of your messages are cached on disk.

    They arrive through os.replace, which keeps the mode of the file being moved, and both
    ffmpeg and a plain open() write 0644 under the usual umask. So the cache filled up with
    world-readable copies of private photos while the directory above them looked fine.
    """

    def test_a_finished_cache_file_is_private(self):
        from pearmsg import media
        old = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                # Stand in for whatever ffmpeg or a download just wrote: 0644.
                dst = os.path.join(tmp, "at_0_ABC-preview.jpg")
                with open(dst, "wb") as fh:
                    fh.write(b"\xff\xd8pretend jpeg")
                self.assertEqual(stat.S_IMODE(os.stat(dst).st_mode) & 0o077, 0o044)
                media._private(dst)
                self.assertEqual(stat.S_IMODE(os.stat(dst).st_mode) & 0o077, 0,
                                 "a cached photo is still readable by other accounts")
        finally:
            os.umask(old)

    def test_cache_directories_are_private_and_repaired(self):
        from pearmsg import daemon
        old = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                d = os.path.join(tmp, "previews")
                os.makedirs(d)                      # as an older version left it: 0755
                self.assertNotEqual(stat.S_IMODE(os.stat(d).st_mode) & 0o077, 0)
                daemon._private_dir(d)
                self.assertEqual(stat.S_IMODE(os.stat(d).st_mode) & 0o077, 0,
                                 "an existing cache directory was not tightened")
        finally:
            os.umask(old)
