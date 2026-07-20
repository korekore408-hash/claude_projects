# -*- coding: utf-8 -*-
"""
直前情報あり予想の検証：朝 p_win に「展示タイム/展示ST/進入変化/風・波」を
どう混ぜると本命1着的中が上がるかを、直前情報履歴(data/before/*.json)で実測する。

朝モデル p_win（predict_win.csv）を基準に、各シグナルを log スコアへ加減して
レース内 softmax → 本命（最尤枠）が実際に1着だったか（result.fin==1 の枠）で採点。
少数日(6/21〜)なので過信は禁物。方向と大きさの当たりを付けるための道具。

使い方: py -3 backtest_tenji_weather.py
"""
import csv
import glob
import json
import math
from collections import defaultdict


def load_pwin(path="predict_win.csv"):
    """race_id -> [p_win(枠1..6)]。predict_win.csv は cp932・列名も cp932。"""
    out = defaultdict(lambda: [None] * 6)
    with open(path, encoding="cp932") as f:
        r = csv.reader(f)
        head = next(r)
        # 位置で取る（ヘッダ文字化け対策）: 0=race_id,1=枠番,3=p_win
        for row in r:
            if len(row) < 4:
                continue
            rid, waku, p = row[0], row[1], row[3]
            try:
                out[rid][int(waku) - 1] = float(p)
            except (ValueError, IndexError):
                pass
    return out


def zscore(arr):
    xs = [x for x in arr if x is not None and math.isfinite(x)]
    if len(xs) < 2:
        return [0.0] * len(arr)
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5 or 1.0
    return [((x - m) / sd) if (x is not None and math.isfinite(x)) else 0.0 for x in arr]


def softmax_top(logscore):
    mx = max(logscore)
    ex = [math.exp(s - mx) for s in logscore]
    sm = sum(ex) or 1.0
    prob = [e / sm for e in ex]
    return prob.index(max(prob))  # 0-based 最尤枠


def winner_lane(fin):
    """result.fin は枠1..6の着順。1着の枠index(0-based)。無ければ None。"""
    for i, v in enumerate(fin):
        if v == 1:
            return i
    return None


def eval_model(records, adjust):
    """adjust(pw, ex) -> logscore[6] を受けて本命的中率を返す。"""
    hit = tot = 0
    for pw, ex, win in records:
        ls = adjust(pw, ex)
        if ls is None:
            # 直前情報が使えない→朝モデルにフォールバック
            ls = [math.log(max(p or 1e-9, 1e-9)) for p in pw]
        if softmax_top(ls) == win:
            hit += 1
        tot += 1
    return hit, tot


def main():
    pwin = load_pwin()
    records = []
    for f in sorted(glob.glob("data/before/*.json")):
        d = json.load(open(f, encoding="utf-8"))
        for rid, v in d.items():
            fin = (v.get("result") or {}).get("fin")
            if not fin or len(fin) < 6:
                continue
            win = winner_lane(fin)
            if win is None:
                continue
            pw = pwin.get(rid)
            if not pw or any(p is None for p in pw):
                continue
            ex = v.get("ex") or {}
            records.append((pw, ex, win))
    print(f"対象レース: {len(records)}（朝p_win と 直前情報結果 が揃うもの）\n")

    def base(pw, ex):
        return [math.log(max(p, 1e-9)) for p in pw]

    def tenji(pw, ex, BT=0.2, BS=0.1):
        ts = ex.get("time") or []
        st = ex.get("st") or []
        if not any(x is not None for x in ts):
            return None
        zt = zscore(ts)
        stEff = [(0.30 if (x is not None and x < 0) else x) for x in st] + [None] * (6 - len(st))
        zst = zscore(stEff[:6])
        return [math.log(max(pw[i], 1e-9)) - BT * zt[i] - BS * zst[i] for i in range(6)]

    def tenji_course(pw, ex, BT=0.2, BS=0.1, BC=0.0):
        base_ls = tenji(pw, ex, BT, BS)
        if base_ls is None:
            return None
        co = ex.get("course") or []
        for i in range(6):
            c = co[i] if i < len(co) else None
            if c is not None:
                base_ls[i] += BC * ((i + 1) - c)   # 枠より内に入った艇を加点
        return base_ls

    def tenji_full(pw, ex, BT=0.2, BS=0.1, BC=0.0, BW=0.0, BV=0.0):
        base_ls = tenji_course(pw, ex, BT, BS, BC)
        if base_ls is None:
            return None
        w = ex.get("weather") or {}
        wind = w.get("wind")
        wave = w.get("wave")
        for i in range(6):
            lane_c = (i + 1) - 3.5     # アウトほど正
            if wind is not None:
                base_ls[i] += BW * wind * lane_c
            if wave is not None:
                base_ls[i] += BV * wave * lane_c
        return base_ls

    trials = [
        ("朝モデルのみ(baseline)", base),
        ("展示T+ST (現行tenjiPred)", lambda p, e: tenji(p, e)),
        ("+進入変化 BC=0.15", lambda p, e: tenji_course(p, e, BC=0.15)),
        ("+進入変化 BC=0.30", lambda p, e: tenji_course(p, e, BC=0.30)),
        ("+進入変化 BC=0.50", lambda p, e: tenji_course(p, e, BC=0.50)),
        ("+風 BW=+0.02(外有利)", lambda p, e: tenji_full(p, e, BC=0.30, BW=0.02)),
        ("+風 BW=-0.02(内有利)", lambda p, e: tenji_full(p, e, BC=0.30, BW=-0.02)),
        ("+波 BV=+0.03", lambda p, e: tenji_full(p, e, BC=0.30, BV=0.03)),
        ("+波 BV=-0.03", lambda p, e: tenji_full(p, e, BC=0.30, BV=-0.03)),
        ("全部 BC=.30 BW=-.02 BV=-.03", lambda p, e: tenji_full(p, e, BC=0.30, BW=-0.02, BV=-0.03)),
    ]
    print(f"{'モデル':<30}{'本命的中':>10}{'率':>9}")
    for name, fn in trials:
        hit, tot = eval_model(records, fn)
        print(f"{name:<30}{hit:>6}/{tot:<4}{hit/tot*100:>7.2f}%")


if __name__ == "__main__":
    main()
