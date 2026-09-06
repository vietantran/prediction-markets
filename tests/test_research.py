"""Behavioral tests for classification, units and honest snapshot comparisons."""

import json
import tempfile
import unittest
from pathlib import Path

from pmresearch.insights import analyze
from pmresearch.normalize import normalize_kalshi, normalize_polymarket
from pmresearch.topics import TopicClassifier


T0 = "2026-09-06T12:00:00+00:00"
T1 = "2026-09-07T12:00:00+00:00"


class TopicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.classifier = TopicClassifier()

    def test_midterms_requires_election_context_and_cycle(self):
        classify = self.classifier.classify
        self.assertIn("midterms_2026", classify("2026 US House control: Will Democrats win the House?")["topics"])
        self.assertIn("midterms_2026", classify("Who wins the 2026 North Carolina Senate election?")["topics"])
        self.assertIn("midterms_2026", classify("Who wins the Georgia gubernatorial election in 2026?")["topics"])
        self.assertIn("midterms_2026", classify("Which party wins the midterms?")["topics"])
        self.assertIn("midterms_2026", classify("Who will win NC-01 in 2026?")["topics"])
        for text in ("Will Trump issue an executive order in 2026?", "Who wins the 2028 US presidential election?", "Who wins the 2024 Senate election?", "Will Democrats win the Senate?"):
            with self.subTest(text=text):
                self.assertNotIn("midterms_2026", classify(text)["topics"])

    def test_foreign_elections_sports_and_fed_substrings_excluded(self):
        for text in ("Who will win Canada's 2026 election?", "Who wins the 2026 Australian Senate election?", "Will Roger Federer win the US tennis tournament?", "Will the NBA Federal Credit Union sponsor basketball?", "Will Britain's prime minister call a 2026 election?"):
            with self.subTest(text=text):
                self.assertFalse(self.classifier.classify(text)["topics"])

    def test_fed_and_us_macro_with_context(self):
        self.assertIn("fed_monetary_policy", self.classifier.classify("Will the Fed cut rates in September?")["topics"])
        self.assertIn("fed_monetary_policy", self.classifier.classify("FOMC decision in September")["topics"])
        self.assertIn("us_macro", self.classifier.classify("US CPI inflation above 3%?")["topics"])
        self.assertIn("us_macro", self.classifier.classify("Will nonfarm payrolls fall?")["topics"])
        self.assertFalse(self.classifier.classify("Will the ECB cut interest rates?")["topics"])
        self.assertFalse(self.classifier.classify("Will GDP increase?")["topics"])

    def test_issue_evidence_and_hypothetical_sector_mapping(self):
        result = self.classifier.classify("Will Virginia ban new data centres before 2027 over electricity bills?")
        self.assertIn("data_centers_ai_power", result["topics"])
        self.assertNotIn("midterms_2026", result["topics"])
        self.assertIn("data centres", result["matched_terms"]["data_centers_ai_power"])
        self.assertIn("utilities", result["sectors"])
        self.assertIn("trade_tariffs", self.classifier.classify("Will Trump raise tariffs on China?")["topics"])
        self.assertIn("healthcare_drug_pricing", self.classifier.classify("Will Medicaid funding be cut?")["topics"])

    def test_custom_taxonomy_and_seeds(self):
        config = {"us_context_terms": ["US"], "topics": {"custom": {"terms": ["testing"], "queries": ["US testing"], "sectors": ["example"]}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "topics.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            classifier = TopicClassifier(path)
            self.assertEqual(classifier.discovery_queries, ["US testing"])
            self.assertEqual(classifier.queries_for(["custom"]), ["US testing"])
            self.assertEqual(classifier.classify("US testing")["topics"], ["custom"])

    def test_headline_cycle_takes_precedence_over_rule_history(self):
        result = self.classifier.classify("Who will win the 2028 US Senate election?\nThe prior 2026 midterms determine incumbents.")
        self.assertNotIn("midterms_2026", result["topics"])
        rows = normalize_polymarket({"id": "x", "question": "Democratic candidate?", "description": "In the prior 2024 Senate election, this district voted Republican."},
                                    {"title": "2026 US Senate election"})
        self.assertIn("midterms_2026", rows[0]["topics"])


class NormalizationTests(unittest.TestCase):
    def test_polymarket_string_arrays_keep_alignment_and_zero(self):
        market = {"id": "m1", "question": "Will Democrats control the Senate in 2026?",
                  "outcomes": '["Yes", "No"]', "outcomePrices": '["0", "1"]',
                  "clobTokenIds": '["10000000000000000001", "10000000000000000002"]',
                  "volumeNum": 0, "volume": "100", "liquidityNum": 0}
        rows = normalize_polymarket(market, {"id": 0, "title": "2026 midterms"}, T1)
        self.assertEqual([row["probability"] for row in rows], [0.0, 1.0])
        self.assertEqual(rows[0]["token_id"], "10000000000000000001")
        self.assertEqual(rows[1]["outcome"], "No")
        self.assertEqual(rows[0]["volume"], 0)
        self.assertEqual(rows[0]["event_id"], "0")
        self.assertIsNone(rows[1]["bid"])
        self.assertEqual(rows[0]["observed_at"], T1)

    def test_polymarket_array_mismatch_does_not_drop_unknown_token(self):
        rows = normalize_polymarket({"id": "x", "outcomes": '["Yes"]', "clobTokenIds": '["1", "2"]', "outcomePrices": "invalid"})
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[1]["outcome"])
        self.assertIsNone(rows[1]["probability"])
        self.assertIn("outcome_array_length_mismatch", rows[1]["quality_flags"])

    def test_polymarket_book_not_copied_to_no_or_midpointed_if_crossed(self):
        market = {"id": "x", "outcomes": ["Yes", "No"], "clobTokenIds": ["1", "2"], "bestBid": 0.6, "bestAsk": 0.4}
        yes, no = normalize_polymarket(market)
        self.assertIsNone(yes["probability"])
        self.assertIsNone(yes["spread"])
        self.assertIn("crossed_orderbook", yes["quality_flags"])
        self.assertIsNone(no["ask"])

    def test_live_polymarket_inactive_party_placeholder_stays_unpriced(self):
        market = {"id": "562795", "question": "Will Party A control the Senate after the 2026 Midterm elections?",
                  "outcomes": '["Yes", "No"]', "outcomePrices": None, "clobTokenIds": '["1", "2"]',
                  "bestBid": 0, "bestAsk": 1, "acceptingOrders": True, "active": False,
                  "closed": False, "enableOrderBook": True, "volumeNum": 0, "liquidityNum": 0,
                  "lastTradePrice": 0, "ready": False, "funded": False}
        yes, no = normalize_polymarket(market)
        self.assertIsNone(yes["probability"])
        self.assertIsNone(no["probability"])
        self.assertIn("market_inactive", yes["quality_flags"])
        self.assertIn("boundary_quotes_unverified", yes["quality_flags"])
        self.assertEqual((yes["bid"], yes["ask"]), (0, 1))

    def test_unverified_boundary_books_never_form_midpoints(self):
        market = {"id": "x", "outcomes": ["Yes", "No"], "clobTokenIds": ["1", "2"], "active": True, "acceptingOrders": True}
        for bid, ask in ((0, 1), (0, 0.4), (0.6, 1)):
            with self.subTest(bid=bid, ask=ask):
                row = normalize_polymarket({**market, "bestBid": bid, "bestAsk": ask})[0]
                self.assertIsNone(row["probability"])
        ordinary = normalize_polymarket({**market, "bestBid": 0.4, "bestAsk": 0.5})[0]
        self.assertEqual(ordinary["probability"], 0.45)
        disabled = normalize_polymarket({**market, "acceptingOrders": False, "bestBid": 0.4, "bestAsk": 0.5})[0]
        self.assertIsNone(disabled["probability"])

    def test_dollar_and_legacy_cent_units_never_guessed_by_magnitude(self):
        cents = normalize_kalshi({"ticker": "CENT", "yes_bid": 1, "yes_ask": 3})[0]
        dollars = normalize_kalshi({"ticker": "USD", "yes_bid_dollars": "0.0100", "yes_ask_dollars": "0.0300", "yes_bid": 80, "yes_ask": 90})[0]
        self.assertEqual(cents["bid"], 0.01)
        self.assertEqual(cents["probability"], 0.02)
        self.assertEqual(dollars["probability"], 0.02)
        self.assertEqual(dollars["spread"], 0.02)
        one_dollar = normalize_kalshi({"ticker": "ONE", "yes_bid_dollars": "1", "yes_ask_dollars": "1"})[0]
        self.assertEqual(one_dollar["probability"], 1)

    def test_fractional_contracts_and_deprecated_liquidity(self):
        row = normalize_kalshi({"ticker": "K", "volume_fp": "12.34", "volume": 12, "open_interest_fp": "0.00", "liquidity_dollars": "0.0000"})[0]
        self.assertEqual(row["volume"], 12.34)
        self.assertEqual(row["open_interest"], 0)
        self.assertEqual(row["volume_unit"], "contracts")
        self.assertIsNone(row["liquidity"])

    def test_null_dollar_field_does_not_fall_back_to_legacy_price(self):
        row = normalize_kalshi({"ticker": "K", "yes_bid_dollars": None, "yes_bid": 50, "yes_ask_dollars": "0.60"})[0]
        self.assertIsNone(row["bid"])
        self.assertIsNone(row["probability"])

    def test_zero_and_one_sided_prices_not_confused(self):
        row = normalize_kalshi({"ticker": "K", "yes_bid_dollars": "0", "last_price_dollars": "0"})[0]
        self.assertEqual(row["bid"], 0)
        self.assertIsNone(row["ask"])
        self.assertEqual(row["probability"], 0)
        self.assertEqual(row["price_source"], "last_trade")
        self.assertIn("one_sided_orderbook", row["quality_flags"])

    def test_empty_sizes_prevent_false_half_probability(self):
        row = normalize_kalshi({"ticker": "K", "yes_bid_dollars": "0", "yes_ask_dollars": "1", "yes_bid_size_fp": "0.0", "yes_ask_size_fp": "0.0"})[0]
        self.assertIsNone(row["probability"])
        self.assertIn("missing_orderbook", row["quality_flags"])

    def test_crossed_kalshi_book_uses_separately_labelled_last_trade(self):
        row = normalize_kalshi({"ticker": "K", "yes_bid_dollars": "0.65", "yes_ask_dollars": "0.45", "last_price_dollars": "0.50"})[0]
        self.assertIsNone(row["spread"])
        self.assertEqual(row["probability"], 0.5)
        self.assertEqual(row["price_source"], "last_trade")
        self.assertIn("crossed_orderbook", row["quality_flags"])

    def test_no_bid_complement_and_nonbinary_guard(self):
        row = normalize_kalshi({"ticker": "K", "yes_bid_dollars": "0.42", "no_bid_dollars": "0.56"})[0]
        self.assertEqual(row["ask"], 0.44)
        self.assertEqual(row["spread"], 0.02)
        scalar = normalize_kalshi({"ticker": "S", "market_type": "scalar", "last_price_dollars": "0.42"})[0]
        self.assertIsNone(scalar["probability"])

    def test_settlement_and_us_series_context(self):
        row = normalize_kalshi({"ticker": "CPI", "title": "CPI over 3%?", "status": "finalized", "result": "no"},
                              {"series_ticker": "KXCPI", "series_context": "United States Bureau of Labor Statistics"}, T1)[0]
        self.assertEqual(row["probability"], 0)
        self.assertEqual(row["price_source"], "settlement_result")
        self.assertEqual(row["series_id"], "KXCPI")
        self.assertIn("us_macro", row["topics"])


class InsightTests(unittest.TestCase):
    def row(self, **kwargs):
        base = {"platform": "polymarket", "market_id": "m1", "event_id": "e1", "outcome": "Yes", "token_id": "t1", "title": "US midterms 2026", "rules": "Same rules", "probability": 0.4, "price_source": "gamma_outcome_price", "observed_at": T0, "topics": ["midterms_2026"], "quality_flags": [], "volume": 5000, "volume_unit": "USD_notional"}
        base.update(kwargs)
        return base

    def test_same_contract_change_is_percentage_points(self):
        old = self.row()
        now = self.row(probability=0.46, observed_at=T1)
        result = analyze([now], [old])
        self.assertEqual(result["changes"][0]["change_pp"], 6.0)
        self.assertEqual(result["watchlist"][0]["change_pp"], 6.0)
        self.assertIn("price_move_requires_review", result["watchlist"][0]["reasons"])
        self.assertEqual(result["snapshot_times"]["previous"]["earliest"], T0)

    def test_same_id_on_different_venue_is_not_matched(self):
        result = analyze([self.row(platform="kalshi", probability=0.9, observed_at=T1)], [self.row()])
        self.assertEqual(result["changes"], [])
        self.assertEqual(len(result["new_coverage"]), 1)
        self.assertEqual(len(result["disappeared_coverage"]), 1)

    def test_stale_timestamps_source_or_rules_changes_not_compared(self):
        variations = [{"observed_at": T0}, {"observed_at": "not a time"}, {"price_source": "last_trade"}, {"rules": "Changed rules"}, {"probability": None}, {"token_id": "changed"}]
        for variation in variations:
            with self.subTest(variation=variation):
                row = self.row(probability=0.9, observed_at=T1)
                row.update(variation)
                result = analyze([row], [self.row()])
                self.assertEqual(result["changes"], [])
                self.assertEqual(len(result["skipped_comparisons"]), 1)

    def test_topic_coverage_counts_unique_markets_not_outcomes(self):
        result = analyze([self.row(), self.row(outcome="No", token_id="t2", probability=0.6)])
        self.assertEqual(result["current_outcome_count"], 2)
        self.assertEqual(result["current_market_count"], 1)
        self.assertEqual(result["topic_market_counts"]["midterms_2026"], 1)
        self.assertFalse(result["comparison_requested"])
        self.assertEqual(result["new_coverage"], [])

    def test_thin_and_wide_markets_flagged_with_units(self):
        result = analyze([self.row(volume=0, spread=0.2)])
        entry = result["watchlist"][0]
        self.assertIn("low_cumulative_volume", entry["quality_flags"])
        self.assertIn("wide_spread", entry["quality_flags"])
        self.assertEqual(entry["volume_unit"], "USD_notional")

    def test_duplicate_identities_are_not_used_for_change_math(self):
        old = self.row()
        now = self.row(observed_at=T1, probability=0.7)
        result = analyze([now, now], [old])
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["skipped_comparisons"][0]["reason"], "duplicate_snapshot_identity")


if __name__ == "__main__":
    unittest.main()
