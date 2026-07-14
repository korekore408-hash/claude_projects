# -*- coding: utf-8 -*-
"""ana_taikou_roi.json → 穴候補/対抗の券種別回収率ヒートマップ(自己完結HTML)。"""
import json
d = json.load(open("ana_taikou_roi.json", encoding="utf-8"))
DATA_JS = json.dumps(d, ensure_ascii=False, separators=(",", ":"))

HTML = r"""<div class="viz-root" id="root">
  <header class="hd">
    <h1>穴候補・対抗の券種別 回収率</h1>
    <p class="sub">競艇 33,987レース（2025-08〜2026-07・honest OOS）。各点100円・非完走艇を含む買い目は返還。
      <b>穴候補</b>＝API 4番人気 ／ <b>対抗</b>＝学習モデル2番手 ／ <b>本命</b>＝学習モデル1番手。
      荒れ度帯＝API本命確率（鉄板≥65% / 標準45–65% / 波乱&lt;45%）。</p>
  </header>

  <div class="tiles">
    <div class="tile"><div class="tl">最高回収率</div><div class="tv">複勝 本命 <b>94.6%</b></div>
      <div class="tc">単勝 本命 91.5% が次点。堅い軸ほど高い</div></div>
    <div class="tile"><div class="tl">波乱帯で光る穴／対抗</div><div class="tv">複勝 対抗 <b>93.5%</b></div>
      <div class="tc">単勝 対抗 90.7% ／ 複勝 穴候補 89.7%</div></div>
    <div class="tile"><div class="tl">全パターン共通</div><div class="tv">100%<b>未満</b></div>
      <div class="tc">控除率ぶん必ずマイナス。“勝てる買い方”は無い</div></div>
  </div>

  <div class="legendbar">
    <span class="lgtitle">回収率</span>
    <span class="ramp" id="ramp"></span>
    <span class="lgmin">45%</span><span class="lgmax">100%（収支トントン）</span>
  </div>

  <figure class="chart"><div class="hm" id="hm"></div>
    <div class="tip" id="tip" hidden></div></figure>

  <p class="note">読み方: 濃いほど回収率が高い。<b>対抗・穴候補の単勝/複勝は「波乱帯」で急に良くなる</b>
    （鉄板レースでは本命が沈まないので対抗・穴を買うほど損）。逆に本命の単勝/複勝は帯によらず安定して高い。
    それでも全マスが100%未満＝どの買い方も長期ではマイナス。“波乱帯で対抗・穴の単複”が最も傷が浅い、が結論。</p>
</div>

<style>
  .viz-root{ --surface-1:#fcfcfb; --plane:#f9f9f7; --text-primary:#0b0b0b; --text-secondary:#52514e;
    --muted:#898781; --grid:#e1e0d9; --border:rgba(11,11,11,.10);
    font-family:system-ui,-apple-system,"Segoe UI",sans-serif; color:var(--text-primary);
    background:var(--plane); max-width:760px; margin:0 auto; padding:20px 16px 28px;}
  @media (prefers-color-scheme:dark){ .viz-root{ --surface-1:#1a1a19; --plane:#0d0d0d; --text-primary:#fff;
    --text-secondary:#c3c2b7; --muted:#898781; --grid:#2c2c2a; --border:rgba(255,255,255,.10);}}
  :root[data-theme=dark] .viz-root{ --surface-1:#1a1a19; --plane:#0d0d0d; --text-primary:#fff;
    --text-secondary:#c3c2b7; --grid:#2c2c2a; --border:rgba(255,255,255,.10);}
  :root[data-theme=light] .viz-root{ --surface-1:#fcfcfb; --plane:#f9f9f7; --text-primary:#0b0b0b;
    --text-secondary:#52514e; --grid:#e1e0d9; --border:rgba(11,11,11,.10);}
  .hd h1{font-size:1.26rem; margin:0 0 4px;} .sub{margin:0; color:var(--text-secondary); font-size:.8rem; line-height:1.55;}
  .sub b{color:var(--text-primary); font-weight:600;}
  .tiles{display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin:16px 0 6px;}
  .tile{background:var(--surface-1); border:1px solid var(--border); border-radius:12px; padding:11px 13px;}
  .tl{font-size:.7rem; color:var(--muted);} .tv{font-size:1.02rem; font-weight:600; margin:3px 0 2px;}
  .tv b{font-size:1.12rem;} .tc{font-size:.68rem; color:var(--text-secondary); line-height:1.4;}
  .legendbar{display:flex; align-items:center; gap:8px; margin:14px 2px 8px; font-size:.72rem; color:var(--muted);}
  .lgtitle{color:var(--text-secondary); font-weight:600;}
  .ramp{flex:0 0 160px; height:11px; border-radius:6px; border:1px solid var(--border);
    background:linear-gradient(90deg,#cde2fb,#6da7ec,#2a78d6,#184f95,#0d366b);}
  .chart{position:relative; margin:2px 0 0;}
  .hm{display:grid; gap:3px;}
  .hrow{display:grid; grid-template-columns:132px repeat(4,1fr); gap:3px; align-items:stretch;}
  .rlab{display:flex; align-items:center; font-size:.76rem; color:var(--text-primary); padding-right:6px;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
  .colhdr{font-size:.72rem; color:var(--muted); text-align:center; font-weight:600; padding-bottom:2px;}
  .colhdr.first{color:var(--text-secondary);}
  .cell{border-radius:6px; height:34px; display:flex; align-items:center; justify-content:center;
    font-size:.8rem; font-weight:600; font-variant-numeric:tabular-nums; cursor:default;
    border:1px solid rgba(0,0,0,.04);}
  .cell.first{box-shadow:inset 0 0 0 1.5px var(--border);}
  .gap{height:7px;}
  .grouplab{font-size:.68rem; color:var(--muted); font-weight:600; letter-spacing:.03em; padding:2px 0 1px 2px;}
  .tip{position:absolute; pointer-events:none; z-index:5; background:var(--surface-1); border:1px solid var(--border);
    border-radius:9px; padding:8px 10px; font-size:.74rem; color:var(--text-primary);
    box-shadow:0 4px 16px rgba(0,0,0,.2); min-width:150px; font-variant-numeric:tabular-nums;}
  .tip .h{font-weight:600; margin-bottom:4px;}
  .tip .r{display:flex; justify-content:space-between; gap:14px; color:var(--text-secondary);}
  .tip .r span:last-child{color:var(--text-primary);}
  .note{font-size:.78rem; color:var(--text-secondary); line-height:1.6; margin:16px 2px 0;}
  .note b{color:var(--text-primary);}
</style>

<script>
const DATA = __DATA__;
(function(){
  const bands=["全体","鉄板","標準","波乱"];
  const hm=document.getElementById('hm'), tip=document.getElementById('tip'), chart=hm.parentElement;
  // 青ランプ補間（45%→100%）
  const stops=[[0,[205,226,251]],[.25,[109,167,236]],[.5,[42,120,214]],[.75,[24,79,149]],[1,[13,54,107]]];
  function lerp(a,b,t){return a+(b-a)*t;}
  function color(roi){
    if(roi==null) return null;
    let t=Math.max(0,Math.min(1,(roi-45)/(100-45)));
    let i=0; while(i<stops.length-1 && t>stops[i+1][0]) i++;
    const [t0,c0]=stops[i],[t1,c1]=stops[Math.min(i+1,stops.length-1)];
    const u=t1>t0?(t-t0)/(t1-t0):0;
    const c=[0,1,2].map(k=>Math.round(lerp(c0[k],c1[k],u)));
    return c;
  }
  function ink(c){ const L=(0.299*c[0]+0.587*c[1]+0.114*c[2]); return L>150?'#0b0b0b':'#fff'; }

  // 列見出し
  const hdr=document.createElement('div'); hdr.className='hrow';
  hdr.innerHTML='<div></div>'+bands.map((b,i)=>`<div class="colhdr${i===0?' first':''}">${b}</div>`).join('');
  hm.appendChild(hdr);

  let lastGrp=null;
  for(const row of DATA.rows){
    if(row.grp!==lastGrp){
      lastGrp=row.grp;
      const g=document.createElement('div'); g.className='grouplab'; g.textContent=row.grp; hm.appendChild(g);
    }
    const tr=document.createElement('div'); tr.className='hrow';
    const lab=document.createElement('div'); lab.className='rlab';
    lab.textContent=row.pat.replace(row.grp,'').trim()||row.pat; tr.appendChild(lab);
    bands.forEach((b,i)=>{
      const roi=row.roi[b]; const cell=document.createElement('div');
      cell.className='cell'+(i===0?' first':'');
      const c=color(roi);
      if(c){ cell.style.background=`rgb(${c[0]},${c[1]},${c[2]})`; cell.style.color=ink(c);
             cell.textContent=roi.toFixed(1); }
      else { cell.style.background='transparent'; cell.textContent='–'; cell.style.color='var(--muted)'; }
      cell.addEventListener('mousemove',e=>showTip(e,row,b,roi));
      cell.addEventListener('mouseleave',()=>tip.hidden=true);
      tr.appendChild(cell);
    });
    hm.appendChild(tr);
  }

  function showTip(e,row,b,roi){
    if(roi==null){tip.hidden=true;return;}
    const rect=chart.getBoundingClientRect();
    tip.hidden=false;
    let h=`<div class="h">${row.pat}<br><span style="color:var(--muted);font-weight:400">${b}</span></div>`+
      `<div class="r"><span>回収率</span><span>${roi.toFixed(1)}%</span></div>`+
      `<div class="r"><span>レース数</span><span>${row.n[b].toLocaleString()}</span></div>`;
    if(b==='全体') h+=`<div class="r"><span>的中率</span><span>${row.hit}%</span></div>`+
      `<div class="r"><span>平均配当</span><span>¥${row.avg.toLocaleString()}</span></div>`;
    tip.innerHTML=h;
    let x=e.clientX-rect.left+12, y=e.clientY-rect.top+12;
    if(x+170>rect.width) x=e.clientX-rect.left-170;
    tip.style.left=x+'px'; tip.style.top=y+'px';
  }
})();
</script>
"""
open("ana_roi.html","w",encoding="utf-8").write(HTML.replace("__DATA__", DATA_JS))
print("ana_roi.html 書き出し完了")
