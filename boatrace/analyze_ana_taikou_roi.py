# -*- coding: utf-8 -*-
"""
穴候補(API4番人気) / 対抗1艇(学習モデル betScore 2番手) を軸にした
単勝・複勝・各種買い目パターンの回収率を honest OOS で集計する。

対象の定義（アプリ build_today と一致）:
  本命   = 学習モデル p_win の1番手（betScore≒p_win。展示反映なしの履歴では p_win）
  対抗   = 学習モデル p_win の2番手      … _taikou_ref のアタマ
  穴候補 = API簡易合成 p_win の4番人気   … _ana_cand_ref のアタマ
  荒れ度帯（本命確率）= API簡易合成 p_win の1番手確率
      鉄板 ≥0.65 / 標準 0.45–0.65 / 波乱 <0.45

配当は k*.txt の各レース詳細ブロックから艇別・組合せ別に抽出（単勝/複勝/2連単/
2連複/拡連複=ワイド/3連単/3連複）。着順も同ブロックから取得。
非完走艇（F/L/失格/欠場＝着順なし）を含む買い目は「返還」＝投資から除外（アプリと同方針）。

回収率 = Σ払戻 / Σ投資 × 100（各点100円）。
"""
import csv, glob, re, itertools
from collections import defaultdict
from features_player_history import VENUE_CODE

ENC = "cp932"
LANE_BASE = {1: 0.50, 2: 0.14, 3: 0.12, 4: 0.10, 5: 0.08, 6: 0.05}
API_GAMMA, API_BETA = 1.3, 0.6

# ───────── API簡易合成 p_win（build_today.api_pwin と同一式） ─────────
def api_rate(nat, loc):
    nat = nat if (nat and nat > 0) else None
    loc = loc if (loc and loc > 0) else None
    if nat is not None and loc is not None: return 0.5*nat + 0.5*loc
    return nat if nat is not None else loc

def api_pwin(race):   # race: {w: {nat,loc,motor}}
    rate = {w: api_rate(b.get("nat"), b.get("loc")) for w, b in race.items()}
    rv = [v for v in rate.values() if v is not None]; ravg = sum(rv)/len(rv) if rv else 1.0
    mot = {w: b.get("motor") for w, b in race.items()}
    mv = [m for m in mot.values() if m is not None]; mavg = sum(mv)/len(mv) if mv else 0.0
    sc = {}
    for w in race:
        r = rate[w] if rate[w] is not None else ravg
        m = mot[w] if mot[w] is not None else mavg
        rel = (r/ravg) if ravg > 0 else 1.0
        sc[w] = max(LANE_BASE.get(w,0.1)*(rel**API_GAMMA)*(1+API_BETA*(m-mavg)/100.0), 0.003)
    tot = sum(sc.values())
    return {w: sc[w]/tot for w in sc} if tot > 0 else {w: 1/6 for w in race}

def to_float(s):
    try: return float(str(s).strip())
    except (ValueError, AttributeError, TypeError): return None

# ───────── 入力 ─────────
def load_predict(path="predict_win.csv"):
    races = defaultdict(dict)
    with open(path, encoding=ENC, newline="") as f:
        r = csv.reader(f); h = next(r); i_p = h.index("p_win")
        for row in r:
            if not row: continue
            try: races[row[0]][int(row[1])] = float(row[i_p])
            except ValueError: continue
    return races

def load_api(path="features_race_relative.csv"):
    races = {}
    with open(path, encoding=ENC, newline="") as f:
        for r in csv.DictReader(f):
            try: w = int(r["枠番"])
            except (ValueError, KeyError): continue
            races.setdefault(r["race_id"], {})[w] = {
                "nat": to_float(r.get("win_rate_national")),
                "loc": to_float(r.get("win_rate_local")),
                "motor": to_float(r.get("motor_top2_rate"))}
    out = {}
    for rid, race in races.items():
        if len(race) == 6:
            out[rid] = api_pwin(race)
    return out

# ───────── k*.txt 全券種パーサ（艇別配当＋着順） ─────────
RACER_RE = re.compile(r'^\s{2}(\d{2})\s{2}([1-6])\s(\d{4})\s')
NONFIN_RE = re.compile(r'^\s{2}(F|L|S\d|K\d)\s+([1-6])\s(\d{4})\s')
RACE_HDR_RE = re.compile(r'^\s{1,5}(\d{1,2})R\s+\D')
SECTION_RE = re.compile(r'^(.+?)［成績］')
DATE_RE = re.compile(r'(\d{4})/\s*(\d{1,2})/\s*(\d{1,2})')
LABELS = {'単勝':'tan','複勝':'fuku','２連単':'nt','２連複':'nf','拡連複':'wide','３連単':'st','３連複':'sf'}
PAIR_ONLY = re.compile(r'^\s+(\d(?:-\d){1,2})\s+(\d+)')   # ワイド継続行 / 組合せ行

def open_txt(path):
    try: return open(path, encoding="utf-8").read().splitlines()
    except UnicodeDecodeError: return open(path, encoding="cp932").read().splitlines()

def parse_k_txt(path, out):
    venue = date = None; race = None; cur = None; curlabel = None
    def flush():
        nonlocal cur
        if cur and cur.get("_rid"):
            out[cur["_rid"]] = cur
        cur = None
    for line in open_txt(path):
        m = SECTION_RE.match(line)
        if m: flush(); venue = m.group(1).replace('　','').strip(); date=None; race=None; continue
        if date is None:
            md = DATE_RE.search(line)
            if md: date = (int(md.group(1)), int(md.group(2)), int(md.group(3)))
        mh = RACE_HDR_RE.match(line)
        if mh:
            flush(); race = int(mh.group(1)); curlabel=None
            cur = {"fin":{}, "status":{}, "tan":None, "fuku":{}, "wide":{},
                   "nt":None, "nf":None, "st":None, "sf":None}
            if venue and date:
                code = VENUE_CODE.get(venue, "00")
                cur["_rid"] = f"{code}{date[0]:04d}{date[1]:02d}{date[2]:02d}{race:02d}"
            else:
                cur["_rid"] = None
            continue
        if cur is None: continue
        mr = RACER_RE.match(line)
        if mr:
            cur["fin"][int(mr.group(2))] = int(mr.group(1)); cur["status"][int(mr.group(2))]="finish"; continue
        mn = NONFIN_RE.match(line)
        if mn:
            cur["status"][int(mn.group(2))] = mn.group(1).strip()[0]; continue
        s = line.strip()
        if not s:
            continue
        lab = None
        for jp, key in LABELS.items():
            if s.startswith(jp): lab = (jp, key); break
        if lab:
            jp, key = lab; body = s[len(jp):]; curlabel = key
            if '特払' in body:
                curlabel = None; continue
            if key in ('tan',):
                m2 = re.search(r'(\d)\s+(\d+)', body)
                if m2: cur["tan"] = (int(m2.group(1)), int(m2.group(2)))
            elif key == 'fuku':
                for bt, pay in re.findall(r'(\d)\s+(\d+)', body):
                    cur["fuku"][int(bt)] = int(pay)
            elif key in ('nt','nf'):
                m2 = re.search(r'(\d-\d)\s+(\d+)', body)
                if m2:
                    combo = tuple(int(x) for x in m2.group(1).split('-'))
                    cur[key] = (combo, int(m2.group(2)))
            elif key == 'wide':
                m2 = re.search(r'(\d-\d)\s+(\d+)', body)
                if m2:
                    pr = frozenset(int(x) for x in m2.group(1).split('-'))
                    cur["wide"][pr] = int(m2.group(2))
            elif key in ('st','sf'):
                m2 = re.search(r'(\d-\d-\d)\s+(\d+)', body)
                if m2:
                    combo = tuple(int(x) for x in m2.group(1).split('-'))
                    cur[key] = (combo, int(m2.group(2)))
            continue
        # ワイド継続行
        if curlabel == 'wide':
            mp = PAIR_ONLY.match(line)
            if mp and mp.group(1).count('-') == 1:
                pr = frozenset(int(x) for x in mp.group(1).split('-'))
                cur["wide"][pr] = int(mp.group(2)); continue
        curlabel = None
    flush()

def load_all_ktxt():
    out = {}
    for p in sorted(glob.glob("data/k*.txt")):
        parse_k_txt(p, out)
    return out

# ───────── 集計 ─────────
class Agg:
    __slots__=("stake","ret","hit","n","paysum")
    def __init__(self): self.stake=self.ret=self.hit=self.n=self.paysum=0
    def add(self, stake, ret, hit, pay=0):
        self.stake+=stake; self.ret+=ret; self.hit+=hit; self.n+=1
        if hit: self.paysum+=pay

def main():
    model = load_predict()
    api = load_api()
    kd = load_all_ktxt()
    print(f"k*.txt レース: {len(kd)}  model: {len(model)}  api: {len(api)}")

    bands = ["鉄板", "標準", "波乱", "全体"]
    # pattern -> band -> Agg
    stats = defaultdict(lambda: {b: Agg() for b in bands})

    def band_of(hon):
        return "鉄板" if hon>=0.65 else "標準" if hon>=0.45 else "波乱"

    def finished(rc, w): return rc["status"].get(w)=="finish"
    def order_by(d, rid):   # 降順index(枠) by score dict {w:score}
        return sorted(range(1,7), key=lambda w: d.get((rid,w), d.get(w,0)), reverse=True)

    used=0
    for rid, rc in kd.items():
        if rid not in api: continue
        mp = model.get(rid)
        if not mp or len(mp)!=6: continue
        if len(rc["fin"])+ sum(1 for w in range(1,7) if rc["status"].get(w) not in (None,"finish")) < 6:
            pass
        ap = api[rid]
        if len(ap)!=6: continue
        # 順位
        m_order = sorted(range(1,7), key=lambda w: mp.get(w,0), reverse=True)
        a_order = sorted(range(1,7), key=lambda w: ap.get(w,0), reverse=True)
        honmei = m_order[0]; taikou = m_order[1]
        ana = a_order[3]                    # API4番人気
        hon = max(ap.values())              # 本命確率(API top1)
        bnd = band_of(hon)
        used+=1

        fin = rc["fin"]                      # {艇:着順}（完走のみ）
        top1 = [w for w in range(1,7) if fin.get(w)==1]
        win = top1[0] if top1 else None
        placed = set(w for w in range(1,7) if fin.get(w) in (1,2))
        top3 = set(w for w in range(1,7) if fin.get(w) in (1,2,3))

        def rec(pat, involved, npts, hit, pay):
            # 非完走艇を含む→返還（投資に計上しない）
            if any(not finished(rc, w) for w in involved):
                return
            stake = npts*100
            r = pay if hit else 0
            for bb in (bnd, "全体"):
                stats[pat][bb].add(stake, r, 1 if hit else 0, pay if hit else 0)

        # ── 単勝 ──
        for who, name in ((honmei,"単勝 本命"),(taikou,"単勝 対抗"),(ana,"単勝 穴候補")):
            if rc["tan"]:
                tw, tp = rc["tan"]
                rec(name, (who,), 1, win==who, tp if win==who else 0)
        # ── 複勝 ──
        for who, name in ((honmei,"複勝 本命"),(taikou,"複勝 対抗"),(ana,"複勝 穴候補")):
            hit = who in placed and who in rc["fuku"]
            rec(name, (who,), 1, hit, rc["fuku"].get(who,0))
        # ── ワイド（トリオの各ペア） ──
        for a,b,name in ((honmei,taikou,"ワイド 本命-対抗"),(taikou,ana,"ワイド 対抗-穴候補"),
                         (honmei,ana,"ワイド 本命-穴候補")):
            if a==b: continue
            pr=frozenset((a,b)); hit = a in top3 and b in top3 and pr in rc["wide"]
            rec(name,(a,b),1,hit,rc["wide"].get(pr,0))
        # ── 2連複 ──
        for a,b,name in ((honmei,taikou,"2連複 本命-対抗"),(taikou,ana,"2連複 対抗-穴候補")):
            if a==b: continue
            hit=False; pay=0
            if rc["nf"]:
                combo,p=rc["nf"]
                if frozenset(combo)==frozenset((a,b)): hit=True; pay=p
            rec(name,(a,b),1,hit,pay)
        # ── 2連単 ──
        for a,b,name in ((honmei,taikou,"2連単 本命→対抗"),(taikou,honmei,"2連単 対抗→本命"),
                         (taikou,ana,"2連単 対抗→穴候補")):
            if a==b: continue
            hit=False; pay=0
            if rc["nt"]:
                combo,p=rc["nt"]
                if combo==(a,b): hit=True; pay=p
            rec(name,(a,b),1,hit,pay)
        # ── 3連単フォーメーション ──
        # 対抗アタマ×{本命,3番手,4番手}の2-3着流し6点（_taikou_ref）
        T=[m_order[0],m_order[2],m_order[3]]
        tk_pts=[(taikou,x,y) for x in T for y in T if x!=y]
        # 穴候補アタマ×{本命,2番手,3番手}の2-3着流し6点（_ana_cand_ref）
        A=[a_order[0],a_order[1],a_order[2]]
        an_pts=[(ana,x,y) for x in A for y in A if x!=y]
        for pts,name in ((tk_pts,"3連単 対抗アタマ6点"),(an_pts,"3連単 穴候補アタマ6点")):
            involved=set(w for c in pts for w in c)
            if any(not finished(rc,w) for w in involved):
                continue
            pay=0; hit=False
            if rc["st"]:
                combo,p=rc["st"]
                if combo in pts: hit=True; pay=p
            stake=len(pts)*100
            for bb in (bnd,"全体"):
                stats[name][bb].add(stake, pay if hit else 0, 1 if hit else 0, pay if hit else 0)

    print(f"集計対象レース: {used}\n")

    order=["単勝 本命","単勝 対抗","単勝 穴候補","複勝 本命","複勝 対抗","複勝 穴候補",
           "ワイド 本命-対抗","ワイド 対抗-穴候補","ワイド 本命-穴候補",
           "2連複 本命-対抗","2連複 対抗-穴候補",
           "2連単 本命→対抗","2連単 対抗→本命","2連単 対抗→穴候補",
           "3連単 対抗アタマ6点","3連単 穴候補アタマ6点"]

    def line(pat, b):
        a=stats[pat][b]
        if a.stake==0: return f"{'-':>7}"
        roi=a.ret/a.stake*100
        return f"{roi:>6.1f}%"

    print("【回収率】券種・買い目パターン × 荒れ度帯（各点100円・非完走艇=返還）")
    print(f"{'パターン':<22}{'全体':>8}{'鉄板':>8}{'標準':>8}{'波乱':>8}   {'的中率(全体)':>10}{'平均配当':>10}")
    print("-"*84)
    for pat in order:
        a=stats[pat]["全体"]
        hr = a.hit/a.n*100 if a.n else 0
        avg = a.paysum/a.hit if a.hit else 0
        print(f"{pat:<22}{line(pat,'全体'):>8}{line(pat,'鉄板'):>8}{line(pat,'標準'):>8}{line(pat,'波乱'):>8}"
              f"   {hr:>9.1f}%{avg:>10,.0f}")

    # JSON 出力（可視化用）
    import json
    grp = {  # パターン→券種グループ
        "単勝 本命":"単勝","単勝 対抗":"単勝","単勝 穴候補":"単勝",
        "複勝 本命":"複勝","複勝 対抗":"複勝","複勝 穴候補":"複勝",
        "ワイド 本命-対抗":"ワイド","ワイド 対抗-穴候補":"ワイド","ワイド 本命-穴候補":"ワイド",
        "2連複 本命-対抗":"2連複","2連複 対抗-穴候補":"2連複",
        "2連単 本命→対抗":"2連単","2連単 対抗→本命":"2連単","2連単 対抗→穴候補":"2連単",
        "3連単 対抗アタマ6点":"3連単","3連単 穴候補アタマ6点":"3連単"}
    rows=[]
    for pat in order:
        a=stats[pat]["全体"]
        rows.append({
            "pat":pat, "grp":grp[pat],
            "roi":{b: (round(stats[pat][b].ret/stats[pat][b].stake*100,1) if stats[pat][b].stake else None)
                   for b in bands},
            "n":{b: stats[pat][b].n for b in bands},
            "hit": round(a.hit/a.n*100,1) if a.n else 0,
            "avg": round(a.paysum/a.hit,0) if a.hit else 0,
        })
    out={"n_races":used, "rows":rows,
         "note":"穴候補=API4番人気 / 対抗=モデル2番手 / 本命=モデル1番手・荒れ度帯=API本命確率"}
    with open("ana_taikou_roi.json","w",encoding="utf-8") as f:
        json.dump(out,f,ensure_ascii=False,separators=(",",":"))
    print("\nana_taikou_roi.json 書き出し")

if __name__ == "__main__":
    main()
