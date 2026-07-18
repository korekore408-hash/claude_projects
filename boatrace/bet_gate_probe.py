# -*- coding: utf-8 -*-
"""
「買う/見送るを判断する“第6感”ゲート」は作れるか、を場外検証する。

ゲート＝各レースを事前特徴だけ見て bet/skip に振り分けるメタ判断層。
効くかどうかの唯一の基準＝「選んだ部分集合の“場外”回収率が全買いより上がるか」。

モデルは train≤2026-04-30 固定＝2026-05以降はすべて場外(honest OOS)。
そのOOSをさらに分割し、ゲートが過学習でないかを確かめる:
  ゲート学習: 2026-05          （このレースは黒字か、を学ぶ）
  ゲート検証: 2026-06〜07       （学習に使っていない未来で回収率が上がるか）

買い目＝アプリ本番の2連複(新k_ex 1/2/3点・確率比例配分ではなくフラット¥100/点で
純粋にゲート効果だけを見る・F返還)。決着の2連複が買い目に入れば的中。

方式A: 単一特徴を四分位に割り、学習で最良だった分位が検証でも最良か（＝信号の安定性）。
方式B: 複数特徴のロジスティック回帰(自前GD)で P(黒字) を学習→検証で上位だけ買う。
"""
import csv, math
from collections import defaultdict
import build_today as B
import analyze_ana_taikou_roi as A

ENC = "cp932"
OOS = "20260430"          # これ以降が場外
GATE_TRAIN_MAX = "20260531"   # 〜5月末=ゲート学習 / 6月〜=ゲート検証


def stdev(xs):
    n = len(xs)
    if n < 2: return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / n)


def load_relfeat():
    """features_race_relative.csv → {(rid,waku): {feat:float}}。ASCII列のみ使う。"""
    out = {}
    with open("features_race_relative.csv", encoding=ENC, newline="") as f:
        r = csv.DictReader(f)
        cols = ["class_gap", "field_strength_std", "motor_top2_rate", "st_avg"]
        for row in r:
            rid = row["race_id"]; waku = row.get("枠番") or row.get("waku")
            try: w = int(waku)
            except (TypeError, ValueError): continue
            d = {}
            for c in cols:
                try: d[c] = float(row[c])
                except (ValueError, KeyError, TypeError): d[c] = None
            out[(rid, w)] = d
    return out


def build_records():
    """OOS各レースの (date, features, stake, ret, hit) を返す。"""
    model = A.load_predict()
    kd = A.load_all_ktxt()
    rel_rows = B.load("features_race_relative.csv")
    api_map = B.build_api_scores(rel_rows)
    feat = load_relfeat()
    dates = sorted({f"{rid[2:6]}-{rid[6:8]}-{rid[8:10]}" for rid in kd})
    payout = B.load_payouts(dates)

    recs = []
    for rid, rc in kd.items():
        if rid[2:10] <= OOS:                       # 学習期間内は除外（honest OOS）
            continue
        mp = model.get(rid)
        if not mp or len(mp) != 6: continue
        api = [api_map.get((rid, w)) for w in range(1, 7)]
        if any(a is None for a in api): continue
        if rid not in payout: continue
        po2f = payout[rid][2]
        if not po2f: continue
        sv = [mp[w] for w in range(1, 7)]
        tot = sum(sv)
        if tot <= 0: continue
        p = [x / tot for x in sv]
        order_p = sorted(range(6), key=lambda i: -p[i])
        hon = max(api)
        # --- 事前特徴 ---
        p1, p2 = p[order_p[0]], p[order_p[1]]
        gap = p1 - p2
        ent = -sum(x * math.log(x) for x in p if x > 0)
        pf_top = B._pf_prob(sv, B._pf_topk(sv, 1)[0])       # 最尤2連複ペアの確率
        m_top1 = order_p[0] + 1                              # モデル本命の枠
        a_top1 = max(range(1, 7), key=lambda w: api[w - 1])  # API本命の枠
        agree = 1.0 if m_top1 == a_top1 else 0.0
        ff = feat.get((rid, m_top1), {})
        cgap = ff.get("class_gap"); motor1 = ff.get("motor_top2_rate")
        fss = ff.get("field_strength_std")
        st_vals = [feat.get((rid, w), {}).get("st_avg") for w in range(1, 7)]
        st_std = stdev([v for v in st_vals if v is not None]) if any(st_vals) else None
        R = int(rid[10:12])
        # --- 決着 & アプリ2連複買い（新k_ex・フラット¥100/点・F返還） ---
        fins = rc["fin"]
        ordf = sorted([w for w in range(1, 7) if fins.get(w)], key=lambda w: fins[w])
        if len(ordf) < 2 or fins[ordf[0]] != 1: continue
        act = tuple(sorted(ordf[:2]))
        fly = {w for w in range(1, 7) if rc["status"].get(w) != "finish"}
        buy = B._pf_topk(sv, B.k_ex(hon))
        stake = ret = 0; hit = 0
        for c in buy:
            if any(w in fly for w in c): continue
            stake += 100
            if c == act: ret += po2f; hit = 1
        if stake == 0: continue
        recs.append({
            "date": rid[2:10], "stake": stake, "ret": ret, "hit": hit,
            "f": {"hon": hon, "p1": p1, "gap": gap, "ent": ent, "pf_top": pf_top,
                  "agree": agree, "class_gap": cgap, "motor1": motor1,
                  "fss": fss, "st_std": st_std, "R": float(R)},
        })
    return recs


def roi(rs):
    st = sum(r["stake"] for r in rs); rt = sum(r["ret"] for r in rs)
    return (rt / st * 100 if st else 0), st, rt, len(rs)


def method_a(train, test):
    print("\n" + "=" * 74)
    print("方式A: 単一特徴を四分位に割る → 学習で最良の分位が検証でも良いか")
    print("=" * 74)
    b_tr = roi(train)[0]; b_te = roi(test)[0]
    print(f"ベースライン（全買い）  学習 {b_tr:.1f}%  ・  検証 {b_te:.1f}%\n")
    feats = ["hon", "p1", "gap", "ent", "pf_top", "class_gap", "motor1",
             "fss", "st_std", "R", "agree"]
    print(f"{'特徴':<10}{'学習の分位別ROI(Q1→Q4)':<34}{'検証の同分位ROI(Q1→Q4)':<34}{'安定?':>6}")
    print("-" * 84)
    for key in feats:
        tr = [r for r in train if r["f"].get(key) is not None]
        te = [r for r in test if r["f"].get(key) is not None]
        if len(tr) < 100 or len(te) < 100: continue
        vals = sorted(r["f"][key] for r in tr)
        qs = [vals[len(vals) * k // 4] for k in range(1, 4)]  # 3 cut points
        def qof(v):
            return 1 if v <= qs[0] else 2 if v <= qs[1] else 3 if v <= qs[2] else 4
        tr_q = {q: [] for q in range(1, 5)}; te_q = {q: [] for q in range(1, 5)}
        for r in tr: tr_q[qof(r["f"][key])].append(r)
        for r in te: te_q[qof(r["f"][key])].append(r)
        tr_roi = {q: roi(tr_q[q])[0] for q in range(1, 5)}
        te_roi = {q: roi(te_q[q])[0] for q in range(1, 5)}
        best_tr = max(range(1, 5), key=lambda q: tr_roi[q])
        # 検証: 学習で最良だった分位が検証でもベースを上回るか
        stable = "○" if te_roi[best_tr] > b_te + 1 else "×"
        s_tr = " ".join(f"{tr_roi[q]:5.0f}" for q in range(1, 5))
        s_te = " ".join(f"{te_roi[q]:5.0f}" for q in range(1, 5))
        print(f"{key:<10}{s_tr:<34}{s_te:<34}  {stable}(bestQ{best_tr})")
    print("\n※学習で高ROIの分位(Q)が検証でもベース超えなら“信号あり”。×ばかりなら過学習/ノイズ。")


# ───────── 方式B: ロジスティック回帰ゲート（自前GD） ─────────
def method_b(train, test):
    print("\n" + "=" * 74)
    print("方式B: 複数特徴のロジスティック回帰で P(このレースは黒字) を学習 → 検証で上位だけ買う")
    print("=" * 74)
    keys = ["hon", "p1", "gap", "ent", "pf_top", "agree", "class_gap",
            "motor1", "fss", "st_std", "R"]
    # 欠損は学習平均で補完
    means = {}
    for k in keys:
        vs = [r["f"][k] for r in train if r["f"].get(k) is not None]
        means[k] = sum(vs) / len(vs) if vs else 0.0
    def vec(r):
        return [r["f"][k] if r["f"].get(k) is not None else means[k] for k in keys]
    Xtr = [vec(r) for r in train]; ytr = [1.0 if r["ret"] > r["stake"] else 0.0 for r in train]
    # 標準化
    mu = [sum(col) / len(col) for col in zip(*Xtr)]
    sd = [stdev(col) or 1.0 for col in zip(*Xtr)]
    def norm(x): return [(x[i] - mu[i]) / sd[i] for i in range(len(x))]
    Ztr = [norm(x) for x in Xtr]
    n, d = len(Ztr), len(keys)
    w = [0.0] * d; b = 0.0; lr = 0.3; lam = 1e-3
    for it in range(400):
        gw = [0.0] * d; gb = 0.0
        for z, y in zip(Ztr, ytr):
            pr = 1 / (1 + math.exp(-(sum(w[j] * z[j] for j in range(d)) + b)))
            e = pr - y
            for j in range(d): gw[j] += e * z[j]
            gb += e
        for j in range(d): w[j] -= lr * (gw[j] / n + lam * w[j])
        b -= lr * gb / n
    def score(r):
        z = norm(vec(r)); return sum(w[j] * z[j] for j in range(d)) + b
    # 学習で得た重みで検証レースをスコア→上位だけ買う
    te = sorted(test, key=score, reverse=True)
    b_te = roi(test)[0]
    print(f"検証ベースライン（全 {len(test):,}レース買い）: 回収率 {b_te:.1f}%\n")
    print("学習した重み（標準化後・絶対値大＝効く特徴）:")
    order = sorted(range(d), key=lambda j: -abs(w[j]))
    print("  " + " / ".join(f"{keys[j]}={w[j]:+.2f}" for j in order[:6]))
    print(f"\n  検証で『ゲートが買えと言った上位X%』だけ買った回収率:")
    print(f"  {'上位':>6}{'レース数':>9}{'回収率':>9}{'的中率':>8}{'収支':>12}")
    for frac in (0.1, 0.25, 0.5, 0.75, 1.0):
        k = max(1, int(len(te) * frac)); sub = te[:k]
        rr, st, rt, nn = roi(sub)
        hr = sum(x["hit"] for x in sub) / nn * 100
        print(f"  {frac*100:>5.0f}%{nn:>9,}{rr:>8.1f}%{hr:>7.1f}%{rt-st:>12,}")
    print("\n※上位を絞るほど回収率がベースを超えて上がれば“第6感あり”。横ばいなら市場に対し無力。")


def main():
    print("データ読込中…")
    recs = build_records()
    train = [r for r in recs if r["date"] <= GATE_TRAIN_MAX]
    test = [r for r in recs if r["date"] > GATE_TRAIN_MAX]
    print(f"OOS {len(recs):,}レース → ゲート学習(5月) {len(train):,} / 検証(6-7月) {len(test):,}")
    method_a(train, test)
    method_b(train, test)


if __name__ == "__main__":
    main()
