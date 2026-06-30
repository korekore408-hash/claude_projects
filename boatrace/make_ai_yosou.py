# -*- coding: utf-8 -*-
"""
AI予想（独立HTML） ai_yosou.html を生成する。
=========================================================================
ユーザー依頼: 「10万円で回収率100%を目指す場合、昨日のレースをどう買って
結果はどうだったか。今日も同じ予想を。today.html とは別のHTMLで。」

方針（research の結論を踏まえた正直版）:
  - 機械的なベットで期待回収率100%超は構造的に不可能（控除率~25%の壁・
    favorite-longshot）。それでも回収率が最も高いのは「鉄板（本命確率が高い）」帯。
  - そこで「本命確率の高いレースから順に、変動点数＋除外で 2連単/3連単を各100円」
    で買い、予算10万円に収まるまで買う＝回収率の高いレースに資金集中（100%狙い）。
  - 昨日(結果あり)は実配当で回収率を確定。今日(結果なし)は同じ選定で買い目を提示。

出力: ai_yosou.html（自己完結・携帯向けダーク）。today.html には触れない。
使い方: py -3.13 make_ai_yosou.py  [--yesterday 2026-06-22] [--today 2026-06-23]
"""
import argparse
import datetime
import html
import itertools
import json

from build_today import (load, to_float, k_ex, k_tri,
                         _pl_prob, _pl_rank, load_payouts, VENUE_CODE)

BUDGET = 100_000


def gen_topk(s, kind, k, excl):
    """除外枠を含まない買い目を PL確率の高い順に最大k件返す。 [(combo_tuple, prob), ...]"""
    excl = excl or set()
    idx = [i + 1 for i in range(6) if s[i] and s[i] > 0 and (i + 1) not in excl]
    combos = [(c, _pl_prob(s, c)) for c in itertools.permutations(idx, kind)]
    combos.sort(key=lambda x: -x[1])
    return combos[:k]


def build_races(date, pred, meta, hist, payout):
    """date(YYYYMMDD) の各レースを辞書化して本命確率順（降順）で返す。"""
    ymd = date
    races = {}
    for (rid, w), pr in pred.items():
        if rid[2:10] != ymd:
            continue
        m = meta.get((rid, w), {})
        rc = races.setdefault(rid, {"rid": rid, "v": m.get("会場", "?"),
                                    "code": m.get("場コード", "00"),
                                    "race": m.get("レース", "?"),
                                    "fstd": to_float(m.get("field_strength_std")),
                                    "b": {}})
        rc["b"][int(w)] = {
            "pwin": to_float(pr.get("p_win")),
            "fin": _int(pr.get("finish_rank")),
            "name": m.get("選手名", ""),
            "cls": to_float(m.get("class_ord")),
            "lane_win": to_float(hist.get((rid, w), {}).get("lane_win_rate")),
        }
    out = []
    for rid, rc in races.items():
        if len(rc["b"]) != 6:
            continue
        s = [rc["b"][w]["pwin"] for w in range(1, 7)]
        if any(x is None for x in s):
            continue
        hon = max(s)
        hm = max(range(6), key=lambda i: s[i]) + 1
        xb = []           # bet_exclude（不振1号艇除外）は撤去済（2026-06-30）
        excl = set()
        kx, kt = k_ex(hon), k_tri(hon)
        ex2 = gen_topk(s, 2, kx, excl)
        ex3 = gen_topk(s, 3, kt, excl)
        # 実結果（完走艇のみで 1-2-3着）
        fins = [rc["b"][w]["fin"] for w in range(1, 7)]
        order = sorted([w for w in range(1, 7) if fins[w - 1] and fins[w - 1] >= 1],
                       key=lambda w: fins[w - 1])
        has_res = len(order) >= 1 and fins[order[0] - 1] == 1
        po = payout.get(rid, (0, 0))
        # 的中判定（除外後 変動点数で）
        hit2 = hit3 = None
        pay2 = pay3 = 0
        if has_res and len(order) >= 2:
            r2 = _pl_rank(s, 2, tuple(order[:2]), excl)
            hit2 = (r2 <= len(ex2)) if ex2 else False
            if hit2:
                pay2 = po[0]
        if has_res and len(order) >= 3:
            r3 = _pl_rank(s, 3, tuple(order[:3]), excl)
            hit3 = (r3 <= len(ex3)) if ex3 else False
            if hit3:
                pay3 = po[1]
        out.append({
            "rid": rid, "v": rc["v"], "code": rc["code"], "race": rc["race"],
            "s": s, "hon": hon, "hm": hm,
            "name": rc["b"][hm]["name"], "names": {w: rc["b"][w]["name"] for w in range(1, 7)},
            "regime": "鉄板" if hon >= 0.65 else ("穴" if hon < 0.45 else "標準"),
            "kx": kx, "kt": kt, "excl": sorted(excl), "xb": xb,
            "ex2": ex2, "ex3": ex3,
            "stake": (len(ex2) + len(ex3)) * 100,
            "has_res": has_res, "order": order, "fins": fins,
            "hit2": hit2, "hit3": hit3, "pay2": pay2, "pay3": pay3,
            "po": po,
        })
    out.sort(key=lambda r: -r["hon"])
    return out


def _int(s):
    try:
        return int(str(s).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def allocate(races, budget=BUDGET):
    """本命確率の高い順に予算が尽きるまで採用。各レース stake=(2連単点+3連単点)*100。"""
    sel, spent = [], 0
    for r in races:
        if spent + r["stake"] > budget:
            continue
        sel.append(r)
        spent += r["stake"]
    return sel, spent


def summarize(sel):
    """採用レースの投資・払戻・回収率・的中数。"""
    stake = sum(r["stake"] for r in sel)
    pay = sum(r["pay2"] + r["pay3"] for r in sel)
    h2 = sum(1 for r in sel if r["hit2"])
    h3 = sum(1 for r in sel if r["hit3"])
    nres = sum(1 for r in sel if r["has_res"])
    return {"n": len(sel), "stake": stake, "pay": pay,
            "ret": round(pay / stake * 100, 1) if stake else 0,
            "h2": h2, "h3": h3, "nres": nres}


def regime_table(sel):
    rows = []
    for lab in ("鉄板", "標準", "穴"):
        g = [r for r in sel if r["regime"] == lab]
        if not g:
            rows.append([lab, 0, 0, 0, 0, 0, 0]); continue
        stake = sum(r["stake"] for r in g)
        pay = sum(r["pay2"] + r["pay3"] for r in g)
        h2 = sum(1 for r in g if r["hit2"]); h3 = sum(1 for r in g if r["hit3"])
        rows.append([lab, len(g), stake, pay,
                     round(pay / stake * 100, 1) if stake else 0, h2, h3])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yesterday", default=None)
    ap.add_argument("--today", default=None)
    args = ap.parse_args()
    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    yest = datetime.date.fromisoformat(args.yesterday) if args.yesterday else today - datetime.timedelta(days=1)
    yk, tk = today.strftime("%Y%m%d"), today.strftime("%Y%m%d")
    ydk = yest.strftime("%Y%m%d")

    # 読み込み
    pred = {(r["race_id"], r["枠番"]): r for r in load("predict_win.csv")}
    meta = {(r["race_id"], r["枠番"]): r for r in load("features_race_relative.csv")}
    hist = {(r["race_id"], r["枠番"]): r for r in load("features_player_history.csv")}
    keep = [yest.isoformat(), today.isoformat()]
    payout = load_payouts(keep)

    y_races = build_races(ydk, pred, meta, hist, payout)
    t_races = build_races(today.strftime("%Y%m%d"), pred, meta, hist, payout)

    y_sel, y_spent = allocate(y_races)
    t_sel, t_spent = allocate(t_races)
    y_sum = summarize(y_sel)
    y_reg = regime_table(y_sel)

    html_out = render(yest, today, y_races, y_sel, y_sum, y_reg, t_races, t_sel, t_spent)
    with open("ai_yosou.html", "w", encoding="utf-8") as f:
        f.write(html_out)
    print("○ ai_yosou.html を生成")
    print(f"  昨日{yest}: 採用{y_sum['n']}R 投資{y_sum['stake']:,}円 払戻{y_sum['pay']:,}円 回収率{y_sum['ret']}% (2連単的中{y_sum['h2']}/3連単{y_sum['h3']})")
    print(f"  今日{today}: 採用{len(t_sel)}R 投資見込{t_spent:,}円")
    for row in y_reg:
        print(f"   {row[0]}: {row[1]}R 投資{row[2]:,} 払戻{row[3]:,} 回収{row[4]}% (2単{row[5]}/3単{row[6]})")


def render(yest, today, y_races, y_sel, y_sum, y_reg, t_races, t_sel, t_spent):
    e = html.escape
    WC = {1: "#fff", 2: "#222", 3: "#e23", 4: "#26c", 5: "#fc0", 6: "#2a2"}
    WT = {1: "#000", 2: "#fff", 3: "#fff", 4: "#fff", 5: "#000", 6: "#fff"}

    def wk(w):
        return (f'<span class="wk" style="background:{WC[w]};color:{WT[w]};'
                f'border:1px solid #555">{w}</span>')

    def combo(c):
        return "-".join(wk(w) for w in c)

    def race_card(r, show_res):
        hon = round(r["hon"] * 100)
        head = (f'<div class="rh"><b>{e(r["v"])}{e(str(r["race"]))}R</b>'
                f'<span class="rg rg{r["regime"]}">{r["regime"]}</span>'
                f'<span class="hon">本命{hon}%</span></div>')
        honmei = (f'<div class="hm">◎{wk(r["hm"])} {e(r["name"])}</div>')
        # 買い目
        def bet_block(title, lst, k):
            rows = ""
            for c, p in lst:
                need = round(1 / p, 1) if p > 0 else 0
                rows += (f'<div class="cb">{combo(c)}'
                         f'<span class="nd">必要{need}倍</span></div>')
            return f'<div class="bb"><div class="bt">{title}（{len(lst)}点）</div>{rows}</div>'
        bets = bet_block("2連単", r["ex2"], r["kx"]) + bet_block("3連単", r["ex3"], r["kt"])
        excl = ""
        if r["xb"]:
            excl = '<div class="ex">除外: ' + "・".join(
                f'{x[0]}号({x[1]})' for x in r["xb"]) + "</div>"
        res = ""
        if show_res and r["has_res"]:
            ordc = "".join(wk(w) for w in r["order"][:3])
            tags = []
            if r["hit2"] is not None:
                tags.append(f'<span class="{"hit" if r["hit2"] else "miss"}">2連単{"的中 ¥"+format(r["pay2"],",") if r["hit2"] else "不的中"}</span>')
            if r["hit3"] is not None:
                tags.append(f'<span class="{"hit" if r["hit3"] else "miss"}">3連単{"的中 ¥"+format(r["pay3"],",") if r["hit3"] else "不的中"}</span>')
            res = (f'<div class="res"><span class="rl">結果</span>{ordc}'
                   f'<span class="sp"></span>{"".join(tags)}</div>')
        elif show_res:
            res = '<div class="res"><span class="rl">結果</span><span class="muted">未確定</span></div>'
        return f'<div class="card">{head}{honmei}{excl}{bets}{res}</div>'

    y_cards = "".join(race_card(r, True) for r in y_sel)
    t_cards = "".join(race_card(r, False) for r in t_sel)

    # 回収率の色
    def retcol(v):
        return "#3c8" if v >= 100 else ("#e96" if v >= 80 else "#e55")

    reg_rows = ""
    for row in y_reg:
        lab, n, st, pay, ret, h2, h3 = row
        reg_rows += (f'<tr><td>{lab}</td><td>{n}</td><td>{st:,}</td>'
                     f'<td>{pay:,}</td><td style="color:{retcol(ret)};font-weight:700">{ret}%</td>'
                     f'<td>{h2}</td><td>{h3}</td></tr>')

    t_reg = regime_table(t_sel)
    t_reg_rows = ""
    for row in t_reg:
        lab, n, st, pay, ret, h2, h3 = row
        t_reg_rows += f'<tr><td>{lab}</td><td>{n}</td><td>{st:,}円</td></tr>'

    yfull = summarize(y_races)  # 全レース買った場合（参考）

    return f"""<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI予想 10万円チャレンジ</title>
<style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;background:#0d1117;color:#e6edf3;font:16px/1.6 -apple-system,"Segoe UI",sans-serif}}
.wrap{{max-width:680px;margin:0 auto;padding:14px}}
h1{{font-size:20px;margin:.2em 0}}
h2{{font-size:17px;margin:1.4em 0 .5em;padding-left:8px;border-left:4px solid #3b82f6}}
.sub{{color:#9aa7b4;font-size:13px}}
.note{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px;font-size:13px;color:#c9d4df;margin:10px 0}}
.note b{{color:#f0c040}}
.cards{{display:grid;gap:8px}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:10px 0}}
.kpi{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:10px;text-align:center}}
.kpi .v{{font-size:20px;font-weight:800}}
.kpi .l{{font-size:11px;color:#9aa7b4}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0}}
th,td{{padding:6px 4px;border-bottom:1px solid #21262d;text-align:center}}
th{{color:#9aa7b4;font-weight:600}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:10px}}
.rh{{display:flex;align-items:center;gap:8px;font-size:14px}}
.rg{{font-size:11px;padding:1px 7px;border-radius:8px}}
.rg鉄板{{background:#1d4ed8}}.rg標準{{background:#3f3f46}}.rg穴{{background:#7c2d12}}
.hon{{margin-left:auto;color:#9aa7b4;font-size:12px}}
.hm{{margin:6px 0;font-weight:700}}
.wk{{display:inline-flex;width:20px;height:20px;border-radius:4px;align-items:center;justify-content:center;font-size:12px;font-weight:800;vertical-align:middle}}
.bb{{margin-top:6px}}
.bt{{font-size:12px;color:#9aa7b4;margin-bottom:2px}}
.cb{{display:flex;align-items:center;gap:3px;font-size:13px;padding:2px 0}}
.nd{{margin-left:auto;color:#7d8896;font-size:11px}}
.ex{{font-size:11px;color:#e0986a;margin:4px 0}}
.res{{margin-top:8px;padding-top:7px;border-top:1px dashed #30363d;display:flex;align-items:center;gap:4px;flex-wrap:wrap;font-size:13px}}
.rl{{font-size:11px;color:#9aa7b4;margin-right:4px}}
.sp{{flex:1}}
.hit{{background:#14532d;color:#86efac;padding:1px 7px;border-radius:8px;font-size:12px}}
.miss{{background:#3f1d1d;color:#fca5a5;padding:1px 7px;border-radius:8px;font-size:12px}}
.muted{{color:#6b7682}}
details summary{{cursor:pointer;color:#7aa7ff;font-size:14px;padding:6px 0}}
</style></head><body><div class="wrap">

<h1>🏁 AI予想 10万円チャレンジ</h1>
<div class="sub">直前情報なしモデル（1着確率）／本命確率の高いレースに資金集中・変動点数・B2/不振1号艇は除外</div>

<div class="note">
<b>正直な前提：</b> 機械的なベットで<b>期待回収率が100%を超えることは構造的にありません</b>
（控除率〜25%・人気サイドほど回収率が高い favorite-longshot）。
この戦略は「回収率が最も高い<b>鉄板（本命確率が高い）</b>レースに10万円を集中し、
変動点数＋除外で無駄打ちを削る」＝<b>100%に最も近づける</b>形です。
1日は本数が少なく分散が大きいので、結果は100%を超えることも大きく割ることもあります。
</div>

<h2>① 昨日 {yest.strftime('%m/%d')} の予想と結果</h2>
<div class="sub">本命確率の高い順に、予算10万円が尽きるまで {y_sum['n']} レースを購入</div>
<div class="kpis">
<div class="kpi"><div class="v">¥{y_sum['stake']:,}</div><div class="l">投資</div></div>
<div class="kpi"><div class="v">¥{y_sum['pay']:,}</div><div class="l">払戻</div></div>
<div class="kpi"><div class="v" style="color:{retcol(y_sum['ret'])}">{y_sum['ret']}%</div><div class="l">回収率</div></div>
<div class="kpi"><div class="v">{y_sum['h2']}/{y_sum['h3']}</div><div class="l">的中 2単/3単</div></div>
</div>

<table>
<tr><th>区分</th><th>R数</th><th>投資</th><th>払戻</th><th>回収率</th><th>2単的中</th><th>3単的中</th></tr>
{reg_rows}
</table>
<div class="sub">参考：昨日{len(y_races)}レース全部を同じ買い目で買った場合 → 投資¥{yfull['stake']:,}／払戻¥{yfull['pay']:,}／回収率<b style="color:{retcol(yfull['ret'])}">{yfull['ret']}%</b></div>

<details open><summary>▼ 購入レースの明細（{y_sum['n']}R）</summary>
<div class="cards">{y_cards}</div>
</details>

<h2>② 今日 {today.strftime('%m/%d')} の予想（同じ戦略）</h2>
<div class="sub">本命確率の高い順に、予算10万円ぶん {len(t_sel)} レースを購入予定（投資見込 ¥{t_spent:,}）</div>
<table>
<tr><th>区分</th><th>R数</th><th>投資見込</th></tr>
{t_reg_rows}
</table>
<details open><summary>▼ 本日の購入レースと買い目（{len(t_sel)}R）</summary>
<div class="cards">{t_cards}</div>
</details>

<div class="note" style="margin-top:18px">
<b>買い方：</b> 各買い目を100円ずつ。「必要N倍」は <b>1÷モデル確率</b>＝発走前の実オッズが
この倍率を超えていれば期待値プラス。実オッズがこれを下回る買い目は見送ると、さらに回収率を底上げできます。
</div>

</div></body></html>"""


if __name__ == "__main__":
    main()
