# -*- coding: utf-8 -*-
"""確率校正（isotonic/PAV）と Brier score・キャリブレーション表 — T5。

入力は v1 の walk-forward OOS 予測（predict_win_oos.csv, honest）のみ。
本命確率 p_win に対し PAV（単調回帰）で較正曲線を推定し、
較正前後の Brier score と 10 分位キャリブ表をレポートする。
EV 計算には較正後の p を使う（レース内で再正規化して PL の strength にする）。

使い方:
  python calibration.py                     # 全OOS期間で fit + レポート + 保存
  python calibration.py --fit-until 2026-06-10   # 選定期間だけで fit（検証を汚さない）
"""
import argparse
import csv
import json
from collections import defaultdict

try:
    from . import config
except ImportError:
    import config


# ---------------- OOS 予測の読込 ----------------

def load_oos(path=None):
    """predict_win_oos.csv → {rid: [(枠, p_win, won01|None), ...]}（6艇そろうレースのみ）。"""
    path = path or config.PRED_OOS
    races = defaultdict(list)
    with open(path, encoding="cp932") as f:
        for r in csv.DictReader(f):
            try:
                w = int(r["枠番"])
                p = float(r["p_win"])
            except (ValueError, KeyError):
                continue
            fin = r.get("finish_rank", "")
            won = None
            if str(fin).strip().isdigit():
                won = 1 if int(fin) == 1 else 0
            races[r["race_id"]].append((w, p, won))
    return {rid: bs for rid, bs in races.items() if len(bs) == 6}


def samples_from(races, until=None, since=None):
    """[(p, won)] の学習サンプル。決着レース（won が全艇判定可能）のみ。
    until/since='YYYY-MM-DD' で期間を絞る（rid の日付部分で判定）。"""
    out = []
    for rid, bs in races.items():
        d = f"{rid[2:6]}-{rid[6:8]}-{rid[8:10]}"
        if until and d > until:
            continue
        if since and d < since:
            continue
        if any(w is None for _, _, w in bs):
            continue
        if sum(w for _, _, w in bs) != 1:      # 勝者が特定できないレースは除外
            continue
        out.extend((p, w) for _, p, w in bs)
    return out


# ---------------- PAV（単調回帰）----------------

def fit_pav(samples):
    """samples=[(p, y01)] → 較正曲線 [(x, yhat)]（x 昇順・yhat 単調非減少）。"""
    pts = sorted(samples)
    if not pts:
        return []
    # ブロック = [sum_y, n, sum_x]
    blocks = []
    for x, y in pts:
        blocks.append([float(y), 1, float(x)])
        while len(blocks) >= 2 and \
                blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
            y2, n2, x2 = blocks.pop()
            blocks[-1][0] += y2
            blocks[-1][1] += n2
            blocks[-1][2] += x2
    return [(b[2] / b[1], b[0] / b[1]) for b in blocks]


def apply_curve(p, curve):
    """較正曲線を線形補間して適用。曲線が空なら恒等。"""
    if not curve:
        return p
    if p <= curve[0][0]:
        return curve[0][1]
    if p >= curve[-1][0]:
        return curve[-1][1]
    lo, hi = 0, len(curve) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if curve[mid][0] <= p:
            lo = mid
        else:
            hi = mid
    x0, y0 = curve[lo]
    x1, y1 = curve[hi]
    if x1 <= x0:
        return y0
    return y0 + (y1 - y0) * (p - x0) / (x1 - x0)


def calibrate_race(boats_p, curve):
    """レース6艇の p リストを較正しレース内で再正規化（PL の strength に使う）。"""
    q = [max(apply_curve(p, curve), 1e-6) for p in boats_p]
    tot = sum(q)
    return [x / tot for x in q]


# ---------------- 指標 ----------------

def brier(samples):
    return sum((p - y) ** 2 for p, y in samples) / len(samples) if samples else 0.0


def calib_table(samples, bins=None):
    """[(bin, n, 予測平均, 実測率)]。予測平均と実測率の乖離が校正誤差。"""
    bins = bins or config.CALIB_BINS
    agg = defaultdict(lambda: [0, 0.0, 0])
    for p, y in samples:
        b = min(int(p * bins), bins - 1)
        agg[b][0] += 1
        agg[b][1] += p
        agg[b][2] += y
    return [(b, n, ps / n, ys / n)
            for b, (n, ps, ys) in sorted(agg.items()) if n]


# ---------------- 保存・読込 ----------------

def save_curve(curve, meta, path=None):
    config.ensure_dirs()
    path = path or config.CALIBRATION_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"curve": curve, "meta": meta}, f, ensure_ascii=False)
    return path


def load_curve(path=None):
    path = path or config.CALIBRATION_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)["curve"]
    except (OSError, ValueError, KeyError):
        return []


# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(description="p_win の校正 fit + Brier/キャリブ表レポート")
    ap.add_argument("--pred", default=None, help="predict_win_oos.csv のパス")
    ap.add_argument("--fit-until", default=None, help="fit に使う最終日 YYYY-MM-DD")
    ap.add_argument("--eval-since", default=None, help="評価開始日（既定=fit-untilの翌日以降）")
    args = ap.parse_args()

    races = load_oos(args.pred)
    fit_samples = samples_from(races, until=args.fit_until)
    curve = fit_pav(fit_samples)

    eval_since = args.eval_since
    if eval_since is None and args.fit_until:
        eval_since = args.fit_until  # 厳密には翌日からだが境界日は僅少
    eval_samples = samples_from(races, since=eval_since) if eval_since else fit_samples
    cal_samples = [(apply_curve(p, curve), y) for p, y in eval_samples]

    print(f"fitサンプル {len(fit_samples)}艇 / 評価サンプル {len(eval_samples)}艇")
    print(f"Brier score : 生 {brier(eval_samples):.4f} → 較正後 {brier(cal_samples):.4f}")
    print("\nキャリブレーション表（評価期間・較正前 → 較正後）")
    print(" bin      n   予測平均  実測率 | 較正後平均  実測率")
    raw_t = {b: (n, pm, ym) for b, n, pm, ym in calib_table(eval_samples)}
    cal_t = {b: (n, pm, ym) for b, n, pm, ym in calib_table(cal_samples)}
    for b in range(config.CALIB_BINS):
        r = raw_t.get(b)
        c = cal_t.get(b)
        rs = f"{r[0]:6d}  {r[1]:.3f}   {r[2]:.3f}" if r else "     -      -       -"
        cs = f"{c[1]:.3f}     {c[2]:.3f}" if c else "    -         -"
        print(f" {b/config.CALIB_BINS:.1f}- {rs} | {cs}")

    path = save_curve(curve, {"fit_until": args.fit_until,
                              "n_fit": len(fit_samples),
                              "brier_raw": round(brier(eval_samples), 5),
                              "brier_cal": round(brier(cal_samples), 5)})
    print(f"\n○ 較正曲線を保存: {path}（EV計算はこれを使用）")


if __name__ == "__main__":
    main()
