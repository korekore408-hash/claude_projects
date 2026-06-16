# -*- coding: utf-8 -*-
"""
当日予想 メイン画面（携帯向け・単体HTML・一覧⇔詳細アプリ）
=========================================================================
当日・前日・前々日の3日分を切替表示（既定=当日）。直前情報なしモデルの
1着確率から 2連単 上位5 / 3連単 上位10 を Plackett-Luce で算出。
前日・前々日は結果（着順・的中判定）まで表示する。

入力: predict_win.csv（p_win, finish_rank）/ features_race_relative.csv
出力: today.html（全レース詳細でも軽量・自己完結）

使い方:
  py -3 build_today.py                  # データ最新日から3日分
  py -3 build_today.py --date 2026-06-16 --days 3
"""

import argparse
import csv
import json


def load(path):
    with open(path, encoding="cp932") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default="predict_win.csv")
    ap.add_argument("--rel", default="features_race_relative.csv")
    ap.add_argument("--date", default=None, help="基準日（当日）。既定=データ最新日")
    ap.add_argument("--days", type=int, default=3, help="さかのぼる日数（既定3=当日/前日/前々日）")
    ap.add_argument("--out", default="today.html")
    args = ap.parse_args()

    rel = load(args.rel)
    pred = {(r["race_id"], r["枠番"]): r for r in load(args.pred)}

    all_dates = sorted({r["日付"] for r in rel})
    base = args.date or all_dates[-1]
    keep = [d for d in all_dates if d <= base][-args.days:]
    keep_set = set(keep)

    # race_id -> {d,c,v,no,mz, b{枠:(名,pm,fin)}}
    races = {}
    for r in rel:
        if r["日付"] not in keep_set:
            continue
        rid = r["race_id"]
        pr = pred.get((rid, r["枠番"]), {})
        try:
            pm = round(float(pr.get("p_win")) * 1000)
        except (TypeError, ValueError):
            pm = None
        try:
            fin = int(pr.get("finish_rank"))
        except (TypeError, ValueError):
            fin = None
        rc = races.setdefault(rid, {"d": r["日付"], "c": r["場コード"], "v": r["会場"],
                                    "no": int(r["レース"]),
                                    "mz": int(r.get("field_maezuke_flag", 0) or 0),
                                    "b": {}})
        rc["b"][int(r["枠番"])] = (r["選手名"], pm, fin)

    out = []
    for rid, rc in races.items():
        if len(rc["b"]) != 6 or any(rc["b"][w][1] is None for w in range(1, 7)):
            continue
        out.append({"d": rc["d"], "c": rc["c"], "v": rc["v"], "no": rc["no"],
                    "mz": rc["mz"],
                    "b": [[rc["b"][w][0], rc["b"][w][1], rc["b"][w][2]]
                          for w in range(1, 7)]})
    out.sort(key=lambda x: (x["d"], x["c"], x["no"]))

    # 日付ラベル（当日/前日/前々日）
    rel_labels = ["当日", "前日", "前々日", "3日前", "4日前", "5日前", "6日前"]
    labels = []
    for i, d in enumerate(reversed(keep)):       # 新しい順
        labels.append([rel_labels[i] if i < len(rel_labels) else d, d])

    payload = {"labels": labels, "base": base, "races": out}
    html = HTML.replace("__DATA__", json.dumps(payload, ensure_ascii=False,
                                               separators=(",", ":")))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"○ 当日予想アプリ: {args.out}")
    print(f"  対象日 {keep}（既定表示={base}）/ レース {len(out)}")


HTML = r"""<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>競艇 予想</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{font-family:"Yu Gothic UI",system-ui,sans-serif;margin:0 auto;padding:12px 14px 40px;
       background:#0f1115;color:#e6e6e6;max-width:560px}
  h1{font-size:18px;margin:0}
  .meta{font-size:12px;color:#9aa3b2;margin:4px 0 8px;line-height:1.5}
  .dsel{display:flex;gap:6px;margin:0 0 10px}
  .dbtn{flex:1;font-size:14px;padding:8px 6px;border-radius:8px;border:0.5px solid #39404d;
        background:transparent;color:#cdd6e2;cursor:pointer;text-align:center}
  .dbtn.on{background:#2b6cb0;color:#fff;border-color:#2b6cb0}
  .dbtn small{display:block;font-size:10px;color:#9aa3b2;margin-top:1px}
  .dbtn.on small{color:#cfe2ff}
  .vfilter{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 6px}
  .vbtn{font-size:14px;padding:6px 12px;border-radius:8px;border:0.5px solid #39404d;
        background:transparent;color:#cdd6e2;cursor:pointer}
  .vbtn.on{background:#374151;color:#fff;border-color:#4b5563}
  h3{font-size:15px;font-weight:600;margin:14px 0 4px;color:#b9c2d0}
  .row{display:flex;align-items:center;gap:8px;padding:10px 4px;border-bottom:0.5px solid #2a2f3a;cursor:pointer}
  .row:active{background:#12161d}
  .rno{font-size:13px;color:#9aa3b2;min-width:30px;font-weight:600}
  .wk{font-weight:700;border-radius:5px;padding:1px 8px;font-size:14px;min-width:24px;text-align:center;display:inline-block;border:0.5px solid #2a2f3a}
  .nm{font-size:13px;color:#e6e6e6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}
  .wn{color:#f0c674;font-size:14px}
  .pw{font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap;color:#9aa3b2}
  .pw.s{color:#ffd54a;font-weight:700}
  .res{font-size:12px;white-space:nowrap;display:flex;align-items:center;gap:3px}
  .ok{color:#43c59e;font-weight:700}.ng{color:#e06b6b}
  .chev{color:#5b6472;font-size:16px}
  .back{display:inline-flex;align-items:center;gap:4px;font-size:14px;color:#6ea8fe;background:transparent;border:none;cursor:pointer;padding:8px 0}
  .dh{font-size:19px;font-weight:700;margin:2px 0}
  .warn{font-size:12px;color:#f0c674;background:#2a2015;border:1px solid #6b5a1a;border-radius:8px;padding:7px 10px;margin:6px 0;line-height:1.5}
  .rbar{display:flex;align-items:center;gap:6px;margin:8px 0;flex-wrap:wrap}
  .rlab{font-size:12px;color:#9aa3b2}
  .arr{color:#5b6472;font-size:12px}
  .boat{display:flex;align-items:center;gap:8px;margin:8px 0}
  .fin{font-size:11px;color:#9aa3b2;min-width:26px}
  .fin b{color:#ffd54a}
  .bn{font-size:13px;color:#e6e6e6;min-width:78px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .barw{flex:1;height:15px;background:#1b212b;border-radius:4px;overflow:hidden}
  .bar{height:100%;border-radius:4px}
  .bp{font-size:12px;font-variant-numeric:tabular-nums;color:#cdd6e2;min-width:40px;text-align:right}
  .sec{font-size:13px;font-weight:600;color:#9aa3b2;margin:16px 0 6px;display:flex;align-items:center;gap:8px}
  .tag{font-size:11px;border-radius:8px;padding:1px 7px}
  .tag.h{background:#10362c;color:#43c59e}.tag.m{background:#3a1f1f;color:#e06b6b}
  .crow{display:flex;align-items:center;gap:6px;padding:6px 2px;border-bottom:0.5px solid #2a2f3a}
  .crow.hit{background:#10362c;border-radius:5px}
  .mc{font-size:13px;border-radius:4px;padding:1px 7px;border:0.5px solid #2a2f3a;font-weight:700;display:inline-block}
  .rk{font-size:11px;color:#5b6472;min-width:20px}
  .cp{margin-left:auto;font-size:12px;font-variant-numeric:tabular-nums;color:#9aa3b2}
  .legend{font-size:11px;color:#7e8796;margin:18px 0 0;line-height:1.6}
  .tabs{display:flex;gap:6px;margin:6px 0 10px}
  .tb{flex:1;font-size:14px;padding:8px 6px;border-radius:8px;border:0.5px solid #39404d;background:transparent;color:#cdd6e2;cursor:pointer;text-align:center}
  .tb.on{background:#2b6cb0;color:#fff;border-color:#2b6cb0}
  .swrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:4px}
  table.st{border-collapse:collapse;width:100%;min-width:520px;font-size:13px}
  table.st th,table.st td{border-bottom:0.5px solid #232a36;padding:7px 8px;text-align:right;white-space:nowrap}
  table.st th{color:#8ea0ba;font-weight:600;position:sticky;top:0;background:#12161d}
  table.st td.k,table.st th.k{text-align:left}
  table.st .g2{color:#7fb2ff}.st .g3{color:#ffd082}
  table.st tbody tr:nth-child(odd){background:#12161d}
  table.st tr.all{font-weight:700}
  table.st tr.all td{border-top:1px solid #39404d;color:#fff}
  .num{font-variant-numeric:tabular-nums}
  .scol{color:#9aa3b2}
</style></head><body>
<div id="app"></div>
<script>
const D=__DATA__;
const LC={1:['#ffffff','#111111'],2:['#1b1b1b','#ffffff'],3:['#e23b3b','#ffffff'],4:['#2f7fd6','#ffffff'],5:['#f2c025','#111111'],6:['#28a35a','#ffffff']};
let selDate=D.labels[0][1], cur='ALL', sel=null, tab='pred';
const root=document.getElementById('app');
const mmdd=s=>s.slice(5);
function chip(w,cls){const a=LC[w];return '<span class="'+(cls||'wk')+'" style="background:'+a[0]+';color:'+a[1]+'">'+w+'</span>';}
function dayRaces(){return D.races.filter(r=>r.d===selDate);}
function hasResult(r){return r.b.every(x=>x[2]!==null&&x[2]>=1);}
function finishOrder(r){return r.b.map((b,i)=>[i+1,b[2]]).filter(x=>x[1]).sort((a,b)=>a[1]-b[1]).map(x=>x[0]);}
function eqArr(a,b){return a&&b&&a.length===b.length&&a.every((v,i)=>v===b[i]);}
function plTop(s,kind,k){const idx=[0,1,2,3,4,5].filter(i=>s[i]>0);const tot=s.reduce((a,b)=>a+b,0);const out=[];
  if(kind===2){for(const i of idx)for(const j of idx){if(j===i)continue;out.push([[i+1,j+1],s[i]/tot*s[j]/(tot-s[i])]);}}
  else{for(const i of idx)for(const j of idx){if(j===i)continue;for(const l of idx){if(l===i||l===j)continue;out.push([[i+1,j+1,l+1],s[i]/tot*s[j]/(tot-s[i])*s[l]/(tot-s[i]-s[j])]);}}}
  out.sort((a,b)=>b[1]-a[1]);return out.slice(0,k);}

function listView(){
  const rs=dayRaces();
  const lab=D.labels.find(l=>l[1]===selDate);
  let h='<div class="dsel">';
  for(const l of D.labels)h+='<button class="dbtn'+(selDate===l[1]?' on':'')+'" data-d="'+l[1]+'">'+l[0]+'<small>'+mmdd(l[1])+'</small></button>';
  h+='</div>';
  h+='<div class="meta">直前情報なしモデル（朝の出走表のみ）・ '+rs.length+'レース ・ タップで詳細'
    +(hasResult(rs[0]||{b:[]})?' ・ 結果あり（◎的中=本命1着）':'')+'</div>';
  const venues=[];const seen={};for(const r of rs){if(!seen[r.c]){seen[r.c]=1;venues.push([r.c,r.v]);}}
  h+='<div class="vfilter"><button class="vbtn'+(cur==='ALL'?' on':'')+'" data-v="ALL">全場</button>';
  for(const a of venues)h+='<button class="vbtn'+(cur===a[0]?' on':'')+'" data-v="'+a[0]+'">'+a[1]+'</button>';
  h+='</div>';
  rs.forEach((r,gi)=>{
    if(cur!=='ALL'&&cur!==r.c)return;
    if(rs.findIndex(x=>x.c===r.c)===gi)h+='<h3>'+r.v+'</h3>';
    let hm=0;for(let w=1;w<6;w++)if(r.b[w][1]>r.b[hm][1])hm=w;
    const pw=Math.round(r.b[hm][1]/10);
    h+='<div class="row" data-i="'+gi+'"><span class="rno">'+r.no+'R</span>'+chip(hm+1)
     +'<span class="nm">'+r.b[hm][0]+'</span>'+(r.mz?'<span class="wn">&#9888;</span>':'');
    if(hasResult(r)){
      const ord=finishOrder(r);const win=ord[0];
      h+='<span class="res">'+chip(win,'mc')+(win===hm+1?'<span class="ok">的中</span>':'<span class="ng">×</span>')+'</span>';
    }else{
      h+='<span class="pw'+(pw>=50?' s':'')+'">'+pw+'%</span>';
    }
    h+='<span class="chev">&rsaquo;</span></div>';
  });
  return h;
}

function detailView(r){
  const s=r.b.map(x=>x[1]);const mx=Math.max(...s);
  const done=hasResult(r);const ord=done?finishOrder(r):null;
  const actEx=done?ord.slice(0,2):null, actTri=done?ord.slice(0,3):null;
  let hm=0;for(let w=1;w<6;w++)if(s[w]>s[hm])hm=w;
  let h='<button class="back">&lsaquo; 一覧へ戻る</button>';
  h+='<div class="dh">'+r.v+' '+r.no+'R</div><div class="meta">'+r.d+' ・ 直前情報なしモデル（展示/オッズ不使用）</div>';
  if(r.mz)h+='<div class="warn">&#9888; 隊形警戒：前づけ常習者がいて枠なりが崩れやすく、本命の信頼度は割り引いて。</div>';
  if(done){
    h+='<div class="rbar"><span class="rlab">結果</span>';
    ord.forEach((w,i)=>{h+=(i?'<span class="arr">&rarr;</span>':'')+chip(w,'mc');});
    h+='<span style="margin-left:6px">'+(ord[0]===hm+1?'<span class="ok">◎的中</span>':'<span class="ng">◎不的中</span>')+'</span></div>';
  }
  h+='<div class="sec">1着確率（モデル）</div>';
  r.b.forEach((b,w)=>{const a=LC[w+1];const fin=b[2];
    h+='<div class="boat">'+(done?'<span class="fin">'+(fin===1?'<b>1着</b>':fin+'着')+'</span>':'')
     +chip(w+1)+'<span class="bn">'+b[0]+'</span>'
     +'<div class="barw"><div class="bar" style="width:'+Math.max(b[1]/mx*100,2)+'%;background:'+a[0]+'"></div></div>'
     +'<span class="bp">'+(b[1]/10).toFixed(1)+'%</span></div>';});
  // 2連単 上位5
  const ex=plTop(s,2,5);
  let exHit=actEx?ex.some(c=>eqArr(c[0],actEx)):false;
  h+='<div class="sec">2連単 上位5'+(actEx?(exHit?'<span class="tag h">的中</span>':'<span class="tag m">圏外</span>'):'')+'</div>';
  ex.forEach((c,i)=>{const hit=actEx&&eqArr(c[0],actEx);
    h+='<div class="crow'+(hit?' hit':'')+'"><span class="rk">'+(i+1)+'</span>'+chip(c[0][0],'mc')+'<span class="arr">&rarr;</span>'+chip(c[0][1],'mc')
     +(hit?'<span class="ok" style="font-size:11px;margin-left:4px">的中</span>':'')+'<span class="cp">'+(c[1]*100).toFixed(1)+'%</span></div>';});
  if(actEx&&!exHit){h+='<div class="crow"><span class="rk">実</span>'+chip(actEx[0],'mc')+'<span class="arr">&rarr;</span>'+chip(actEx[1],'mc')+'<span class="cp ng">実際の結果</span></div>';}
  // 3連単 上位10
  const tri=plTop(s,3,10);
  let triHit=actTri?tri.some(c=>eqArr(c[0],actTri)):false;
  h+='<div class="sec">3連単 上位10'+(actTri?(triHit?'<span class="tag h">的中</span>':'<span class="tag m">圏外</span>'):'')+'</div>';
  tri.forEach((c,i)=>{const hit=actTri&&eqArr(c[0],actTri);
    h+='<div class="crow'+(hit?' hit':'')+'"><span class="rk">'+(i+1)+'</span>'+chip(c[0][0],'mc')+'<span class="arr">&rarr;</span>'+chip(c[0][1],'mc')+'<span class="arr">&rarr;</span>'+chip(c[0][2],'mc')
     +(hit?'<span class="ok" style="font-size:11px;margin-left:4px">的中</span>':'')+'<span class="cp">'+(c[1]*100).toFixed(1)+'%</span></div>';});
  if(actTri&&!triHit){h+='<div class="crow"><span class="rk">実</span>'+chip(actTri[0],'mc')+'<span class="arr">&rarr;</span>'+chip(actTri[1],'mc')+'<span class="arr">&rarr;</span>'+chip(actTri[2],'mc')+'<span class="cp ng">実際の結果</span></div>';}
  h+='<div class="legend">※ 確率は朝の出走表のみから算出（展示・オッズ不使用）。本命=1着確率最大の枠。前日・前々日は結果と的中可否を表示。</div>';
  return h;
}

function nav(){
  return '<h1>競艇 予想</h1><div class="tabs">'
    +'<button class="tb'+(tab==='pred'?' on':'')+'" data-t="pred">予想</button>'
    +'<button class="tb'+(tab==='stats'?' on':'')+'" data-t="stats">場別成績</button></div>';
}
function statsView(){
  // today.html が持つ日付範囲のうち、結果のあるレースから場別的中率を集計。
  const done=D.races.filter(hasResult);
  const ds=[...new Set(done.map(r=>r.d))].sort();
  const agg={};
  for(const r of done){
    const s=r.b.map(x=>x[1]);const ord=finishOrder(r);
    let hm=0;for(let w=1;w<6;w++)if(s[w]>s[hm])hm=w;
    const ex=plTop(s,2,5).map(c=>c[0]),tri=plTop(s,3,10).map(c=>c[0]);
    const aEx=ord.slice(0,2),aTri=ord.slice(0,3);
    const a=agg[r.c]||(agg[r.c]={v:r.v,n:0,win:0,e1:0,e3:0,e5:0,t1:0,t3:0,t10:0});
    a.n++; a.win+=(hm+1===ord[0]);
    a.e1+=eqArr(ex[0],aEx); a.e3+=ex.slice(0,3).some(c=>eqArr(c,aEx)); a.e5+=ex.some(c=>eqArr(c,aEx));
    a.t1+=eqArr(tri[0],aTri); a.t3+=tri.slice(0,3).some(c=>eqArr(c,aTri)); a.t10+=tri.some(c=>eqArr(c,aTri));
  }
  const rows=Object.entries(agg).map(([c,a])=>({c,...a})).sort((x,y)=>y.win/y.n-x.win/x.n);
  const pc=(x,n)=>n?(x/n*100).toFixed(0)+'%':'-';
  let h=nav();
  if(!rows.length){return h+'<div class="meta">この期間はまだ結果がありません（前日・前々日のレース確定後に集計されます）。</div>';}
  const T=rows.reduce((o,a)=>{for(const k of['n','win','e1','e3','e5','t1','t3','t10'])o[k]=(o[k]||0)+a[k];return o;},{});
  h+='<div class="meta">対象 '+(ds[0]?ds[0].slice(5):'')+'〜'+(ds[ds.length-1]?ds[ds.length-1].slice(5):'')
    +'（'+T.n+'レース）・ 直前情報なしモデル ・ 数字=予想上位K通り以内に決着が入った割合</div>';
  h+='<div class="swrap"><table class="st"><thead><tr>'
    +'<th class="k">会場</th><th>R数</th><th>本命<br>1着</th>'
    +'<th class="g2">2連単<br>本命</th><th class="g2">top3</th><th class="g2">top5</th>'
    +'<th class="g3">3連単<br>本命</th><th class="g3">top3</th><th class="g3">top10</th></tr></thead><tbody>';
  for(const a of rows){
    h+='<tr><td class="k">'+a.v+'</td><td class="num scol">'+a.n+'</td>'
      +'<td class="num">'+pc(a.win,a.n)+'</td>'
      +'<td class="num g2">'+pc(a.e1,a.n)+'</td><td class="num g2">'+pc(a.e3,a.n)+'</td><td class="num g2">'+pc(a.e5,a.n)+'</td>'
      +'<td class="num g3">'+pc(a.t1,a.n)+'</td><td class="num g3">'+pc(a.t3,a.n)+'</td><td class="num g3">'+pc(a.t10,a.n)+'</td></tr>';
  }
  h+='<tr class="all"><td class="k">全場</td><td class="num">'+T.n+'</td><td class="num">'+pc(T.win,T.n)+'</td>'
    +'<td class="num">'+pc(T.e1,T.n)+'</td><td class="num">'+pc(T.e3,T.n)+'</td><td class="num">'+pc(T.e5,T.n)+'</td>'
    +'<td class="num">'+pc(T.t1,T.n)+'</td><td class="num">'+pc(T.t3,T.n)+'</td><td class="num">'+pc(T.t10,T.n)+'</td></tr>';
  h+='</tbody></table></div>';
  h+='<div class="legend">※ today.html が表示している日付範囲（当日/前日/前々日）の結果のみから集計。本命=1着確率最大の枠。'
    +'top3/5/10=予想上位3/5/10通りの中に実際の決着が含まれた割合（その点数を買えば当たる割合）。サンプルが少ない日は変動します。</div>';
  return h;
}
function render(){
  if(tab==='stats'){
    root.innerHTML=statsView();
    document.querySelectorAll('.tb').forEach(b=>b.onclick=()=>{tab=b.dataset.t;sel=null;render();});
    window.scrollTo(0,0);return;
  }
  root.innerHTML = sel===null ? nav()+listView() : detailView(dayRaces()[sel]);
  window.scrollTo(0,0);
  if(sel===null){
    document.querySelectorAll('.tb').forEach(b=>b.onclick=()=>{tab=b.dataset.t;sel=null;render();});
    document.querySelectorAll('.dbtn').forEach(b=>b.onclick=()=>{selDate=b.dataset.d;cur='ALL';render();});
    document.querySelectorAll('.vbtn').forEach(b=>b.onclick=()=>{cur=b.dataset.v;render();});
    document.querySelectorAll('.row').forEach(rw=>rw.onclick=()=>{sel=+rw.dataset.i;render();});
  }else{
    document.querySelector('.back').onclick=()=>{sel=null;render();};
  }
}
render();
</script></body></html>"""


if __name__ == "__main__":
    main()
