# -*- coding: utf-8 -*-
"""boatrace_v2 共通設定。

v2 の原則（v1 コードレビュー指摘を設計に織り込む）:
  T1: 回収率・的中率の集計は walk-forward OOS 予測のみを入力にする（in-sample 禁止）
  T2: オッズは fetched_at 付きスナップショットとして追記保存し、
      バックテストは「発走 N 分前以内の取得」だけを購入オッズとして採用、実配当で決済
  T3: パーサはセル数を厳格検証し、構造変化の疑いがあれば空を返して警告
  T4: 回収率は必ず n と ブートストラップ95%CI を併記。閾値の選定期間と検証期間を分離
  T7/T8: HTTP は共通ゲート（同時 MAX_CONCURRENCY 本・リクエスト間隔つき）経由のみ
"""
import os

V2_DIR = os.path.dirname(os.path.abspath(__file__))
V1_DIR = os.path.normpath(os.path.join(V2_DIR, "..", "boatrace"))
V1_DATA = os.path.join(V1_DIR, "data")          # K-file / B-file（読み取りのみ）
V1_ODDS_DIR = os.path.join(V1_DATA, "odds")     # v1 形式オッズ（フォールバック読込）

DATA_DIR = os.path.join(V2_DIR, "data")
ODDS_SNAP_DIR = os.path.join(DATA_DIR, "odds")             # v2 スナップショット
START_TIMES_DIR = os.path.join(DATA_DIR, "start_times")    # {rid: "HH:MM"} 日別JSON
CALIBRATION_PATH = os.path.join(DATA_DIR, "calibration.json")

# v1 が生成する予測CSV（walk-forward OOS が v2 バックテストの唯一の入力）
PRED_OOS = os.path.join(V1_DIR, "predict_win_oos.csv")
PRED_TODAY = os.path.join(V1_DIR, "predict_win.csv")   # 当日EVピック用（最新モデル行）

# ---- HTTP（取得先への負荷配慮）----
MAX_CONCURRENCY = 3       # 公式サイトへの同時接続は最大3本
REQUEST_INTERVAL = 0.4    # 1リクエストごとに置く間隔（秒）
TIMEOUT = 30
RETRIES = 2               # ネットワーク失敗時のリトライ（指数バックオフ）
USER_AGENT = "Mozilla/5.0 (boatrace-study-v2)"

# ---- EV / バックテスト ----
TOP_2T = 5                # 買い目候補: 2連単 PL上位
TOP_3T = 10               # 買い目候補: 3連単 PL上位
STAKE = 100               # 1点あたり（円）
EV_THRESHOLDS = [0.0, 1.0, 1.2, 1.5, 2.0]
PURCHASE_WINDOW_MIN = 10  # 発走 N 分前以内に取得したオッズだけを「購入オッズ」と認める
SELECT_FRACTION = 0.6     # 閾値選定に使う日数割合（残りが検証期間）
BOOTSTRAP_N = 2000
CALIB_BINS = 10

VENUE_CODE = {
    "桐生": "01", "戸田": "02", "江戸川": "03", "平和島": "04",
    "多摩川": "05", "浜名湖": "06", "蒲郡": "07", "常滑": "08",
    "津": "09", "三国": "10", "びわこ": "11", "住之江": "12",
    "尼崎": "13", "鳴門": "14", "丸亀": "15", "児島": "16",
    "宮島": "17", "徳山": "18", "下関": "19", "若松": "20",
    "芦屋": "21", "福岡": "22", "唐津": "23", "大村": "24",
}


def ensure_dirs():
    for d in (DATA_DIR, ODDS_SNAP_DIR, START_TIMES_DIR):
        os.makedirs(d, exist_ok=True)
