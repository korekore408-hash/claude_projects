# -*- coding: utf-8 -*-
"""
的中率サマリーページ生成（別ページ）
=========================================================================
predict_win.csv（各艇の strength と確定着順 finish_rank）から、各レースの
Plackett-Luce 予想を展開し「実際の決着がモデル予想の上位K位以内に入ったか」
＝的中を判定する。集計は

  - 年（年間） × {2連単, 3連単} × top-K（K=1本命 / 3 / 5 / 10）
  - 競艇場ごと（会場別）× 同上

の的中率のみを、集計済み数値だけ埋め込んだ軽量 HTML として出力する。
（全レコードは載せないので年単位データでもページは小さい。）

使い方:
  py -3 build_summary.py
  py -3 build_summary.py --pred predict_win.csv --out summary.html
"""

import argparse
import csv
import json
from collections import defaultdict

from features_player_history import VENUE_CODE

CODE_VENUE = {v: k for k, v in VENUE_CODE.items()}
KS = [1, 3, 5, 10]


def hit_rank(strengths, actual, kind):
    """actual 組合せ（枠tuple）の予想内順位を返す（1=本命）。
    全組合せの確率を出さず、actual より確率が高い組合せ数を数えて順位化する。"""
    tot = sum(strengths)
    idx = range(6)

    def pl(combo):
        p, rem = 1.0, tot
        for w in combo:
            s = strengths[w - 1]
            p *= s / rem
            rem -= s
        return p

    pa = pl(actual)
    greater = 0
    if kind == 2:
        for i in idx:
            for j in idx:
                if j == i:
                    continue
                if pl((i + 1, j + 1)) > pa:
                    greater += 1
    else:
        for i in idx:
            for j in idx:
                if j == i:
                    continue
                for k in idx:
                    if k in (i, j):
                        continue
                    if pl((i + 1, j + 1, k + 1)) > pa:
                        greater += 1
    return greater + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default="predict_win.csv")
    ap.add_argument("--flags", default="race_flags.csv")
    ap.add_argument("--label", default="", help="ヘッダに出す評価モードの注記")
    ap.add_argument("--out", default="summary.html")
    args = ap.parse_args()

    # race_id -> {枠: (strength, finish_rank)}
    races = defaultdict(dict)
    for r in csv.DictReader(open(args.pred, encoding="cp932")):
        rid = r["race_id"]
        fr = r["finish_rank"]
        races[rid][int(r["枠番"])] = (
            float(r["strength"]),
            int(fr) if fr not in ("", None) else None,
        )

    # 対象外フラグ（本番でレーン変更/転覆・失格/フライング/欠場/安定板）を読む。
    # これらは予想の前提が崩れたレースなので的中率の集計から除外する。
    excluded = {}
    reason_counts = defaultdict(int)
    try:
        for r in csv.DictReader(open(args.flags, encoding="cp932")):
            excluded[r["race_id"]] = int(r["excluded"])
            for key in ("lane_changed", "has_dnf", "has_flying", "has_absent", "stabilizer"):
                if int(r[key]):
                    reason_counts[key] += 1
    except FileNotFoundError:
        print(f"! フラグ CSV が無いので全レースを集計します: {args.flags}")

    # 集計器: agg[year][code]['ex'/'tri'][K]=ヒット数, ['n_ex'/'n_tri']=各券種の評価母数
    # （2連単と3連単で決着確定レース数が異なり得るので分母を分ける）
    def new_bucket():
        return {"n_ex": 0, "n_tri": 0,
                "ex": {k: 0 for k in KS},
                "tri": {k: 0 for k in KS}}

    agg = defaultdict(lambda: defaultdict(new_bucket))   # year -> code -> bucket
    ALL = "_all"

    n_excluded = 0
    for rid, boats in races.items():
        if len(boats) < 6:
            continue
        if excluded.get(rid):          # 対象外レースは集計しない
            n_excluded += 1
            continue
        year = rid[2:6]
        code = rid[:2]
        strengths = [boats.get(w, (0.0, None))[0] for w in range(1, 7)]
        fin = {}
        for w, (_, fr) in boats.items():
            if fr is not None:
                fin[fr] = w

        # 2連単・3連単それぞれ、決着が確定していれば順位を出して的中判定
        for kind, need in ((2, [1, 2]), (3, [1, 2, 3])):
            if not all(p in fin for p in need):
                continue
            actual = tuple(fin[p] for p in need)
            rank = hit_rank(strengths, actual, kind)
            key = "ex" if kind == 2 else "tri"
            nkey = "n_ex" if kind == 2 else "n_tri"
            for yk in (year, "ALL"):
                for ck in (code, ALL):
                    b = agg[yk][ck]
                    b[nkey] += 1            # 券種ごとの母数
                    for K in KS:
                        if rank <= K:
                            b[key][K] += 1

    def rates(b):
        ne = b["n_ex"] or 1
        nt = b["n_tri"] or 1
        return {
            "n": b["n_ex"],
            "ex": {str(K): round(b["ex"][K] / ne, 4) for K in KS},
            "tri": {str(K): round(b["tri"][K] / nt, 4) for K in KS},
        }

    years = sorted(y for y in agg if y != "ALL")
    payload = {"years": years, "ks": KS, "byYear": {}}
    for yk in years + ["ALL"]:
        overall = rates(agg[yk][ALL])
        venues = []
        for ck in sorted(c for c in agg[yk] if c != ALL):
            vb = rates(agg[yk][ck])
            vb["code"] = ck
            vb["name"] = CODE_VENUE.get(ck, ck)
            venues.append(vb)
        venues.sort(key=lambda v: v["tri"]["3"], reverse=True)
        payload["byYear"][yk] = {"overall": overall, "venues": venues}

    total_n = agg["ALL"][ALL]["n_ex"]
    rc = reason_counts
    exc_txt = (f"対象外 {n_excluded} レースを除外（レーン変更 {rc['lane_changed']} / "
               f"転覆・失格系 {rc['has_dnf']} / フライング {rc['has_flying']} / "
               f"欠場 {rc['has_absent']} / 安定板 {rc['stabilizer']}（判定不可））")
    label = args.label or "全期間集計（学習・検証を区別しない簡易版）"
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False)) \
                        .replace("__TOTN__", str(total_n)) \
                        .replace("__EXC__", exc_txt) \
                        .replace("__LABEL__", label)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"○ サマリー生成: {args.out}")
    print(f"  対象レース {total_n} / 対象外除外 {n_excluded} / 年 {years} / 会場 {len(payload['byYear']['ALL']['venues'])}")
    ov = payload["byYear"]["ALL"]["overall"]
    print(f"  全体 2連単 top1={ov['ex']['1']:.3f} top3={ov['ex']['3']:.3f}"
          f" / 3連単 top1={ov['tri']['1']:.3f} top3={ov['tri']['3']:.3f}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>的中率サマリー｜競艇 直前情報なしモデル</title>
<style>
  * { box-sizing: border-box; }
  body { font-family:"Segoe UI","Yu Gothic UI",system-ui,sans-serif; margin:0; padding:0 0 60px;
         background:#0f1115; color:#e6e6e6; }
  header { padding:18px 24px; background:#161a22; border-bottom:1px solid #2a2f3a; }
  h1 { font-size:19px; margin:0 0 4px; }
  .sub { font-size:12px; color:#9aa3b2; }
  a.link { color:#6ea8fe; text-decoration:none; font-size:13px; }
  .controls { margin-top:12px; }
  select { background:#0f1115; color:#e6e6e6; border:1px solid #39404d; border-radius:6px; padding:6px 8px; font-size:14px; }
  section { padding:0 24px; }
  h2 { font-size:14px; color:#b9c2d0; margin:22px 0 10px; font-weight:600; }
  .cards { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; max-width:760px; }
  @media (max-width:720px){ .cards{ grid-template-columns:repeat(2,1fr);} }
  .mc { background:#12161d; border:1px solid #2a2f3a; border-radius:10px; padding:12px 14px; }
  .mc .lab { font-size:12px; color:#9aa3b2; }
  .mc .val { font-size:26px; font-weight:600; margin-top:4px; font-variant-numeric:tabular-nums; }
  .mc .base { font-size:11px; color:#6b7280; margin-top:2px; }
  table { border-collapse:collapse; width:100%; max-width:920px; font-size:13px; margin-top:6px; }
  th,td { border-bottom:1px solid #232a36; padding:7px 10px; text-align:right; }
  th { color:#8ea0ba; font-weight:600; cursor:pointer; user-select:none; white-space:nowrap; }
  th.k, td.k { text-align:left; }
  th:hover { color:#fff; }
  tbody tr:hover { background:#12161d; }
  .barcell { position:relative; }
  .bar { position:absolute; left:8px; top:50%; transform:translateY(-50%); height:7px; border-radius:3px; background:#2b6cb0; opacity:.5; }
  .num { position:relative; font-variant-numeric:tabular-nums; }
  .grp2 { color:#7fb2ff; } .grp3 { color:#ffd082; }
  .note { font-size:11px; color:#7e8796; margin:14px 24px; line-height:1.7; max-width:920px; }
</style>
</head>
<body>
<header>
  <h1>的中率サマリー <span class="sub">競艇 直前情報なしモデル（Plackett-Luce 予想）</span></h1>
  <div class="sub" style="color:#9fd0a0">評価方式：__LABEL__</div>
  <div class="sub">対象レース __TOTN__ ・ 2連単/3連単を「予想上位K位以内に実際の決着が入った割合」で集計 ・ <a class="link" href="viewer.html">レース別ビューアへ →</a></div>
  <div class="sub" style="margin-top:4px;color:#c9a36a">__EXC__</div>
  <div class="controls"><label class="sub">対象期間 <select id="selYear"></select></label></div>
</header>

<section>
  <h2>年間 的中率（全会場）</h2>
  <div class="sub" style="margin-bottom:8px">2連単</div>
  <div class="cards" id="exCards"></div>
  <div class="sub" style="margin:14px 0 8px">3連単</div>
  <div class="cards" id="triCards"></div>

  <h2>競艇場ごとの的中率</h2>
  <table id="venueTbl">
    <thead><tr>
      <th class="k" data-sort="name">会場</th>
      <th data-sort="n">レース</th>
      <th class="grp2" data-sort="ex1">2連単<br>本命</th>
      <th class="grp2" data-sort="ex3">2連単<br>top3</th>
      <th class="grp2" data-sort="ex5">2連単<br>top5</th>
      <th class="grp3" data-sort="tri1">3連単<br>本命</th>
      <th class="grp3" data-sort="tri3">3連単<br>top3</th>
      <th class="grp3" data-sort="tri10">3連単<br>top10</th>
    </tr></thead>
    <tbody></tbody>
  </table>
</section>

<div class="note">
  ※ 「本命」=予想確率1位の組合せ。top3/5/10=予想上位 3/5/10 通りの中に実際の決着が含まれた割合（＝その点数を買えば当たる割合）。<br>
  ※ ランダム期待値の目安：2連単 本命 1/30=3.3%、3連単 本命 1/120=0.8%。これを大きく上回るほどモデルが効いている。<br>
  ※ 現状の埋め込みデータは学習・検証を区別しない全期間集計（構想用）。本番の年間評価はウォークフォワード（各時点で過去のみ学習）の out-of-sample で出すこと。
</div>

<script>
const DATA = __DATA__;
const KS = DATA.ks;
const selYear = document.getElementById('selYear');
const opts = [['ALL','全期間']].concat(DATA.years.map(y => [y, y + '年']));
for (const [v, t] of opts) selYear.add(new Option(t, v));

const pct = x => (x * 100).toFixed(1) + '%';
let sortKey = 'tri3', sortDir = -1;

function card(lab, val, base) {
  return `<div class="mc"><div class="lab">${lab}</div><div class="val">${pct(val)}</div>`
       + (base ? `<div class="base">${base}</div>` : '') + `</div>`;
}

function render() {
  const y = selYear.value;
  const d = DATA.byYear[y];
  const o = d.overall;
  document.getElementById('exCards').innerHTML =
    card('本命（top1）', o.ex['1'], 'ランダム 3.3%') + card('top3', o.ex['3'], '3点')
    + card('top5', o.ex['5'], '5点') + card('top10', o.ex['10'], '10点');
  document.getElementById('triCards').innerHTML =
    card('本命（top1）', o.tri['1'], 'ランダム 0.8%') + card('top3', o.tri['3'], '3点')
    + card('top5', o.tri['5'], '5点') + card('top10', o.tri['10'], '10点');
  renderVenues(d.venues);
}

function val(v, key) {
  if (key === 'name') return v.name;
  if (key === 'n') return v.n;
  const m = key.match(/^(ex|tri)(\d+)$/);
  return v[m[1]][m[2]];
}

function renderVenues(venues) {
  const rows = venues.slice().sort((a, b) => {
    const va = val(a, sortKey), vb = val(b, sortKey);
    if (typeof va === 'string') return va.localeCompare(vb) * sortDir;
    return (va - vb) * sortDir;
  });
  const maxTri3 = Math.max(...rows.map(v => v.tri['3']), 0.0001);
  const tb = document.querySelector('#venueTbl tbody');
  tb.innerHTML = rows.map(v => `
    <tr>
      <td class="k">${v.name}</td>
      <td>${v.n}</td>
      <td class="num grp2">${pct(v.ex['1'])}</td>
      <td class="num grp2">${pct(v.ex['3'])}</td>
      <td class="num grp2">${pct(v.ex['5'])}</td>
      <td class="num grp3">${pct(v.tri['1'])}</td>
      <td class="barcell grp3"><span class="bar" style="width:${Math.round(v.tri['3']/maxTri3*70)}px"></span><span class="num">${pct(v.tri['3'])}</span></td>
      <td class="num grp3">${pct(v.tri['10'])}</td>
    </tr>`).join('');
}

document.querySelectorAll('#venueTbl th').forEach(th => {
  th.onclick = () => {
    const k = th.dataset.sort;
    if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = (k === 'name') ? 1 : -1; }
    render();
  };
});
selYear.onchange = render;
render();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
