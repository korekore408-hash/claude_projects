# -*- coding: utf-8 -*-
"""
非公式 OpenAPI 出走表（④ フォールバック/クロスチェック）
-------------------------------------------------------------------------
公式の番組表(B-file, LZH)が主データ。本モジュールはその「保険」と「発走時刻」用。

取得元: https://boatraceopenapi.github.io/programs/v2/today.json
  当日の全場×全レースを JSON で配信（艇番・登番・各種勝率・締切時刻など）。
  ※ 主データ(歴史的結果K・出走表B)は公式LZHのまま。ここは置換しない。

用途:
  1. fetch_start_times(): race_id -> "HH:MM"（締切＝発走予定時刻）。
     ※2026-07-20から主ソースは公式B-fileの『電話投票締切予定』(build_today.bfile_start_times)。
     ここはB-fileが読めない時のフォールバック（フィードは丸1日止まることがある）。
  2. fetch_programs(): 正規化した当日出走表。公式Bが取れない時のフォールバック源／
     艇番・登番のクロスチェック用。
  3. CLI（crosscheck）: 同一日の OpenAPI 由来と B-file 由来の (艇番→登番) を突き合わせ、
     差分0なら「フォールバックに使える」ことを確認する。

通信失敗時はすべて空（呼び出し側は公式Bにフォールバック）＝サイト/予想は止めない。

使い方:
  py -3 fetch_openapi.py                 # 当日の B-file とクロスチェック
  py -3 fetch_openapi.py --date 2026-06-27
  py -3 fetch_openapi.py --times         # race_id と発走時刻を一覧表示
"""
import datetime
import re
import time

import requests

URL = "https://boatraceopenapi.github.io/programs/v2/today.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (boatrace-study-script)"}

# OpenAPI の race_grade_number → 表示グレード（B-file/公式と概ね対応）。
_GRADE_NUM = {1: "SG", 2: "G1", 3: "G2", 4: "一般"}

# フィードは GitHub Pages 静的配信で、たまに一瞬 空/未更新 を返す。その1回で
# 締切時刻が全消えするのを防ぐため、取得を数回リトライ（指数バックオフ）する。
_MAX_TRIES = 4
_BACKOFF = (2, 4, 8)   # 秒


def _fetch_feed():
    """today.json 全体の programs リストを返す。通信失敗・非200・JSON不正・空応答は
    リトライし、全試行だめなら []（呼び出し側は公式Bにフォールバック＝サイトは止めない）。"""
    last = "?"
    for i in range(_MAX_TRIES):
        try:
            res = requests.get(URL, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            last = f"通信失敗 {type(e).__name__}"
        else:
            if res.status_code != 200:
                last = f"HTTP {res.status_code}"
            else:
                try:
                    progs = res.json().get("programs", [])
                except ValueError:
                    last = "JSON parse 失敗"
                else:
                    if progs:               # 中身あり＝成功
                        return progs
                    last = "programs 空"
        if i < _MAX_TRIES - 1:
            time.sleep(_BACKOFF[min(i, len(_BACKOFF) - 1)])
    print(f"  [fetch_openapi] today.json 取得できず（{_MAX_TRIES}回試行）: {last}")
    return []


def _raw(date_str=None):
    """today.json を取得して programs リストを返す。失敗時 []。date_str(YYYYMMDD) 指定時は絞り込み。
    date_str 指定で0件のときは、フィードが別日を配信している可能性を警告表示（時刻全消えの診断用）。"""
    progs = _fetch_feed()
    if not progs or not date_str:
        return progs
    ymd = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    sel = [p for p in progs if p.get("race_date") == ymd]
    if not sel:
        feed_dates = sorted({p.get("race_date") for p in progs})
        print(f"  [fetch_openapi] 要求日 {ymd} の programs が0件。"
              f"フィード配信日={feed_dates}（発走時刻が付きません）")
    return sel


def fetch_programs(date_str=None):
    """正規化した当日出走表を返す。失敗時 []。
    各要素: {race_id, code, race_no, date, closed_at, hm(発走HH:MM), grade, boats:[...]}
      boats[i] = {艇番, 登番, 選手名, 級別num, 全国勝率, 当地勝率, モーター番号}
    race_id は他モジュールと同形式: 場コード2桁 + YYYYMMDD + レース2桁。"""
    out = []
    for p in _raw(date_str):
        try:
            code = f"{int(p['race_stadium_number']):02d}"
            race_no = int(p["race_number"])
            ymd = p["race_date"].replace("-", "")
        except (KeyError, ValueError, TypeError):
            continue
        race_id = f"{code}{ymd}{race_no:02d}"
        closed = str(p.get("race_closed_at") or "")
        m = re.search(r"(\d{1,2}):(\d{2})", closed)
        hm = f"{int(m.group(1)):d}:{m.group(2)}" if m else ""
        boats = []
        for b in p.get("boats", []):
            boats.append({
                "艇番": b.get("racer_boat_number"),
                "登番": b.get("racer_number"),
                "選手名": b.get("racer_name", ""),
                "級別num": b.get("racer_class_number"),
                "全国勝率": b.get("racer_national_top_1_percent"),
                "当地勝率": b.get("racer_local_top_1_percent"),
                "モーター番号": b.get("racer_assigned_motor_number"),
            })
        out.append({
            "race_id": race_id, "code": code, "race_no": race_no,
            "date": p["race_date"], "closed_at": closed, "hm": hm,
            "grade": _GRADE_NUM.get(p.get("race_grade_number"), "一般"),
            "boats": boats,
        })
    return out


def fetch_start_times(date_str=None):
    """race_id -> "HH:MM"（締切＝発走予定時刻）。失敗時 {}。build_today から import して使う。"""
    return {p["race_id"]: p["hm"] for p in fetch_programs(date_str) if p["hm"]}


# ---- クロスチェック（CLI）: OpenAPI 由来 vs B-file 由来の (艇番→登番) 一致確認 ----
def _bfile_entry(date_str):
    """data/b{yymmdd}.txt（fetch_range が保存）を parse して {race_id: {艇番: 登番}}。"""
    import fetch_range
    from features_player_history import VENUE_CODE
    path = f"data/b{date_str[2:4]}{date_str[4:6]}{date_str[6:8]}.txt"
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    ent = {}
    for r in fetch_range.program_text_to_rows(text, iso):
        code = VENUE_CODE.get(r["会場"], "00")
        try:
            rid = f"{code}{date_str}{int(r['レース']):02d}"
            ent.setdefault(rid, {})[int(r["艇番"])] = int(r["登番"])
        except (ValueError, KeyError, TypeError):
            pass
    return ent


def crosscheck(date_str):
    """OpenAPI と B-file の (艇番→登番) を突き合わせ、差分件数を表示。差分0なら一致。"""
    progs = fetch_programs(date_str)
    if not progs:
        print("× OpenAPI 取得失敗（または当日データ無し）")
        return
    api = {}
    for p in progs:
        api[p["race_id"]] = {b["艇番"]: b["登番"] for b in p["boats"]
                             if b["艇番"] is not None}
    bf = _bfile_entry(date_str)
    if bf is None:
        print(f"B-file 未取得（data/b{date_str[2:]}.txt 無し）。OpenAPI のみ {len(api)}レース取得。")
        return

    common = set(api) & set(bf)
    only_api = set(api) - set(bf)
    only_bf = set(bf) - set(api)
    diff = 0
    for rid in sorted(common):
        if api[rid] != bf[rid]:
            diff += 1
            print(f"  差分 {rid}: API={api[rid]} / B={bf[rid]}")
    print(f"OpenAPI {len(api)}レース / B-file {len(bf)}レース / 共通 {len(common)}")
    print(f"  艇番→登番の不一致: {diff}レース"
          + ("（★完全一致＝フォールバック利用可）" if diff == 0 else ""))
    if only_api:
        print(f"  OpenAPIのみ: {len(only_api)}レース {sorted(only_api)[:5]}")
    if only_bf:
        print(f"  B-fileのみ: {len(only_bf)}レース {sorted(only_bf)[:5]}")


def log_crosscheck(date_str, path="data/openapi_crosscheck_log.csv"):
    """crosscheck 結果を 1 行 CSV に追記（daily.py から毎日呼び、数日分の一致を自動蓄積）。
    フォールバック本組込は、このログで n_diff=0 が数日続くのを確認してから有効化する。
    同じ date の既存行は上書き（再実行で重複しない）。失敗時 None。"""
    import csv
    import os
    progs = fetch_programs(date_str)
    api = {}
    for p in progs:
        api[p["race_id"]] = {b["艇番"]: b["登番"] for b in p["boats"]
                             if b["艇番"] is not None}
    n_times = sum(1 for p in progs if p.get("hm"))
    bf = _bfile_entry(date_str)
    if not progs:
        row = {"date": date_str, "n_api": 0, "n_bfile": (len(bf) if bf else 0),
               "n_common": 0, "n_diff": "", "n_times": 0, "note": "no_openapi"}
    elif bf is None:
        row = {"date": date_str, "n_api": len(api), "n_bfile": 0,
               "n_common": 0, "n_diff": "", "n_times": n_times, "note": "no_bfile"}
    else:
        common = set(api) & set(bf)
        diff = sum(1 for rid in common if api[rid] != bf[rid])
        ok = diff == 0 and len(api) > 0 and len(common) > 0
        row = {"date": date_str, "n_api": len(api), "n_bfile": len(bf),
               "n_common": len(common), "n_diff": diff, "n_times": n_times,
               "note": "match" if ok else "check"}
    fn = ["date", "n_api", "n_bfile", "n_common", "n_diff", "n_times", "note"]
    rows = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8", newline="") as f:
                rows = [r for r in csv.DictReader(f) if r.get("date") != date_str]
        except OSError:
            rows = []
    rows.append(row)
    rows.sort(key=lambda r: r.get("date", ""))
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fn)
        w.writeheader()
        w.writerows(rows)
    print(f"crosscheck log → {path}: {row}")
    return row


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="非公式OpenAPI出走表 取得・クロスチェック")
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y%m%d"),
                    help="対象日 YYYYMMDD（既定=今日）")
    ap.add_argument("--times", action="store_true", help="race_id と発走時刻を一覧表示")
    ap.add_argument("--log", action="store_true",
                    help="crosscheck 結果を data/openapi_crosscheck_log.csv に追記")
    args = ap.parse_args()
    d = args.date.replace("-", "")

    if args.times:
        ts = fetch_start_times(d)
        print(f"date={d}  発走時刻 {len(ts)}レース")
        for rid in sorted(ts):
            print(f"  {rid}: {ts[rid]}")
    elif args.log:
        log_crosscheck(d)
    else:
        crosscheck(d)
