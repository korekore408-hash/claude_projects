// オッズ時系列ロガー（Cloudflare Worker・単体デプロイ）
// =====================================================
// cron(5分毎)で「締切30分前〜締切+2分」のレースの公式オッズ(2連単30点+3連単120点)を
// 取得し KV に追記する。目的＝「オッズの時間変化と結果の関係」を将来検証するための
// データ蓄積（今の予想サイトには一切影響しない・完全に独立）。
//
// セットアップ（Cloudflareダッシュボード・1回だけ）:
//   1. Workers & Pages → Create → Worker（名前例: boatrace-odds-logger）→ このコードを貼る
//   2. KV: Storage & Databases → KV → Create namespace「boatrace-odds」
//      Worker → Settings → Bindings → KV namespace: 変数名 BR_ODDS = boatrace-odds
//   3. Worker → Settings → Trigger Events → Cron Triggers: */5 * * * *
//   4. Worker → Settings → Variables → Secret: EXPORT_TOKEN = (任意の長い文字列)
//
// 取り出し（PCから・fetch_oddslog.py か ブラウザ）:
//   https://<worker名>.<account>.workers.dev/export?date=YYYYMMDD&token=<EXPORT_TOKEN>
//   https://<worker名>.<account>.workers.dev/dates?token=<EXPORT_TOKEN>   … 保存済み日付一覧
//
// 容量目安: 1日 ≒ 150snapshot × ~8R × 2.5KB ≒ 3MB。TTL90日で自動削除（KV無料枠1GBに余裕）。
// 書込は 5分毎に1キー（≦288/日 ＜ 無料枠1000/日）。夜間(JST 21時〜翌8時)は即終了。

const PROGRAMS_URL = "https://boatraceopenapi.github.io/programs/v2/today.json";
const URL_2T = "https://www.boatrace.jp/owpc/pc/race/odds2tf?rno={r}&jcd={jcd}&hd={hd}";
const URL_3T = "https://www.boatrace.jp/owpc/pc/race/odds3t?rno={r}&jcd={jcd}&hd={hd}";
const UA = { "User-Agent": "Mozilla/5.0 (odds-logger)" };
const WINDOW_BEFORE = 30;   // 締切何分前からログするか
const WINDOW_AFTER = 2;     // 締切後何分までログするか（最終オッズ確保）
const MAX_RACES = 18;       // 1tickの取得上限（無料枠サブリクエスト50の保険）
const TTL = 90 * 86400;     // 90日で自動削除

// ---- 公式オッズ表パーサ（functions/api/odds.js と同一ロジック）----
function parseExacta(html) {
  if (!html) return null;
  const re = /oddsPoint[^>]*>\s*([0-9]+\.[0-9]+|[0-9]+|欠場|---|-)\s*</g;
  const vals = []; let m;
  while ((m = re.exec(html)) && vals.length < 30) { const v = parseFloat(m[1]); vals.push(isNaN(v) ? null : v); }
  const o2 = {};
  vals.forEach((v, p) => {
    if (v == null) return;
    const a = (p % 6) + 1;
    const others = [1, 2, 3, 4, 5, 6].filter((x) => x !== a);
    o2[a + "-" + others[Math.floor(p / 6)]] = v;
  });
  return Object.keys(o2).length ? o2 : null;
}

// 2連複15点（2連単30セルの直後・三角順 [1-2,1-3,2-3,1-4,...]）
function parseQuinella(html) {
  if (!html) return null;
  const re = /oddsPoint[^>]*>\s*([0-9]+\.[0-9]+|[0-9]+|欠場|---|-)\s*</g;
  const vals = []; let m;
  while ((m = re.exec(html)) && vals.length < 45) { const v = parseFloat(m[1]); vals.push(isNaN(v) ? null : v); }
  if (vals.length < 45) return null;
  const o2f = {}; let q = 0;
  for (let b = 2; b <= 6; b++)
    for (let a = 1; a < b; a++) {
      const v = vals[30 + q++];
      if (v != null) o2f[a + "-" + b] = v;
    }
  return Object.keys(o2f).length ? o2f : null;
}

function parseTrifecta(html) {
  if (!html) return null;
  const re = /oddsPoint[^>]*>\s*([0-9]+\.[0-9]+|[0-9]+|欠場|---|-)\s*</g;
  const vals = []; let m;
  while ((m = re.exec(html)) && vals.length < 120) { const v = parseFloat(m[1]); vals.push(isNaN(v) ? null : v); }
  const o3 = {};
  vals.forEach((v, p) => {
    if (v == null) return;
    const a = (p % 6) + 1; const k = Math.floor(p / 6);
    const others = [1, 2, 3, 4, 5, 6].filter((x) => x !== a);
    const b = others[Math.floor(k / 4)];
    const thirds = others.filter((x) => x !== b);
    o3[a + "-" + b + "-" + thirds[k % 4]] = v;
  });
  return Object.keys(o3).length ? o3 : null;
}

// ---- JST ユーティリティ ----
function jstNow() {
  const t = new Date(Date.now() + 9 * 3600 * 1000);
  return {
    ymd: t.getUTCFullYear() * 10000 + (t.getUTCMonth() + 1) * 100 + t.getUTCDate(),
    min: t.getUTCHours() * 60 + t.getUTCMinutes(),
    hhmm: String(t.getUTCHours()).padStart(2, "0") + String(t.getUTCMinutes()).padStart(2, "0"),
  };
}

function closeMin(closedAt) {
  const m = /(\d{1,2}):(\d{2})/.exec(String(closedAt || ""));
  return m ? (+m[1]) * 60 + (+m[2]) : null;
}

export default {
  // ---- cron: 対象レースのオッズを1キーにまとめて保存 ----
  async scheduled(event, env, ctx) {
    const now = jstNow();
    const h = Math.floor(now.min / 60);
    if (h < 8 || h >= 21) return;                    // レース時間外
    let pj;
    try {
      const res = await fetch(PROGRAMS_URL, { headers: UA });
      if (!res.ok) return;
      pj = await res.json();
    } catch (e) { return; }
    const targets = [];
    for (const p of pj.programs || []) {
      // race_date は "2026-07-07" 形式 → 数字だけにして比較
      if (String(p.race_date || "").replace(/\D/g, "") !== String(now.ymd)) continue;
      const cm = closeMin(p.race_closed_at);
      if (cm == null) continue;
      if (now.min >= cm - WINDOW_BEFORE && now.min <= cm + WINDOW_AFTER) {
        targets.push({ jcd: +p.race_stadium_number, rno: +p.race_number, cm });
        if (targets.length >= MAX_RACES) break;
      }
    }
    if (!targets.length) return;
    const hd = String(now.ymd);
    const entries = await Promise.all(targets.map(async (t) => {
      const jcd2 = String(t.jcd).padStart(2, "0");
      const u2 = URL_2T.replace("{r}", t.rno).replace("{jcd}", jcd2).replace("{hd}", hd);
      const u3 = URL_3T.replace("{r}", t.rno).replace("{jcd}", jcd2).replace("{hd}", hd);
      let o2 = null, o2f = null, o3 = null;
      try {
        const [r2, r3] = await Promise.all([fetch(u2, { headers: UA }), fetch(u3, { headers: UA })]);
        if (r2.ok) { const h2 = await r2.text(); o2 = parseExacta(h2); o2f = parseQuinella(h2); }
        if (r3.ok) o3 = parseTrifecta(await r3.text());
      } catch (e) { /* 失敗レースはnullのまま記録 */ }
      const cmin = Math.floor(t.cm / 60), csec = t.cm % 60;
      return { jcd: t.jcd, rno: t.rno,
               close: String(cmin).padStart(2, "0") + ":" + String(csec).padStart(2, "0"),
               o2, o2f, o3 };
    }));
    const kept = entries.filter((e) => e.o2 || e.o3);
    if (!kept.length) return;
    await env.BR_ODDS.put(`log:${now.ymd}:${now.hhmm}`, JSON.stringify(kept),
                          { expirationTtl: TTL });
  },

  // ---- 取り出しAPI ----
  async fetch(req, env) {
    const u = new URL(req.url);
    if ((u.searchParams.get("token") || "") !== env.EXPORT_TOKEN) {
      return new Response("forbidden", { status: 403 });
    }
    if (u.pathname === "/dates") {
      const seen = new Set();
      let cursor;
      do {
        const l = await env.BR_ODDS.list({ prefix: "log:", cursor });
        l.keys.forEach((k) => seen.add(k.name.split(":")[1]));
        cursor = l.list_complete ? null : l.cursor;
      } while (cursor);
      return Response.json([...seen].sort());
    }
    if (u.pathname === "/export") {
      const date = u.searchParams.get("date") || "";
      if (!/^\d{8}$/.test(date)) return new Response("date=YYYYMMDD required", { status: 400 });
      const out = {};
      let cursor;
      do {
        const l = await env.BR_ODDS.list({ prefix: `log:${date}:`, cursor });
        for (const k of l.keys) {
          const v = await env.BR_ODDS.get(k.name, "json");
          if (v) out[k.name.split(":")[2]] = v;      // hhmm -> entries
        }
        cursor = l.list_complete ? null : l.cursor;
      } while (cursor);
      return Response.json({ date, snapshots: out });
    }
    return new Response("ok: /dates /export?date=YYYYMMDD (token required)");
  },
};
