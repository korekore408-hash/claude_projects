# -*- coding: utf-8 -*-
"""
EV バックテスト結果ページ生成 (iPhone/PWA 対応)
=============================================
backtest_ev.run_backtest() の集計を、自己完結の静的HTML (CDN不要・モバイルダーク) に
出力する。iOS Safari の「ホーム画面に追加」でアプリのように全画面起動できるよう
apple-mobile-web-app-* メタ + web manifest (data URI) + apple-touch-icon を埋め込む。

静的ファイルなのでサーバ常時起動は不要。外出先で見るには任意の静的ホストに置く。

使い方:
  py -3.13 build_backtest.py                      # 取得済みオッズ全期間
  py -3.13 build_backtest.py --dates 20260604-20260617
  py -3.13 build_backtest.py --out backtest.html
"""
import argparse
import datetime
import html
import json

from backtest_ev import run_backtest

# ホーム画面アイコン (SVG data URI / manifest+apple-touch-icon 兼用)
ICON_SVG = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 180 180'>"
    "<rect width='180' height='180' rx='40' fill='%230b1220'/>"
    "<text x='90' y='86' font-size='52' fill='%2360e0a0' text-anchor='middle'"
    " font-family='sans-serif' font-weight='700'>EV</text>"
    "<text x='90' y='132' font-size='30' fill='%23cbd5e1' text-anchor='middle'"
    " font-family='sans-serif'>検証</text></svg>"
)


def roi_bar_svg(meta, stats):
    """しきい値ごとの合計回収率を CDN 不要の SVG 棒グラフに。"""
    ts = meta["thresholds"]
    rois = [stats[t]["all"]["roi"] for t in ts]
    flat = stats[0.0]["all"]["roi"]
    W, H = 340, 200
    pad_l, pad_b, pad_t = 34, 28, 10
    plot_w, plot_h = W - pad_l - 10, H - pad_b - pad_t
    ymax = max(110, max(rois) + 8)
    n = len(ts)
    bw = plot_w / n * 0.6
    gap = plot_w / n

    def y(v):
        return pad_t + plot_h * (1 - v / ymax)

    parts = [f"<svg viewBox='0 0 {W} {H}' xmlns='http://www.w3.org/2000/svg' "
             f"style='width:100%;height:auto'>"]
    # グリッド (50/100%)
    for gv in (50, 100):
        yy = y(gv)
        col = "#3b82f6" if gv == 100 else "#334155"
        parts.append(f"<line x1='{pad_l}' y1='{yy:.1f}' x2='{W-10}' y2='{yy:.1f}' "
                     f"stroke='{col}' stroke-dasharray='4 3' stroke-width='1'/>")
        parts.append(f"<text x='{pad_l-4}' y='{yy+3:.1f}' font-size='9' fill='#94a3b8' "
                     f"text-anchor='end'>{gv}%</text>")
    # フラット基準線
    yf = y(flat)
    parts.append(f"<line x1='{pad_l}' y1='{yf:.1f}' x2='{W-10}' y2='{yf:.1f}' "
                 f"stroke='#f59e0b' stroke-width='1'/>")
    # 棒
    best = max(rois)
    for i, (t, r) in enumerate(zip(ts, rois)):
        x = pad_l + gap * i + (gap - bw) / 2
        yy = y(r)
        col = "#60e0a0" if (r == best and t != 0.0) else ("#f59e0b" if t == 0.0 else "#3b8f6f")
        parts.append(f"<rect x='{x:.1f}' y='{yy:.1f}' width='{bw:.1f}' "
                     f"height='{(pad_t+plot_h-yy):.1f}' fill='{col}' rx='2'/>")
        parts.append(f"<text x='{x+bw/2:.1f}' y='{yy-3:.1f}' font-size='9' fill='#e2e8f0' "
                     f"text-anchor='middle'>{r:.0f}</text>")
        lab = "フラット" if t == 0.0 else f"≥{t:.1f}"
        parts.append(f"<text x='{x+bw/2:.1f}' y='{H-9}' font-size='9' fill='#94a3b8' "
                     f"text-anchor='middle'>{lab}</text>")
    parts.append("</svg>")
    return "".join(parts)


def page(meta, stats):
    ts = meta["thresholds"]
    flat = stats[0.0]["all"]
    best_t = max((t for t in ts if t != 0.0), key=lambda t: stats[t]["all"]["roi"])
    best = stats[best_t]["all"]
    delta = best["roi"] - flat["roi"]
    gen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    def row(t):
        a = stats[t]["all"]
        tag = "フラット買い" if t == 0.0 else f"EV≥{t:.1f}"
        cls = " class='best'" if t == best_t else (" class='flat'" if t == 0.0 else "")
        roicls = "pos" if a["roi"] >= 100 else ("warn" if a["roi"] >= flat["roi"] else "neg")
        return (f"<tr{cls}><td>{tag}</td><td>{a['bets']:,}</td>"
                f"<td>{a['hit_rate']:.1f}%</td>"
                f"<td class='{roicls}'>{a['roi']:.1f}%</td></tr>")

    def detail_rows(t):
        out = []
        for bt, nm in (("2t", "2連単"), ("3t", "3連単")):
            s = stats[t][bt]
            out.append(f"<tr><td>{nm}</td><td>{s['bets']:,}</td>"
                       f"<td>{s['hit_rate']:.1f}%</td><td>{s['roi']:.1f}%</td></tr>")
        return "".join(out)

    sweep = "".join(row(t) for t in ts)
    detail_blocks = "".join(
        f"<details><summary>{('フラット' if t==0.0 else f'EV≥{t:.1f}')} "
        f"の内訳</summary><table class='mini'><thead><tr><th>券種</th>"
        f"<th>点数</th><th>的中</th><th>回収</th></tr></thead><tbody>"
        f"{detail_rows(t)}</tbody></table></details>"
        for t in ts)

    data_json = html.escape(json.dumps({"meta": meta, "stats": stats}, ensure_ascii=False))
    chart = roi_bar_svg(meta, stats)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="EV検証">
<meta name="theme-color" content="#0b1220">
<link rel="apple-touch-icon" href="{ICON_SVG}">
<link rel="icon" href="{ICON_SVG}">
<link rel="manifest" href='data:application/manifest+json,{html.escape(json.dumps({
    "name": "EVバックテスト", "short_name": "EV検証",
    "display": "standalone", "background_color": "#0b1220", "theme_color": "#0b1220",
    "icons": [{"src": ICON_SVG, "sizes": "any", "type": "image/svg+xml"}],
}, ensure_ascii=False))}'>
<title>EVバックテスト</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#0b1220;color:#e2e8f0;font:16px/1.6 -apple-system,
  "Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif;
  padding:max(12px,env(safe-area-inset-top)) 12px calc(24px+env(safe-area-inset-bottom))}}
.wrap{{max-width:560px;margin:0 auto}}
h1{{font-size:20px;margin:.2em 0 .1em}}
.sub{{color:#94a3b8;font-size:13px;margin-bottom:14px}}
.card{{background:#111c2e;border:1px solid #1e2d44;border-radius:14px;padding:14px;margin:12px 0}}
.kpi{{display:flex;gap:10px;flex-wrap:wrap}}
.kpi div{{flex:1;min-width:120px;background:#0e1830;border-radius:10px;padding:10px 12px}}
.kpi .lbl{{color:#94a3b8;font-size:12px}}
.kpi .val{{font-size:22px;font-weight:700}}
.pos{{color:#34d399}} .warn{{color:#fbbf24}} .neg{{color:#f87171}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{padding:8px 6px;text-align:right;border-bottom:1px solid #1e2d44}}
th:first-child,td:first-child{{text-align:left}}
thead th{{color:#94a3b8;font-weight:600;font-size:12px}}
tr.best{{background:#0f2a20}} tr.best td{{font-weight:700}}
tr.flat td{{color:#cbd5e1}}
details{{margin:8px 0;background:#0e1830;border-radius:10px;padding:4px 12px}}
summary{{cursor:pointer;color:#93c5fd;font-size:13px;padding:6px 0}}
table.mini th,table.mini td{{font-size:12px;padding:5px 4px;border-color:#1a2740}}
.note{{color:#94a3b8;font-size:12.5px;line-height:1.7}}
.note b{{color:#cbd5e1}}
.foot{{color:#64748b;font-size:11px;margin-top:18px;text-align:center}}
.legend{{font-size:11px;color:#94a3b8;margin-top:6px}}
.legend i{{font-style:normal}}
</style>
</head>
<body>
<div class="wrap">
  <h1>EV バックテスト</h1>
  <div class="sub">honest OOS × 締切実オッズ ・ 期間 {meta['date_min']}〜{meta['date_max']} ({meta['n_days']}日 / {meta['n_evaluated']:,}R)</div>

  <div class="card">
    <div class="kpi">
      <div><div class="lbl">フラット買い</div>
        <div class="val">{flat['roi']:.1f}%</div></div>
      <div><div class="lbl">最良 (EV≥{best_t:.1f})</div>
        <div class="val {'pos' if best['roi']>=100 else 'warn'}">{best['roi']:.1f}%</div></div>
      <div><div class="lbl">改善幅</div>
        <div class="val {'pos' if delta>=0 else 'neg'}">{delta:+.1f}pt</div></div>
    </div>
    <div class="legend">買い目=2連単上位{meta['top_2t']}/3連単上位{meta['top_3t']}、
      1点{meta['stake']}円均等。<i>EV=モデル確率×実オッズ</i></div>
  </div>

  <div class="card">
    <div style="font-weight:700;margin-bottom:6px">閾値別 回収率 (合計)</div>
    {chart}
    <div class="legend">黄線=フラット基準 / 青破線=100%(収支トントン)</div>
  </div>

  <div class="card">
    <table>
      <thead><tr><th>戦略</th><th>点数</th><th>的中率</th><th>回収率</th></tr></thead>
      <tbody>{sweep}</tbody>
    </table>
    {detail_blocks}
  </div>

  <div class="card note">
    <b>読み方</b><br>
    ・ EVで絞るとフラット買いより回収率が上がる（選別に意味がある）。<br>
    ・ ただし最良でも 100% 未満なら、この買い目範囲では控除率（約25%）の壁を越えられない。<br>
    ・ 閾値を上げすぎると点数が減り高配当依存で振れる。サンプル小の際は参考値。
  </div>

  <div class="foot">生成 {gen}・実オッズは締切時値</div>
</div>
<script type="application/json" id="bt-data">{data_json}</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default="", help="例 20260604-20260617")
    ap.add_argument("--out", default="backtest.html")
    args = ap.parse_args()

    dates_filter = None
    if args.dates:
        if "-" in args.dates:
            a, b = args.dates.split("-")
            dates_filter = {str(d) for d in range(int(a), int(b) + 1)}
        else:
            dates_filter = {args.dates}

    result = run_backtest(dates_filter=dates_filter)
    if result is None:
        print("オッズが見つかりません。fetch_odds.py で取得してください。")
        return
    meta, stats, _ = result
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(page(meta, stats))
    print(f"生成: {args.out}  ({meta['n_days']}日 / {meta['n_evaluated']:,}R)")
    print(f"  フラット {stats[0.0]['all']['roi']:.1f}%  →  "
          f"最良 {max((stats[t]['all']['roi'] for t in meta['thresholds'] if t!=0.0)):.1f}%")


if __name__ == "__main__":
    main()
