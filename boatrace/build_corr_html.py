# -*- coding: utf-8 -*-
"""corr_data.json を読み、本命確率×3連単配当の相関図(自己完結HTML)を書き出す。"""
import json

with open("corr_data.json", encoding="utf-8") as f:
    data = json.load(f)
DATA_JS = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

HTML = r"""<div class="viz-root" id="root">
  <header class="hd">
    <h1>本命確率 × 3連単配当</h1>
    <p class="sub">競艇 33,935レース（2025-08〜2026-07・train≤2026-04-30 固定モデルの honest OOS 予測）。
       本命確率＝モデル top1 p_win。配当は対数軸。</p>
  </header>

  <div class="tiles">
    <div class="tile">
      <div class="tl">相関 Spearman ρ</div>
      <div class="tv" id="tv-sp">−0.25</div>
      <div class="tc">本命が強いほど配当は下がる（負・中程度）</div>
    </div>
    <div class="tile">
      <div class="tl">3連単配当 中央値</div>
      <div class="tv">¥<span id="tv-med">2,470</span></div>
      <div class="tc">最小 ¥160 ／ 最大 ¥501,250</div>
    </div>
    <div class="tile">
      <div class="tl">対象レース</div>
      <div class="tv"><span id="tv-n">33,935</span></div>
      <div class="tc">6艇・全スコア・配当あり</div>
    </div>
  </div>

  <div class="legend">
    <span class="lg"><i class="sw-dot"></i>各レース（1点=1レース）</span>
    <span class="lg"><i class="sw-band"></i>四分位範囲 (25–75%)</span>
    <span class="lg"><i class="sw-line"></i>本命確率帯ごとの中央値</span>
  </div>

  <figure class="chart" id="chart">
    <canvas id="cv"></canvas>
    <svg id="ov" preserveAspectRatio="none"></svg>
    <div class="tip" id="tip" hidden></div>
  </figure>

  <details class="tbl">
    <summary>データ表（本命確率帯ごとの配当分布）</summary>
    <div class="tblwrap"><table id="statTable"><thead>
      <tr><th>本命確率</th><th>レース数</th><th>25%</th><th>中央値</th><th>75%</th><th>平均</th></tr>
    </thead><tbody></tbody></table></div>
  </details>

  <p class="note">読み方: 縦の散らばりの大きさ＝「同じ本命確率でも配当は大きくブレる」。
     橙の中央値ラインは右下がり（本命が強い＝堅いレースほど配当は小さい）だが、
     どの帯でも高配当（上方の点）は一定数出る。相関が −0.25 に留まるのはこのため。</p>
</div>

<style>
  .viz-root{
    --surface-1:#fcfcfb; --plane:#f9f9f7;
    --text-primary:#0b0b0b; --text-secondary:#52514e; --muted:#898781;
    --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
    --series:#2a78d6; --accent:#eb6834;
    --dot:rgba(42,120,214,.34); --band:rgba(42,120,214,.22);
    font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
    color:var(--text-primary); background:var(--plane);
    max-width:820px; margin:0 auto; padding:20px 16px 28px;
  }
  @media (prefers-color-scheme:dark){ .viz-root{
    --surface-1:#1a1a19; --plane:#0d0d0d;
    --text-primary:#fff; --text-secondary:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
    --series:#3987e5; --accent:#d95926;
    --dot:rgba(57,135,229,.30); --band:rgba(57,135,229,.26);
  }}
  :root[data-theme=dark] .viz-root{
    --surface-1:#1a1a19; --plane:#0d0d0d;
    --text-primary:#fff; --text-secondary:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
    --series:#3987e5; --accent:#d95926;
    --dot:rgba(57,135,229,.30); --band:rgba(57,135,229,.26);
  }
  :root[data-theme=light] .viz-root{
    --surface-1:#fcfcfb; --plane:#f9f9f7;
    --text-primary:#0b0b0b; --text-secondary:#52514e; --muted:#898781;
    --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
    --series:#2a78d6; --accent:#eb6834;
    --dot:rgba(42,120,214,.34); --band:rgba(42,120,214,.22);
  }
  .hd h1{font-size:1.28rem; margin:0 0 4px; letter-spacing:.01em;}
  .sub{margin:0; color:var(--text-secondary); font-size:.82rem; line-height:1.5;}
  .tiles{display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin:18px 0 8px;}
  .tile{background:var(--surface-1); border:1px solid var(--border); border-radius:12px; padding:12px 14px;}
  .tl{font-size:.72rem; color:var(--muted);}
  .tv{font-size:1.5rem; font-weight:650; margin:3px 0 2px; letter-spacing:-.01em;}
  .tc{font-size:.7rem; color:var(--text-secondary); line-height:1.35;}
  .legend{display:flex; flex-wrap:wrap; gap:14px; margin:12px 2px 6px; font-size:.76rem; color:var(--text-secondary);}
  .lg{display:inline-flex; align-items:center; gap:6px;}
  .sw-dot{width:9px; height:9px; border-radius:50%; background:var(--series); opacity:.7;}
  .sw-band{width:14px; height:9px; border-radius:3px; background:var(--band); border:1px solid var(--series);}
  .sw-line{width:16px; height:0; border-top:2.5px solid var(--accent);}
  .chart{position:relative; margin:6px 0 0; width:100%; height:440px;
    background:var(--surface-1); border:1px solid var(--border); border-radius:12px; overflow:hidden;}
  #cv{position:absolute; inset:0; width:100%; height:100%;}
  #ov{position:absolute; inset:0; width:100%; height:100%;}
  .tip{position:absolute; pointer-events:none; z-index:5; background:var(--surface-1);
    border:1px solid var(--border); border-radius:9px; padding:8px 10px; font-size:.74rem;
    color:var(--text-primary); box-shadow:0 4px 16px rgba(0,0,0,.18); min-width:130px;
    font-variant-numeric:tabular-nums;}
  .tip b{color:var(--accent);}
  .tip .r{display:flex; justify-content:space-between; gap:12px; color:var(--text-secondary);}
  .tip .r span:last-child{color:var(--text-primary);}
  .tbl{margin:16px 0 0; font-size:.8rem;}
  .tbl summary{cursor:pointer; color:var(--series); font-weight:550;}
  .tblwrap{overflow-x:auto; margin-top:8px;}
  table{border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums;}
  th,td{padding:5px 10px; text-align:right; border-bottom:1px solid var(--grid); white-space:nowrap;}
  th:first-child,td:first-child{text-align:left;}
  th{color:var(--muted); font-weight:600; font-size:.74rem;}
  .note{font-size:.78rem; color:var(--text-secondary); line-height:1.55; margin:16px 2px 0;}
  text{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;}
</style>

<script>
const DATA = __DATA__;
(function(){
  const cv=document.getElementById('cv'), ov=document.getElementById('ov'),
        chart=document.getElementById('chart'), tip=document.getElementById('tip'),
        root=document.getElementById('root');
  const cssv=n=>getComputedStyle(root).getPropertyValue(n).trim();
  const fmt=n=>n.toLocaleString('en-US');
  // Y(log)ドメイン・X(linear)ドメイン
  const yMin=100, yMax=600000;
  const xs=DATA.scatter.map(d=>d[0]/1000);
  let xMin=Math.min(...xs), xMax=Math.max(...xs);
  xMin=Math.floor(xMin*20)/20; xMax=Math.ceil(xMax*20)/20;   // 0.05刻みに丸め
  const L2=Math.log10(yMin), L3=Math.log10(yMax);
  const M={l:52,r:14,t:14,b:38};
  let W=0,H=0, PW=0,PH=0;

  function px(p){ return M.l + (p-xMin)/(xMax-xMin)*PW; }
  function py(v){ return M.t + (1-(Math.log10(v)-L2)/(L3-L2))*PH; }

  function fmtY(v){ if(v>=10000) return (v/10000)+'万'; return v.toLocaleString('en-US'); }

  function draw(){
    const rect=chart.getBoundingClientRect();
    W=rect.width; H=rect.height; PW=W-M.l-M.r; PH=H-M.t-M.b;
    const dpr=Math.min(window.devicePixelRatio||1,2);
    cv.width=W*dpr; cv.height=H*dpr;
    const ctx=cv.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.clearRect(0,0,W,H);
    // 散布点
    ctx.fillStyle=cssv('--dot');
    const r=1.5;
    for(const d of DATA.scatter){
      const x=px(d[0]/1000), y=py(Math.max(yMin,Math.min(yMax,d[1])));
      ctx.beginPath(); ctx.arc(x,y,r,0,6.2832); ctx.fill();
    }
    drawOverlay();
  }

  function svgEl(t,a){ const e=document.createElementNS('http://www.w3.org/2000/svg',t);
    for(const k in a) e.setAttribute(k,a[k]); return e; }

  function drawOverlay(){
    ov.setAttribute('viewBox',`0 0 ${W} ${H}`); ov.innerHTML='';
    const grid=cssv('--grid'), axis=cssv('--axis'), muted=cssv('--muted'),
          accent=cssv('--accent'), series=cssv('--series'), band=cssv('--band');
    // Y グリッド＋ラベル（10のべき＋2,5）
    const yticks=[100,200,500,1000,2000,5000,10000,20000,50000,100000,200000,500000];
    for(const v of yticks){ if(v<yMin||v>yMax) continue;
      const y=py(v), major=(String(v)[0]==='1');
      ov.appendChild(svgEl('line',{x1:M.l,y1:y,x2:W-M.r,y2:y,stroke:grid,
        'stroke-width':1,'stroke-dasharray':major?'':'2 3',opacity:major?1:.6}));
      const t=svgEl('text',{x:M.l-7,y:y+3.5,fill:muted,'font-size':10,'text-anchor':'end'});
      t.textContent=fmtY(v); ov.appendChild(t);
    }
    // X グリッド＋ラベル（%）
    for(let p=Math.ceil(xMin*10)/10; p<=xMax+1e-9; p+=0.1){
      const x=px(p);
      ov.appendChild(svgEl('line',{x1:x,y1:M.t,x2:x,y2:H-M.b,stroke:grid,'stroke-width':1,opacity:.5}));
      const t=svgEl('text',{x:x,y:H-M.b+15,fill:muted,'font-size':10,'text-anchor':'middle'});
      t.textContent=Math.round(p*100)+'%'; ov.appendChild(t);
    }
    // 軸タイトル
    const xt=svgEl('text',{x:M.l+PW/2,y:H-4,fill:muted,'font-size':10.5,'text-anchor':'middle'});
    xt.textContent='本命確率（モデル top1 p_win）'; ov.appendChild(xt);
    const yt=svgEl('text',{x:13,y:M.t+PH/2,fill:muted,'font-size':10.5,'text-anchor':'middle',
      transform:`rotate(-90 13 ${M.t+PH/2})`}); yt.textContent='3連単配当（円・対数）'; ov.appendChild(yt);
    // IQR バンド
    const bins=DATA.bins.filter(b=>b.x>=xMin&&b.x<=xMax);
    let up='M', lo='';
    bins.forEach((b,i)=>{ up+=`${px(b.x)},${py(b.p75)} `; });
    for(let i=bins.length-1;i>=0;i--){ up+=`${px(bins[i].x)},${py(bins[i].p25)} `; }
    ov.appendChild(svgEl('path',{d:up+'Z',fill:band,stroke:'none'}));
    // 中央値ライン
    let md='M'; bins.forEach((b,i)=>{ md+=(i?' L':'')+`${px(b.x)},${py(b.med)}`; });
    ov.appendChild(svgEl('path',{d:md,fill:'none',stroke:accent,'stroke-width':2.5,
      'stroke-linejoin':'round','stroke-linecap':'round'}));
    // 枠
    ov.appendChild(svgEl('rect',{x:M.l,y:M.t,width:PW,height:PH,fill:'none',stroke:axis,'stroke-width':1}));
    // crosshair placeholder
    cross=svgEl('line',{x1:0,y1:M.t,x2:0,y2:H-M.b,stroke:accent,'stroke-width':1,
      'stroke-dasharray':'3 3',opacity:0}); ov.appendChild(cross);
    dot=svgEl('circle',{r:4,fill:accent,opacity:0,stroke:cssv('--surface-1'),'stroke-width':1.5}); ov.appendChild(dot);
  }

  let cross,dot;
  function onMove(e){
    const rect=chart.getBoundingClientRect();
    const mx=(e.touches?e.touches[0].clientX:e.clientX)-rect.left;
    if(mx<M.l||mx>W-M.r){ hideTip(); return; }
    const p=xMin+(mx-M.l)/PW*(xMax-xMin);
    let best=null,bd=1e9;
    for(const b of DATA.bins){ const d=Math.abs(b.x-p); if(d<bd){bd=d;best=b;} }
    if(!best){ hideTip(); return; }
    const x=px(best.x);
    cross.setAttribute('x1',x); cross.setAttribute('x2',x); cross.setAttribute('opacity',.8);
    dot.setAttribute('cx',x); dot.setAttribute('cy',py(best.med)); dot.setAttribute('opacity',1);
    tip.hidden=false;
    tip.innerHTML=`<div style="font-weight:600;margin-bottom:4px">本命確率 ≈ <b>${Math.round(best.x*100)}%</b></div>`+
      `<div class="r"><span>中央値</span><span>¥${fmt(best.med)}</span></div>`+
      `<div class="r"><span>25–75%</span><span>¥${fmt(best.p25)}–${fmt(best.p75)}</span></div>`+
      `<div class="r"><span>平均</span><span>¥${fmt(best.mean)}</span></div>`+
      `<div class="r"><span>レース数</span><span>${fmt(best.n)}</span></div>`;
    let tx=x+12; if(tx+150>W) tx=x-12-150;
    tip.style.left=tx+'px'; tip.style.top=(M.t+8)+'px';
  }
  function hideTip(){ tip.hidden=true; if(cross){cross.setAttribute('opacity',0);dot.setAttribute('opacity',0);} }
  chart.addEventListener('mousemove',onMove);
  chart.addEventListener('mouseleave',hideTip);
  chart.addEventListener('touchstart',onMove,{passive:true});
  chart.addEventListener('touchmove',onMove,{passive:true});

  // タイル＆表
  document.getElementById('tv-sp').textContent=(DATA.spearman<0?'−':'')+Math.abs(DATA.spearman).toFixed(2);
  document.getElementById('tv-med').textContent=fmt(DATA.pay_median);
  document.getElementById('tv-n').textContent=fmt(DATA.n);
  const tb=document.querySelector('#statTable tbody');
  for(const b of DATA.bins){ const tr=document.createElement('tr');
    tr.innerHTML=`<td>${Math.round((b.x-0.01)*100)}–${Math.round((b.x+0.01)*100)}%</td>`+
      `<td>${fmt(b.n)}</td><td>¥${fmt(b.p25)}</td><td>¥${fmt(b.med)}</td>`+
      `<td>¥${fmt(b.p75)}</td><td>¥${fmt(b.mean)}</td>`; tb.appendChild(tr); }

  draw();
  let rt; new ResizeObserver(()=>{clearTimeout(rt);rt=setTimeout(draw,80);}).observe(chart);
  const mq=window.matchMedia('(prefers-color-scheme:dark)'); mq.addEventListener&&mq.addEventListener('change',draw);
  new MutationObserver(draw).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
})();
</script>
"""

html = HTML.replace("__DATA__", DATA_JS)
with open("corr.html", "w", encoding="utf-8") as f:
    f.write(html)
print(f"corr.html 書き出し完了  ({len(html)/1024:.0f}KB)")
