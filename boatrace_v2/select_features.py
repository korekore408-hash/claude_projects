# -*- coding: utf-8 -*-
"""特徴量の再選定（貪欲前進選択）— T10。リークなしプロトコル版。

v1 の CONT_FEATURES は「test 期間を含むデータでの貪欲選択」の結果だった
（predict_combos.py の注記どおり）。v2 では選定を次のプロトコルで行う:

  1. --select-until より前のデータ**だけ**を選定期間とする（以降は一切触らない）
  2. 選定期間内を日単位で train(先頭 --train-frac) / valid(残り) に時系列分割
  3. 枠ダミーのみをベースラインに、valid の勝者 logloss が最も下がる特徴を
     1つずつ追加。改善が --min-gain 未満になったら停止
  4. 選ばれた特徴で v1 の walk-forward（--select-until 以降を含む）を回して
     最終評価する（このスクリプトは選定まで。評価コマンドを最後に表示）

入力は v1 が生成する features_player_history.csv / features_race_relative.csv と
K-file（勝者）。モデルは v1 と同じ条件付きロジット（レース内 softmax）。

使い方:
  python select_features.py --select-until 2026-05-01
  python select_features.py --select-until 2026-05-01 --iters 80 --max-feats 12
※ 純Python実装のため全候補×全ラウンドで数十分〜数時間かかる。--iters を下げるか
  --candidates で候補を絞ると速い。
"""
import argparse
import json
import math
import os
import csv
from collections import defaultdict

try:
    from . import config, results
except ImportError:
    import config
    import results

# 候補特徴 → どのCSVから引くか（v1 predict_combos.py の FEATURE_SOURCE と同一。
# レース内で全艇共通の特徴は softmax で打ち消されるため最初から含めない）。
FEATURE_SOURCE = {
    "lane_win_rate": "hist", "lane_top3_rate": "hist",
    "local_win_rate": "hist", "local_top3_rate": "hist",
    "flying_rate": "hist", "st_avg": "hist", "st_std": "hist",
    "recent30_winrate": "hist", "recent30_avgrank": "hist",
    "recentN_winrate": "hist", "recentN_avgrank": "hist",
    "venue_own_lane_winrate": "hist",
    "motor_intrinsic_win": "hist", "motor_intrinsic_top2": "hist",
    "wakunari_rate": "hist", "maezuke_rate": "hist",
    "class_ord": "rel", "win_rate_national": "rel", "motor_top2_rate": "rel",
    "class_gap": "rel", "winrate_rank_in_race": "rel",
    "winrate_diff_top": "rel", "motor_rank_in_race": "rel",
    "st_rank_in_race": "rel",
    "venue_rough_x_gap": "rel", "wind_x_lane": "rel", "wave_x_lane": "rel",
    "top2_rate_national": "rel", "win_rate_local": "rel", "top2_rate_local": "rel",
    "boat_top2_rate": "rel", "weight": "rel", "age": "rel",
}


def _to_float(s):
    try:
        return float(str(s).strip())
    except (ValueError, AttributeError):
        return None


# ---------------- データ読込（v1 生成CSV） ----------------

def load_races(hist_path=None, rel_path=None):
    """history×relative を結合 → {rid: {"date": "YYYY-MM-DD",
    "boats": [{"lane": int, "cont": {feat: float|None}}]}}（6艇レースのみ）。"""
    hist_path = hist_path or config.V1_HIST
    rel_path = rel_path or config.V1_REL
    hist = {}
    with open(hist_path, encoding="cp932") as f:
        for r in csv.DictReader(f):
            hist[(r["race_id"], r["枠番"])] = r
    races = defaultdict(lambda: {"date": None, "boats": []})
    with open(rel_path, encoding="cp932") as f:
        for r in csv.DictReader(f):
            h = hist.get((r["race_id"], r["枠番"]), {})
            cont = {}
            for feat, src in FEATURE_SOURCE.items():
                cont[feat] = _to_float(h.get(feat) if src == "hist" else r.get(feat))
            races[r["race_id"]]["date"] = r["日付"]
            races[r["race_id"]]["boats"].append(
                {"lane": int(r["枠番"]), "cont": cont})
    return {rid: v for rid, v in races.items() if len(v["boats"]) == 6}


def load_winners():
    """K-file → {rid: 勝者の枠}（2連単組合の1桁目。艇番=枠の前提は v1 と同じ）。"""
    win = {}
    for rid, rec in results.load_results().items():
        c = rec.get("combo2") or ""
        if c[:1].isdigit():
            win[rid] = int(c.split("-")[0])
    return win


# ---------------- 分割・標準化 ----------------

def split_days(races, until, train_frac):
    """選定期間（date < until）を日単位で train/valid に時系列分割。"""
    ids = [rid for rid, v in races.items() if v["date"] and v["date"] < until]
    days = sorted({races[rid]["date"] for rid in ids})
    n_tr = max(1, int(len(days) * train_frac))
    tr_days = set(days[:n_tr])
    train = [rid for rid in ids if races[rid]["date"] in tr_days]
    valid = [rid for rid in ids if races[rid]["date"] not in tr_days]
    return sorted(train), sorted(valid)


def standardize(races, train_ids, feats):
    """train のみの平均・標準偏差で各艇に z値（欠損→0）を付与。
    特徴ごとに独立なので選定ラウンド間で再利用できる。"""
    for c in feats:
        vals = [b["cont"][c] for rid in train_ids for b in races[rid]["boats"]
                if b["cont"].get(c) is not None]
        mean = sum(vals) / len(vals) if vals else 0.0
        var = (sum((v - mean) ** 2 for v in vals) / len(vals)
               if len(vals) > 1 else 1.0)
        std = math.sqrt(var) if var > 0 else 1.0
        for v in races.values():
            for b in v["boats"]:
                x = b["cont"].get(c)
                b.setdefault("z", {})[c] = 0.0 if x is None else (x - mean) / std


# ---------------- 条件付きロジット（v1 と同一の学習則） ----------------

def _vecs(boats, feats):
    return [[b["z"][c] for c in feats] +
            [1.0 if b["lane"] == w else 0.0 for w in range(2, 7)]
            for b in boats]


def _prep(races, ids, winner, feats):
    prepared = []
    for rid in ids:
        wb = winner.get(rid)
        if wb is None:
            continue
        boats = races[rid]["boats"]
        widx = next((i for i, b in enumerate(boats) if b["lane"] == wb), None)
        if widx is None:
            continue
        prepared.append((_vecs(boats, feats), widx))
    return prepared


def train_eval(races, train_ids, valid_ids, winner, feats,
               iters=120, lr=0.3, l2=1.0):
    """feats（＋枠ダミー）で学習し、valid の勝者 logloss を返す。"""
    tr = _prep(races, train_ids, winner, feats)
    va = _prep(races, valid_ids, winner, feats)
    if not tr or not va:
        return float("inf")
    nf = len(feats) + 5
    w = [0.0] * nf
    n = len(tr)
    for _ in range(iters):
        grad = [0.0] * nf
        for vecs, widx in tr:
            vs = [sum(wi * xi for wi, xi in zip(w, v)) for v in vecs]
            mx = max(vs)
            s = [math.exp(v - mx) for v in vs]
            tot = sum(s)
            p = [si / tot for si in s]
            for k in range(nf):
                ex = sum(p[i] * vecs[i][k] for i in range(len(vecs)))
                grad[k] += vecs[widx][k] - ex
        for k in range(nf):
            w[k] += lr * (grad[k] / n - l2 * w[k] / n)
    ll = 0.0
    for vecs, widx in va:
        vs = [sum(wi * xi for wi, xi in zip(w, v)) for v in vecs]
        mx = max(vs)
        s = [math.exp(v - mx) for v in vs]
        tot = sum(s)
        ll += -math.log(max(s[widx] / tot, 1e-12))
    return ll / len(va)


# ---------------- 貪欲前進選択 ----------------

def greedy_select(races, train_ids, valid_ids, winner, candidates,
                  iters=120, min_gain=0.001, max_feats=20, verbose=True):
    """valid logloss が下がる限り1特徴ずつ追加。返り値 (選択リスト, 履歴)。"""
    standardize(races, train_ids, candidates)
    selected = []
    base = train_eval(races, train_ids, valid_ids, winner, selected, iters=iters)
    history = [("(枠ダミーのみ)", base)]
    if verbose:
        print(f"ベースライン（枠ダミーのみ）: valid logloss {base:.4f}")
    cur = base
    while len(selected) < max_feats:
        best_f, best_ll = None, cur
        for c in candidates:
            if c in selected:
                continue
            ll = train_eval(races, train_ids, valid_ids, winner,
                            selected + [c], iters=iters)
            if ll < best_ll:
                best_f, best_ll = c, ll
        if best_f is None or cur - best_ll < min_gain:
            if verbose:
                print(f"改善 < {min_gain} → 停止")
            break
        selected.append(best_f)
        cur = best_ll
        history.append((best_f, cur))
        if verbose:
            print(f"  + {best_f:24s} → valid logloss {cur:.4f}")
    return selected, history


# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(description="特徴量の貪欲前進選択（リークなし — T10）")
    ap.add_argument("--select-until", required=True,
                    help="選定に使う最終日（これより前だけを使用）YYYY-MM-DD")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--iters", type=int, default=120)
    ap.add_argument("--min-gain", type=float, default=0.001)
    ap.add_argument("--max-feats", type=int, default=20)
    ap.add_argument("--candidates", default=None,
                    help="候補をカンマ区切りで絞る（省略時は全候補）")
    args = ap.parse_args()

    for p in (config.V1_HIST, config.V1_REL):
        if not os.path.exists(p):
            print(f"特徴量CSVが見つかりません: {p}\n"
                  f"→ v1 の特徴量生成（daily.py の features ステップ）を先に実行してください。")
            return
    candidates = list(FEATURE_SOURCE)
    if args.candidates:
        candidates = [c.strip() for c in args.candidates.split(",") if c.strip()]
        bad = [c for c in candidates if c not in FEATURE_SOURCE]
        if bad:
            ap.error(f"未知の特徴: {bad}")

    print("読込中…")
    races = load_races()
    winner = load_winners()
    train_ids, valid_ids = split_days(races, args.select_until, args.train_frac)
    if not train_ids or not valid_ids:
        print(f"選定期間（< {args.select_until}）のデータが不足しています。")
        return
    print(f"選定期間: train {len(train_ids)}R / valid {len(valid_ids)}R "
          f"（{args.select_until} 以降は選定に使いません）")
    print(f"候補 {len(candidates)}特徴 × iters={args.iters}"
          f"（全ラウンドで数十分〜数時間かかります）")

    selected, history = greedy_select(
        races, train_ids, valid_ids, winner, candidates,
        iters=args.iters, min_gain=args.min_gain, max_feats=args.max_feats)

    config.ensure_dirs()
    with open(config.SELECTED_FEATURES_PATH, "w", encoding="utf-8") as f:
        json.dump({"features": selected,
                   "history": history,
                   "protocol": {"select_until": args.select_until,
                                "train_frac": args.train_frac,
                                "iters": args.iters, "min_gain": args.min_gain,
                                "n_train": len(train_ids),
                                "n_valid": len(valid_ids)}},
                  f, ensure_ascii=False, indent=1)
    print(f"\n○ 保存: {config.SELECTED_FEATURES_PATH}")
    print(f"選択された {len(selected)} 特徴: {','.join(selected) or '(なし)'}")
    print("\n最終評価（選定期間より後を含む walk-forward）は v1 で:")
    print(f"  cd {config.V1_DIR} && python predict_combos.py --mode walkforward "
          f"--features {','.join(selected)}")


if __name__ == "__main__":
    main()
