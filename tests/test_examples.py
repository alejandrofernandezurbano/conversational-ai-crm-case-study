import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))

from cost_control import CostMeter, Usage, build_messages, cost_usd, pick_model  # noqa: E402
from provider import ProviderA, ProviderB, last_unanswered  # noqa: E402
from webhooks import WebhookVerifier, sign  # noqa: E402


class CostTest(unittest.TestCase):
    def test_routing_uses_cheapest_capable_model(self):
        self.assertEqual(pick_model("classify_intent").tier, 1)
        self.assertEqual(pick_model("complex_negotiation").tier, 3)

    def test_cache_makes_messages_cheaper(self):
        m = pick_model("draft_reply")
        full = cost_usd(m, Usage(6000, 200))
        cached = cost_usd(m, Usage(800, 200, cache_read_tokens=5200))
        self.assertLess(cached, full * 0.5)

    def test_stable_prompt_part_is_first_and_cacheable(self):
        body = build_messages("voice", "rules", "likes X", [{"role": "user", "content": "hi"}])
        self.assertIn("cache_control", body["system"][0])
        self.assertNotIn("cache_control", body["system"][1])

    def test_budget_per_tenant(self):
        meter = CostMeter({"t1": 0.01})
        meter.record("t1", pick_model("draft_reply"), Usage(2000, 200))
        self.assertFalse(meter.can_spend("t1", 0.01))
        self.assertTrue(meter.can_spend("other", 99))


class FakeHttpA:
    def get(self, path, params):
        if path == "/contacts":
            return {"data": [{"id": "1", "name": "Ana"}], "next_cursor": None if params["cursor"] else "p2"} \
                if not params["cursor"] else {"data": [{"id": "2", "name": "Beto"}]}
        return {"data": [{"text": "hola", "created_at": "2026-09-01T10:00:00", "is_inbound": True}]}

    def post(self, path, json):
        return {"id": "m1"}


class FakeHttpB:
    def get(self, path, params):
        if path == "/v2/users":
            return {"users": [{"user_id": 7, "username": "carla"}]}
        return {"items": [{"body": "gracias", "ts": 1788000000, "direction": "out"}]}

    def post(self, path, json):
        return {"message_id": 99}


class ProviderTest(unittest.TestCase):
    def test_same_logic_works_on_both_providers(self):
        a, b = ProviderA(FakeHttpA()), ProviderB(FakeHttpB())
        self.assertEqual([c.display_name for c in a.list_contacts("t")], ["Ana", "Beto"])
        self.assertEqual(b.list_contacts("t")[0].id, "7")
        self.assertIsNotNone(last_unanswered(a, "1"))
        self.assertIsNone(last_unanswered(b, "7"))
        self.assertEqual((a.send("1", "x"), b.send("7", "x")), ("m1", "99"))


class WebhookTest(unittest.TestCase):
    def setUp(self):
        self.v = WebhookVerifier(b"s3cret")
        self.body = json.dumps({"id": "evt_1", "type": "message"}).encode()
        self.now = 1_790_000_000

    def call(self, body=None, ts=None, sig=None):
        body = body or self.body
        ts = str(ts or self.now)
        return self.v.check(ts, sig if sig is not None else sign(b"s3cret", ts, body), body, now=self.now)[0]

    def test_valid_then_duplicate(self):
        self.assertEqual(self.call(), 202)
        self.assertEqual(self.call(), 200)

    def test_forged_and_stale_requests_are_rejected(self):
        self.assertEqual(self.call(sig="0" * 64), 401)
        self.assertEqual(self.call(ts=self.now - 3600), 401)


if __name__ == "__main__":
    unittest.main()
