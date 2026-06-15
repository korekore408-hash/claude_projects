# -*- coding: utf-8 -*-
"""
特徴量ビューア生成スクリプト
=========================================================================
features_player_history.csv（5.1 選手履歴）と features_race_relative.csv（5.5
レース内相対）を (race_id, 枠) で結合し、レース単位で 6 艇を一覧できる
単体 HTML（依存なし・ダブルクリックで開ける）を生成する。

使い方:
  py -3 build_viewer.py
  py -3 build_viewer.py --out viewer.html
"""

import argparse
import csv
import json
from collections import defaultdict


def load(path):
    with open(path, encoding="cp932") as f:
        return list(csv.DictReader(f))


def num(s):
    if s is None or s == "":
        return None
    try:
        f = float(s)
        return int(f) if f == int(f) else f
    except ValueError:
        return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", default="features_player_history.csv")
    ap.add_argument("--rel", default="features_race_relative.csv")
    ap.add_argument("--pred", default="predict_win.csv")
    ap.add_argument("--flags", default="race_flags.csv")
    ap.add_argument("--out", default="viewer.html")
    ap.add_argument("--last-days", type=int, default=0,
                    help="直近N日だけ出力（0=全期間）。iPhone表示はファイル肥大を避けて 14〜30 推奨")
    args = ap.parse_args()

    hist = {(r["race_id"], r["枠番"]): r for r in load(args.hist)}
    rel = load(args.rel)
    try:
        pred = {(r["race_id"], r["枠番"]): r for r in load(args.pred)}
    except FileNotFoundError:
        pred = {}
        print(f"! 予想 CSV が無いので予想欄は空になります: {args.pred}")
    try:
        flags = {r["race_id"]: r for r in load(args.flags)}
    except FileNotFoundError:
        flags = {}
        print(f"! フラグ CSV が無いので対象外表示は出ません: {args.flags}")

    races = {}
    for r in rel:
        rid = r["race_id"]
        h = hist.get((rid, r["枠番"]), {})
        pr = pred.get((rid, r["枠番"]), {})
        fl = flags.get(rid, {})
        race = races.setdefault(rid, {
            "id": rid,
            "date": r["日付"],
            "venue": r["会場"],
            "code": r["場コード"],
            "no": int(r["レース"]),
            "field_std": num(r["field_strength_std"]),
            "excluded": int(fl.get("excluded", 0) or 0),
            "reason": fl.get("reason", ""),
            "boats": [],
        })
        race["boats"].append({
            "枠": int(r["枠番"]),
            "登番": r["登番"],
            "名": r["選手名"],
            # 5.5 レース内相対
            "class_ord": num(r["class_ord"]),
            "class_gap": num(r["class_gap"]),
            "win_nat": num(r["win_rate_national"]),
            "win_rank": num(r["winrate_rank_in_race"]),
            "win_diff": num(r["winrate_diff_top"]),
            "motor2": num(r["motor_top2_rate"]),
            "motor_rank": num(r["motor_rank_in_race"]),
            "st_rank": num(r["st_rank_in_race"]),
            # 5.1 選手履歴
            "lane_win": num(h.get("lane_win_rate")),
            "lane_top3": num(h.get("lane_top3_rate")),
            "lane_n": num(h.get("lane_n")),
            "local_win": num(h.get("local_win_rate")),
            "local_n": num(h.get("local_n")),
            "venue1": num(h.get("venue_lane1_winrate")),
            "vown": num(h.get("venue_own_lane_winrate")),
            "mintr": num(h.get("motor_intrinsic_win")),
            "mintr_n": num(h.get("motor_intrinsic_n")),
            "flying": num(h.get("flying_rate")),
            "st_avg": num(h.get("st_avg")),
            "st_std": num(h.get("st_std")),
            "r30_win": num(h.get("recent30_winrate")),
            "r30_rank": num(h.get("recent30_avgrank")),
            "rN_win": num(h.get("recentN_winrate")),
            "n_used": num(h.get("n_races_used")),
            "low": num(h.get("is_low_sample")),
            # 予想
            "pwin": num(pr.get("p_win")),
            "str": num(pr.get("strength")),
            "fin": num(pr.get("finish_rank")),
        })

    data = [races[k] for k in sorted(races)]
    for race in data:
        race["boats"].sort(key=lambda b: b["枠"])

    # iPhone 表示はファイルが肥大すると開けないので直近 N 日に絞れるようにする。
    if args.last_days and data:
        from datetime import date as _date
        all_dates = sorted({r["date"] for r in data})
        keep = set(all_dates[-args.last_days:])
        data = [r for r in data if r["date"] in keep]

    n_races = len(data)
    n_rows = sum(len(r["boats"]) for r in data)
    dmin = min(r["date"] for r in data)
    dmax = max(r["date"] for r in data)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    html = HTML_TEMPLATE.replace("__DATA__", payload) \
                        .replace("__NRACES__", str(n_races)) \
                        .replace("__NROWS__", str(n_rows)) \
                        .replace("__DMIN__", dmin) \
                        .replace("__DMAX__", dmax)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"○ ビューア生成: {args.out}")
    print(f"  レース {n_races} / 行 {n_rows} / 期間 {dmin}〜{dmax}")
    print("  ダブルクリックでブラウザ表示できます。")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>競艇 特徴量ビューア（直前情報なしモデル）</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body { font-family: "Segoe UI", "Yu Gothic UI", system-ui, sans-serif;
         margin: 0; padding: 0 0 40px; background: #0f1115; color: #e6e6e6; }
  header { padding: 16px 20px; background: #161a22; border-bottom: 1px solid #2a2f3a;
           position: sticky; top: 0; z-index: 5; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .sub { font-size: 12px; color: #9aa3b2; }
  .controls { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; align-items: center; }
  /* font-size 16px 以上で iOS の入力時オートズームを抑止 */
  select { background: #0f1115; color: #e6e6e6; border: 1px solid #39404d;
           border-radius: 6px; padding: 8px 10px; font-size: 16px; }
  label { font-size: 12px; color: #9aa3b2; display: flex; flex-direction: column; gap: 3px; }
  .meta { font-size: 13px; color: #c9d1dc; margin: 14px 20px 6px; }
  .meta b { color: #fff; }
  .wrap { padding: 0 20px; overflow-x: auto; -webkit-overflow-scrolling: touch; }
  .scrollhint { display:none; font-size:11px; color:#7e8796; margin:6px 20px 0; }
  table { border-collapse: collapse; width: 100%; min-width: 1000px; font-size: 13px; }
  th, td { border: 1px solid #2a2f3a; padding: 5px 8px; text-align: right; white-space: nowrap; }
  th { background: #1b212b; color: #b9c2d0; position: sticky; top: 0; font-weight: 600; }
  td.k { text-align: left; }
  thead .grp th { background: #232a36; color: #8ea0ba; font-size: 11px; letter-spacing: .04em; }
  tbody tr:nth-child(odd) { background: #12161d; }
  .waku { font-weight: 700; color: #0f1115; border-radius: 4px; padding: 1px 7px; }
  .w1{background:#ffffff}.w2{background:#111;color:#fff}.w3{background:#e74c3c;color:#fff}
  .w4{background:#3498db;color:#fff}.w5{background:#f1c40f}.w6{background:#2ecc71;color:#111}
  .rank1 { color:#ffd54a; font-weight:700; }
  .muted { color:#6b7280; }
  .pill { font-size:11px; padding:1px 6px; border-radius:10px; background:#3a2a2a; color:#ff8f8f; }
  .legend { font-size:11px; color:#7e8796; margin:10px 20px; line-height:1.6; }
  .bar { display:inline-block; height:8px; background:#3b82f6; border-radius:2px; vertical-align:middle; }
  .pred { display:grid; grid-template-columns: 1.1fr 1fr 1fr; gap:14px; margin:6px 20px 4px; }
  @media (max-width:820px){ .pred{ grid-template-columns:1fr; } }
  .card { background:#12161d; border:1px solid #2a2f3a; border-radius:10px; padding:10px 12px; }
  .card h3 { margin:0 0 8px; font-size:12px; color:#9aa3b2; font-weight:600; letter-spacing:.04em; }
  .prow { display:flex; align-items:center; gap:8px; margin:3px 0; font-size:13px; }
  .pbar { height:9px; border-radius:3px; background:#3b82f6; min-width:2px; }
  .combo { font-variant-numeric:tabular-nums; }
  .hit { outline:2px solid #ffd54a; border-radius:4px; }
  .badge { font-size:10px; color:#ffd54a; border:1px solid #6b5a1a; border-radius:8px; padding:0 5px; margin-left:6px; }
  .result { margin:8px 20px 2px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; font-size:13px; }
  .result .lab { color:#9aa3b2; }
  .ok { color:#2ecc71; font-weight:700; } .ng { color:#e06b6b; }
  .arrow { color:#6b7280; margin:0 2px; }
  .res-fin1 { font-weight:700; color:#0f1115; }
  .comment { margin:8px 20px 2px; padding:9px 12px; background:#141a1f; border-left:3px solid #3b82f6;
             border-radius:0 6px 6px 0; font-size:13px; line-height:1.6; color:#cdd6e2; }
  .comment .h { color:#8ea0ba; font-size:11px; margin-right:6px; }
  .exclude { margin:8px 20px 0; padding:8px 12px; background:#2a2015; border:1px solid #6b5a1a;
             border-radius:6px; font-size:13px; color:#f0c674; }
  .exclude b { color:#ffd082; }

  /* ───────── iPhone / 狭幅画面向け ───────── */
  @media (max-width:600px) {
    body { padding-bottom: 24px; }
    header { padding: 10px 12px; }
    h1 { font-size: 16px; }
    h1 .sub { display:block; margin-top:2px; }
    .sub { font-size: 11px; }
    /* セレクタは3つを横並びのまま大きめタップ領域に */
    .controls { gap: 8px; margin-top: 10px; }
    .controls label { flex: 1 1 0; }
    .controls select { width: 100%; }
    .meta, .legend, .comment, .result, .exclude { margin-left: 12px; margin-right: 12px; }
    .meta { font-size: 12px; }
    /* 予想カードを主役に（1カラム・大きめ） */
    .pred { margin: 6px 12px 4px; gap: 10px; }
    .prow { font-size: 14px; }
    /* 特徴量テーブルは横スクロール。フォント/余白を詰めて指スクロール前提 */
    .wrap { padding: 0 12px; }
    table { min-width: 760px; font-size: 12px; }
    th, td { padding: 4px 6px; }
    .scrollhint { display:block; }
  }
</style>
</head>
<body>
<header>
  <h1>競艇 特徴量ビューア <span class="sub">直前情報なしモデル / 5.1 選手履歴 + 5.5 レース内相対</span></h1>
  <div class="sub">レース __NRACES__ ・ 行 __NROWS__ ・ 期間 __DMIN__ 〜 __DMAX__ ・ as-of（当日を含まない過去のみ） ・ <a href="summary.html" style="color:#6ea8fe;text-decoration:none">的中率サマリーへ →</a></div>
  <div class="controls">
    <label>日付<select id="selDate"></select></label>
    <label>場<select id="selVenue"></select></label>
    <label>レース<select id="selRace"></select></label>
  </div>
</header>

<div class="meta" id="meta"></div>
<div class="exclude" id="exclude"></div>
<div class="comment" id="comment"></div>
<div class="result" id="result"></div>
<div class="pred" id="pred"></div>
<div class="scrollhint">↔ 表は横スクロールできます</div>
<div class="wrap"><table id="tbl"></table></div>
<div class="legend">
  ※ <b>順位</b>系（win_rank/motor_rank/st_rank）は 1=最上位、黄色字が 1 位。<b>st_rank</b> は平均STが小さいほど上位。<br>
  ※ <b>lane_win/top3</b>=この選手のこの枠での as-of 勝率/3連対率（母数 lane_n）。<b>local_win</b>=この場での as-of 勝率（local_n）。<br>
  ※ <b>flying</b>=F率、<b>recent30</b>=直近30日、<b>recentN</b>=直近20走。空欄=母数不足。<span class="pill">low</span>=is_low_sample。
</div>

<script>
const DATA = __DATA__;
const byId = Object.fromEntries(DATA.map(r => [r.id, r]));

const selDate = document.getElementById('selDate');
const selVenue = document.getElementById('selVenue');
const selRace = document.getElementById('selRace');

const dates = [...new Set(DATA.map(r => r.date))].sort();
for (const d of dates) selDate.add(new Option(d, d));

function fillVenues() {
  const d = selDate.value;
  const vs = [...new Map(DATA.filter(r => r.date === d).map(r => [r.code, r.venue])).entries()]
             .sort((a,b) => a[0].localeCompare(b[0]));
  selVenue.innerHTML = '';
  for (const [code, name] of vs) selVenue.add(new Option(name, code));
}
function fillRaces() {
  const d = selDate.value, c = selVenue.value;
  const rs = DATA.filter(r => r.date === d && r.code === c).sort((a,b) => a.no - b.no);
  selRace.innerHTML = '';
  for (const r of rs) selRace.add(new Option(r.no + 'R', r.id));
}

const f = (v, dp=2) => (v === null || v === undefined || v === '') ? '<span class="muted">–</span>' : (typeof v === 'number' ? v.toFixed(dp) : v);
const fi = (v) => (v === null || v === undefined || v === '') ? '<span class="muted">–</span>' : v;
const rk = (v) => (v === 1) ? `<span class="rank1">${v}</span>` : fi(v);

const COLS = [
  ['基本', [['枠','waku'],['着','fin','fin'],['登番','登番'],['選手','名',1],['級','class_ord']]],
  ['実力（レース内相対 5.5）', [['全国勝率','win_nat'],['順','win_rank','r'],['トップ差','win_diff'],['級差','class_gap'],['場ばらつき','field_std_race']]],
  ['モーター', [['M2率','motor2'],['順','motor_rank','r']]],
  ['ST（5.1→5.5順位）', [['平均ST','st_avg',0,3],['STσ','st_std',0,3],['順','st_rank','r']]],
  ['枠成績（as-of 5.1）', [['枠勝率','lane_win'],['枠3連','lane_top3'],['n','lane_n','i']]],
  ['当地', [['当地勝率','local_win'],['n','local_n','i']]],
  ['場×枠/機力（5.3/5.2）', [['場枠1率','venue1'],['場自枠率','vown'],['機力素','mintr'],['n','mintr_n','i']]],
  ['調子', [['F率','flying'],['30日勝率','r30_win'],['30日平着','r30_rank'],['20走勝率','rN_win']]],
  ['母数', [['総走','n_used','i'],['','low','flag']]],
];

function render() {
  const race = byId[selRace.value];
  if (!race) return;
  document.getElementById('meta').innerHTML =
    `<b>${race.date}</b> ・ <b>${race.venue}</b>（${race.code}） <b>${race.no}R</b> ・ race_id <b>${race.id}</b> ・ field_strength_std <b>${f(race.field_std)}</b>`;

  renderExclude(race);
  renderComment(race);
  renderResult(race);
  renderPred(race);

  const tbl = document.getElementById('tbl');
  let head = '<thead><tr class="grp">';
  for (const [g, cols] of COLS) head += `<th colspan="${cols.length}">${g}</th>`;
  head += '</tr><tr>';
  for (const [, cols] of COLS) for (const c of cols) head += `<th>${c[0]}</th>`;
  head += '</tr></thead>';

  let body = '<tbody>';
  for (const b of race.boats) {
    body += '<tr>';
    for (const [, cols] of COLS) for (const c of cols) {
      const [label, key, mode, dp] = c;
      let v;
      if (key === 'waku') { body += `<td><span class="waku w${b['枠']}">${b['枠']}</span></td>`; continue; }
      if (key === 'field_std_race') { body += `<td>${f(race.field_std)}</td>`; continue; }
      v = b[key];
      if (mode === 1) body += `<td class="k">${fi(v)}</td>`;
      else if (mode === 'r') body += `<td>${rk(v)}</td>`;
      else if (mode === 'i') body += `<td>${fi(v)}</td>`;
      else if (mode === 'fin') body += `<td>${v === 1 ? '<span class="rank1">1</span>' : fi(v)}</td>`;
      else if (mode === 'flag') body += `<td>${v == 1 ? '<span class="pill">low</span>' : ''}</td>`;
      else body += `<td>${f(v, dp || 2)}</td>`;
    }
    body += '</tr>';
  }
  body += '</tbody>';
  tbl.innerHTML = head + body;
}

const WC = {1:'#ffffff',2:'#111',3:'#e74c3c',4:'#3498db',5:'#f1c40f',6:'#2ecc71'};
const WT = {1:'#111',2:'#fff',3:'#fff',4:'#fff',5:'#111',6:'#111'};
const chip = (w) => `<span class="waku w${w}" style="background:${WC[w]};color:${WT[w]}">${w}</span>`;

function plTop(strengths, kind, topk) {
  const idx = strengths.map((_, i) => i);
  const tot = strengths.reduce((a, b) => a + b, 0);
  const out = [];
  if (kind === 2) {
    for (const i of idx) for (const j of idx) if (j !== i) {
      const p = (strengths[i] / tot) * (strengths[j] / (tot - strengths[i]));
      out.push([[i + 1, j + 1], p]);
    }
  } else {
    for (const i of idx) for (const j of idx) if (j !== i) for (const k of idx) if (k !== i && k !== j) {
      const p = (strengths[i] / tot) * (strengths[j] / (tot - strengths[i]))
              * (strengths[k] / (tot - strengths[i] - strengths[j]));
      out.push([[i + 1, j + 1, k + 1], p]);
    }
  }
  out.sort((a, b) => b[1] - a[1]);
  return out.slice(0, topk);
}

function renderExclude(race) {
  const el = document.getElementById('exclude');
  if (race.excluded) {
    el.style.display = '';
    el.innerHTML = `<b>対象外レース</b> ― ${race.reason}。本番で予想の前提が崩れた／レースが乱れたため、的中率の集計から除外しています。`;
  } else {
    el.style.display = 'none';
    el.innerHTML = '';
  }
}

function renderComment(race) {
  const el = document.getElementById('comment');
  const boats = race.boats.slice();
  const haveP = boats.every(b => typeof b.pwin === 'number');
  if (!haveP) { el.style.display = 'none'; return; }
  el.style.display = '';
  boats.sort((a, b) => b.pwin - a.pwin);
  const t1 = boats[0], t2 = boats[1];

  const reasons = (b) => {
    const r = [];
    if (b['枠'] <= 2) r.push(b['枠'] === 1 ? 'イン最有利' : '好枠');
    if (b.win_rank === 1) r.push('全国勝率トップ');
    if (b.motor_rank === 1) r.push('機力レース内1位');
    if (b.st_rank === 1) r.push('平均ST最速');
    if (typeof b.lane_win === 'number' && b.lane_win >= 0.5 && b.lane_n >= 3) r.push('枠成績良好');
    if (typeof b.vown === 'number' && b['枠'] === 1 && b.vown >= 0.55) r.push('当場イン強い');
    if (!r.length) {
      if (typeof b.win_rank === 'number' && b.win_rank <= 2) r.push('実力上位');
      else r.push('総合力で上位');
    }
    return r.slice(0, 2);
  };

  const strong = t1.pwin >= 0.5;
  const tight = typeof race.field_std === 'number' && race.field_std < 0.7;
  const line1 = `◎${t1['枠']}号艇 ${t1['名']}：${reasons(t1).join('・')}で1着確率${(t1.pwin*100).toFixed(0)}%。`;
  const line2 = `相手本線は${t2['枠']}号艇（${reasons(t2).join('・')}）。`
    + (strong ? '本命濃厚の構成。' : (tight ? '実力拮抗で波乱含み。' : '中穴も一考。'));
  el.innerHTML = `<span class="h">予想コメント</span>${line1}<br><span class="h" style="visibility:hidden">予想コメント</span>${line2}`;
}

function renderResult(race) {
  const el = document.getElementById('result');
  const fin = {};
  race.boats.forEach(b => { if (b.fin) fin[b.fin] = b['枠']; });
  const order = Object.keys(fin).map(Number).sort((a, b) => a - b);
  if (!order.length) { el.innerHTML = '<span class="lab">結果</span><span class="muted">着順データなし</span>'; return; }

  let chips = order.map(rk => chip(fin[rk])).join('<span class="arrow">→</span>');
  let html = `<span class="lab">結果（着順）</span>${chips}`;

  const haveStr = race.boats.every(b => typeof b.str === 'number');
  if (haveStr) {
    const byWaku = {}; race.boats.forEach(b => byWaku[b['枠']] = b);
    const strengths = [1,2,3,4,5,6].map(w => (byWaku[w] ? byWaku[w].str : 0));
    const eq = (a, c) => a && c && a.length === c.length && a.every((v, i) => v === c[i]);
    const topEx = plTop(strengths, 2, 1)[0][0];
    const topTri = plTop(strengths, 3, 1)[0][0];
    const actEx = (fin[1] && fin[2]) ? [fin[1], fin[2]] : null;
    const actTri = (fin[1] && fin[2] && fin[3]) ? [fin[1], fin[2], fin[3]] : null;
    const mark = (ok) => ok ? '<span class="ok">的中</span>' : '<span class="ng">不的中</span>';
    if (actEx) html += `<span class="lab" style="margin-left:14px">本命2連単</span>${chip(topEx[0])}<span class="arrow">→</span>${chip(topEx[1])} ${mark(eq(topEx, actEx))}`;
    if (actTri) html += `<span class="lab" style="margin-left:14px">本命3連単</span>${chip(topTri[0])}<span class="arrow">→</span>${chip(topTri[1])}<span class="arrow">→</span>${chip(topTri[2])} ${mark(eq(topTri, actTri))}`;
  }
  el.innerHTML = html;
}

function renderPred(race) {
  const el = document.getElementById('pred');
  const boats = race.boats;
  const haveStr = boats.every(b => typeof b.str === 'number');
  if (!haveStr) { el.innerHTML = '<div class="card"><h3>予想</h3><span class="muted">この期間は予想データがありません</span></div>'; return; }

  const byWaku = {}; boats.forEach(b => byWaku[b['枠']] = b);
  const strengths = [1,2,3,4,5,6].map(w => (byWaku[w] ? byWaku[w].str : 0));
  const pwin = [1,2,3,4,5,6].map(w => (byWaku[w] ? byWaku[w].pwin : 0));

  const fin = {}; boats.forEach(b => { if (b.fin) fin[b.fin] = b['枠']; });
  const actEx = (fin[1] && fin[2]) ? [fin[1], fin[2]] : null;
  const actTri = (fin[1] && fin[2] && fin[3]) ? [fin[1], fin[2], fin[3]] : null;
  const eq = (a, c) => a && c && a.length === c.length && a.every((v, i) => v === c[i]);

  const pmax = Math.max(...pwin);
  let win = '<div class="card"><h3>1着確率（モデル）</h3>';
  for (let w = 1; w <= 6; w++) {
    win += `<div class="prow">${chip(w)}<div class="pbar" style="width:${Math.round(pwin[w-1]/pmax*120)}px;background:${WC[w]==='#ffffff'?'#9aa3b2':WC[w]}"></div>`
         + `<span class="combo">${(pwin[w-1]*100).toFixed(1)}%</span>`
         + `${fin[1]===w?'<span class="badge">的中1着</span>':''}</div>`;
  }
  win += '</div>';

  const ex = plTop(strengths, 2, 6);
  let exH = `<div class="card"><h3>2連単 上位${actEx?' ・ 実際 '+actEx[0]+'-'+actEx[1]:''}</h3>`;
  for (const [c, p] of ex) {
    const hit = eq(actEx, c);
    exH += `<div class="prow${hit?' hit':''}"><span class="combo">${chip(c[0])}→${chip(c[1])}</span>`
         + `<span class="combo" style="margin-left:auto">${(p*100).toFixed(1)}%</span></div>`;
  }
  exH += '</div>';

  const tri = plTop(strengths, 3, 6);
  let triH = `<div class="card"><h3>3連単 上位${actTri?' ・ 実際 '+actTri.join('-'):''}</h3>`;
  for (const [c, p] of tri) {
    const hit = eq(actTri, c);
    triH += `<div class="prow${hit?' hit':''}"><span class="combo">${chip(c[0])}→${chip(c[1])}→${chip(c[2])}</span>`
          + `<span class="combo" style="margin-left:auto">${(p*100).toFixed(1)}%</span></div>`;
  }
  triH += '</div>';

  el.innerHTML = win + exH + triH;
}

selDate.onchange = () => { fillVenues(); fillRaces(); render(); };
selVenue.onchange = () => { fillRaces(); render(); };
selRace.onchange = render;

fillVenues(); fillRaces(); render();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
