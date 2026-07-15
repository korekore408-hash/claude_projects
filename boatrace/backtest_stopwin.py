# -*- coding: utf-8 -*-
"""
「朝からレースを買っていき、その日の累積回収率が100%を超えたらやめる」利確ルールの検証。

比較: 同じ買い方で
  (A) 全レース買う（利確なし）
  (B) 累積回収率>100% になった時点でその日は打ち切り（翌日リセット）
の総回収率（Σ払戻/Σ投資）と、日別の勝ち負け分布。

レース時系列順 = B-file の「電話投票締切予定 HH:MM」で全会場横断に並べる。
買い方は複数（低分散〜高分散）で試し、利確ルールが回収率を変えるかを見る。
非完走艇を含む買い目は返還（賭けない）＝アプリと同方針。
"""
import re, glob, unicodedata
from collections import defaultdict
import analyze_ana_taikou_roi as A

# ───────── B-file 締切時刻 → race_id:分 ─────────
VENUE_HDR = re.compile(r'ボートレース(\S+?)(\d{1,2})月(\d{1,2})日')
def load_deadlines():
    dl = {}
    for p in sorted(glob.glob("data/b*.txt")):
        try: lines = open(p, encoding="utf-8").read().splitlines()
        except UnicodeDecodeError: lines = open(p, encoding="cp932").read().splitlines()
        venue = code = None; year = None
        m = re.search(r'b(\d{2})(\d{2})(\d{2})', p)
        year = 2000 + int(m.group(1)) if m else 2025
        for l in lines:
            z = unicodedata.normalize('NFKC', l)
            zc = z.replace(' ', '')
            mh = VENUE_HDR.search(zc)
            if mh:
                venue = mh.group(1); code = A.VENUE_CODE.get(venue, None)
                mo, dd = int(mh.group(2)), int(mh.group(3)); continue
            mr = re.search(r'(\d{1,2})R.*?締切予定\s*(\d{1,2}):(\d{2})', z)
            if mr and code:
                race = int(mr.group(1)); hh, mm = int(mr.group(2)), int(mr.group(3))
                rid = f"{code}{year:04d}{mo:02d}{dd:02d}{race:02d}"
                dl[rid] = hh * 60 + mm
    return dl

# ───────── 買い方（レース→(stake,ret)。返還時は(0,0)） ─────────
def make_bets(kd, model, api):
    """各 race_id に対し、各戦略の (stake, ret) を返す辞書を作る。"""
    def finished(rc, w): return rc["status"].get(w) == "finish"
    out = defaultdict(dict)
    for rid, rc in kd.items():
        mp = model.get(rid); ap = api.get(rid)
        if not mp or len(mp) != 6 or not ap or len(ap) != 6:
            continue
        m_order = sorted(range(1, 7), key=lambda w: mp.get(w, 0), reverse=True)
        a_order = sorted(range(1, 7), key=lambda w: ap.get(w, 0), reverse=True)
        honmei, taikou = m_order[0], m_order[1]
        fin = rc["fin"]
        win = next((w for w in range(1, 7) if fin.get(w) == 1), None)
        placed = set(w for w in range(1, 7) if fin.get(w) in (1, 2))

        def single(who, kind):
            if not finished(rc, who): return (0, 0)
            if kind == "tan":
                if not rc["tan"]: return (0, 0)
                return (100, rc["tan"][1] if win == who else 0)
            else:
                return (100, rc["fuku"].get(who, 0) if who in placed else 0)

        out[rid]["単勝本命"] = single(honmei, "tan")
        out[rid]["複勝本命"] = single(honmei, "fuku")
        out[rid]["単勝対抗"] = single(taikou, "tan")

        # 3連単 対抗アタマ6点
        T = [m_order[0], m_order[2], m_order[3]]
        pts = [(taikou, x, y) for x in T for y in T if x != y]
        inv = set(w for c in pts for w in c)
        if all(finished(rc, w) for w in inv):
            pay = 0
            if rc["st"] and rc["st"][0] in pts: pay = rc["st"][1]
            out[rid]["3単対抗6点"] = (600, pay)
        else:
            out[rid]["3単対抗6点"] = (0, 0)
    return out

# ───────── シミュレーション ─────────
def simulate(bets, dl, strat):
    """日別に締切順で買う。stop-win: 累積>100%で打ち切り。"""
    byday = defaultdict(list)   # date -> [(time, rid)]
    for rid in bets:
        if strat not in bets[rid]: continue
        s, _ = bets[rid][strat]
        if s == 0: continue
        t = dl.get(rid)
        if t is None: continue
        byday[rid[2:10]].append((t, rid))

    full_stake = full_ret = 0
    sw_stake = sw_ret = 0
    days = 0; days_stopped = 0; races_full = 0; races_sw = 0
    day_roi_full = []; day_roi_sw = []
    for d, lst in byday.items():
        lst.sort()
        days += 1
        # 全買い
        fs = fr = 0
        for _, rid in lst:
            s, r = bets[rid][strat]; fs += s; fr += r
        full_stake += fs; full_ret += fr; races_full += len(lst)
        if fs: day_roi_full.append(fr / fs)
        # 利確
        cs = cr = 0; nb = 0; stopped = False
        for _, rid in lst:
            s, r = bets[rid][strat]; cs += s; cr += r; nb += 1
            if cr > cs:                      # 累積回収率 > 100%
                stopped = True; break
        sw_stake += cs; sw_ret += cr; races_sw += nb
        if stopped: days_stopped += 1
        if cs: day_roi_sw.append(cr / cs)
    return {
        "days": days, "days_stopped": days_stopped,
        "full_roi": full_ret / full_stake * 100 if full_stake else 0,
        "sw_roi": sw_ret / sw_stake * 100 if sw_stake else 0,
        "full_stake": full_stake, "full_ret": full_ret,
        "sw_stake": sw_stake, "sw_ret": sw_ret,
        "races_full": races_full, "races_sw": races_sw,
        "day_win_full": sum(1 for x in day_roi_full if x >= 1.0),
        "day_win_sw": sum(1 for x in day_roi_sw if x >= 1.0),
        "n_days_full": len(day_roi_full), "n_days_sw": len(day_roi_sw),
    }

def main():
    print("データ読込中…")
    model = A.load_predict(); api = A.load_api(); kd = A.load_all_ktxt()
    dl = load_deadlines()
    print(f"締切時刻あり: {len(dl)} レース")
    bets = make_bets(kd, model, api)

    strategies = ["単勝本命", "複勝本命", "単勝対抗", "3単対抗6点"]
    print("\n【利確ルール検証】その日の累積回収率>100%で打ち切り vs 全レース買い")
    print("買い方は各点100円・非完走艇=返還。時系列＝締切時刻順（全会場横断）。\n")
    hdr = f"{'買い方':<12}{'全買い回収率':>12}{'利確版回収率':>12}{'利確で終えた日':>14}{'黒字で終わる日(全/利確)':>22}"
    print(hdr); print("-" * len(hdr.encode('ascii','ignore')) if False else "-" * 84)
    for st in strategies:
        r = simulate(bets, dl, st)
        dw_full = f"{r['day_win_full']}/{r['n_days_full']}"
        dw_sw = f"{r['day_win_sw']}/{r['n_days_sw']}"
        print(f"{st:<12}{r['full_roi']:>11.1f}%{r['sw_roi']:>11.1f}%"
              f"{r['days_stopped']:>9}/{r['days']:<4}"
              f"   全{dw_full} 利確{dw_sw}")
    print()
    # 詳細（3単対抗6点で投資額・利益の実額）
    for st in ["複勝本命", "3単対抗6点"]:
        r = simulate(bets, dl, st)
        print(f"[{st}] 全買い: 投資¥{r['full_stake']:,} 回収¥{r['full_ret']:,} "
              f"収支¥{r['full_ret']-r['full_stake']:,}（{r['races_full']}レース）")
        print(f"{' '*len(st)}   利確: 投資¥{r['sw_stake']:,} 回収¥{r['sw_ret']:,} "
              f"収支¥{r['sw_ret']-r['sw_stake']:,}（{r['races_sw']}レース／{r['days']}日中{r['days_stopped']}日は利確で終了）")
    print("\n※利確ルールは『賭ける総額』を減らすだけで、1円あたりの回収率(=期待値)は変えられない。")

if __name__ == "__main__":
    main()
