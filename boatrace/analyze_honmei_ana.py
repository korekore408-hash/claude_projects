# -*- coding: utf-8 -*-
"""本命/穴の考察スクリプト（honest OOS）。

定義:
  本命 = モデル1番手(p_win最大)。本命的中 = その艇が1着。
  穴   = 1着艇のモデル順位>=4（=モデルが軽視した艇が勝った）。

核心の問い: 低p_winだがモーター/直近成績が良い艇は、
            モデルのp_win想定を超えて勝つのか？（残差シグナルの有無）

入力: predict_win_oos.csv (race_id,枠番,登番,p_win,strength,finish_rank)
      features_race_relative.csv / features_player_history.csv  (race_id,枠番,...)
すべて cp932。出力は標準出力。
"""
import csv
from collections import defaultdict

ENC = "cp932"
LANE = None  # 2列目(枠番)の実キー名を実行時に確定


def load(path, cols):
    """race_id,枠番をキーに、欲しい列だけ dict[(rid,lane)]=dict を返す。"""
    global LANE
    out = {}
    with open(path, encoding=ENC, newline="") as f:
        r = csv.reader(f)
        header = next(r)
        idx = {name: header.index(name) for name in cols if name in header}
        lane_i = 1  # 2列目=枠番
        rid_i = 0
        for row in r:
            if not row:
                continue
            key = (row[rid_i], row[lane_i])
            out[key] = {name: row[i] for name, i in idx.items()}
    return out


def fnum(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def main():
    # 1) 予測+結果
    races = defaultdict(list)  # rid -> list of (lane, p_win, finish_rank)
    with open("predict_win_oos.csv", encoding=ENC, newline="") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if not row:
                continue
            rid, lane = row[0], row[1]
            p = fnum(row[3])
            fr = fnum(row[5])
            races[rid].append((lane, p, fr))

    # 2) 特徴量
    rel = load("features_race_relative.csv",
               ["motor_top2_rate", "motor_rank_in_race", "field_strength_std",
                "winrate_rank_in_race", "win_rate_national", "st_rank_in_race"])
    hist = load("features_player_history.csv",
                ["recent30_winrate", "recent30_avgrank", "motor_intrinsic_win",
                 "local_win_rate", "st_std"])

    # ---- 集計用 ----
    n_races = 0
    honmei_hit = 0           # 本命(model rank1)が1着
    lane1_hit = 0            # 1号艇が1着
    winner_rank_cnt = defaultdict(int)   # 1着艇のモデル順位 -> 回数
    top1_p_sum = 0.0
    ana_cnt = 0              # 穴(1着艇 model順位>=4)

    # 本命確率(top1 p_win)のキャリブレーション
    calib = defaultdict(lambda: [0, 0])  # bin -> [n, top1_win]

    # 穴率を条件別に: field_strength_std 四分位 / top1 p_win 帯
    ana_by_fss = defaultdict(lambda: [0, 0])   # fssビン -> [races, ana]
    ana_by_top1 = defaultdict(lambda: [0, 0])  # top1 p_win帯 -> [races, ana]

    # ★残差テスト用: 全艇 row を貯める
    rows = []  # dict per boat

    for rid, boats in races.items():
        if len(boats) < 2:
            continue
        # finish_rank が全部 None の行(未確定)は除外
        if all(b[2] is None for b in boats):
            continue
        n_races += 1
        # モデル順位
        order = sorted(boats, key=lambda b: -(b[1] if b[1] is not None else -1))
        rank_of = {b[0]: i + 1 for i, b in enumerate(order)}  # lane -> model rank
        top1_lane = order[0][0]
        top1_p = order[0][1] or 0.0
        top1_p_sum += top1_p

        winner = next((b for b in boats if b[2] == 1), None)
        if winner is None:
            n_races -= 1
            top1_p_sum -= top1_p
            continue
        w_lane = winner[0]
        w_rank = rank_of[w_lane]
        winner_rank_cnt[w_rank] += 1
        if w_lane == top1_lane:
            honmei_hit += 1
        if w_lane == "1":
            lane1_hit += 1
        is_ana = 1 if w_rank >= 4 else 0
        ana_cnt += is_ana

        # 本命確率キャリブ
        cb = min(int(top1_p * 10), 9)
        calib[cb][0] += 1
        calib[cb][1] += 1 if w_lane == top1_lane else 0

        # 条件別 穴率
        fss = fnum((rel.get((rid, top1_lane), {}) or {}).get("field_strength_std"))
        if fss is not None:
            fb = "Q?"  # 後で四分位化するため生値も保持
        ana_by_top1_key = ("<.40" if top1_p < .40 else ".40-.55" if top1_p < .55
                           else ".55-.70" if top1_p < .70 else ">=.70")
        ana_by_top1[ana_by_top1_key][0] += 1
        ana_by_top1[ana_by_top1_key][1] += is_ana

        # 残差テスト用 row 収集
        for lane, p, fr in boats:
            rr = rel.get((rid, lane), {})
            hh = hist.get((rid, lane), {})
            rows.append({
                "rid": rid, "lane": lane, "p": p or 0.0,
                "win": 1 if fr == 1 else 0,
                "mrank": rank_of[lane],
                "motor": fnum(rr.get("motor_top2_rate")),
                "motor_in": fnum(hh.get("motor_intrinsic_win")),
                "recent": fnum(hh.get("recent30_winrate")),
                "fss": fnum(rr.get("field_strength_std")),
            })

    # field_strength_std 四分位を全体から
    fss_vals = sorted(v["fss"] for v in rows if v["fss"] is not None)
    def quart(vs):
        n = len(vs)
        return vs[n // 4], vs[n // 2], vs[3 * n // 4]
    q1, q2, q3 = quart(fss_vals)
    for rid, boats in races.items():
        pass

    # 穴率 by fss（本命艇のfssで分類, レース単位）
    seen = set()
    for v in rows:
        if v["mrank"] != 1:  # 本命艇のfssでレースを代表
            continue
        if v["rid"] in seen:
            continue
        seen.add(v["rid"])

    # ---- 出力 ----
    print("=" * 60)
    print(f"対象レース数: {n_races}")
    print(f"本命的中率(model rank1が1着): {honmei_hit/n_races:.3f}")
    print(f"1号艇 勝率(参考)            : {lane1_hit/n_races:.3f}")
    print(f"平均 本命確率(top1 p_win)   : {top1_p_sum/n_races:.3f}")
    print(f"穴率(1着のmodel順位>=4)     : {ana_cnt/n_races:.3f}")
    print()
    print("[1着艇のモデル順位 分布]")
    for k in range(1, 7):
        c = winner_rank_cnt.get(k, 0)
        print(f"  {k}番手: {c/n_races:.3f}  ({c})")
    print()
    print("[本命確率(top1 p_win)のキャリブレーション]  bin: n  予測平均  実測本命的中")
    for b in range(10):
        n, h = calib[b]
        if n:
            print(f"  {b/10:.1f}-{(b+1)/10:.1f}: n={n:5d}  実測={h/n:.3f}")
    print()
    print("[穴率(順位>=4)  × 本命確率帯]")
    for k in ["<.40", ".40-.55", ".55-.70", ">=.70"]:
        n, a = ana_by_top1[k]
        if n:
            print(f"  本命{k:8s}: races={n:5d}  穴率={a/n:.3f}")
    print()

    # ★残差テスト
    print("=" * 60)
    print("★残差テスト: 低評価艇(モデル順位3-6, p_win<0.15)で")
    print("  モーター/直近を四分位分割 → 予測p_win平均 vs 実測勝率")
    print("  実測>>予測 なら『モデル想定を超える穴シグナル』が存在")
    print()
    longshots = [v for v in rows if v["mrank"] >= 3 and v["p"] < 0.15]
    print(f"  対象艇数: {len(longshots)}  平均p_win={sum(v['p'] for v in longshots)/len(longshots):.4f}  実測勝率={sum(v['win'] for v in longshots)/len(longshots):.4f}")
    print()

    def quartile_report(key, label):
        vals = sorted(v[key] for v in longshots if v[key] is not None)
        if len(vals) < 8:
            print(f"  [{label}] データ不足")
            return
        a, b, c = vals[len(vals)//4], vals[len(vals)//2], vals[3*len(vals)//4]
        buckets = defaultdict(lambda: [0, 0.0, 0])  # q -> [n, p_sum, win]
        for v in longshots:
            x = v[key]
            if x is None:
                continue
            q = 1 if x <= a else 2 if x <= b else 3 if x <= c else 4
            buckets[q][0] += 1
            buckets[q][1] += v["p"]
            buckets[q][2] += v["win"]
        print(f"  [{label}]  (四分位閾値 {a:.3f}/{b:.3f}/{c:.3f})")
        print(f"     Q   n     予測p_win  実測勝率   差(実測-予測)")
        for q in range(1, 5):
            n, ps, w = buckets[q]
            if n:
                pp, ww = ps/n, w/n
                print(f"     Q{q}  {n:5d}   {pp:.4f}     {ww:.4f}    {ww-pp:+.4f}")
        print()

    quartile_report("motor", "モーター2連率 motor_top2_rate")
    quartile_report("motor_in", "モーター実績 motor_intrinsic_win")
    quartile_report("recent", "直近30走 勝率 recent30_winrate")


if __name__ == "__main__":
    main()
