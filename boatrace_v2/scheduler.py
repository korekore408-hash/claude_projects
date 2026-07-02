# -*- coding: utf-8 -*-
"""締切優先スケジューラ — T9。

発走時刻（v1 fetch_openapi の非公式OpenAPI）を基準に、
  - 定期スイープ: --sweep-min 間隔で全開催レースのオッズをスナップショット
  - 締切直前ブースト: 発走 lead 分前（既定5分）に該当レースだけ追加取得
を行う。取得はすべて net.polite_get 経由（同時3本・間隔つき）なので
高頻度化しても取得先への同時負荷は増えない。

発走時刻は data/start_times/start_times_YYYYMMDD.json に保存し、
backtest.py の「購入時点フィルタ」がこれを参照する。

使い方:
  python scheduler.py                      # 今日を最終レースまで監視
  python scheduler.py --date 2026-07-01 --lead 5 --sweep-min 30
  python scheduler.py --once               # 1回だけスイープして終了（cron/CI 用）
"""
import argparse
import datetime
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

try:
    from . import config, odds, before
except ImportError:
    import config
    import odds
    import before

RESULT_DELAY_MIN = 6      # 発走から結果取得を試み始めるまで（分）
RESULT_RETRY_MIN = 5      # 未確定時の再試行間隔（分）
RESULT_GIVEUP_MIN = 60    # 発走からこの分数を過ぎたら諦める


def fetch_start_times(hd):
    """v1 fetch_openapi.fetch_start_times を再利用して保存。失敗時は保存済みを読む。"""
    config.ensure_dirs()
    p = os.path.join(config.START_TIMES_DIR, f"start_times_{hd}.json")
    st = {}
    try:
        sys.path.insert(0, config.V1_DIR)
        cwd = os.getcwd()
        os.chdir(config.V1_DIR)          # v1 は相対パスで data/ を参照するため
        try:
            import fetch_openapi
            st = fetch_openapi.fetch_start_times(hd)
        finally:
            os.chdir(cwd)
    except Exception as e:
        print(f"[sched] 発走時刻の取得失敗: {e}")
    if st:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        return st
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def parse_rid(rid):
    if not (rid and len(rid) == 12 and rid.isdigit()):
        return None
    return int(rid[:2]), rid[2:10], int(rid[10:12])


def snapshot_races(hd, rids, label):
    if not rids:
        return
    def one(rid):
        jcd, _hd, rno = parse_rid(rid)
        tri, exa = odds.fetch_race_odds(jcd, rno, hd)
        return rid, tri, exa
    n = 0
    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENCY) as ex:
        for rid, tri, exa in ex.map(one, rids):
            if tri or exa:
                n += odds.append_snapshot(hd, rid, tri, exa)
    print(f"[sched {datetime.datetime.now():%H:%M:%S}] {label}: "
          f"{len(rids)}R → {n}行追記")


def run(hd, lead, sweep_min, once=False, on_update=None):
    """on_update: スナップショット追記のたびに呼ぶフック（app.py の画面再生成用）。"""
    def notify():
        if on_update:
            try:
                on_update()
            except Exception as e:
                print(f"[sched] on_update 失敗: {e}")

    st = fetch_start_times(hd)
    if not st:
        print("[sched] 発走時刻なし → 全場スイープのみ実行")
        odds.collect(hd, list(range(1, 25)), list(range(1, 13)))
        notify()
        return
    starts = {}
    for rid, hm in st.items():
        pr = parse_rid(rid)
        if not pr or pr[1] != hd:
            continue
        try:
            h, m = map(int, hm.split(":"))
            starts[rid] = datetime.datetime(int(hd[:4]), int(hd[4:6]), int(hd[6:8]), h, m)
        except ValueError:
            continue
    print(f"[sched] 開催 {len(starts)}レース "
          f"(締切ブースト: 発走{lead}分前 / スイープ: {sweep_min}分毎)")

    boosted = set()
    resulted, res_next = set(), {}
    next_sweep = datetime.datetime.now()
    while True:
        now = datetime.datetime.now()
        # 1) 定期スイープ（未発走レースすべて）
        if now >= next_sweep:
            pend = [r for r, s in starts.items() if s > now]
            snapshot_races(hd, pend, "定期スイープ")
            notify()
            next_sweep = now + datetime.timedelta(minutes=sweep_min)
            if once:
                return
        # 2) 締切直前ブースト（発走 lead 分前を過ぎた未ブーストレース）
        #    オッズに加えて展示（直前情報）も取得する
        due = [r for r, s in starts.items()
               if r not in boosted and
               s - datetime.timedelta(minutes=lead) <= now < s]
        if due:
            snapshot_races(hd, due, f"締切{lead}分前ブースト")
            exs = before.fetch_and_save(hd, due, want_before=True, want_result=False)
            print(f"[sched {datetime.datetime.now():%H:%M:%S}] 展示取得: "
                  f"{len(due)}R → {len(exs)}件")
            notify()
            boosted |= set(due)
        # 3) 発走後の結果取得（未確定なら間隔をあけて再試行）
        due_res = [r for r, s in starts.items()
                   if r not in resulted
                   and now >= s + datetime.timedelta(minutes=RESULT_DELAY_MIN)
                   and now >= res_next.get(r, now)]
        if due_res:
            recs = before.fetch_and_save(hd, due_res,
                                         want_before=False, want_result=True)
            got = {r for r in due_res if recs.get(r, {}).get("result")}
            resulted |= got
            for r in set(due_res) - got:
                if now >= starts[r] + datetime.timedelta(minutes=RESULT_GIVEUP_MIN):
                    resulted.add(r)          # 中止等で確定しないレースは諦める
                else:
                    res_next[r] = now + datetime.timedelta(minutes=RESULT_RETRY_MIN)
            print(f"[sched {datetime.datetime.now():%H:%M:%S}] 結果取得: "
                  f"{len(due_res)}R → 確定{len(got)}件")
            if got:
                notify()
        # 4) 終了判定と次イベントまで sleep
        future = [s for s in starts.values() if s > now]
        if not future and len(resulted) == len(starts):
            print("[sched] 全レース発走済み・結果取得完了 → 終了")
            return
        events = [next_sweep] + [s - datetime.timedelta(minutes=lead)
                                 for r, s in starts.items() if r not in boosted
                                 and s > now]
        wait = min((e - now).total_seconds() for e in events)
        time.sleep(max(5, min(wait, 60)))


def main():
    ap = argparse.ArgumentParser(description="締切優先オッズスナップショット収集")
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--lead", type=int, default=5, help="発走何分前にブースト取得するか")
    ap.add_argument("--sweep-min", type=int, default=30, help="定期スイープ間隔（分）")
    ap.add_argument("--once", action="store_true", help="スイープ1回で終了（CI cron 用）")
    args = ap.parse_args()
    run(args.date.replace("-", ""), args.lead, args.sweep_min, once=args.once)


if __name__ == "__main__":
    main()
