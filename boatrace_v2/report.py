# -*- coding: utf-8 -*-
"""当日画面の生成 — ev_picks.compute_picks の結果を today.html / today.json に書き出す。

server.py は data/web/ のホワイトリストだけを配信し、app.py がオッズ更新のたびに
ここを再生成する（v1 の build_today.py のような集計は行わない。
回収率の話は backtest.py の CI 付きレポートに委ねる — T1/T4）。

使い方:
  python report.py                      # 今日の today.html / today.json を生成
  python report.py --date 2026-07-02 --ev-min 1.2
"""
import argparse
import datetime
import html
import json
import os

try:
    from . import config, ev_picks, before
except ImportError:
    import config
    import ev_picks
    import before

_CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#1c2530;--sub:#5b6b7c;--line:#e3e8ee;
--acc:#0f6ab4;--warn:#b45309;--hit:#0a7d55}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:"Hiragino Sans","Noto Sans JP",system-ui,sans-serif;line-height:1.55}
.wrap{max-width:760px;margin:0 auto;padding:16px}
header{display:flex;justify-content:space-between;align-items:baseline;
flex-wrap:wrap;gap:4px;margin-bottom:4px}
h1{font-size:22px;margin:0}
h1 small{font-size:12px;color:var(--sub);font-weight:normal;margin-left:8px}
.meta{font-size:12px;color:var(--sub)}
.note{font-size:12px;color:var(--sub);margin:6px 0 14px}
.warn{color:var(--warn)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:12px 14px;margin-bottom:12px}
.card h2{font-size:15px;margin:0 0 8px;display:flex;gap:10px;align-items:baseline}
.card h2 .hon{font-size:12px;color:var(--acc);font-weight:normal}
.card h2 .st{font-size:12px;color:var(--sub);font-weight:normal}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--sub);font-weight:normal;text-align:left;padding:2px 6px;
border-bottom:1px solid var(--line)}
td{padding:3px 6px;border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.ev{color:var(--hit);font-weight:600}
.stale{color:var(--warn)}
.res{font-size:12px;color:var(--ink);background:#eef6f1;border:1px solid #cfe5d8;
border-radius:6px;padding:1px 8px}
.exrow{font-size:12px;color:var(--sub);margin-top:8px;line-height:1.7}
.exrow b{color:var(--ink)}
tr.hit td{background:#f0faf5}
td.hitmark{color:var(--hit);font-weight:700}
td.missmark{color:var(--sub)}
.empty{padding:26px;text-align:center;color:var(--sub)}
.sum{background:#eef4fb;border-color:#cdddf0}
.sum .big{font-size:15px;font-weight:600}
.sum .plus{color:var(--hit)}
.sum .minus{color:#b0453a}
footer{font-size:11px;color:var(--sub);margin:18px 0 8px}
@media (max-width:480px){
.wrap{padding:10px}
h1{font-size:19px}
table{font-size:12px}
td,th{padding:3px 4px}
.card{padding:10px 10px}
}
"""


def summarize(races, before_data):
    """結果が確定したレース分の「表示買い目を各100円購入した場合」の途中集計。"""
    n_res = bets = hits = stake = ret = 0
    for race in races:
        res = (before_data.get(race["rid"]) or {}).get("result")
        if not res or not res.get("order"):
            continue
        n_res += 1
        for r in race["rows"]:
            k = 2 if r["bt"] == "2t" else 3
            if len(res["order"]) < k:
                continue
            bets += 1
            stake += 100
            if r["combo"] == "-".join(map(str, res["order"][:k])):
                hits += 1
                po = res.get("po2") if k == 2 else res.get("po3")
                ret += po or 0
    if not n_res:
        return None
    return {"races": n_res, "bets": bets, "hits": hits, "stake": stake,
            "ret": ret, "roi": ret / stake * 100 if stake else 0.0}


def _ex_line(ex):
    """展示情報1行（展示タイム・進入・天候）。"""
    parts = []
    times = ex.get("time") or []
    if any(t is not None for t in times):
        best = min(t for t in times if t is not None)
        ts = " / ".join(
            (f"<b>{t:.2f}★</b>" if t == best else f"{t:.2f}") if t is not None else "-"
            for t in times)
        parts.append(f"展示 {ts}")
    course = ex.get("course") or []
    if any(c is not None for c in course):
        entry = sorted((c, w + 1) for w, c in enumerate(course) if c is not None)
        parts.append("進入 " + "-".join(str(w) for _, w in entry))
    w = ex.get("weather") or {}
    wparts = [w.get("tenki") or ""]
    if w.get("wind") is not None:
        wparts.append(f"風{w['wind']:g}m")
    if w.get("wave") is not None:
        wparts.append(f"波{w['wave']:g}cm")
    ws = " ".join(x for x in wparts if x)
    if ws:
        parts.append(ws)
    return " ／ ".join(parts)


def render_html(hd, races, meta, ev_min, hon_min, generated_at, before_data=None):
    before_data = before_data or {}
    d = f"{hd[:4]}-{hd[4:6]}-{hd[6:8]}"
    warns = []
    if meta.get("no_pred"):
        warns.append("当日の予測がありません（v1 daily.py を先に実行してください）")
    if meta.get("no_snaps"):
        warns.append("オッズスナップショット未取得（app.py / scheduler.py が収集します）")
    if meta.get("no_curve"):
        warns.append("較正曲線なし（calibration.py 未実行）→ 生の p_win を使用中")
    if meta.get("legacy"):
        warns.append("v1形式オッズ（取得履歴なし）を使用中")
    out = [
        "<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta http-equiv='refresh' content='60'>",
        f"<title>{html.escape(config.APP_TITLE)} {d}</title>",
        f"<style>{_CSS}</style></head><body><div class='wrap'>",
        "<header>",
        f"<h1>{html.escape(config.APP_TITLE)}<small>v2</small></h1>",
        f"<div class='meta'>{d} ／ 更新 {generated_at}（60秒毎に自動再読込）</div>",
        "</header>",
        f"<div class='note'>較正後確率 × 最新オッズで EV≥{ev_min:g}・"
        f"本命確率≥{hon_min*100:.0f}% の買い目のみ表示。",
    ]
    for w in warns:
        out.append(f"<div class='note warn'>⚠ {html.escape(w)}</div>")
    out.append("</div>")
    sm = summarize(races, before_data)
    if sm:
        cls = "plus" if sm["ret"] >= sm["stake"] else "minus"
        out.append(
            f"<div class='card sum'><div class='big'>本日の途中経過"
            f"（結果確定 {sm['races']}R分）: {sm['bets']}点中 {sm['hits']}的中 ／ "
            f"100円換算 投資 {sm['stake']:,}円 → 回収 {sm['ret']:,}円 "
            f"<span class='{cls}'>（回収率 {sm['roi']:.0f}%）</span></div>"
            f"<div class='note' style='margin:4px 0 0'>※ 表示中の買い目を各100円"
            f"購入したと仮定した参考値（実際の購入・オッズ変動とは異なります）</div>"
            f"</div>")
    if not races:
        out.append("<div class='card empty'>条件に合致する買い目なし"
                   "（無理に張らないのが正解）</div>")
    for race in races:
        bf = before_data.get(race["rid"]) or {}
        res = bf.get("result")
        st = f"<span class='st'>発走 {html.escape(race['start'])}</span>" \
            if race.get("start") else ""
        badge = ""
        if res and res.get("order"):
            o = res["order"]
            pays = []
            if res.get("po2") is not None:
                pays.append(f"2連単 ¥{res['po2']:,}")
            if res.get("po3") is not None:
                pays.append(f"3連単 ¥{res['po3']:,}")
            badge = (f"<span class='res'>結果 {'-'.join(map(str, o[:3]))}"
                     f"{'（' + '・'.join(pays) + '）' if pays else ''}</span>")
        out.append(
            f"<div class='card'><h2>{html.escape(race['venue'])} "
            f"{race['rno']}R {st}"
            f"<span class='hon'>較正後本命 {race['hon']*100:.0f}%</span>"
            f"{badge}</h2>")
        res_col = bool(res and res.get("order"))
        out.append("<table><tr><th>式別</th><th>買い目</th>"
                   "<th class='num'>p</th><th class='num'>オッズ</th>"
                   "<th class='num'>EV</th><th class='num'>鮮度</th>"
                   + ("<th>結果</th>" if res_col else "") + "</tr>")
        for r in race["rows"]:
            if r["age"] is None:
                age = "<td class='num'>-</td>"
            else:
                cls = " class='num stale'" if r["age"] > 15 else " class='num'"
                mark = " ⚠" if r["age"] > 15 else ""
                age = f"<td{cls}>{r['age']:.0f}分前{mark}</td>"
            hitcell = rowcls = ""
            if res_col:
                k = 2 if r["bt"] == "2t" else 3
                win = "-".join(map(str, res["order"][:k])) if len(res["order"]) >= k else ""
                if r["combo"] == win:
                    hitcell, rowcls = "<td class='hitmark'>○的中</td>", " class='hit'"
                else:
                    hitcell = "<td class='missmark'>×</td>"
            out.append(
                f"<tr{rowcls}><td>{'2連単' if r['bt'] == '2t' else '3連単'}</td>"
                f"<td>{html.escape(r['combo'])}</td>"
                f"<td class='num'>{r['p']:.3f}</td>"
                f"<td class='num'>{r['odds']:.1f}</td>"
                f"<td class='num ev'>{r['ev']:.2f}</td>{age}{hitcell}</tr>")
        out.append("</table>")
        if bf.get("ex"):
            line = _ex_line(bf["ex"])
            if line:
                out.append(f"<div class='exrow'>{line}</div>")
        out.append("</div>")
    out.append(
        "<footer>EV=較正後確率×オッズ。オッズは発走直前に変動します（締切5分前に"
        "自動再取得）。検証期間の回収率95%CIが100%を跨ぐ限り優位性は未実証です — "
        "少点・高分散を前提に。</footer></div></body></html>")
    return "".join(out)


def build(hd=None, ev_min=1.5, hon_min=0.60, max_age=None):
    """today.html / today.json を data/web/ に生成。返り値=htmlパス。"""
    config.ensure_dirs()
    hd = hd or datetime.date.today().strftime("%Y%m%d")
    generated_at = datetime.datetime.now().strftime("%H:%M:%S")
    races, meta = ev_picks.compute_picks(hd, ev_min=ev_min, hon_min=hon_min,
                                         max_age=max_age)
    before_data = before.load_day(hd)
    jp = os.path.join(config.WEB_DIR, "today.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump({"app": config.APP_TITLE, "date": hd,
                   "generated_at": generated_at, "ev_min": ev_min,
                   "hon_min": hon_min, "meta": meta, "races": races,
                   "before": {r["rid"]: before_data[r["rid"]] for r in races
                              if r["rid"] in before_data}},
                  f, ensure_ascii=False)
    hp = os.path.join(config.WEB_DIR, "today.html")
    with open(hp, "w", encoding="utf-8") as f:
        f.write(render_html(hd, races, meta, ev_min, hon_min, generated_at,
                            before_data))
    return hp


def main():
    ap = argparse.ArgumentParser(description="today.html / today.json の生成")
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--ev-min", type=float, default=1.5)
    ap.add_argument("--hon-min", type=float, default=0.60)
    ap.add_argument("--max-age", type=float, default=None)
    args = ap.parse_args()
    hp = build(args.date.replace("-", ""), args.ev_min, args.hon_min, args.max_age)
    print(f"○ 生成: {hp}")


if __name__ == "__main__":
    main()
