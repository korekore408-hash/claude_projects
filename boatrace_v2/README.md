# Nishio-boooto（boatrace_v2）— 予想・EV・回収率パイプライン v2

v1（`../boatrace`）のコードレビュー指摘（`../boatrace/PROGRESS.md` T1〜T13）を
**修正パッチではなく設計に織り込んだ新規実装**。v1 は当面そのまま動かし、
v2 はデータ資産（K-file・B-file・予測CSV）を読み取り再利用しながら段階移行する。
アプリ名は `config.APP_TITLE`（1箇所）で変更できる。

## クイックスタート（アプリ起動）

```bash
cd boatrace_v2
python app.py            # 収集＋画面生成＋配信を1コマンドで起動
# → http://127.0.0.1:8788/ に当日のEVピック画面（60秒毎自動更新）
```

`app.py` はこれ1つで「オッズ収集（定期スイープ＋締切5分前ブースト）→
today.html 再生成 → ブラウザ配信」まで回る（PC内起動時はブラウザも自動で開く）。

## スマホで見る

```bash
python app.py --lan
```

起動時に **スマホで開くURL**（`http://192.168.x.x:8788/?token=...`）が表示されるので、
同じWi-Fiにつないだスマホでそれを開く。一度開けばトークンはCookieに保存され、
以後は `http://192.168.x.x:8788/` だけでOK（ブックマーク可・トークンは起動ごとに変わる。
固定したい場合は `SERVE_TOKEN=xxxx python app.py --lan`）。

つながらないときのチェック:
1. スマホがPCと**同じWi-Fi**にいるか（モバイル回線・ゲスト用SSIDは不可）
2. PC側ファイアウォールで Python の受信を許可したか（Windows は初回起動時の
   「アクセスを許可」ダイアログで「許可」。出なかった場合は
   設定 → ファイアウォール → アプリの許可 で python を追加）
3. URLのIPが変わっていないか（PC再起動でIPが変わることがある。起動ログのURLを再確認）

## v1 との対応（何が変わったか）

| 指摘 | v2 での扱い |
|------|------------|
| T1 学習期間の in-sample 混入 | `backtest.py` は walk-forward OOS（predict_win_oos.csv）**のみ**受け付ける。in-sample を含む predict_win.csv は評価入力として拒否 |
| T2 オッズ時点ズレ・取得時オッズ決済 | オッズは `fetched_at` 付き**追記スナップショット**（上書きしない）。バックテストは「発走10分前以内の取得」だけを購入オッズに採用し、決済は **K-file 実配当** |
| T3 パーサ構造検証なし | セル数厳格チェック（3連単=120 / 2tf=45or30）・odds<1.0 棄却。不一致は警告して破棄（サイレント誤マッピング防止） |
| T4 信頼区間なし・閾値の自己選定 | 期間を選定60%/検証40%に分割。EV閾値は選定期間で選び、検証期間の成績を**レース単位ブートストラップ95%CI付き**で報告 |
| T5 校正なし | `calibration.py`: PAV（isotonic）で p_win を較正。Brier score・キャリブ表を出力し、EV は較正後確率で計算 |
| T6 無認証LAN公開 | `server.py`: LAN公開は SERVE_TOKEN 必須（無ければ起動拒否）。静的配信はホワイトリストのみ |
| T7/T8 並列度10・直列CLI | 全取得が `net.polite_get`（同時3本・0.4s間隔・リトライ）経由。CLIも3並列 |
| T9 締切優先なし | `scheduler.py`: 定期スイープ＋**発走5分前の直前ブースト取得**。発走時刻を保存しT2のフィルタにも供給 |
| T12 依存未固定 | 依存は requests のみ（`requirements.txt` でピン止め） |
| T13 モデル系統不明示 | バックテストは使用した予測ファイルを常に表示 |

フェーズ2（対応済み）:
- **UI**: `app.py` + `report.py`（当日画面・60秒自動更新・的中/結果表示）
- **展示・当日結果のv2化**: `before.py`（polite取得・fetched_at付きJSON保存・
  v1形式フォールバック読込）。scheduler が締切前に展示・発走後に結果を自動取得
- **T10 特徴量再選定**: `select_features.py`（選定期間を `--select-until` より前に固定し、
  期間内 train/valid 時系列分割で貪欲前進選択＝testリークなし。実行には
  v1 の特徴量CSVが必要）

残課題: T10 の実行と選択結果での walk-forward 再評価（ユーザー環境でのバッチ実行）。

## 構成

```
app.py          ★アプリ本体（収集＋画面再生成＋配信を1コマンドで）
config.py       共通設定（APP_TITLE・並列度・閾値・パス。v1データはここから参照）
net.py          polite HTTP（同時3本・間隔・リトライ）
odds.py         オッズ取得・厳格パース・スナップショット追記保存（CLIあり）
results.py      K-file 実配当・公式組合ローダ（決済用）
calibration.py  PAV校正 + Brier/キャリブ表（CLIあり）
pl.py           Plackett-Luce 展開
backtest.py     EVバックテスト（honest・実配当・購入時点フィルタ・95%CI）
scheduler.py    締切優先オッズ収集（定期スイープ＋直前ブースト＋展示・結果取得）
before.py       展示（直前情報）・当日結果の取得（polite・JSON保存・v1フォールバック）
select_features.py  T10 特徴量の貪欲前進選択（リークなしプロトコル）
ev_picks.py     当日EVピック（compute_picks が UI/CLI 共通コア）
report.py       today.html / today.json の生成（data/web/。展示・結果・的中も表示）
server.py       認証付き配信サーバ（配信は data/web/ のホワイトリストのみ）
test_v2.py      ユニットテスト（ネットワーク不要・30件）
data/           v2 の出力（スナップショット・発走時刻・較正曲線・展示結果・web）
```

## 使い方（日次フロー）

```bash
# 0) 前提: v1 の daily.py が回っていること（特徴量・予測CSVを生成）
#    バックテスト・較正には walk-forward OOS が必要（初回のみ・約97分）:
cd ../boatrace && python predict_combos.py --mode walkforward

cd ../boatrace_v2

# 1) テスト
python -m unittest test_v2

# 2) 校正曲線の作成（OOS予測から。定期的に更新）
python calibration.py --fit-until 2026-06-10

# 3) アプリ起動（収集＋画面＋配信。個別に動かす場合は scheduler.py / server.py）
python app.py

# 4) 当日のEVピック（CLI版。画面と同じ内容）
python ev_picks.py --ev-min 1.5

# 5) バックテスト（選定/検証分離・95%CI付き）
python backtest.py --dates 20260604-20260617

# 6) T10: 特徴量の再選定（リークなし。v1特徴量CSVが必要・数十分〜数時間）
python select_features.py --select-until 2026-05-01
#    → 選ばれた特徴で v1 の walk-forward を回すコマンドが最後に表示される
```

## 設計メモ

- **v1形式オッズのフォールバック**: v2スナップショットが無い日は v1 の
  `data/odds/odds_YYYYMMDD.csv` を読むが、上書き保存のため取得履歴がなく
  購入時点フィルタは適用できない。レポートに「v1形式使用」と明示される。
- **決済**: 的中判定は K-file の公式組合（無い年式は着順1-3から再構成）、
  払戻は実配当。取得時オッズでは決済しない。
- **結論の読み方**: 検証期間の 95%CI が 100% を跨ぐ限り「優位性は未実証」。
  点推定の回収率だけで判断しない。
