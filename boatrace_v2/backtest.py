# -*- coding: utf-8 -*-
"""EVバックテスト v2 — honest・実配当決済・購入時点フィルタ・95%CI・選定/検証分離。

v1 (backtest_ev.py / make_ev_backtest.py) からの改善:
  T1: 入力は walk-forward OOS 予測（predict_win_oos.csv）のみ。in-sample は受け付けない
  T2: 決済は K-file の実配当・公式組番。購入オッズは「発走 PURCHASE_WINDOW_MIN 分前以内に
      取得したスナップショット」だけを採用（発走時刻が無い日はフィルタ不可と明示カウント）
  T4: 期間を選定/検証に分割（SELECT_FRACTION）。EV閾値は選定期間の成績で選び、
      検証期間の成績（n・レース単位ブートストラップ95%CI付き）を最終評価とする
  T5: EV に使う確率は較正済み（calibration.py の PAV 曲線を選定期間で fit して適用）

使い方:
  python backtest.py                       # 取得済みオッズ全期間
  python backtest.py --dates 20260604-20260617
  python backtest.py --no-calib            # 較正なし（比較用）
"""
import argparse
import datetime
import glob
import json
import os
import random
import re
from collections import defaultdict

try:
    from . import config, odds, results, calibration, pl
except ImportError:
    import config
    import odds
    import results
    import calibration
    import pl


# ---------------- 発走時刻・購入オッズの選択 ----------------

def load_start_times(hd):
    """scheduler.py が保存した {rid: "HH:MM"}。無ければ {}。"""
    p = os.path.join(config.START_TIMES_DIR, f"start_times_{hd}.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def purchase_odds(snaps, key, start_dt, window_min=None):
    """(採用オッズ, フィルタ適用可否)。
    start_dt があれば [発走-window, 発走] 内の最終スナップショットのみ採用。
    無ければ最終値を採用し unfiltered=True を返す（レポートで明示する）。"""
    hist = snaps.get(key)
    if not hist:
        return None, False
    if start_dt is None:
        return hist[-1][1], True
    window_min = window_min or config.PURCHASE_WINDOW_MIN
    lo = start_dt - datetime.timedelta(minutes=window_min)
    best = None
    for fa, o in hist:
        try:
            t = datetime.datetime.strptime(fa, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if lo <= t <= start_dt:
            best = o
    return best, False


# ---------------- 1レースの評価 ----------------

def eval_race(rid, boats, curve, snaps, result, start_dt):
    """買い目候補（較正確率PL上位）× 購入オッズ → 各ベットの (ev, stake, ret, bt)。
    返り値 [(ev, bet_type, combo_str, stake, ret)] と unfiltered フラグ。"""
    ps = [p for _, p, _ in sorted(boats)]           # 枠順の p_win
    strengths = calibration.calibrate_race(ps, curve)
    cands = [("2t", c, p) for c, p in pl.pl_top(strengths, 2, config.TOP_2T)]
    cands += [("3t", c, p) for c, p in pl.pl_top(strengths, 3, config.TOP_3T)]
    bets, unfiltered = [], False
    for bt, combo, p in cands:
        cs = "-".join(map(str, combo))
        o, unf = purchase_odds(snaps, (rid, bt, cs), start_dt)
        unfiltered = unfiltered or unf
        if o is None:
            continue
        hit, pay = results.settle(bt, cs, result)
        bets.append((p * o, bt, cs, config.STAKE,
                     pay * config.STAKE // 100 if hit else 0))
    return bets, unfiltered


# ---------------- 集計・CI ----------------

def roi_ci(per_race, n_boot=None, seed=0):
    """per_race=[(stake, ret)] レース単位。ブートストラップ95%CI を返す。"""
    if not per_race:
        return 0.0, 0.0
    n_boot = n_boot or config.BOOTSTRAP_N
    rnd = random.Random(seed)
    n = len(per_race)
    rois = []
    for _ in range(n_boot):
        s = r = 0
        for _ in range(n):
            st, rt = per_race[rnd.randrange(n)]
            s += st
            r += rt
        rois.append(r / s * 100 if s else 0.0)
    rois.sort()
    return rois[int(0.025 * n_boot)], rois[int(0.975 * n_boot)]


def aggregate(race_bets, threshold):
    """しきい値でフィルタして集計。返り値 dict + レース単位 (stake, ret) リスト。"""
    stake = ret = bets = hits = 0
    per_race = []
    for _rid, blist in race_bets:
        s = r = 0
        for ev, _bt, _cs, st, rt in blist:
            if ev < threshold:
                continue
            s += st
            r += rt
            bets += 1
            hits += 1 if rt > 0 else 0
        if s:
            per_race.append((s, r))
            stake += s
            ret += r
    roi = ret / stake * 100 if stake else 0.0
    return {"bets": bets, "hits": hits, "stake": stake, "ret": ret,
            "roi": roi, "races": len(per_race)}, per_race


# ---------------- メイン ----------------

def available_dates(filter_spec=None):
    ds = set()
    for pat in (os.path.join(config.ODDS_SNAP_DIR, "odds_snap_*.csv"),
                os.path.join(config.V1_ODDS_DIR, "odds_*.csv")):
        for p in glob.glob(pat):
            m = re.search(r"(\d{8})", os.path.basename(p))
            if m:
                ds.add(m.group(1))
    ds = sorted(ds)
    if filter_spec:
        if "-" in filter_spec:
            a, b = filter_spec.split("-")
            ds = [d for d in ds if a <= d <= b]
        else:
            ds = [d for d in ds if d == filter_spec]
    return ds


def run(dates, use_calib=True, pred_path=None):
    races = calibration.load_oos(pred_path)
    kres = results.load_results(dates=set(dates))

    n_sel = max(1, int(len(dates) * config.SELECT_FRACTION))
    sel_dates, val_dates = dates[:n_sel], dates[n_sel:]
    fit_until = (f"{sel_dates[-1][:4]}-{sel_dates[-1][4:6]}-{sel_dates[-1][6:8]}"
                 if sel_dates else None)
    curve = (calibration.fit_pav(calibration.samples_from(races, until=fit_until))
             if use_calib else [])

    def collect(day_list):
        out, n_unf, n_legacy = [], 0, 0
        for hd in day_list:
            snaps, legacy = odds.load_snapshots(hd)
            if not snaps:
                continue
            n_legacy += 1 if legacy else 0
            st_map = load_start_times(hd)
            rids = sorted({k[0] for k in snaps})
            for rid in rids:
                boats = races.get(rid)
                res = kres.get(rid)
                if not boats or not res:
                    continue
                start_dt = None
                hm = st_map.get(rid)
                if hm:
                    try:
                        h, mi = map(int, hm.split(":"))
                        start_dt = datetime.datetime(int(hd[:4]), int(hd[4:6]),
                                                     int(hd[6:8]), h, mi)
                    except ValueError:
                        pass
                bets, unf = eval_race(rid, boats, curve, snaps, res, start_dt)
                if bets:
                    out.append((rid, bets))
                    n_unf += 1 if unf else 0
        return out, n_unf, n_legacy

    sel_bets, sel_unf, sel_leg = collect(sel_dates)
    val_bets, val_unf, val_leg = collect(val_dates)

    # 選定期間で最良しきい値を選ぶ（bets>=30 のものに限定＝少数サンプルの偶然を除外）
    best_t, best_roi = None, -1.0
    print(f"\n=== EVバックテスト v2 ===")
    print(f"予測: {pred_path or config.PRED_OOS}（walk-forward OOS）/ 較正: "
          f"{'PAV適用' if use_calib else 'なし'}")
    print(f"選定期間 {len(sel_dates)}日({len(sel_bets)}R) / 検証期間 "
          f"{len(val_dates)}日({len(val_bets)}R)")
    if sel_leg or val_leg:
        print(f"⚠ v1形式オッズ使用日: 選定{sel_leg}日/検証{val_leg}日"
              f"（取得履歴なし＝最終保存値を購入オッズとみなす）")
    if sel_unf or val_unf:
        print(f"⚠ 発走時刻なし（購入時点フィルタ不可）: 選定{sel_unf}R/検証{val_unf}R")

    print("\n[選定期間] しきい値スイープ（ここで閾値を選ぶ・検証には使わない）")
    for t in config.EV_THRESHOLDS:
        st, _pr = aggregate(sel_bets, t)
        print(f"  EV>={t:3.1f}: {st['bets']:5d}点 {st['races']:4d}R "
              f"投資{st['stake']:8,d} 回収率 {st['roi']:6.1f}%")
        if st["bets"] >= 30 and st["roi"] > best_roi:
            best_t, best_roi = t, st["roi"]

    print(f"\n[検証期間] 全しきい値の成績（最終判断はこちら・95%CIはレース単位ブートストラップ）")
    for t in config.EV_THRESHOLDS:
        st, pr = aggregate(val_bets, t)
        lo, hi = roi_ci(pr)
        mark = " ←選定" if t == best_t else ""
        print(f"  EV>={t:3.1f}: {st['bets']:5d}点 {st['races']:4d}R "
              f"投資{st['stake']:8,d} 回収率 {st['roi']:6.1f}% "
              f"(95%CI [{lo:.0f}, {hi:.0f}]){mark}")
    if best_t is None:
        print("\n結論: 選定期間に十分な点数(>=30)のしきい値がなく、閾値選定不能。")
    else:
        st, pr = aggregate(val_bets, best_t)
        lo, hi = roi_ci(pr)
        verdict = ("プラス圏の証拠あり" if lo > 100 else
                   "CIが100%を跨ぐ＝優位性は未実証" if hi > 100 else "マイナス圏")
        print(f"\n結論: 選定 EV>={best_t} の検証成績 {st['roi']:.1f}% "
              f"(n={st['races']}R, 95%CI [{lo:.0f}, {hi:.0f}]) → {verdict}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default=None, help="例 20260604-20260617")
    ap.add_argument("--pred", default=None, help="predict_win_oos.csv のパス")
    ap.add_argument("--no-calib", action="store_true")
    args = ap.parse_args()
    dates = available_dates(args.dates)
    if not dates:
        print("オッズ（v2スナップショット/v1 CSV とも）が見つかりません。")
        return
    if not os.path.exists(args.pred or config.PRED_OOS):
        print(f"walk-forward OOS 予測が見つかりません: {args.pred or config.PRED_OOS}\n"
              f"→ v1 で `python predict_combos.py --mode walkforward` を実行してください。\n"
              f"※ v2 は in-sample を含む predict_win.csv での回収率集計を意図的に受け付けません(T1)。")
        return
    run(dates, use_calib=not args.no_calib, pred_path=args.pred)


if __name__ == "__main__":
    main()
