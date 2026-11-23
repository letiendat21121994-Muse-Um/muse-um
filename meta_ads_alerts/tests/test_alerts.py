import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from meta_ads_alerts.__main__ import Monitor, cli
from meta_ads_alerts.rules import Config, Thresholds, evaluate, load_config
from meta_ads_alerts.sources import CHECKOUT, PURCHASE, DemoSource, Entity, MetaSource, Metrics, SourceError


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.demo = DemoSource().snapshot(7)

    def test_demo_covers_alerts_without_credentials(self):
        alerts = evaluate(self.demo, Thresholds())
        self.assertEqual({alert.code for alert in alerts}, {
            "cpa_spike", "roas_drop", "ctr_drop", "cpm_rise", "cpc_rise",
            "conversion_rate_drop", "fatigue", "pace_fast", "pace_slow", "learning_limited",
            "disapproved", "delivery_issue", "zero_purchase", "purchases_drop", "checkouts_drop",
        })
        self.assertEqual({alert.code for alert in alerts if alert.severity == "critical"},
                         {"disapproved", "zero_purchase"})

    def test_no_alerts_for_small_samples_or_missing_baseline(self):
        entity = Entity("campaign", "1", "New", Metrics(spend=2, impressions=30, frequency=10))
        self.assertEqual(evaluate(replace(self.demo, entities=(entity,)), Thresholds()), ())
        entity = replace(entity, today=Metrics(spend=20, purchases=1, impressions=10000))
        self.assertEqual(evaluate(replace(self.demo, entities=(entity,)), Thresholds()), ())

    def test_early_day_does_not_trigger_pacing_or_zero_purchase(self):
        early = replace(self.demo, at=self.demo.at.replace(hour=2))
        codes = {alert.code for alert in evaluate(early, Thresholds())}
        self.assertFalse(codes & {"pace_fast", "pace_slow", "zero_purchase", "purchases_drop", "checkouts_drop"})
        self.assertIn("disapproved", codes)

    def test_partial_day_campaign_is_not_compared_to_full_day_budget(self):
        repair = next(entity for entity in self.demo.entities if entity.id == "demo-repair")
        repair = replace(repair, started_at=self.demo.at.replace(hour=12))
        codes = {alert.code for alert in evaluate(replace(self.demo, entities=(repair,)), Thresholds())}
        self.assertNotIn("pace_fast", codes)
        self.assertIn("cpa_spike", codes)

    def test_config_file_overrides_defaults_and_rejects_bad_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text('{"poll_seconds":120,"thresholds":{"baseline_days":3}}')
            self.assertEqual(load_config(path), Config(120, Thresholds(baseline_days=3)))
            for bad in ('{"poll_seconds":true}', '{"thresholds":{"pace_fast":0.9}}',
                        '{"thresholds":{"cpa_rise":NaN}}', '{"thresholds":{"unknown":1}}'):
                path.write_text(bad)
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    load_config(path)


class MetaSourceTests(unittest.TestCase):
    at = datetime(2026, 11, 22, 2, tzinfo=timezone.utc)

    def test_write_and_unapproved_edge_blocked_before_transport(self):
        def unexpected(*args, **kwargs):
            self.fail("blocked request reached transport")

        source = MetaSource("fake-test-token", "act_42", open_request=unexpected)
        for method in ("POST", "PUT", "PATCH", "DELETE", "get"):
            with self.subTest(method=method), self.assertRaises(ValueError):
                source._request(method, "ads")
        with self.assertRaises(ValueError):
            source._request("GET", "https://example.invalid")

    def test_meta_snapshot_uses_get_cursor_paging_and_correct_budget_level(self):
        calls = []
        account = {"id": "act_42", "name": "Demo", "timezone_name": "America/New_York", "currency": "USD"}

        def fake(request, timeout):
            url = urlsplit(request.full_url)
            query = parse_qs(url.query)
            self.assertEqual(url.hostname, "graph.facebook.com")
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.get_header("Authorization"), "Bearer fake-test-token")
            self.assertNotIn("fake-test-token", request.full_url)
            self.assertTrue(request.full_url.startswith("https://graph.facebook.com/v27.0/act_42"))
            self.assertEqual(timeout, 15)
            edge = url.path.split("/")[-1]
            calls.append((edge, query))
            if edge == "act_42":
                payload = account
            elif edge == "campaigns":
                payload = {"data": [{"id": "1", "effective_status": "ACTIVE", "daily_budget": "12000"}]}
            elif edge == "adsets":
                payload = {"data": [{"id": "2", "campaign_id": "1", "effective_status": "ACTIVE",
                                     "daily_budget": "2000", "learning_stage_info": {"status": "FAIL"}}]}
            elif edge == "ads":
                if "after" not in query:
                    payload = {"data": [], "paging": {"next": "https://example.invalid/fake",
                                                      "cursors": {"after": "safe+cursor"}}}
                else:
                    self.assertEqual(query["after"], ["safe+cursor"])
                    payload = {"data": [{"id": "3", "effective_status": "DISAPPROVED",
                                         "ad_review_feedback": {"reason": "test"}}]}
            else:
                level = query["level"][0]
                date_range = json.loads(query["time_range"][0])
                self.assertEqual(date_range, {"since": "2026-11-21", "until": "2026-11-21"}
                                 if date_range["since"] == "2026-11-21"
                                 else {"since": "2026-11-14", "until": "2026-11-20"})
                self.assertEqual(query["action_report_time"], ["conversion"])
                self.assertEqual(json.loads(query["action_attribution_windows"][0]), ["7d_click", "1d_view"])
                ids = {"account": "42", "campaign": "1", "adset": "2", "ad": "3"}
                payload = {"data": [{f"{level}_id": ids[level], "spend": "12.50", "impressions": "1000",
                                     "inline_link_clicks": "50", "frequency": "2.5",
                                     "actions": [{"action_type": PURCHASE, "value": "3"},
                                                 {"action_type": CHECKOUT, "value": "5"},
                                                 {"action_type": "omni_purchase", "value": "9"}],
                                     "action_values": [{"action_type": PURCHASE, "value": "50"}]}]}
            return io.BytesIO(json.dumps(payload).encode())

        snapshot = MetaSource("fake-test-token", "42", open_request=fake, clock=lambda: self.at).snapshot(7)
        self.assertEqual(snapshot.at.hour, 21)
        self.assertEqual(len(calls), 13)
        account, campaign, adset, ad = snapshot.entities
        self.assertEqual((account.today.purchases, account.today.checkouts, account.today.roas), (3, 5, 4))
        self.assertEqual(campaign.daily_budget, 120)
        self.assertIsNone(adset.daily_budget)
        self.assertEqual(adset.learning_status, "FAIL")
        self.assertTrue(ad.rejected and ad.has_review_feedback)

    def test_http_errors_do_not_echo_token_or_body(self):
        def fake(request, timeout):
            body = io.BytesIO(b'{"error":{"code":190,"message":"fake-test-token"}}')
            raise HTTPError(request.full_url, 400, "fake-test-token", {}, body)

        source = MetaSource("fake-test-token", "42", open_request=fake)
        with self.assertRaises(SourceError) as error:
            source.snapshot(7)
        self.assertEqual(error.exception.code, 190)
        self.assertNotIn("fake-test-token", str(error.exception))

    def test_incomplete_response_becomes_source_error_even_for_http_error_body(self):
        class Truncated(io.BytesIO):
            def read(self, *args):
                raise IncompleteRead(b"fake-test-token", 100)

        for status in (200, 502):
            def fake(request, timeout):
                response = Truncated()
                if status != 200:
                    raise HTTPError(request.full_url, status, "upstream failure", {}, response)
                return response

            source = MetaSource("fake-test-token", "42", open_request=fake)
            with self.subTest(status=status), self.assertRaises(SourceError) as result:
                source.snapshot(7)
            self.assertNotIn("fake-test-token", str(result.exception))


class DeliveryTests(unittest.TestCase):
    def test_monitor_loop_marks_unexpected_poll_failure_and_recovers(self):
        class Fixture:
            calls = 0

            def snapshot(self, days):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("detail must not be exposed")
                return DemoSource().snapshot(days)

        states = []

        class Stop:
            def is_set(self):
                return len(states) == 3

            def wait(self, seconds):
                states.append(monitor.state())
                return self.is_set()

        with tempfile.TemporaryDirectory() as temp:
            monitor = Monitor(Fixture(), "demo", Config(), Path(temp) / "alerts.jsonl")
            monitor.run(Stop())

        first, failed, recovered = states
        self.assertIsNone(first["error"])
        self.assertIsNotNone(first["snapshot"])
        self.assertEqual(failed["snapshot"], first["snapshot"])
        self.assertIn("Kiểm tra thất bại", failed["error"])
        self.assertNotIn("detail must not be exposed", failed["error"])
        self.assertIsNotNone(failed["last_attempt"])
        self.assertIsNone(recovered["error"])

    def test_log_deduplicates_alerts_and_last_good_snapshot_survives_failure(self):
        class Fixture:
            fail = False

            def snapshot(self, days):
                if self.fail:
                    raise SourceError("fixture unavailable")
                return DemoSource().snapshot(days)

        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "alerts.jsonl"
            source = Fixture()
            monitor = Monitor(source, "demo", Config(), log)
            monitor.poll()
            first = log.read_text()
            rows = [json.loads(line) for line in first.splitlines()]
            self.assertTrue(rows)
            self.assertEqual(len(rows), len(monitor.state()["snapshot"]["alerts"]))
            self.assertEqual(rows[0]["severity"], "critical")
            monitor.poll()
            self.assertEqual(log.read_text(), first)
            source.fail = True
            monitor.poll()
            self.assertEqual(monitor.state()["error"], "fixture unavailable")
            self.assertEqual(monitor.state()["snapshot"]["name"], "Lumière Hair US")

    def test_check_prints_vietnamese_alerts_without_starting_server(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli(["check"]), 0)
        self.assertIn("[KHẨN]", output.getvalue())
        self.assertIn("Kiểm tra", output.getvalue())


if __name__ == "__main__":
    unittest.main()
