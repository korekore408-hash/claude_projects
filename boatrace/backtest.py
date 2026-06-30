# -*- coding: utf-8 -*-
"""バックテスト環境（点数ポリシー／標準帯の混合買い目の回収率検証）。

build_today.py の特徴量・予想ロジック（API簡易合成・PL確率・除外）を流用。
荒れ度（鉄板/標準/穴）は **API本命確率** でレース共通に判定する（2026-06-27 ユーザー決定:
割合・帯分けともAPIに統一）。そのうえで任意の「点数ポリシー」「標準帯の買い目構成」を
全履歴で検証する。

検証できるもの:
  - 点数ポリシー（確率連動の変動点数の絞り方）  … POLICIES
  - 標準帯の混合買い目（本命×標準×穴を意図的に混ぜる）… STD_BUILDERS
  系統は model=従来モデル / api=簡易合成（どちらも荒れ度・点数・除外はAPI共通）。

使い方:
  py -3 backtest.py                                  # 点数ポリシー比較（全系統）
  py -3 backtest.py --mode stdmix                    # 標準帯の混合買い目を比較
  py -3 backtest.py --mode stdmix --since 2026-05-01 # 学習期間を除く純検証
  py -3 backtest.py --system api --detail            # API系統だけ帯別詳細
"""
import argparse
import itertools

import build_today as B


def load_races(rel_path, pred_path, hist_path, since):
    """rel/pred/hist を読み、レース単位 {fin,cl,lw,model,api} と hon_canon(API本命確率)・payout。"""
    rel = B.load(rel_path)
    pred = {(r["race_id"], r["枠番"]): r for r in B.load(pred_path)}
    hist = {(r["race_id"], r["枠番"]): r for r in B.load(hist_path)}
    api_map = B.build_api_scores(rel)
    model_map = {}
    for (rid, w), pr in pred.items():
        v = B.to_float(pr.get("p_win"))
        if v is not None:
            try:
                model_map[(rid, int(w))] = v
            except (ValueError, TypeError):
                pass
    # 荒れ度（鉄板/標準/穴）の基準＝本命確率(max p_win)。api / model それぞれで作り
    #   --hon で選べるようにする（学習モデル主役化＝フル復帰の検証用）。
    def _hon(mp):
        h = {}
        for (rid, w), v in mp.items():
            if v is not None and v > h.get(rid, -1.0):
                h[rid] = v
        return h
    hon_api = _hon(api_map)
    hon_model = _hon(model_map)

    races = {}
    for r in rel:
        if r["日付"] < since:
            continue
        rid = r["race_id"]
        try:
            w = int(r["枠番"])
        except (ValueError, KeyError):
            continue
        h = hist.get((rid, r["枠番"]), {})
        pr = pred.get((rid, r["枠番"]), {})
        try:
            fin = int(pr.get("finish_rank"))
        except (TypeError, ValueError):
            fin = None
        rc = races.setdefault(rid, {"c": r["場コード"], "v": r["会場"], "d": r["日付"],
                                    "fin": {}, "cl": {}, "lw": {},
                                    "model": {}, "api": {}})
        rc["fin"][w] = fin
        rc["cl"][w] = B.to_float(r.get("class_ord"))
        rc["lw"][w] = B.to_float(h.get("lane_win_rate"))
        rc["model"][w] = model_map.get((rid, w))
        rc["api"][w] = api_map.get((rid, w))
    payout = B.load_payouts(sorted({r["日付"] for r in rel if r["日付"] >= since}))
    return races, hon_api, hon_model, payout


# ---- 点数ポリシー: hon(本命確率0-1) -> (2連単点数, 3連単点数)。0=その帯は買わない ----
def pol_current(hon):
    return B.k_ex(hon), B.k_tri(hon)


def pol_tight(hon):
    k2 = 1 if hon >= 0.65 else 2 if hon >= 0.45 else 3
    k3 = 2 if hon >= 0.65 else 4 if hon >= 0.50 else 6 if hon >= 0.40 else 10
    return k2, k3


def pol_no_ana(hon):
    if hon < 0.45:
        return 0, 0
    return B.k_ex(hon), B.k_tri(hon)


def pol_tetsu_only(hon):
    if hon < 0.65:
        return 0, 0
    return 2, 4


def pol_no_ana_tri(hon):
    """穴帯(本命<0.45)は3連単のみ停止し2連単は残す。
    穴帯3連単=72.9%(資金を溶かす主犯)を落とし、穴帯2連単=83.4%(下支え)は維持する案。"""
    k2, k3 = B.k_ex(hon), B.k_tri(hon)
    return (k2, 0) if hon < 0.45 else (k2, k3)


POLICIES = {"current": pol_current, "tight": pol_tight,
            "no_ana": pol_no_ana, "no_ana_tri": pol_no_ana_tri,
            "tetsu_only": pol_tetsu_only}


# ---- PL確率で全組合せを列挙して降順ソート（JS plTop の Python版）----
def pl_order(s, kind, excl):
    """s=スコア配列(len6, 0-1 or per-mille)。excl=除外枠set。combo(枠tuple)とPL確率の降順list。"""
    idx = [i for i in range(6) if s[i] and s[i] > 0 and (i + 1) not in excl]
    tot = sum(s)
    out = []
    for perm in itertools.permutations(idx, kind):
        p, rem = 1.0, tot
        ok = True
        for i in perm:
            if rem <= 0:
                ok = False
                break
            p *= s[i] / rem
            rem -= s[i]
        if ok:
            out.append((tuple(j + 1 for j in perm), p))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def lane_ranks(s):
    """枠 -> 予想順位（1=スコア最大）。穴=順位4以上の枠。"""
    order = sorted(range(6), key=lambda i: s[i], reverse=True)
    return {i + 1: r + 1 for r, i in enumerate(order)}


# ---- 標準帯の買い目ビルダー: 3連単の買い目list(combo,prob)を返す。s=スコア, k=点数 ----
def build_topk(order, k, s, rank):
    """通常: PL上位 k 点。"""
    return order[:k]


def _std_mix(order, k, s, rank, frac):
    """標準帯: 上位(1-frac)＋穴絡み(順位4-6を含む)上位 frac を混ぜて k 点。
    本命近傍に偏りがちな上位Kに、軽視艇絡みの目を意図的に差し込む。"""
    q = round(k * frac)
    ana = [c for c in order if any(rank[w] >= 4 for w in c[0])]
    chosen = order[:k - q] + ana[:q]
    seen, out = set(), []
    for c in chosen + order:          # 不足分は上位から補完
        key = c[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= k:
            break
    return out


def build_std20(order, k, s, rank):
    return _std_mix(order, k, s, rank, 0.20)


def build_std30(order, k, s, rank):
    return _std_mix(order, k, s, rank, 0.30)


def build_std40(order, k, s, rank):
    return _std_mix(order, k, s, rank, 0.40)


def build_axis(order, k, s, rank):
    """本命1着固定の総流し的: 1着=本命軸、2・3着は上位/穴を広く（点数 k に収まる範囲）。"""
    honlane = min(rank, key=rank.get)          # 順位1の枠
    axis = [c for c in order if c[0][0] == honlane]
    rest = [c for c in order if c[0][0] != honlane]
    seen, out = set(), []
    for c in axis + rest:
        if c[0] in seen:
            continue
        seen.add(c[0])
        out.append(c)
        if len(out) >= k:
            break
    return out


# STD_BUILDERS: 標準帯のみ差し替え（鉄板/穴は build_topk 固定）。
STD_BUILDERS = {"current": build_topk, "std20": build_std20, "std30": build_std30,
                "std40": build_std40, "axis": build_axis}


def backtest(races, system, hon_canon, payout, kpol=pol_current, std_builder=build_topk,
             min_lw1=None):
    """system='model'|'api'。荒れ度=API本命確率(hon_canon)共通。
    標準帯(gi==1)の3連単のみ std_builder で買い目を構成（他帯・2連単は PL上位K）。
    min_lw1 を指定すると 1号艇の lane_win_rate が閾値未満のレースを丸ごと除外（買わない）。
    返り値 agg[gi]=[n,win,n2,h2,pts2,pay2,n3,h3,pts3,pay3]。"""
    agg = [[0] * 10 for _ in range(3)]
    for rid, rc in races.items():
        if len(rc["fin"]) != 6:
            continue
        s = [rc[system].get(w) for w in range(1, 7)]
        if any(x is None for x in s):
            continue
        hon = hon_canon.get(rid)
        if hon is None:
            continue
        if min_lw1 is not None:
            lw1 = rc["lw"].get(1)
            if lw1 is None or lw1 < min_lw1:
                continue          # 弱い1号艇のレースは買わない（レースごと除外）
        fins = [rc["fin"][w] for w in range(1, 7)]
        order = sorted([w for w in range(1, 7)
                        if fins[w - 1] is not None and fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        if len(order) < 2 or fins[order[0] - 1] != 1:
            continue
        gi = 0 if hon >= 0.65 else (2 if hon < 0.45 else 1)
        k2, k3 = kpol(hon)
        excl = set()   # bet_exclude（不振1号艇除外）は撤去済（2026-06-30, 本番基準で逆効果）
        m = sum(1 for w in range(1, 7) if s[w - 1] and s[w - 1] > 0 and w not in excl)
        bet2 = min(k2, m * (m - 1)) if k2 else 0
        bet3 = min(k3, m * (m - 1) * (m - 2)) if k3 else 0
        po = payout.get(rid, (0, 0))
        hm = max(range(6), key=lambda i: s[i]) + 1
        a = agg[gi]
        a[0] += 1
        a[1] += (hm == order[0])
        if bet2 > 0:
            buy2 = pl_order(s, 2, excl)[:bet2]
            a[2] += 1
            a[4] += len(buy2)
            if tuple(order[:2]) in {c[0] for c in buy2}:
                a[3] += 1
                a[5] += po[0]
        if len(order) >= 3 and bet3 > 0:
            o3 = pl_order(s, 3, excl)
            rank = lane_ranks(s)
            builder = std_builder if gi == 1 else build_topk
            buy3 = builder(o3, bet3, s, rank)
            a[6] += 1
            a[8] += len(buy3)
            if tuple(order[:3]) in {c[0] for c in buy3}:
                a[7] += 1
                a[9] += po[1]
    return agg


def _pct(x, n):
    return round(x / n * 100, 1) if n else 0.0


def summarize(agg):
    tot = [sum(agg[g][i] for g in range(3)) for i in range(10)]
    n, win, n2, h2, pts2, pay2, n3, h3, pts3, pay3 = tot
    return {"n": n, "win": _pct(win, n), "h2": _pct(h2, n2), "r2": _pct(pay2, pts2 * 100),
            "h3": _pct(h3, n3), "r3": _pct(pay3, pts3 * 100),
            "stake": (pts2 + pts3) * 100, "payout": pay2 + pay3,
            "pts2": pts2, "pts3": pts3, "roi": _pct(pay2 + pay3, (pts2 + pts3) * 100)}


LABS = ["鉄板", "標準", "穴 "]


def print_detail(tag, agg):
    print(f"\n=== {tag} ===")
    print(" 帯   R数   本命1着  2連的中 2連回収 (点)   3連的中 3連回収 (点)")
    for g in range(3):
        n, win, n2, h2, pts2, pay2, n3, h3, pts3, pay3 = agg[g]
        print(f" {LABS[g]} {n:6d}  {_pct(win,n):5.1f}%   "
              f"{_pct(h2,n2):5.1f}% {_pct(pay2,pts2*100):6.1f}% {pts2:6d}  "
              f"{_pct(h3,n3):5.1f}% {_pct(pay3,pts3*100):6.1f}% {pts3:6d}")
    s = summarize(agg)
    print(f" 全体 {s['n']:6d}  {s['win']:5.1f}%   "
          f"{s['h2']:5.1f}% {s['r2']:6.1f}% {s['pts2']:6d}  "
          f"{s['h3']:5.1f}% {s['r3']:6.1f}% {s['pts3']:6d}")
    print(f"   賭け金 ¥{s['stake']:,} / 払戻 ¥{s['payout']:,} / 総回収率 {s['roi']:.1f}%")


def main():
    ap = argparse.ArgumentParser(description="点数ポリシー／標準帯混合買い目のバックテスト")
    ap.add_argument("--rel", default="features_race_relative.csv")
    ap.add_argument("--pred", default="predict_win.csv")
    ap.add_argument("--hist", default="features_player_history.csv")
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--mode", choices=["policy", "stdmix", "lane1"], default="policy",
                    help="policy=点数ポリシー比較 / stdmix=標準帯の混合買い目比較 / "
                         "lane1=弱い1号艇のレース除外（1号艇lane_win_rate下限を掃引）")
    ap.add_argument("--lw1-grid", default="0.0,0.40,0.50,0.55,0.60,0.65,0.70",
                    help="lane1モードの1号艇lane_win_rate下限グリッド（0.0=除外なし）")
    ap.add_argument("--system", nargs="*", default=["model", "api"],
                    choices=["model", "api"])
    ap.add_argument("--detail", action="store_true", help="帯別の詳細表も表示")
    ap.add_argument("--hon", choices=["api", "model"], default="api",
                    help="荒れ度(鉄板/標準/穴)・点数・除外の基準とする本命確率: "
                         "api=簡易合成(現状) / model=学習モデル(フル復帰の検証)")
    args = ap.parse_args()

    print(f"データ読込（since={args.since}・荒れ度基準={args.hon}本命確率で共通判定）…")
    races, hon_api, hon_model, payout = load_races(args.rel, args.pred, args.hist, args.since)
    hon_canon = hon_model if args.hon == "model" else hon_api
    nb = [0, 0, 0]
    for rid in races:
        h = hon_canon.get(rid)
        if h is not None:
            nb[0 if h >= 0.65 else (2 if h < 0.45 else 1)] += 1
    print(f"対象レース {len(races)}  / API荒れ度: 鉄板{nb[0]} 標準{nb[1]} 穴{nb[2]}")

    if args.mode == "lane1":
        grid = [float(x) for x in args.lw1_grid.split(",")]
        print("\n==== 弱い1号艇のレース除外（点数ポリシー=current・買い目据置）====")
        print(" 系統 1号艇下限   残R数  残率   本命1着  2連回収 3連回収 総回収  賭け金")
        for system in args.system:
            base_roi = None
            for thr in grid:
                ml = None if thr <= 0 else thr
                agg = backtest(races, system, hon_canon, payout,
                               pol_current, build_topk, min_lw1=ml)
                s = summarize(agg)
                if thr <= 0:
                    base_roi = s["roi"]
                sname = "従来" if system == "model" else "API "
                d = f"{s['roi']-base_roi:+.1f}" if base_roi is not None else "  – "
                lab = "除外なし" if thr <= 0 else f">={thr:.2f} "
                rr = _pct(s["n"], nb[0] + nb[1] + nb[2])
                print(f" {sname} {lab:<8s} {s['n']:6d} {rr:5.1f}% {s['win']:5.1f}%   "
                      f"{s['r2']:6.1f}% {s['r3']:6.1f}% {s['roi']:6.1f}%({d}) ￥{s['stake']:>9,}")
        print("\n※残R数=1号艇が閾値以上で実際に賭けたレース数。総回収(差)=除外なし比。")
        print("  回収率が上がっても賭け金(母数)が減るので、利益額は『総回収×賭け金』で見ること。")
        return

    rows = []
    if args.mode == "policy":
        variants = [(p, POLICIES[p], build_topk) for p in POLICIES]
        title = "点数ポリシー"
    else:
        variants = [(b, pol_current, STD_BUILDERS[b]) for b in STD_BUILDERS]
        title = "標準帯ビルダー（current=PL上位/他は本命×標準×穴を混合）"

    base = {}
    for system in args.system:
        for name, kpol, builder in variants:
            agg = backtest(races, system, hon_canon, payout, kpol, builder)
            if args.detail:
                sname = "従来" if system == "model" else "API"
                print_detail(f"[{sname}] {name}", agg)
            s = summarize(agg)
            if name == "current":
                base[system] = s["stake"]
            rows.append((system, name, s))

    print(f"\n==== サマリ：{title} ====")
    print(" 系統 名称           賭け金       2連回収 3連回収 総回収  賭け金比")
    for system, name, s in rows:
        b = base.get(system)
        ratio = f"{s['stake']/b*100:5.0f}%" if b else "  –  "
        sname = "従来" if system == "model" else "API "
        print(f" {sname} {name:<13s} ¥{s['stake']:>10,}  "
              f"{s['r2']:6.1f}% {s['r3']:6.1f}% {s['roi']:6.1f}%  {ratio}")
    print("\n※回収率100%超で利益（控除率約25%で全買い基準線≒75%）。標準帯の混合買い目は")
    print("  3連単のみ差し替え（鉄板/穴・2連単は不変）。採用案は build_today に折り込む。")


if __name__ == "__main__":
    main()
