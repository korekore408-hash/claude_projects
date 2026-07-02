# -*- coding: utf-8 -*-
"""boatrace_v2 ユニットテスト（ネットワーク不要）。

実行: cd boatrace_v2 && python -m unittest test_v2 -v
"""
import datetime
import unittest

import odds
import pl
import calibration
import results
import backtest
import config
import ev_picks
import report


def synth_html(n_cells, value="5.0"):
    return "".join(f'<td class="oddsPoint">{value}</td>' for _ in range(n_cells))


class TestOddsMapping(unittest.TestCase):
    def test_trifecta_unique_and_anchors(self):
        combos = [odds.trifecta_combo(p) for p in range(120)]
        self.assertEqual(len(set(combos)), 120)
        self.assertEqual(combos[0], (1, 2, 3))
        self.assertEqual(combos[6], (1, 2, 4))   # v1 self_test と同一アンカー
        for a, b, c in combos:
            self.assertEqual(len({a, b, c}), 3)

    def test_exacta_unique_and_anchors(self):
        ex = [odds.exacta_combo(p) for p in range(30)]
        self.assertEqual(len(set(ex)), 30)
        self.assertEqual(ex[0], (1, 2))
        self.assertEqual(ex[6], (1, 3))


class TestOddsParser(unittest.TestCase):
    def test_valid_120_cells(self):
        d = odds.parse_trifecta(synth_html(120))
        self.assertEqual(len(d), 120)
        self.assertEqual(d[(1, 2, 3)], 5.0)

    def test_wrong_cell_count_rejected(self):
        """構造変化（セル数不一致）は誤マッピングせず空を返す — T3"""
        self.assertEqual(odds.parse_trifecta(synth_html(119)), {})
        self.assertEqual(odds.parse_trifecta(synth_html(126)), {})
        self.assertEqual(odds.parse_exacta(synth_html(31)), {})

    def test_empty_page_is_not_error(self):
        self.assertEqual(odds.parse_trifecta(""), {})

    def test_kesujo_and_bad_values(self):
        """欠場セルは None、odds<1.0 は棄却 — T3"""
        html = synth_html(119) + '<td class="oddsPoint">欠場</td>'
        d = odds.parse_trifecta(html)
        self.assertEqual(len(d), 119)          # 欠場1セルだけ落ちる
        html0 = synth_html(119, "5.0") + '<td class="oddsPoint">0.0</td>'
        self.assertEqual(len(odds.parse_trifecta(html0)), 119)

    def test_exacta_accepts_45_and_30(self):
        self.assertEqual(len(odds.parse_exacta(synth_html(45))), 30)
        self.assertEqual(len(odds.parse_exacta(synth_html(30))), 30)


class TestPL(unittest.TestCase):
    def test_probs_sum_and_order(self):
        s = [0.5, 0.2, 0.1, 0.1, 0.05, 0.05]
        top = pl.pl_top(s, 2, 30)
        self.assertAlmostEqual(sum(p for _, p in top), 1.0, places=9)
        self.assertEqual(top[0][0], (1, 2))     # 最大 strength 順
        top3 = pl.pl_top(s, 3, 120)
        self.assertAlmostEqual(sum(p for _, p in top3), 1.0, places=9)


class TestCalibration(unittest.TestCase):
    def test_pav_monotone_and_improves_brier(self):
        import random
        rnd = random.Random(1)
        # 過大confidenceな予測: 真の確率は p*0.7
        samples = []
        for _ in range(4000):
            p = rnd.random()
            samples.append((p, 1 if rnd.random() < p * 0.7 else 0))
        curve = calibration.fit_pav(samples)
        ys = [y for _, y in curve]
        self.assertEqual(ys, sorted(ys))        # 単調非減少
        cal = [(calibration.apply_curve(p, curve), y) for p, y in samples]
        self.assertLess(calibration.brier(cal), calibration.brier(samples))

    def test_apply_curve_identity_when_empty(self):
        self.assertEqual(calibration.apply_curve(0.4, []), 0.4)

    def test_calibrate_race_normalizes(self):
        out = calibration.calibrate_race([0.5, 0.2, 0.1, 0.1, 0.05, 0.05], [])
        self.assertAlmostEqual(sum(out), 1.0, places=9)


class TestSettlement(unittest.TestCase):
    def test_settle_uses_official_combo_and_payout(self):
        res = {"combo2": "1-2", "pay2": 360, "combo3": "1-2-4", "pay3": 710}
        self.assertEqual(results.settle("2t", "1-2", res), (True, 360))
        self.assertEqual(results.settle("2t", "2-1", res), (False, 360))
        self.assertEqual(results.settle("3t", "1-2-4", res), (True, 710))
        self.assertEqual(results.settle("3t", "1-2-3", res)[0], False)
        self.assertEqual(results.settle("2t", "1-2", None), (False, 0))


class TestBacktest(unittest.TestCase):
    def test_purchase_window_filter(self):
        """発走前 window 内の最終スナップショットだけを購入オッズにする — T2"""
        start = datetime.datetime(2026, 7, 1, 15, 0)
        snaps = {("r", "2t", "1-2"): [
            ("2026-07-01 12:00:00", 9.9),   # 3時間前 → 窓外
            ("2026-07-01 14:52:00", 4.4),   # 8分前 → 窓内
            ("2026-07-01 14:56:00", 4.0),   # 4分前 → 窓内・最終＝採用
            ("2026-07-01 15:03:00", 3.5),   # 発走後 → 除外
        ]}
        o, unf = backtest.purchase_odds(snaps, ("r", "2t", "1-2"), start, window_min=10)
        self.assertEqual(o, 4.0)
        self.assertFalse(unf)

    def test_purchase_window_none_when_all_stale(self):
        start = datetime.datetime(2026, 7, 1, 15, 0)
        snaps = {("r", "2t", "1-2"): [("2026-07-01 12:00:00", 9.9)]}
        o, _ = backtest.purchase_odds(snaps, ("r", "2t", "1-2"), start, window_min=10)
        self.assertIsNone(o)

    def test_no_start_time_flags_unfiltered(self):
        snaps = {("r", "2t", "1-2"): [("2026-07-01 12:00:00", 9.9)]}
        o, unf = backtest.purchase_odds(snaps, ("r", "2t", "1-2"), None)
        self.assertEqual(o, 9.9)
        self.assertTrue(unf)

    def test_roi_ci_contains_point_estimate(self):
        per_race = [(200, 150), (200, 360), (200, 0), (200, 240)] * 25
        stake = sum(s for s, _ in per_race)
        ret = sum(r for _, r in per_race)
        roi = ret / stake * 100
        lo, hi = backtest.roi_ci(per_race, n_boot=500)
        self.assertLess(lo, roi)
        self.assertGreater(hi, roi)

    def test_aggregate_threshold(self):
        race_bets = [("r1", [(1.6, "2t", "1-2", 100, 360), (0.8, "2t", "1-3", 100, 0)]),
                     ("r2", [(1.2, "3t", "1-2-3", 100, 0)])]
        st, pr = backtest.aggregate(race_bets, 1.5)
        self.assertEqual((st["bets"], st["stake"], st["ret"]), (1, 100, 360))
        self.assertEqual(pr, [(100, 360)])
        st0, _ = backtest.aggregate(race_bets, 0.0)
        self.assertEqual(st0["bets"], 3)


class TestPicksAndReport(unittest.TestCase):
    RID = "012026070201"       # 桐生 2026-07-02 1R

    def _fixture(self):
        preds = {self.RID: [0.60, 0.20, 0.10, 0.05, 0.03, 0.02]}
        snaps = {(self.RID, "2t", "1-2"): [("2026-07-02 09:00:00", 9.0),
                                           ("2026-07-02 10:00:00", 3.0)],
                 (self.RID, "3t", "1-2-3"): [("2026-07-02 10:00:00", 8.0)]}
        starts = {self.RID: "10:30"}
        now = datetime.datetime(2026, 7, 2, 10, 5)
        return preds, snaps, starts, now

    def test_compute_picks_structure(self):
        preds, snaps, starts, now = self._fixture()
        races, meta = ev_picks.compute_picks(
            "20260702", ev_min=0.5, hon_min=0.5, now=now,
            preds=preds, snaps=snaps, curve=[], legacy=False, starts=starts)
        self.assertEqual(len(races), 1)
        race = races[0]
        self.assertEqual((race["venue"], race["rno"], race["start"]),
                         ("桐生", 1, "10:30"))
        combos = {r["combo"]: r for r in race["rows"]}
        self.assertIn("1-2", combos)
        self.assertEqual(combos["1-2"]["odds"], 3.0)     # 最新スナップショットを採用
        self.assertAlmostEqual(combos["1-2"]["age"], 5.0, places=1)
        evs = [r["ev"] for r in race["rows"]]
        self.assertEqual(evs, sorted(evs, reverse=True)) # EV降順
        self.assertEqual(meta["n_picks"], len(race["rows"]))

    def test_compute_picks_hon_min_filters_race(self):
        preds, snaps, starts, now = self._fixture()
        races, meta = ev_picks.compute_picks(
            "20260702", ev_min=0.5, hon_min=0.99, now=now,
            preds=preds, snaps=snaps, curve=[], legacy=False, starts=starts)
        self.assertEqual(races, [])
        self.assertEqual(meta["n_picks"], 0)

    def test_render_html_contains_title_and_picks(self):
        preds, snaps, starts, now = self._fixture()
        races, meta = ev_picks.compute_picks(
            "20260702", ev_min=0.5, hon_min=0.5, now=now,
            preds=preds, snaps=snaps, curve=[], legacy=False, starts=starts)
        html_out = report.render_html("20260702", races, meta, 0.5, 0.5, "10:05:00")
        self.assertIn(config.APP_TITLE, html_out)
        self.assertIn("桐生", html_out)
        self.assertIn("1-2", html_out)

    def test_render_html_empty_and_warnings(self):
        meta = {"no_pred": True, "no_snaps": True, "no_curve": True,
                "legacy": False, "n_picks": 0}
        html_out = report.render_html("20260702", [], meta, 1.5, 0.6, "10:05:00")
        self.assertIn("買い目なし", html_out)
        self.assertIn("予測がありません", html_out)


if __name__ == "__main__":
    unittest.main()
