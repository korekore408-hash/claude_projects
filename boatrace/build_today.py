# -*- coding: utf-8 -*-
"""
当日予想 メイン画面（携帯向け・単体HTML）
=========================================================================
predict_win.csv（各艇 strength / p_win）と features_race_relative.csv（会場・
選手名・日付・field_maezuke_flag）から、指定日（既定=データ最新日＝当日）の
全レースを「会場ごとの一覧」で表示する。各レース:
  ◎本命（1着確率最大の枠）・確率・2連単/3連単 本命（Plackett-Luce）。
携帯で開く前提のモバイル1カラム設計。タップで各レースのビューア詳細へ。

使い方:
  py -3 build_today.py                      # 最新日（当日）
  py -3 build_today.py --date 2026-06-16
  py -3 build_today.py --out today.html
"""

import argparse
import csv
import json
from collections import defaultdict


def load(path):
    with open(path, encoding="cp932") as f:
        return list(csv.DictReader(f))


def num(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def pl_top1(strengths, kind):
    """strengths(len6, 1始まり順) から 2/3連単の本命(最尤)組を返す。"""
    idx = [i for i in range(6) if strengths[i] > 0]
    tot = sum(strengths)
    best, bestp = None, -1.0
    if kind == 2:
        for i in idx:
            for j in idx:
                if j == i:
                    continue
                p = strengths[i] / tot * strengths[j] / (tot - strengths[i])
                if p > bestp:
                    best, bestp = (i + 1, j + 1), p
    else:
        for i in idx:
            for j in idx:
                if j == i:
                    continue
                for k in idx:
                    if k in (i, j):
                        continue
                    p = (strengths[i] / tot * strengths[j] / (tot - strengths[i])
                         * strengths[k] / (tot - strengths[i] - strengths[j]))
                    if p > bestp:
                        best, bestp = (i + 1, j + 1, k + 1), p
    return best, bestp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default="predict_win.csv")
    ap.add_argument("--rel", default="features_race_relative.csv")
    ap.add_argument("--flags", default="race_flags.csv")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（既定=データ最新日）")
    ap.add_argument("--out", default="today.html")
    args = ap.parse_args()

    rel = load(args.rel)
    pred = {(r["race_id"], r["枠番"]): r for r in load(args.pred)}
    try:
        flags = {r["race_id"]: r for r in load(args.flags)}
    except FileNotFoundError:
        flags = {}

    target = args.date or max(r["日付"] for r in rel)

    # race_id -> {venue, code, no, boats:[{枠,名,p,str,fin}], maezuke}
    races = {}
    for r in rel:
        if r["日付"] != target:
            continue
        rid = r["race_id"]
        pr = pred.get((rid, r["枠番"]), {})
        race = races.setdefault(rid, {
            "venue": r["会場"], "code": r["場コード"], "no": int(r["レース"]),
            "maezuke": int(r.get("field_maezuke_flag", 0) or 0),
            "boats": [],
        })
        race["boats"].append({
            "枠": int(r["枠番"]), "名": r["選手名"],
            "p": num(pr.get("p_win")), "str": num(pr.get("strength")),
            "fin": pr.get("finish_rank", ""),
        })

    # 会場→レース一覧（番号順）を構築し、本命と連単本命を計算。
    venues = defaultdict(list)
    for rid, rc in races.items():
        boats = sorted(rc["boats"], key=lambda b: b["枠"])
        have = all(isinstance(b["str"], float) for b in boats) and len(boats) == 6
        rec = {"no": rc["no"], "venue": rc["venue"], "code": rc["code"],
               "maezuke": rc["maezuke"], "id": rid}
        if have:
            byw = {b["枠"]: b for b in boats}
            strv = [byw[w]["str"] for w in range(1, 7)]
            top = max(boats, key=lambda b: b["p"])
            ex, _ = pl_top1(strv, 2)
            tri, _ = pl_top1(strv, 3)
            rec.update({"honmei": top["枠"], "honmei_name": top["名"],
                        "pwin": top["p"], "ex": ex, "tri": tri})
        venues[(rc["code"], rc["venue"])].append(rec)
    for v in venues.values():
        v.sort(key=lambda x: x["no"])

    payload = {
        "date": target,
        "n_races": len(races),
        "venues": [{"code": c, "venue": v, "races": rs}
                   for (c, v), rs in sorted(venues.items())],
    }
    html = HTML.replace("__DATA__", json.dumps(payload, ensure_ascii=False,
                                               separators=(",", ":")))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"○ 当日予想ページ: {args.out}")
    print(f"  日付 {target} / 会場 {len(venues)} / レース {len(races)}")


HTML = r"""<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>今日の予想</title>
<style>
  :root{color-scheme:dark light}
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{font-family:"Yu Gothic UI",system-ui,sans-serif;margin:0;padding:0 0 40px;
       background:#0f1115;color:#e6e6e6}
  header{padding:14px 16px;background:#161a22;border-bottom:1px solid #2a2f3a;
         position:sticky;top:0;z-index:5}
  h1{font-size:18px;margin:0}
  .sub{font-size:12px;color:#9aa3b2;margin-top:3px}
  .vsel{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap}
  .vsel button{background:#0f1115;color:#cdd6e2;border:1px solid #39404d;border-radius:14px;
       padding:6px 12px;font-size:14px}
  .vsel button.on{background:#2b6cb0;color:#fff;border-color:#2b6cb0}
  section{padding:6px 14px}
  h2{font-size:15px;color:#b9c2d0;margin:16px 0 8px}
  .race{display:flex;align-items:center;gap:10px;padding:9px 10px;margin:6px 0;
        background:#12161d;border:1px solid #2a2f3a;border-radius:10px}
  .rno{font-weight:700;font-size:14px;color:#9aa3b2;min-width:34px}
  .pick{display:flex;align-items:center;gap:7px;flex:1;min-width:0}
  .waku{font-weight:700;border-radius:5px;padding:2px 9px;font-size:15px}
  .w1{background:#fff;color:#111}.w2{background:#111;color:#fff}.w3{background:#e74c3c;color:#fff}
  .w4{background:#3498db;color:#fff}.w5{background:#f1c40f;color:#111}.w6{background:#2ecc71;color:#111}
  .nm{font-size:13px;color:#e6e6e6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .pw{font-size:13px;font-weight:700;font-variant-numeric:tabular-nums;margin-left:auto;white-space:nowrap}
  .pw.strong{color:#ffd54a}
  .combo{font-size:11px;color:#8ea0ba;font-variant-numeric:tabular-nums;white-space:nowrap;margin-left:4px}
  .warn{font-size:10px;color:#e0b0e0;border:1px solid #6b4a6b;border-radius:8px;padding:0 5px;margin-left:4px}
  .chip{font-weight:700;border-radius:4px;padding:0 6px;font-size:12px}
  .empty{color:#6b7280;font-size:12px}
  .legend{font-size:11px;color:#7e8796;margin:14px 16px;line-height:1.6}
</style></head><body>
<div id="app"></div>
<script>
const D=__DATA__;
const WC={1:'#fff',2:'#111',3:'#e74c3c',4:'#3498db',5:'#f1c40f',6:'#2ecc71'};
const WT={1:'#111',2:'#fff',3:'#fff',4:'#fff',5:'#111',6:'#111'};
const chip=(w)=>`<span class="chip" style="background:${WC[w]};color:${WT[w]}">${w}</span>`;
const waku=(w,t)=>`<span class="waku w${w}">${w}</span><span class="nm">${t}</span>`;
let cur='ALL';

function combo(arr){return arr?arr.map(chip).join('<span style="color:#6b7280">→</span>'):'';}

function render(){
  const app=document.getElementById('app');
  let vbtns=`<button class="${cur==='ALL'?'on':''}" data-v="ALL">全場</button>`;
  for(const v of D.venues) vbtns+=`<button class="${cur===v.code?'on':''}" data-v="${v.code}">${v.venue}</button>`;
  let body='';
  for(const v of D.venues){
    if(cur!=='ALL'&&cur!==v.code) continue;
    body+=`<section><h2>${v.venue}</h2>`;
    for(const r of v.races){
      if(r.honmei){
        const strong=r.pwin>=0.5;
        body+=`<div class="race"><span class="rno">${r.no}R</span>`
          +`<span class="pick">${waku(r.honmei,r.honmei_name)}`
          +(r.maezuke?`<span class="warn">警戒</span>`:'')+`</span>`
          +`<span class="pw ${strong?'strong':''}">${(r.pwin*100).toFixed(0)}%</span>`
          +`<span class="combo">${combo(r.tri)}</span></div>`;
      }else{
        body+=`<div class="race"><span class="rno">${r.no}R</span>`
          +`<span class="empty">予想データなし</span></div>`;
      }
    }
    body+='</section>';
  }
  app.innerHTML=`<header><h1>今日の予想 <span class="sub">${D.date}</span></h1>`
    +`<div class="sub">直前情報なしモデル ・ 会場 ${D.venues.length} ・ レース ${D.n_races} ・ ◎=1着確率最大 / 数字=1着確率 / →=3連単本命</div>`
    +`<div class="vsel">${vbtns}</div></header>${body}`
    +`<div class="legend">※ 確率は朝の出走表のみから算出（展示・オッズ不使用）。`
    +`<span class="warn">警戒</span>=前づけ常習者あり＝隊形が崩れやすく当てにくい。`
    +`黄字=本命確率50%以上。</div>`;
  for(const b of document.querySelectorAll('.vsel button'))
    b.onclick=()=>{cur=b.dataset.v;render();window.scrollTo(0,0);};
}
render();
</script></body></html>"""


if __name__ == "__main__":
    main()
