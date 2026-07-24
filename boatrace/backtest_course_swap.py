# -*- coding: utf-8 -*-
"""
進入（コース）入れ替わりが「結果」と「予想」にどれだけ効くかを実測する。
=========================================================================
朝モデル(predict_win.csv)は各艇を「枠番」で評価する。しかし本番では前づけ等で
進入コースが枠番とズレる。その入れ替わりが

  (1) どれくらいの頻度で起きるか
  (2) 結果(1着の分布)をどう変えるか … 枠 vs 進入コースのどちらが効くか
  (3) 朝予想(本命=最尤枠)の的中率をどれだけ下げるか … 予想への影響度

を、K-file(過去約1年)の実結果で採点する。さらに直前情報履歴(data/before)の
展示進入(ex.course)が、この入れ替わりを事前に見抜けるかも小サンプルで確認する。

使い方: py -3 backtest_course_swap.py
"""
import csv
import glob
import json
from collections import defaultdict

from features_player_history import VENUE_CODE


def race_id_of(r):
    code = VENUE_CODE.get(r["会場"], "00")
    y, m, d = r["日付"].split("/")
    return f"{code}{int(y):04d}{int(m):02d}{int(d):02d}{int(r['レース']):02d}"


def load_kfiles(glob_pat="data/k*.csv"):
    races = defaultdict(list)
    for p in sorted(glob.glob(glob_pat)):
        with open(p, encoding="cp932") as f:
            for r in csv.DictReader(f):
                races[race_id_of(r)].append(r)
    return races


def load_pwin(path="predict_win.csv"):
    """race_id -> [p_win(枠1..6)]。cp932・列名も cp932。位置で取る。"""
    out = defaultdict(lambda: [None] * 6)
    with open(path, encoding="cp932") as f:
        rr = csv.reader(f)
        next(rr)
        for row in rr:
            if len(row) < 4:
                continue
            rid, waku, p = row[0], row[1], row[3]
            try:
                out[rid][int(waku) - 1] = float(p)
            except (ValueError, IndexError):
                pass
    return out


def as_int(x):
    try:
        return int(x)
    except (ValueError, TypeError):
        return None


def main():
    races = load_kfiles()
    pwin = load_pwin()

    # ---- 集計器 ----
    n_races = 0
    n_swap = 0                 # 進入≠枠 の艇を含むレース
    n_clean = 0
    # 本命的中（朝モデル最尤枠 == 1着枠）
    hit_all = hit_clean = hit_swap = 0
    tot_all = tot_clean = tot_swap = 0
    # 1着が「内に入った艇(前づけ)」だった回数（入れ替わりレース内）
    swap_winner_moved_in = 0
    swap_winner_frame1 = 0     # 入れ替わりレースで枠1が1着
    swap_winner_course1 = 0    # 入れ替わりレースで進入1が1着
    # 枠なり vs 全体での枠1・進入1 の1着率
    clean_frame1_win = 0
    swap_races_valid = 0
    # 前づけした艇そのものの成績（内に入った艇は1着になりやすい？）
    movedin_boats = 0
    movedin_boats_win = 0
    # 枠1が押し出された（枠1の進入>1）レースでの枠1の1着率
    frame1_pushed = 0
    frame1_pushed_win = 0
    frame1_kept = 0
    frame1_kept_win = 0

    for rid, rs in races.items():
        fins = [r for r in rs if (r.get("status") or "finish") == "finish"]
        if len(fins) < 4:
            continue
        # 艇番→進入, 着順
        frame_course = {}
        winner_frame = None
        for r in fins:
            fr = as_int(r["艇番"]); co = as_int(r["進入コース"]); rk = as_int(r["着順"])
            if fr is None:
                continue
            frame_course[fr] = co
            if rk == 1:
                winner_frame = fr
        if winner_frame is None:
            continue
        n_races += 1

        swapped = any(co is not None and co != fr for fr, co in frame_course.items())
        winner_course = frame_course.get(winner_frame)

        # 前づけ艇（進入<枠）の成績
        for fr, co in frame_course.items():
            if co is not None and co < fr:   # 内に入った
                movedin_boats += 1
                if fr == winner_frame:
                    movedin_boats_win += 1

        # 枠1の押し出され判定
        c1 = frame_course.get(1)
        if c1 is not None:
            if c1 > 1:
                frame1_pushed += 1
                if winner_frame == 1:
                    frame1_pushed_win += 1
            else:
                frame1_kept += 1
                if winner_frame == 1:
                    frame1_kept_win += 1

        # 本命的中（朝モデル）
        pw = pwin.get(rid)
        has_pw = pw and all(p is not None for p in pw)
        if has_pw:
            fav = pw.index(max(pw)) + 1
            hit = 1 if fav == winner_frame else 0
            hit_all += hit; tot_all += 1

        if swapped:
            n_swap += 1
            swap_races_valid += 1
            if winner_course is not None and winner_frame is not None and winner_course < winner_frame:
                swap_winner_moved_in += 1
            if winner_frame == 1:
                swap_winner_frame1 += 1
            if winner_course == 1:
                swap_winner_course1 += 1
            if has_pw:
                hit_swap += hit; tot_swap += 1
        else:
            n_clean += 1
            if winner_frame == 1:
                clean_frame1_win += 1
            if has_pw:
                hit_clean += hit; tot_clean += 1

    print("=" * 66)
    print("【1】進入入れ替わりの頻度（K-file 実結果, {} レース）".format(n_races))
    print("-" * 66)
    print(f"  枠なり(進入=枠)      : {n_clean:6d}  ({n_clean/n_races*100:5.1f}%)")
    print(f"  入れ替わりあり       : {n_swap:6d}  ({n_swap/n_races*100:5.1f}%)")
    print()

    print("=" * 66)
    print("【2】結果への影響（1着はどこから出るか）")
    print("-" * 66)
    print(f"  枠なりレースで枠1が1着       : {clean_frame1_win/max(n_clean,1)*100:5.1f}%")
    print(f"  入れ替わりレースで枠1が1着   : {swap_winner_frame1/max(n_swap,1)*100:5.1f}%")
    print(f"  入れ替わりレースで進入1が1着 : {swap_winner_course1/max(n_swap,1)*100:5.1f}%")
    print(f"    → 入れ替わり時は『枠1』より『進入1』の方が1着に近い"
          f"（差 {(swap_winner_course1-swap_winner_frame1)/max(n_swap,1)*100:+.1f}pt）")
    print()
    print(f"  入れ替わりレースで1着が『前づけ艇(進入<枠)』だった: "
          f"{swap_winner_moved_in/max(n_swap,1)*100:5.1f}%")
    print(f"  前づけ艇そのものの1着率 : {movedin_boats_win}/{movedin_boats} = "
          f"{movedin_boats_win/max(movedin_boats,1)*100:5.1f}%"
          f"（枠平均1着=約{100/6:.1f}%より高いか）")
    print()
    print(f"  枠1が枠なり(進入1)を保った時の1着率 : "
          f"{frame1_kept_win}/{frame1_kept} = {frame1_kept_win/max(frame1_kept,1)*100:5.1f}%")
    print(f"  枠1が押し出された(進入>1)時の1着率  : "
          f"{frame1_pushed_win}/{frame1_pushed} = {frame1_pushed_win/max(frame1_pushed,1)*100:5.1f}%")
    print()

    print("=" * 66)
    print("【3】朝予想(本命=最尤枠)の的中率への影響")
    print("-" * 66)
    print(f"  全体       : {hit_all}/{tot_all} = {hit_all/max(tot_all,1)*100:5.2f}%")
    print(f"  枠なり     : {hit_clean}/{tot_clean} = {hit_clean/max(tot_clean,1)*100:5.2f}%")
    print(f"  入れ替わり : {hit_swap}/{tot_swap} = {hit_swap/max(tot_swap,1)*100:5.2f}%")
    if tot_clean and tot_swap:
        print(f"    → 入れ替わりレースは本命的中が "
              f"{hit_swap/tot_swap*100 - hit_clean/tot_clean*100:+.2f}pt 悪化")
    print()

    # ---- 【4】展示進入(ex.course)で事前に見抜けるか（before/*.json）----
    bt_records = 0
    ex_pred_swap = 0        # 展示で入れ替わりを示唆
    ex_correct = 0         # 展示入れ替わり示唆 → 本番も入れ替わり
    ex_says_clean_but_swap = 0
    for f in sorted(glob.glob("data/before/*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for rid, v in d.items():
            co = ((v or {}).get("ex") or {}).get("course")
            fin = ((v or {}).get("result") or {}).get("fin")
            if not co or not fin or len(fin) < 6:
                continue
            # 本番の入れ替わり: K-file から
            rs = races.get(rid)
            if not rs:
                continue
            fins = [r for r in rs if (r.get("status") or "finish") == "finish"]
            actual_swap = any(as_int(r["進入コース"]) is not None and
                              as_int(r["進入コース"]) != as_int(r["艇番"]) for r in fins)
            # 展示進入: co は枠1..6の展示コース。枠番と違えば示唆
            ex_swap = any(c is not None and (i + 1) != c for i, c in enumerate(co))
            bt_records += 1
            if ex_swap:
                ex_pred_swap += 1
                if actual_swap:
                    ex_correct += 1
            elif actual_swap:
                ex_says_clean_but_swap += 1
    if bt_records:
        print("=" * 66)
        print(f"【4】展示進入(ex.course)で入れ替わりを事前に見抜けるか"
              f"（直前情報 {bt_records} レース・小サンプル）")
        print("-" * 66)
        print(f"  展示で入れ替わり示唆     : {ex_pred_swap}"
              f"（うち本番も入れ替わり {ex_correct} = "
              f"{ex_correct/max(ex_pred_swap,1)*100:4.0f}%）")
        print(f"  展示は枠なり→本番入れ替わり(見逃し) : {ex_says_clean_but_swap}")
        print()

    # ---- 【5】展示示唆swap レースで本命をどう扱うと当たるか ----
    #   a) 朝モデルの最尤枠をそのまま本命
    #   b) 展示で進入1に入った艇（前づけ先頭）を本命に差し替え
    a_hit = a_tot = 0
    b_hit = 0
    for f in sorted(glob.glob("data/before/*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for rid, v in d.items():
            co = ((v or {}).get("ex") or {}).get("course")
            fin = ((v or {}).get("result") or {}).get("fin")
            if not co or not fin or len(fin) < 6:
                continue
            ex_swap = any(c is not None and (i + 1) != c for i, c in enumerate(co))
            if not ex_swap:
                continue
            pw = pwin.get(rid)
            if not pw or any(p is None for p in pw):
                continue
            winner_frame = None
            for i, v2 in enumerate(fin):
                if v2 == 1:
                    winner_frame = i + 1
            if winner_frame is None:
                continue
            a_tot += 1
            fav_model = pw.index(max(pw)) + 1
            if fav_model == winner_frame:
                a_hit += 1
            # 展示で進入1に入った枠を本命に
            fav_ex = fav_model
            for i, c in enumerate(co):
                if c == 1:
                    fav_ex = i + 1
                    break
            if fav_ex == winner_frame:
                b_hit += 1
    if a_tot:
        print("=" * 66)
        print(f"【5】展示で入れ替わり示唆のレース({a_tot}件)で本命の当て方を比較")
        print("-" * 66)
        print(f"  a) 朝モデル最尤枠のまま本命      : {a_hit}/{a_tot} = {a_hit/a_tot*100:5.1f}%")
        print(f"  b) 展示で進入1に入った艇を本命に : {b_hit}/{a_tot} = {b_hit/a_tot*100:5.1f}%")
        print(f"     → 差 {(b_hit-a_hit)/a_tot*100:+.1f}pt")
        print()


if __name__ == "__main__":
    main()
