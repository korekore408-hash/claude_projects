# PROGRESS

## 方針決定（2026-07-01）: v1修正ではなく v2 新規構築

ユーザー決定により、下記指摘は v1 へのパッチではなく **`../boatrace_v2/` の新規実装**に
織り込む形で対応する。対応状況は boatrace_v2/README.md の対応表を参照。
- フェーズ1（実装済み）: T1〜T9, T12, T13 を設計に組み込んだコアパイプライン
  （polite取得・厳格パース・スナップショット・校正・honest EVバックテスト・
    締切優先スケジューラ・認証付きサーバ・ユニットテスト）
- フェーズ2（2026-07-02 完了）: アプリ名 **Nishio-boooto**（config.APP_TITLE で変更可）。
  - UI: `app.py` 1コマンドで「オッズ収集＋today.html 生成＋配信」が起動
    （report.py / ev_picks.compute_picks）
  - 展示・当日結果のv2化: `before.py`（polite取得・JSON保存・v1フォールバック）。
    scheduler が締切前に展示・発走後に結果を自動取得し、画面に展示・結果・的中を表示
  - T10: `select_features.py`（選定期間固定＋期間内時系列分割の貪欲前進選択＝リークなし）
  - ユニットテスト28件。残: T10 のバッチ実行（v1特徴量CSVが必要）と walk-forward 再評価。
  v1 は特徴量・予測CSVの生成元として当面併用する。

## コードレビュー指摘・修正タスクリスト（2026-07-01 実施）

対象: fetch_odds.py / serve_odds.py / build_today.py / backtest.py / backtest_ev.py /
make_ev_backtest.py / predict_combos.py / daily.py / ci_update.py ほか EV・回収率ロジック。
※ 修正は未着手。ユーザー確認後に着手する。

### 高（結果の信頼性・データ破損に直結）

- [ ] **T1: 学習期間の in-sample リークを集計から除外**
  `build_today.py:810`（`--stats-from` 既定 2026-01-01）と `backtest.py:291`（`--since` 既定 2026-01-01）が、
  `daily.py:113` の固定 split（train-end **2026-04-30**）で学習した `predict_win.csv` の
  2026-01-01〜04-30 の **学習期間内（in-sample）予測** をそのまま的中率・回収率に混入させている。
  → 既定を `2026-05-01` に変更（train-end と定数を共有）。today.html に表示される
  場別成績・帯別回収率・仮想100万円チャレンジ(game_ledger)すべてが対象。

- [ ] **T2: EVバックテストの決済を実配当に変更＋オッズ取得時点の記録・考慮**
  `backtest_ev.py:141` は的中時 `returned += o * STAKE`（＝取得時オッズで決済）だが、
  実際の払戻は締切オッズ。取得は最大30分以上前（CI 30分毎 cron）で、鉄板の人気目は
  締切に向けて締まる傾向があるため **EV・回収率とも系統的に過大評価**。
  → K-file 実配当（`load_payouts`）で決済し、`fetched_at` と発走時刻の差を記録して
  「発走X分前以内の取得のみ採用」フィルタを追加。`_merge_save_odds`（serve_odds.py:118）も
  上書きでなく時系列スナップショットで保持する（combo キーに fetched_at を含める等）。

- [ ] **T3: オッズパーサの構造検証（サイレント誤マッピング防止）**
  `fetch_odds.py:77-105` は oddsPoint セルを**文書順の位置だけ**で組番に復元しており、
  セル数の検証がない（`99-100` の `if len(pts)<120: pts=pts[:120]` はデッドコード）。
  公式ページの構造が変わる／セルが途中欠落すると **誤った組番に誤ったオッズを黙って割り当てる**。
  → 3連単=120 / 2tf先頭=30+2連複15 のセル数を厳格チェックし、不一致なら空を返して警告ログ。
  odds < 1.0 の異常値も棄却。self_test のアンカー照合（1-2-3位置）を実行時にも軽量実施。

### 中（過信・セキュリティ・速度）

- [ ] **T4: 回収率に信頼区間とサンプル数を併記（結論の過信防止）**
  信頼区間の計算がどこにもない。「鉄板×EV≥1.5 だけプラス」(make_ev_backtest.py) は
  少数的中による高分散で、しかも **EV閾値をその同一データで選定**（多重比較）。
  → レース単位ブートストラップで回収率の95%CIを算出し、レポート/HTMLに n と CI を必ず表示。
  閾値選定期間と検証期間を分ける。

- [ ] **T5: 確率校正の常設化（Brier score・キャリブレーション表）**
  logloss は predict_combos.py にあるが Brier なし。キャリブ表は analyze_honmei_ana.py の
  手動オフラインのみ。EVの根幹である p（特に PL 展開した 2連単/3連単の組合せ確率）の
  校正は未検証。
  → walk-forward OOS 出力に対する Brier / 校正表（本命確率10分位・PL組合せ確率）を
  daily パイプラインで自動出力し、EV には校正済み確率を使う。

- [ ] **T6: serve_odds.py の LAN 公開時の認証・配信範囲**
  バインド既定は 127.0.0.1 で問題ないが、ドキュメント記載の `--bind 0.0.0.0` 運用時は
  **無認証**で、SimpleHTTPRequestHandler がフォルダ内の全ファイル（data/ のCSV・ログ含む）を
  配信し、/update で外部スクレイピングも起動できる。
  → 簡易トークン認証（環境変数 SERVE_TOKEN、クエリ/ヘッダ照合）＋配信対象の
  ホワイトリスト化（today.html / update.json 等のみ）。

- [ ] **T7: 取得並列度を 2〜3 に制限（取得先への負荷配慮）**
  `serve_odds.py:36` UPDATE_WORKERS=10 で公式サイトへ同時10接続・リクエスト間隔なし。
  → BoundedSemaphore(3) ＋ リクエスト間 0.3〜0.5s の polite_get を fetch_odds / fetch_before
  共通で導入し、ワーカー数を 3 に。

- [ ] **T8: fetch_odds.py CLI の直列取得を並列化（同時2〜3本・間隔つき）**
  `collect()` は全場×12R×2種を 0.8s 待ちで直列（全場で1時間超）。
  → T7 の polite_get 基盤の上で ThreadPoolExecutor(max_workers=3) 化。

- [ ] **T9: 締切近接レースの優先更新（動的間隔）**
  CI は 30分固定 cron。締切直前のオッズが最重要なのに、直前レースも30分前の値のまま。
  → ci_update / serve_odds に「発走 N 分前以内のレースだけを対象に短間隔で再取得する」
  優先キューを追加（window 機構と start_times を流用。ジョブ内ループで発走5分前に再取得等）。

- [ ] **T10: 特徴量選定の test リーク解消**
  `predict_combos.py:67` のコメントどおり CONT_FEATURES は test 漏れを含む貪欲選択の結果。
  → walk-forward 内でのネスト選択、または選定専用の古い期間で再選定。

### 低（品質・保守）

- [ ] **T11: fetch_odds.py の小さな掃除**
  デッドコード（99-100行）、odds==0 許容、R1空振り時の場スキップ判定の見直し。
- [ ] **T12: 依存のピン止めとログのコミット防止**
  requests / lhafile がバージョン未固定（pip-audit は既知脆弱性0を確認済み・2026-07-01）。
  data/*.log（serve.log 等ランタイムログ）が git 追跡対象になり得るので .gitignore に追加。
- [ ] **T13: 本番モデルと評価モデルの系統差の明示**
  本番当日予想は「前日まで学習の最新モデル」(daily.py 4b/4c)、バックテストは
  「4/30固定モデル」の予測を評価しており厳密には別物。評価レポートに系統を明記する。
