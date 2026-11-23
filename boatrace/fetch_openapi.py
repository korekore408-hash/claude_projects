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
                # 持ちフライング(F数)/出遅れ(L数): 選手が現在保有する事故点（期内・斡旋停止に直結）。
                # 今節フライング（この開催内）とは別物。フィールドが無い時は None（表示しない）。
                "F数": b.get("racer_flying_count"),
                "L数": b.get("racer_late_count"),
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


def fetch_flying(date_str=None):
    """race_id -> [F数×6]（艇番1..6順・選手の持ちフライング）。当日のみ・失敗/欠損時は空 {}。
    「持ちフライング」＝選手が現在保有するフライング事故点（期内・次のFで即帰郷/斡旋停止のリスク）。
    今節フライング（この開催中のF・B-fileの今節成績由来）とは別物。build_today が当日レースに付与。
    フィードにF数が無い/全艇 None のレースは載せない（＝表示しない・縮退安全）。"""
    out = {}
    for p in fetch_programs(date_str):
        arr = [None] * 6
        for b in p.get("boats", []):
            try:
                i = int(b.get("艇番")) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= i < 6:
                f = b.get("F数")
                try:
                    arr[i] = int(f) if f is not None else None
                except (TypeError, ValueError):
                    arr[i] = None
        if any(v is not None for v in arr):
            out[p["race_id"]] = arr
    return out


# ============================================================
# B-file フォールバック: 公式 B-file(出走表) が取得できない日、OpenAPI フィードから
# fetch_range.py が保存するのと同形式の「番組表CSV行」を生成する。
#   クロスチェック(data/openapi_crosscheck_log.csv)で (艇番→登番) が B-file と n_diff=0
#   で一致し続けているため、B-file 欠落時の代替入力として妥当。OpenAPI は「当日」専用
#   フィードなので、対象日が当日のときのみ有効（過去日は空）。
#   欠損フィールドは "" にする（下流の to_float は "" を None 扱いで縮退安全）。
# ============================================================

# 場コード(int) → 会場名。features_* は 会場名→コード で突合するため名称で出力する。
_CODE_TO_VENUE = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川", 6: "浜名湖",
    7: "蒲郡", 8: "常滑", 9: "津", 10: "三国", 11: "びわこ", 12: "住之江",
    13: "尼崎", 14: "鳴門", 15: "丸亀", 16: "児島", 17: "宮島", 18: "徳山",
    19: "下関", 20: "若松", 21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}
# OpenAPI racer_class_number(1..4) → 公式級別ラベル（B-file 級別 と同形式）。
_CLASS_NUM_TO_LABEL = {1: "A1", 2: "A2", 3: "B1", 4: "B2"}


# data/b{yymmdd}.csv の正式な列（to_csv.convert_program 出力・cp932・18列）。
# ★日付列は無い（features_player_history はファイル名YYMMDDから日付を導く）。
BFILE_COLUMNS = [
    "会場", "レース", "艇番", "登番", "選手名", "年齢", "支部", "体重", "級別",
    "全国勝率", "全国2率", "当地勝率", "当地2率", "モーター番号", "モーター2率",
    "ボート番号", "ボート2率", "今節成績ほか",
]


def program_rows_for_bfile(date_str):
    """OpenAPI フィードから B-file 番組表CSV(data/b*.csv)と同じ列の行リストを返す
    （失敗/非当日は []）。列は BFILE_COLUMNS（18列・日付列なし）。
    下流が使う列を優先し、未提供列は ""（to_float は "" を None 扱いで縮退安全）。"""
    def g(d, *keys):
        """最初に見つかった非None値を返す（フィールド名ゆらぎ対策）。無ければ ""。"""
        for k in keys:
            v = d.get(k)
            if v is not None:
                return v
        return ""

    rows = []
    for p in _raw(date_str):
        try:
            code = int(p["race_stadium_number"])
            race_no = int(p["race_number"])
        except (KeyError, ValueError, TypeError):
            continue
        venue = _CODE_TO_VENUE.get(code, "")
        for b in p.get("boats", []):
            cls = _CLASS_NUM_TO_LABEL.get(b.get("racer_class_number"), "")
            reg = b.get("racer_number")
            rows.append({
                "会場": venue, "レース": race_no,
                "艇番": g(b, "racer_boat_number"),
                "登番": (str(reg) if reg is not None else ""),
                "選手名": g(b, "racer_name"),
                "年齢": g(b, "racer_age"),
                "支部": "",
                "体重": g(b, "racer_weight"),
                "級別": cls,
                "全国勝率": g(b, "racer_national_top_1_percent"),
                "全国2率": g(b, "racer_national_top_2_percent"),
                "当地勝率": g(b, "racer_local_top_1_percent"),
                "当地2率": g(b, "racer_local_top_2_percent"),
                "モーター番号": g(b, "racer_assigned_motor_number"),
                "モーター2率": g(b, "racer_assigned_motor_top_2_percent",
                                 "racer_motor_top_2_percent"),
                "ボート番号": g(b, "racer_assigned_boat_number", "racer_boat_number_assigned"),
                "ボート2率": g(b, "racer_assigned_boat_top_2_percent",
                               "racer_boat_top_2_percent"),
                "今節成績ほか": "",
            })
    return rows


def write_bfile_fallback(date_str, force=False):
    """公式B-fileが無い日、OpenAPIから data/b{yymmdd}.csv を生成する（cp932・18列）。
    既に非空の data/b{yymmdd}.csv があれば生成しない（本物のB-fileを優先）＝冪等。
    戻り値: 書き出した行数（スキップ/失敗時 0）。daily.py から当日分だけ呼ぶ想定。"""
    import csv
    import os
    yymmdd = date_str[2:4] + date_str[4:6] + date_str[6:8]
    path = f"data/b{yymmdd}.csv"
    if not force and os.path.exists(path) and os.path.getsize(path) > 0:
        print(f"  [bfile-fallback] {path} 既存（本物B-file優先）— スキップ")
        return 0
    rows = program_rows_for_bfile(date_str)
    if not rows:
        print(f"  [bfile-fallback] OpenAPI 0件（当日データ無し/通信失敗）— 生成せず")
        return 0
    os.makedirs("data", exist_ok=True)
    # cp932 に無い文字（稀な人名漢字等）は "?" に置換して落とさない（errors="replace"）。
    with open(path, "w", encoding="cp932", newline="", errors="replace") as f:
        w = csv.DictWriter(f, fieldnames=BFILE_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"  [bfile-fallback] {path} を OpenAPI から生成: {len(rows)}行")
    return len(rows)


# ================================================================
# K-file（競走成績）フォールバック: 公式K(結果LZH)欠落日を OpenAPI results で補完
# ================================================================
# data/k{yymmdd}.csv（k_to_csv.py 出力）と同じ列。1艇1行。完走行は全列、非完走行は着順空+status。
KFILE_COLUMNS = [
    "会場", "日付", "レース", "着順", "艇番", "登番", "選手名", "status",
    "モーター番号", "ボート番号", "展示タイム", "進入コース", "スタートタイミング",
    "レースタイム", "決まり手", "距離", "天候", "風向", "風速", "波高",
    "3連単_組合", "3連単_配当", "3連複_組合", "3連複_配当",
    "2連単_組合", "2連単_配当", "2連複_組合", "2連複_配当",
]
# race_technique_number → 決まり手(和名)。公式の決まり手コード。
_TECH_NUM_TO_KM = {1: "逃げ", 2: "差し", 3: "まくり", 4: "まくり差し", 5: "抜き", 6: "恵まれ"}

_RESULTS_URL_TMPL = "https://boatraceopenapi.github.io/results/v2/{y}/{ymd}.json"
_RESULTS_TODAY_URL = "https://boatraceopenapi.github.io/results/v2/today.json"


def _fetch_results(date_str, today_ymd=None):
    """results フィードの results リストを返す（失敗は []、未配信日は 404→[]）。
    date_str==今日 なら today.json（確定前の途中状態）、過去日は日付別ファイル(確定)。"""
    url = (_RESULTS_TODAY_URL if (today_ymd and date_str == today_ymd)
           else _RESULTS_URL_TMPL.format(y=date_str[:4], ymd=date_str))
    last = "?"
    for i in range(_MAX_TRIES):
        try:
            res = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            last = f"通信失敗 {type(e).__name__}"
        else:
            if res.status_code != 200:
                last = f"HTTP {res.status_code}"      # 未配信日は404
                if res.status_code == 404:
                    return []                          # その日は無い＝即終了（リトライ不要）
            else:
                try:
                    return res.json().get("results", [])   # 空でもそのまま（中止日など）
                except ValueError:
                    last = "JSON parse 失敗"
        if i < _MAX_TRIES - 1:
            time.sleep(_BACKOFF[min(i, len(_BACKOFF) - 1)])
    print(f"  [kfile-fallback] results 取得できず {date_str}: {last}")
    return []


def results_rows_for_kfile(date_str, today_ymd=None):
    """OpenAPI results → K-file CSV(data/k*.csv)と同じ列の行リスト（1艇1行）。失敗/空は []。
    着順=racer_place_number(1..6=着・それ以外は非完走で着順空)。配当は payouts の金額。
    下流が使う列（着順/艇番/登番/会場/status/配当）を優先。未提供列は ""（to_float/guard で縮退安全）。"""
    def first(a):
        return a[0] if isinstance(a, list) and a else None

    def comb_pay(pay, key):
        e = first((pay or {}).get(key))
        if not e:
            return "", ""
        comb = str(e.get("combination", "")).replace("=", "-")
        amt = e.get("payout")
        return comb, (amt if amt is not None else "")

    date_fmt = f"{int(date_str[:4])}/{int(date_str[4:6])}/{int(date_str[6:8])}"
    rows = []
    for rs in _fetch_results(date_str, today_ymd):
        try:
            code = int(rs["race_stadium_number"])
            race_no = int(rs["race_number"])
        except (KeyError, ValueError, TypeError):
            continue
        venue = _CODE_TO_VENUE.get(code, "")
        pay = rs.get("payouts") or {}
        p3c, p3a = comb_pay(pay, "trifecta")
        p3fc, p3fa = comb_pay(pay, "trio")
        p2c, p2a = comb_pay(pay, "exacta")
        p2fc, p2fa = comb_pay(pay, "quinella")
        km = _TECH_NUM_TO_KM.get(rs.get("race_technique_number"), "")
        wind = rs.get("race_wind")
        wave = rs.get("race_wave")
        for b in rs.get("boats") or []:
            try:
                waku = int(b["racer_boat_number"])
            except (KeyError, ValueError, TypeError):
                continue
            reg = b.get("racer_number")
            pl = b.get("racer_place_number")
            st = b.get("racer_start_timing")
            finisher = isinstance(pl, int) and 1 <= pl <= 6
            if finisher:
                status, chaku = "finish", pl
            elif isinstance(st, (int, float)) and st < 0:
                status, chaku = "F", ""            # フライング（flying_rate 用）
            else:
                status, chaku = "K0", ""           # 欠場/失格など（starts 分母から除外）
            row = {c: "" for c in KFILE_COLUMNS}
            row["会場"] = venue
            row["日付"] = date_fmt
            row["レース"] = race_no
            row["着順"] = chaku
            row["艇番"] = waku
            row["登番"] = (str(reg) if reg is not None else "")
            row["選手名"] = b.get("racer_name", "") or ""
            row["status"] = status
            if b.get("racer_course_number") is not None:
                row["進入コース"] = b["racer_course_number"]
            if st is not None:
                row["スタートタイミング"] = st
            if finisher:                            # 決まり手/気象/配当は完走行に（k_to_csv と同様）
                row["決まり手"] = km
                if wind is not None:
                    row["風速"] = wind
                if wave is not None:
                    row["波高"] = wave
                row["3連単_組合"], row["3連単_配当"] = p3c, p3a
                row["3連複_組合"], row["3連複_配当"] = p3fc, p3fa
                row["2連単_組合"], row["2連単_配当"] = p2c, p2a
                row["2連複_組合"], row["2連複_配当"] = p2fc, p2fa
            rows.append(row)
    return rows


def write_kfile_fallback(date_str, force=False, today_ymd=None):
    """公式K-fileが無い日、OpenAPI results から data/k{yymmdd}.csv を生成（cp932・k_to_csv同形式）。
    既に非空の data/k{yymmdd}.csv があれば生成しない（本物のK-fileを優先）＝冪等。
    1着(確定)が1つも無い日（全レース未確定/中止）は生成しない＝決着前の空ファイルを作らない。
    戻り値: 書き出した行数（スキップ/失敗時 0）。"""
    import csv
    import os
    yymmdd = date_str[2:4] + date_str[4:6] + date_str[6:8]
    path = f"data/k{yymmdd}.csv"
    if not force and os.path.exists(path) and os.path.getsize(path) > 0:
        print(f"  [kfile-fallback] {path} 既存（本物K-file優先）— スキップ")
        return 0
    rows = results_rows_for_kfile(date_str, today_ymd)
    if not rows:
        print(f"  [kfile-fallback] results 0件 {date_str}（未配信/中止/通信失敗）— 生成せず")
        return 0
    if not any(r["status"] == "finish" and str(r["着順"]) == "1" for r in rows):
        print(f"  [kfile-fallback] {date_str} 確定(1着)レース無し — 生成せず")
        return 0
    os.makedirs("data", exist_ok=True)
    with open(path, "w", encoding="cp932", newline="", errors="replace") as f:
        w = csv.DictWriter(f, fieldnames=KFILE_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    nfin = sum(1 for r in rows if r["status"] == "finish")
    print(f"  [kfile-fallback] {path} を OpenAPI results から生成: {len(rows)}行(完走{nfin})")
    return len(rows)


def backfill_kfiles(end_ymd, days=35, force=False):
    """end_ymd(含む・通常=昨日)から days 日さかのぼり、K-CSV が無い日を OpenAPI results で生成。
    本物K優先・冪等。公式K欠落で止まった 10万円チャレンジ/場別成績/過去日結果の復旧用。
    戻り値: (生成した日数, 総行数)。"""
    today_ymd = datetime.date.today().strftime("%Y%m%d")
    end = datetime.date(int(end_ymd[:4]), int(end_ymd[4:6]), int(end_ymd[6:8]))
    n_days, n_rows = 0, 0
    for i in range(days):
        d = (end - datetime.timedelta(days=i)).strftime("%Y%m%d")
        r = write_kfile_fallback(d, force=force, today_ymd=today_ymd)
        if r:
            n_days += 1
            n_rows += r
    print(f"  [kfile-backfill] {end_ymd} から{days}日: {n_days}日 生成 / 計{n_rows}行")
    return n_days, n_rows


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
    ap.add_argument("--bfile-fallback", action="store_true",
                    help="公式B-fileが無い日、OpenAPIから data/b{yymmdd}.csv を生成（既存は温存）")
    ap.add_argument("--kfile-fallback", action="store_true",
                    help="公式K-fileが無い日、OpenAPI results から data/k{yymmdd}.csv を生成（既存は温存）")
    ap.add_argument("--kfile-backfill", action="store_true",
                    help="--end から --days 日さかのぼり、K-CSV が無い日を OpenAPI results で補完")
    ap.add_argument("--end", default=None,
                    help="--kfile-backfill の終端日 YYYYMMDD（既定=昨日）")
    ap.add_argument("--days", type=int, default=35,
                    help="--kfile-backfill でさかのぼる日数（既定35）")
    ap.add_argument("--force", action="store_true",
                    help="--bfile-fallback/--kfile-fallback 時、既存CSVがあっても上書きする")
    args = ap.parse_args()
    d = args.date.replace("-", "")

    if args.times:
        ts = fetch_start_times(d)
        print(f"date={d}  発走時刻 {len(ts)}レース")
        for rid in sorted(ts):
            print(f"  {rid}: {ts[rid]}")
    elif getattr(args, "bfile_fallback", False):
        write_bfile_fallback(d, force=args.force)
    elif getattr(args, "kfile_backfill", False):
        end = (args.end.replace("-", "") if args.end
               else (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d"))
        backfill_kfiles(end, days=args.days, force=args.force)
    elif getattr(args, "kfile_fallback", False):
        write_kfile_fallback(d, force=args.force,
                             today_ymd=datetime.date.today().strftime("%Y%m%d"))
    elif args.log:
        log_crosscheck(d)
    else:
        crosscheck(d)
