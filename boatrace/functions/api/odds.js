// Cloudflare Pages Function: GET /api/odds?jcd=NN&rno=R&hd=YYYYMMDD
// ─────────────────────────────────────────────────────────────────────
// 公式 odds2tf（2連単オッズ）を直接取得し {o2:{"1-2":3.4,...}} で返す。
//   ・用途＝鉄板レースの見送り判定（1点目2連単の実オッズ<2.0なら「見送り推奨」）。
//     backtest(2026-06・鉄板81R・最終オッズ): 見送りで回収88.8→98.9%(+10.1pt)。
//   ・パーサは fetch_odds.py fetch_exacta の JS 移植:
//     2tfページの oddsPoint セル先頭30個＝2連単（文書順 p → 1着=(p%6)+1, 2着=残り昇順[p//6]）。
//   ・Cache API で90秒キャッシュ＝多端末ポーリングの重複取得を公式サイトへ流さない。
//   ・公式が落ちている/未発売時は {o2:null} → クライアントは表示なし（縮退安全）。
// 認証は _middleware.js が /api/* に対して実施済み（認証Cookie必須）。

const URL_2T = "https://www.boatrace.jp/owpc/pc/race/odds2tf?rno={r}&jcd={jcd}&hd={hd}";
const UA = { "user-agent": "Mozilla/5.0 (boatrace-study-script)" };

// odds2tf HTML → {"1-2":3.4,...}（最大30組）。欠場/未発売セルは除外。オッズ皆無は null。
// export はローカル node テスト用。
export function parseExacta(html) {
  if (!html) return null;
  const re = /oddsPoint[^>]*>\s*([0-9]+\.[0-9]+|[0-9]+|欠場|---|-)\s*</g;
  const vals = [];
  let m;
  while ((m = re.exec(html)) && vals.length < 30) {
    const v = parseFloat(m[1]);
    vals.push(isNaN(v) ? null : v);
  }
  const o2 = {};
  vals.forEach((v, p) => {
    if (v == null) return;
    const a = (p % 6) + 1;                                  // 1着＝列（文書順を6で割った余り）
    const others = [1, 2, 3, 4, 5, 6].filter((x) => x !== a);
    o2[a + "-" + others[Math.floor(p / 6)]] = v;            // 2着＝残り5艇昇順の行番目
  });
  return Object.keys(o2).length ? o2 : null;
}

function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "public, max-age=90" },
  });
}

export async function onRequestGet(context) {
  const u = new URL(context.request.url);
  const jcd = u.searchParams.get("jcd") || "";
  const rno = u.searchParams.get("rno") || "";
  const hd = u.searchParams.get("hd") || "";
  if (!/^\d{1,2}$/.test(jcd) || !/^\d{1,2}$/.test(rno) || !/^\d{8}$/.test(hd))
    return json({ error: "bad params" }, 400);
  const jcd2 = jcd.padStart(2, "0");

  // 90秒キャッシュ（正規化キー＝同一レースの多端末アクセスを1回の公式取得に集約）
  const cacheKey = new Request(u.origin + "/api/odds?jcd=" + jcd2 + "&rno=" + +rno + "&hd=" + hd);
  const cache = caches.default;
  const hit = await cache.match(cacheKey);
  if (hit) return hit;

  let o2 = null;
  try {
    const res = await fetch(
      URL_2T.replace("{r}", String(+rno)).replace("{jcd}", jcd2).replace("{hd}", hd),
      { headers: UA }
    );
    if (res.ok) o2 = parseExacta(await res.text());
  } catch (e) { /* 公式落ち/ブロックは o2:null＝クライアントは表示なし */ }

  const resp = json({ o2, fetched_at: new Date().toISOString() });
  context.waitUntil(cache.put(cacheKey, resp.clone()));
  return resp;
}
