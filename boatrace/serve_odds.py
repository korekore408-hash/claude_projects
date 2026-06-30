# -*- coding: utf-8 -*-
"""
ローカル配信サーバ（today.html 用・オッズ更新ボタンの中継）
-------------------------------------------------------------------------
today.html は静的ページなので、ブラウザから boatrace 公式へ直接 fetch すると
CORS でブロックされる。そこで PC 側でこの小サーバを立て、
  ・カレントフォルダのファイル（today.html 等）を配信
  ・GET /odds?id={race_id} で「押された時だけ」公式オッズを取得して JSON 返却
する。自動取得はしない（ボタン押下＝1リクエスト＝1レース分のみ）。

使い方:
  py -3.13 serve_odds.py                  # http://localhost:8787/today.html
  py -3.13 serve_odds.py --port 8787 --bind 0.0.0.0   # LANで携帯から
    （携帯は http://<PCのIP>:8787/today.html を開く。要ファイアウォール許可）

/odds の戻り値:
  {"id":"012026061801","2t":{"1-2":4.4,...},"3t":{"1-2-3":8.7,...},
   "fetched_at":"2026-06-18 21:00:12"}
"""

import os
import sys
import json
import argparse
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import fetch_odds      # 同フォルダの取得・パース関数を再利用
import fetch_before    # 展示（直前情報）・結果の取得

# 公式サイトは1リクエスト約9秒（接続レイテンシ）だが同時接続は捌ける。
# 逐次だと全場で約70分かかるため並列取得する。多重押下は1本に直列化。
UPDATE_WORKERS = 10
_update_lock = threading.Lock()


def parse_race_id(rid: str):
    """race_id '012026061801' -> (jcd=1, hd='20260618', rno=1)。不正なら None。"""
    if not (rid and len(rid) == 12 and rid.isdigit()):
        return None
    return int(rid[:2]), rid[2:10], int(rid[10:12])


# 展示・結果のディスクキャッシュ（確定済みレースは再取得しない）
BEFORE_DIR = os.path.join("data", "before")


def _cache_path(hd):
    return os.path.join(BEFORE_DIR, f"before_{hd}.json")


def _load_cache(hd):
    try:
        with open(_cache_path(hd), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(hd, cache):
    os.makedirs(BEFORE_DIR, exist_ok=True)
    with open(_cache_path(hd), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def _rec(d):
    return {"ex": d["ex"], "result": d["result"], "status": d["status"]}


def _complete(rec):
    """結果確定＋展示取得済み＝これ以上変わらない（再取得不要）。"""
    return bool(rec and rec.get("status") == "result" and rec.get("ex"))


def _merge(old, new):
    """以前その日に調べた情報(old)に最新取得(new)を重ねる。展示・結果は消さずに残す。
    （レース終了後の取得で展示が空でも、朝に取れた展示を保持し続けるのが狙い。）"""
    if not old:
        return new
    if not new:
        return old
    out = dict(new)
    if not out.get("ex") and old.get("ex"):            # 展示は一度取れたら残す
        out["ex"] = old["ex"]
    if not out.get("result") and old.get("result"):    # 結果は確定情報なので残す
        out["result"] = old["result"]
    out["status"] = ("result" if out.get("result")
                     else ("before" if out.get("ex") else "none"))
    if out.get("ev") is None and old.get("ev") is not None:   # EVは発走前に取れたら残す
        out["ev"] = old["ev"]
    return out


# ============================================================
# EV（期待値）付与：鉄板レースの実オッズを取得して 🎯勝負 を点灯させる
# ============================================================
_MODEL_CACHE = {"hd": None, "races": None}


def _build_models(hd):
    """当日の各レースの本命確率・買い目(ex2/ex3 とPL確率)を build_races で得る。
    予測CSVは1日不変なのでプロセス内キャッシュ（更新ごとの再読込を避ける）。"""
    if _MODEL_CACHE["hd"] == hd and _MODEL_CACHE["races"] is not None:
        return _MODEL_CACHE["races"]
    import build_today as bt
    from make_ai_yosou import build_races
    pred = {(r["race_id"], r["枠番"]): r for r in bt.load("predict_win.csv")}
    meta = {(r["race_id"], r["枠番"]): r for r in bt.load("features_race_relative.csv")}
    hist = {(r["race_id"], r["枠番"]): r for r in bt.load("features_player_history.csv")}
    races = build_races(hd, pred, meta, hist, {})       # payout不要（EVは確率×オッズ）
    _MODEL_CACHE.update(hd=hd, races=races)
    return races


def _merge_save_odds(hd, rows):
    """取得したオッズ行を data/odds/odds_{hd}.csv に**マージ保存**（既存行は消さない）。
    fetch_odds.save は上書きだが、こちらは30分毎の追記取得で過去取得分を保持する。"""
    if not rows:
        return
    import csv
    p = os.path.join("data", "odds", f"odds_{hd}.csv")
    cols = ["race_id", "bet_type", "combo", "odds", "fetched_at"]
    merged = {}
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    merged[(r["race_id"], r["bet_type"], r["combo"])] = r
        except OSError:
            pass
    for r in rows:
        merged[(r["race_id"], r["bet_type"], r["combo"])] = r   # 最新オッズで上書き
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for k in sorted(merged):
            w.writerow(merged[k])


def _attach_ev(hd, out, workers=UPDATE_WORKERS):
    """鉄板(hon≥0.65)かつ未確定のレースだけ実オッズを並列取得し、
    買い目(ex2/ex3)の中の最良 EV=モデル確率×実オッズ を rec['ev'] に入れる。
    取得したオッズは CSV にもマージ保存する（バックテスト/評価用キャッシュ）。"""
    try:
        models = {r["rid"]: r for r in _build_models(hd)}
    except Exception as e:                      # 予測CSV未生成など → EVなしで続行
        print(f"[ev] skip build_models: {e}")
        return
    targets = []
    for rid, rec in out.items():
        m = models.get(rid)
        if not m or m["hon"] < 0.65:            # 勝負バッジの対象は鉄板のみ
            continue
        if rec and rec.get("status") == "result":   # 終了レースはオッズ無し
            continue
        targets.append(rid)
    if not targets:
        return

    def fetch_one(rid):
        jcd, hd2, rno = parse_race_id(rid)
        tri = fetch_odds.fetch_trifecta(jcd, rno, hd2)
        exa = fetch_odds.fetch_exacta(jcd, rno, hd2)
        return rid, tri, exa

    fetched_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    n_ev = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rid, tri, exa in ex.map(fetch_one, targets):
            m = models[rid]
            best = 0.0
            for combo, p in m["ex2"]:
                od = exa.get(combo)
                if od:
                    best = max(best, p * od)
            for combo, p in m["ex3"]:
                od = tri.get(combo)
                if od:
                    best = max(best, p * od)
            if best > 0:
                rec = out.get(rid) or {}
                rec["ev"] = round(best, 3)
                out[rid] = rec
                n_ev += 1
            for (a, b, c), o in tri.items():
                rows.append({"race_id": rid, "bet_type": "3t",
                             "combo": f"{a}-{b}-{c}", "odds": o, "fetched_at": fetched_at})
            for (a, b), o in exa.items():
                rows.append({"race_id": rid, "bet_type": "2t",
                             "combo": f"{a}-{b}", "odds": o, "fetched_at": fetched_at})
    _merge_save_odds(hd, rows)
    print(f"[ev] 鉄板{len(targets)}R オッズ取得 → EV付与{n_ev}R / オッズ{len(rows)}行保存")


def collect_update(hd, workers=UPDATE_WORKERS, with_odds=False,
                   window=False, now=None, lead=7, lag=10,
                   retries=0, retry_wait=120):
    """当日の展示+結果を集める。取得済み（結果確定＋展示）はキャッシュ流用。
    その日に調べた展示・結果はすべて引き継いで返し、ディスクにも保存する。

    window=False（既定）: 従来の全場スイープ。①各場R1で開催場判定→②開催場R2..R12を並列取得。

    window=True（窓取得・targeted）: 発走時刻（非公式OpenAPI programs）を基準に、
      ・展示＋天候 ＝ 発走 lead 分前（既定7分）から
      ・結果＋配当 ＝ 発走 lag 分後（既定10分）から
      だけ取りに行く。窓外（まだ発走7分前より前）・取得済み・中止場（＝当日番組に無い）はスキップ。
      retries>0 なら、窓内なのにまだデータが空のレースを retry_wait 秒後（既定120＝2分）に
      再取得（最大 retries 回）。発走時刻が取れない日は全取得にフォールバック（取りこぼし防止）。
      ※ 各レースは必要な分（展示のみ／結果のみ）だけ取得し、無駄な HTTP を減らす。"""
    import time as _time
    cache = _load_cache(hd)
    out = dict(cache)            # その日これまでに調べた情報をすべて引き継ぐ

    start_times = {}
    if window:
        try:
            import fetch_openapi
            start_times = fetch_openapi.fetch_start_times(hd)   # {rid:"HH:MM"} 開催レースのみ
        except Exception as e:
            print(f"[window] 発走時刻の取得に失敗→全取得にフォールバック: {e}")

    if window and start_times:
        def fetch_one(t):
            jcd, rno, wb, wr = t
            return jcd, rno, fetch_before.fetch_race(jcd, rno, hd,
                                                     want_before=wb, want_result=wr)

        def scan():
            """今この瞬間に『窓内かつ未取得』のレース (jcd,rno,want_before,want_result) を列挙。"""
            cur = now or datetime.datetime.now()
            todo = []
            for rid, hm in start_times.items():
                pr = parse_race_id(rid)
                if not pr:
                    continue
                jcd, rhd, rno = pr
                if rhd != hd:
                    continue
                rec = out.get(rid)
                if _complete(rec):                       # 結果確定＋展示あり＝もう変わらない
                    continue
                try:
                    h, m = map(int, hm.split(":"))
                except (ValueError, AttributeError):
                    continue
                start = cur.replace(hour=h, minute=m, second=0, microsecond=0)
                have_ex = bool(rec and rec.get("ex"))
                have_res = bool(rec and rec.get("result"))
                need_ex = (not have_ex) and cur >= start - datetime.timedelta(minutes=lead)
                need_res = (not have_res) and cur >= start + datetime.timedelta(minutes=lag)
                if need_ex or need_res:
                    todo.append((jcd, rno, need_ex, need_res))
            return todo

        for attempt in range(retries + 1):
            todo = scan()
            label = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[window {label}] 試行{attempt + 1}/{retries + 1} 取得対象 {len(todo)}レース"
                  f"（開催{len(start_times)}・確定/窓外は流用）")
            if todo:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    for jcd, rno, d in ex.map(fetch_one, todo):
                        rid = f"{jcd:02d}{hd}{rno:02d}"
                        if d["status"] != "none":
                            out[rid] = _merge(out.get(rid), _rec(d))
            # 窓内なのにまだ空のレースが残っていれば retry_wait 秒後に再取得。
            if attempt < retries and scan():
                print(f"[window] 未取得が残存→{retry_wait}秒後に再取得")
                _time.sleep(retry_wait)
            else:
                break
    else:
        if window:
            print("[window] 発走時刻が無いため全取得にフォールバック")

        def fetch_one(jr):
            jcd, rno = jr
            return jcd, rno, fetch_before.fetch_race(jcd, rno, hd)

        # ① 開催場判定。完成済みR1は流用、それ以外の場だけR1を並列取得。
        held = set()
        need_r1 = []
        for jcd in range(1, 25):
            rid = f"{jcd:02d}{hd}01"
            if _complete(out.get(rid)):
                held.add(jcd)                              # 既に完成→再取得不要・開催場として維持
            else:
                need_r1.append((jcd, 1))
        if need_r1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for jcd, _rno, d in ex.map(fetch_one, need_r1):
                    rid = f"{jcd:02d}{hd}01"
                    if d["status"] != "none":
                        held.add(jcd)
                        out[rid] = _merge(out.get(rid), _rec(d))
                    elif rid in out:                       # 以前開催と判定済みなら維持
                        held.add(jcd)

        # ② 開催場の R2..R12。完成済みは流用、残りを並列取得してマージ。
        todo = []
        for jcd in sorted(held):
            for rno in range(2, 13):
                rid = f"{jcd:02d}{hd}{rno:02d}"
                if not _complete(out.get(rid)):
                    todo.append((jcd, rno))
        if todo:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for jcd, rno, d in ex.map(fetch_one, todo):
                    rid = f"{jcd:02d}{hd}{rno:02d}"
                    if d["status"] != "none":
                        out[rid] = _merge(out.get(rid), _rec(d))

    # ③ 鉄板レースの実オッズを取得して EV を付与（🎯勝負 バッジ点灯用）。
    if with_odds:
        _attach_ev(hd, out, workers=workers)

    # その日に調べた展示・結果をすべて保存（再押下・再起動・リロードで復元）。
    _save_cache(hd, out)
    return out


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        p = urlparse(self.path).path.rstrip("/")
        if p == "/odds":
            return self._odds()
        if p == "/update":
            return self._update()
        return super().do_GET()

    def _update(self):
        qs = parse_qs(urlparse(self.path).query)
        date = (qs.get("date") or [""])[0]             # YYYYMMDD or YYYY-MM-DD
        hd = date.replace("-", "")
        if not (len(hd) == 8 and hd.isdigit()):
            hd = datetime.date.today().strftime("%Y%m%d")
        with_odds = (qs.get("odds") or ["0"])[0] not in ("0", "false", "")  # &odds=1 でEVも取得
        # 多重押下ガード: 既に取得中なら走らせず busy を返す（暴走防止）。
        if not _update_lock.acquire(blocking=False):
            return self._json({"busy": True,
                               "msg": "前回の更新がまだ取得中です。完了までお待ちください。"}, 409)
        try:
            races = collect_update(hd, with_odds=with_odds)
        finally:
            _update_lock.release()
        body = {"date": hd,
                "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "races": races}
        n_res = sum(1 for r in races.values() if r["status"] == "result")
        self.log_message("update hd=%s races=%d result=%d", hd, len(races), n_res)
        return self._json(body)

    def _odds(self):
        qs = parse_qs(urlparse(self.path).query)
        rid = (qs.get("id") or [""])[0]
        parsed = parse_race_id(rid)
        if not parsed:
            return self._json({"error": "bad id"}, 400)
        jcd, hd, rno = parsed
        tri = fetch_odds.fetch_trifecta(jcd, rno, hd)
        exa = fetch_odds.fetch_exacta(jcd, rno, hd)
        body = {
            "id": rid,
            "2t": {f"{a}-{b}": o for (a, b), o in exa.items()},
            "3t": {f"{a}-{b}-{c}": o for (a, b, c), o in tri.items()},
            "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.log_message("odds id=%s 2t=%d 3t=%d", rid, len(exa), len(tri))
        return self._json(body)

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser(description="today.html 配信＋/odds 中継サーバ")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--bind", default="127.0.0.1", help="LAN配信は 0.0.0.0")
    args = ap.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    # pythonw.exe（コンソール無し）で常駐起動すると stdout/stderr が None になり、
    # アクセスログ出力で落ちる。ログをファイルに固定する（コンソール無し＝Ctrl+Cで殺されない）。
    if sys.stdout is None or sys.stderr is None:
        os.makedirs("data", exist_ok=True)
        logf = open(os.path.join("data", "serve.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = logf
    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    host = "localhost" if args.bind in ("127.0.0.1", "localhost") else args.bind
    print(f"配信中: http://{host}:{args.port}/today.html")
    print("  オッズ更新ボタン → GET /odds?id=race_id（押した時だけ取得）")
    print("  更新ボタン       → GET /update?date=YYYYMMDD（当日の展示+結果を一括／確定分はキャッシュ）")
    print("  Ctrl+C で停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")


if __name__ == "__main__":
    main()
