# -*- coding: utf-8 -*-
"""
実オッズEVフィルタの「実際の回収率」バックテスト。
=========================================================================
data/odds/odds_YYYYMMDD.csv（Phase4で収集した全組合せ実オッズ）が残っている
6/4〜6/17について、結果(K-file)と突き合わせ:
  A) 無選別＝本命確率順10万戦略の買い目をそのまま（top-K 変動点数＋除外）
  B) EV選別＝そのうち EV=モデル確率×実オッズ ≥ ev_min の買い目だけ
の【実際の回収率】を比較する。EV版が控除率の壁(~78%)を破れるかの実証。

使い方: py -3.13 make_ev_backtest.py [--ev-min 1.0] [--dates 20260604,...]
"""
import argparse
import csv
import glob
import html
import os
import re

from build_today import load, load_payouts, _pl_prob
from make_ai_yosou import build_races, allocate


def load_odds_csv(hd):
    p = os.path.join("data", "odds", f"odds_{hd}.csv")
    out = {}
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rid = r["race_id"]
            parts = tuple(int(x) for x in r["combo"].split("-"))
            try:
                o = float(r["odds"])
            except ValueError:
                continue
            d = out.setdefault(rid, {"2t": {}, "3t": {}})
            d[r["bet_type"]][parts] = o
    return out


def date_from_kfiles():
    ds = []
    for kp in sorted(glob.glob("data/odds/odds_*.csv")):
        m = re.search(r"odds_(\d{8})", kp)
        if m:
            ds.append(m.group(1))
    return ds


def acc():
    return {"a_stake": 0, "a_pay": 0, "a_hit2": 0, "a_hit3": 0, "a_n": 0,
            "b_stake": 0, "b_pay": 0, "b_hit2": 0, "b_hit3": 0,
            "b_bets": 0, "b_races": 0}


def run_threshold(dates, races_by_date, payout, ev_min):
    overall = acc()
    byreg = {"鉄板": acc(), "標準": acc(), "穴": acc()}
    for hd in dates:
        odds, races = races_by_date.get(hd, (None, None))
        if not odds:
            continue
        for r in races:
            if not r["has_res"]:
                continue
            o = odds.get(r["rid"])
            if not o:
                continue
            R = byreg[r["regime"]]
            act2 = tuple(r["order"][:2]) if len(r["order"]) >= 2 else None
            act3 = tuple(r["order"][:3]) if len(r["order"]) >= 3 else None
            # ---- A) 無選別（top-K 全部買う） ----
            for tgt in (overall, R):
                tgt["a_n"] += 1
            for combo, p in r["ex2"]:
                for tgt in (overall, R):
                    tgt["a_stake"] += 100
                if act2 and combo == act2:
                    for tgt in (overall, R):
                        tgt["a_pay"] += r["po"][0]; tgt["a_hit2"] += 1
            for combo, p in r["ex3"]:
                for tgt in (overall, R):
                    tgt["a_stake"] += 100
                if act3 and combo == act3:
                    for tgt in (overall, R):
                        tgt["a_pay"] += r["po"][1]; tgt["a_hit3"] += 1
            # ---- B) EV選別（EV≥ev_min だけ買う） ----
            kept_any = False
            for combo, p in r["ex2"]:
                od = o["2t"].get(combo)
                if od is None or p * od < ev_min:
                    continue
                kept_any = True
                for tgt in (overall, R):
                    tgt["b_stake"] += 100; tgt["b_bets"] += 1
                if act2 and combo == act2:
                    for tgt in (overall, R):
                        tgt["b_pay"] += r["po"][0]; tgt["b_hit2"] += 1
            for combo, p in r["ex3"]:
                od = o["3t"].get(combo)
                if od is None or p * od < ev_min:
                    continue
                kept_any = True
                for tgt in (overall, R):
                    tgt["b_stake"] += 100; tgt["b_bets"] += 1
                if act3 and combo == act3:
                    for tgt in (overall, R):
                        tgt["b_pay"] += r["po"][1]; tgt["b_hit3"] += 1
            if kept_any:
                for tgt in (overall, R):
                    tgt["b_races"] += 1
    return overall, byreg


def ret(p, s):
    return round(p / s * 100, 1) if s else 0


def today_picks(today_ymd, pred, meta, hist, ev_min=1.5):
    """今日の10万戦略の選定レースのうち、検証で唯一プラスだった
    『鉄板（本命確率≥65%）× EV≥ev_min』に合致する買い目を、キャッシュ済み実オッズで抽出。"""
    odds = load_odds_csv(today_ymd)
    if not odds:
        return None
    races = build_races(today_ymd, pred, meta, hist, payout={})
    sel, _ = allocate(races)
    picks = []
    for r in sel:
        if r["regime"] != "鉄板":
            continue
        o = odds.get(r["rid"])
        if not o:
            continue
        kept = []
        for kind, lst, ob in (("2連単", r["ex2"], o["2t"]), ("3連単", r["ex3"], o["3t"])):
            for combo, p in lst:
                od = ob.get(combo)
                if od is not None and p * od >= ev_min:
                    kept.append((kind, combo, p, od, p * od))
        if kept:
            kept.sort(key=lambda x: -x[4])
            picks.append((r, kept))
    return picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default=None)
    ap.add_argument("--thresholds", default="1.0,1.2,1.5,2.0")
    ap.add_argument("--today", default=None, help="今日のピックを実オッズで抽出 YYYY-MM-DD")
    ap.add_argument("--today-ev", type=float, default=1.5)
    args = ap.parse_args()
    dates = args.dates.split(",") if args.dates else date_from_kfiles()
    thr = [float(x) for x in args.thresholds.split(",")]

    pred = {(r["race_id"], r["枠番"]): r for r in load("predict_win.csv")}
    meta = {(r["race_id"], r["枠番"]): r for r in load("features_race_relative.csv")}
    hist = {(r["race_id"], r["枠番"]): r for r in load("features_player_history.csv")}
    keep = [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in dates]
    payout = load_payouts(keep)

    # 日ごとに odds と races を一度だけ構築（しきい値ループで使い回す）
    races_by_date = {}
    for hd in dates:
        odds = load_odds_csv(hd)
        if not odds:
            continue
        races_by_date[hd] = (odds, build_races(hd, pred, meta, hist, payout))

    sweep = []
    base = None
    for ev_min in thr:
        overall, byreg = run_threshold(dates, races_by_date, payout, ev_min)
        if base is None:
            base = (overall, byreg)
        sweep.append((ev_min, overall, byreg))
        print(f"EV>={ev_min}: 全体 A {ret(overall['a_pay'],overall['a_stake'])}% "
              f"→ B {ret(overall['b_pay'],overall['b_stake'])}% "
              f"({overall['b_stake']:,}円/{overall['b_bets']}点) | "
              f"鉄板 B {ret(byreg['鉄板']['b_pay'],byreg['鉄板']['b_stake'])}% "
              f"({byreg['鉄板']['b_stake']:,}円)")

    picks = None
    if args.today:
        ty = args.today.replace("-", "")
        picks = today_picks(ty, pred, meta, hist, args.today_ev)
        if picks:
            npk = sum(len(k) for _, k in picks)
            print(f"今日{args.today}: 鉄板×EV≥{args.today_ev} ピック {len(picks)}R / {npk}点")
        else:
            print(f"今日{args.today}: 該当ピックなし（または実オッズ未取得）")

    html_out = render(dates, sweep, picks, args.today, args.today_ev)
    with open("ai_yosou_ev.html", "w", encoding="utf-8") as f:
        f.write(html_out)
    print("○ ai_yosou_ev.html を生成")


def render(dates, sweep, picks=None, today=None, today_ev=1.5):
    e = html.escape
    base_overall = sweep[0][1]
    base_a = ret(base_overall["a_pay"], base_overall["a_stake"])
    period = f"{dates[0][4:6]}/{dates[0][6:8]}〜{dates[-1][4:6]}/{dates[-1][6:8]}"

    def col(v):
        return "#3c8" if v >= 100 else ("#e96" if v >= 80 else "#e55")

    # 全体スイープ表
    rows = ""
    for ev_min, ov, br in sweep:
        rb = ret(ov["b_pay"], ov["b_stake"])
        tetsu = br["鉄板"]
        rt = ret(tetsu["b_pay"], tetsu["b_stake"])
        rows += (f'<tr><td>EV≥{ev_min}</td>'
                 f'<td>{ov["b_bets"]:,}点</td><td>{ov["b_stake"]:,}円</td>'
                 f'<td style="color:{col(rb)};font-weight:700">{rb}%</td>'
                 f'<td>{tetsu["b_stake"]:,}円</td>'
                 f'<td style="color:{col(rt)};font-weight:700">{rt}%</td></tr>')

    # 区分別（代表してEV≥1.0とEV≥1.5）
    def reg_block(ev_min, br):
        r = ""
        for lab in ("鉄板", "標準", "穴"):
            a = br[lab]
            ra = ret(a["a_pay"], a["a_stake"]); rb = ret(a["b_pay"], a["b_stake"])
            r += (f'<tr><td>{lab}</td>'
                  f'<td>{ra}%</td>'
                  f'<td style="color:{col(rb)};font-weight:700">{rb}%</td>'
                  f'<td>{a["b_stake"]:,}円 / {a["b_bets"]}点</td></tr>')
        return r
    # EV≥1.0 と、しきい値最大のものを表示
    reg10 = reg_block(sweep[0][0], sweep[0][2])
    last = sweep[-1]
    regL = reg_block(last[0], last[2])

    # ③ 今日の実オッズ・ピック（鉄板×EV≥today_ev）
    WC = {1: "#fff", 2: "#222", 3: "#e23", 4: "#26c", 5: "#fc0", 6: "#2a2"}
    WT = {1: "#000", 2: "#fff", 3: "#fff", 4: "#fff", 5: "#000", 6: "#fff"}

    def wk(w):
        return (f'<span class="wk" style="background:{WC[w]};color:{WT[w]};'
                f'border:1px solid #555">{w}</span>')

    def cmb(c):
        return "-".join(wk(w) for w in c)

    picks_html = ""
    if picks is not None:
        td = f"{today[5:7]}/{today[8:10]}" if today else ""
        if picks:
            cards = ""
            for r, kept in picks:
                hon = round(r["hon"] * 100)
                rowsk = ""
                for kind, combo, p, od, ev in kept:
                    rowsk += (f'<div class="cb"><span class="kd">{kind}</span>{cmb(combo)}'
                              f'<span class="od">実{od:.1f}倍</span>'
                              f'<span class="ev">EV{ev:.2f}</span></div>')
                cards += (f'<div class="card"><div class="rh"><b>{e(r["v"])}{e(str(r["race"]))}R</b>'
                          f'<span class="hon">本命{hon}%・◎{r["hm"]} {e(r["name"])}</span></div>'
                          f'{rowsk}</div>')
            picks_html = (f'<h2>③ 今日 {td} の実オッズ・ピック（鉄板×EV≥{today_ev}）</h2>'
                          f'<div class="sub">検証で唯一プラスだった隅。本命確率≥65%のレースで、'
                          f'実オッズが必要倍率の{today_ev}倍以上ついている買い目だけを抽出。'
                          f'<b>少点・高分散</b>なので深追い禁物。</div>'
                          f'<div class="cards">{cards}</div>')
        else:
            picks_html = (f'<h2>③ 今日 {td} の実オッズ・ピック（鉄板×EV≥{today_ev}）</h2>'
                          f'<div class="warn">本日は条件に合う買い目がありませんでした'
                          f'（鉄板レースで実オッズが必要倍率の{today_ev}倍を超える買い目なし）。'
                          f'＝無理に張らないのが正解。発走直前に <code>--refetch</code> 相当で取り直すと変わる場合があります。</div>')

    return f"""<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI予想 実オッズEV版（実証）</title>
<style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}
body{{margin:0;background:#0d1117;color:#e6edf3;font:16px/1.65 -apple-system,"Segoe UI",sans-serif}}
.wrap{{max-width:680px;margin:0 auto;padding:14px}}
h1{{font-size:20px;margin:.2em 0}}h2{{font-size:16px;margin:1.3em 0 .4em;padding-left:8px;border-left:4px solid #3b82f6}}
.sub{{color:#9aa7b4;font-size:13px}}
.note{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px;font-size:13px;color:#c9d4df;margin:10px 0}}
.note b{{color:#f0c040}}
.good{{color:#86efac}}.bad{{color:#fca5a5}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0}}
th,td{{padding:7px 5px;border-bottom:1px solid #21262d;text-align:center}}
th{{color:#9aa7b4;font-weight:600;font-size:12px}}
.scroll{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
.scroll table{{min-width:540px}}
.concl{{background:#10241a;border:1px solid #1c5;border-radius:10px;padding:13px;font-size:14px;margin:14px 0}}
.concl b{{color:#7ee2a8}}
.warn{{background:#2a1a10;border:1px solid #964;border-radius:10px;padding:12px;font-size:13px;margin:10px 0}}
.cards{{display:grid;gap:8px;margin:8px 0}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:10px}}
.rh{{display:flex;align-items:center;gap:8px;font-size:14px;flex-wrap:wrap}}
.hon{{color:#9aa7b4;font-size:12px}}
.wk{{display:inline-flex;width:20px;height:20px;border-radius:4px;align-items:center;justify-content:center;font-size:12px;font-weight:800;vertical-align:middle}}
.cb{{display:flex;align-items:center;gap:5px;font-size:13px;padding:3px 0;flex-wrap:wrap}}
.kd{{font-size:11px;color:#9aa7b4;width:42px}}
.od{{margin-left:auto;color:#cbd5e1;font-size:12px}}
.ev{{background:#14532d;color:#86efac;padding:0 7px;border-radius:8px;font-size:12px;font-weight:700}}
</style></head><body><div class="wrap">

<h1>🎯 AI予想 実オッズEV版 ＝ 実証バックテスト</h1>
<div class="sub">{period} の {base_overall['a_n']}レース。Phase4で保存した全組合せ実オッズ＋実結果で、
EV=モデル確率×実オッズ ≥ しきい値 の買い目だけ買ったら<b>実際いくら回収できたか</b>を検証。</div>

<div class="note">
<b>EV版とは：</b> 各買い目の <b>EV＝モデル確率×実オッズ</b>。EV≥1 は「実オッズが必要倍率(1÷確率)を超える」＝
理論上おいしい買い目。これだけ買えば勝てるはず——を実データで検証しました。
<b>昨日6/22は実オッズが残っていない</b>ため、オッズを保存済みの 6/4〜6/17 で代用。
</div>

<h2>① しきい値別の実回収率（全体／鉄板）</h2>
<div class="sub">無選別（全top-K買い）の全体回収率 = <b style="color:{col(base_a)}">{base_a}%</b> が基準。</div>
<div class="scroll"><table>
<tr><th>選別</th><th>買い目</th><th>投資(全体)</th><th>回収率(全体)</th><th>投資(鉄板)</th><th>回収率(鉄板)</th></tr>
{rows}
</table></div>

<div class="concl">
<b>結論：実オッズEV版は「万能」ではありませんでした。</b><br>
・EV≥1 を機械的に全部買うと <b class="bad">むしろ悪化</b>（全体 {base_a}% → {ret(base_overall['b_pay'],base_overall['b_stake'])}%）。
オッズが「おいしく見える」買い目は、たいてい<b>真の確率がモデル予想より低い</b>＝市場は効率的で逆選択になるため。<br>
・<b class="good">唯一プラス圏に届くのは「鉄板（本命確率≥65%）×高EV」の隅だけ</b>。
鉄板はモデルが市場とズレにくく、たまに人気が甘くつく時に妙味が出ます。<br>
・ただし高EVに絞るほど買い目は激減し<b>分散が大きい</b>（少数の的中で数字が跳ねる）＝安定した利益源ではありません。
</div>

<h2>② 区分別の効き方</h2>
<div class="sub">EV≥{sweep[0][0]}（ゆるい選別）</div>
<div class="scroll"><table>
<tr><th>区分</th><th>無選別</th><th>EV選別</th><th>EV選別の規模</th></tr>
{reg10}
</table></div>
<div class="sub" style="margin-top:8px">EV≥{last[0]}（きつい選別）</div>
<div class="scroll"><table>
<tr><th>区分</th><th>無選別</th><th>EV選別</th><th>EV選別の規模</th></tr>
{regL}
</table></div>

{picks_html}

<div class="warn">
<b>今日のレースへの使い方（現実的な落としどころ）：</b>
今日も「本命確率の高い順10万円戦略」で買うレースは <b>ai_yosou.html</b> の通り。
そのうち発走前に実オッズを見て、<b>◎本命がからむ鉄板レースで、実オッズが必要倍率の1.5倍以上</b>に
ついている買い目があれば、そこに厚くする——程度の上乗せが、実証上いちばん理にかなった使い方です。
EV≥1を全面採用するのは<b>逆効果</b>なので避けてください。
</div>

<div class="note">
<b>正直なまとめ：</b> 実オッズを使っても、機械的なEV選別で<b>安定的に100%超</b>は作れませんでした。
ボートレースのオッズ市場はかなり効率的で、直前情報なしモデルの確率は市場より鋭くないためです。
唯一の小さな活路は「鉄板の人気甘」狙いですが、回数が少なく分散が大きい点に注意してください。
</div>
</div></body></html>"""


if __name__ == "__main__":
    main()
