// Cloudflare Pages Function: GET /api/result?jcd=NN&rno=R&hd=YYYYMMDD
// ─────────────────────────────────────────────────────────────────────
// 公式 raceresult（レース結果）を直接取得し、クライアントの applyUpd 形式で返す。
//   ・OpenAPI results フィードも実測2〜4時間おきで確定直後に間に合わない
//     → 締切を過ぎたレースだけクライアントがここを叩き、確定後数分で結果を反映する。
//   ・パーサは fetch_before.py parse_result の JS 移植（2026 公式 raceresult 構造で確認済み）。
//   ・Cache API で120秒キャッシュ＝多端末・繰返しポーリングの重複取得を集約。
//   ・未確定/公式落ち時は {result:null} → クライアントは update.json 等にフォールバック（縮退安全）。
// 認証は _middleware.js が /api/* に対して実施済み（認証Cookie必須）。

const URL_RESULT = "https://www.boatrace.jp/owpc/pc/race/raceresult?rno={r}&jcd={jcd}&hd={hd}";
const UA = { "user-agent": "Mozilla/5.0 (boatrace-study-script)" };

const ZEN = "０１２３４５６７８９";
function zen2han(s) {
  let o = "";
  for (const c of s) { const i = ZEN.indexOf(c); o += i >= 0 ? String(i) : c; }
  return o;
}

// 払戻テーブルの各行 [組番digits(文字列), 金額(数値|null)] を文書順に返す。
function payoutRows(html) {
  const rows = [];
  const re = /numberSet1_row[^>]*>([\s\S]*?)<\/div>/g;
  let rm;
  while ((rm = re.exec(html))) {
    const nums = Array.from(rm[1].matchAll(/numberSet1_number is-type\d">\s*(\d)/g), (m) => m[1]);
    if (!nums.length) continue;
    const tail = html.slice(rm.index + rm[0].length, rm.index + rm[0].length + 400);
    const am = tail.match(/is-payout\d">\s*&yen;\s*([0-9,]+)/);
    rows.push([nums.join(""), am ? parseInt(am[1].replace(/,/g, ""), 10) : null]);
  }
  return rows;
}

// raceresult HTML → {fin:[着順 枠1..6], order, km, po2, po3, po2f}。未確定/無ければ null。
// export はローカル node テスト用。
export function parseResult(html) {
  if (!html) return null;
  // 着順td（全角/半角数字・Ｆ/Ｌ/失/欠）→ 直後の is-boatColor td（中身＝枠番）
  const fin = [null, null, null, null, null, null];
  const seen = new Set();
  const re = /<td[^>]*>\s*([０-９0-9ＦFＬL失欠]+)\s*<\/td>\s*<td[^>]*is-boatColor([1-6])/g;
  let m;
  while ((m = re.exec(html))) {
    const w = +m[2];
    if (seen.has(w)) continue;
    seen.add(w);
    const c = zen2han(m[1]);
    fin[w - 1] = /^\d+$/.test(c) ? +c : null;   // 非完走(F/L/失/欠)は null
    if (seen.size === 6) break;
  }
  if (!fin.some((f) => f === 1)) return null;    // 1着が無い＝未確定
  const order = [];
  for (let w = 1; w <= 6; w++) if (fin[w - 1]) order.push(w);
  order.sort((a, b) => fin[a - 1] - fin[b - 1]);
  let km = "";
  const mk = html.match(/(まくり差し|逃げ|差し|まくり|抜き|恵まれ)/);
  if (mk) km = mk[1];
  // 表の並び＝3連単→3連複→2連単→2連複…: 3桁の1行目=3連単 / 2桁の1行目=2連単・2行目=2連複。
  const rows = payoutRows(html);
  const po3 = (rows.find((x) => x[0].length === 3) || [null, null])[1];
  const two = rows.filter((x) => x[0].length === 2);
  const po2 = two.length ? two[0][1] : null;
  const po2f = two.length > 1 ? two[1][1] : null;   // 2連複（2026-07-07 券種切替で追加）
  return { fin, order, km, po2, po3, po2f };
}

function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "public, max-age=120" },
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

  const cacheKey = new Request(u.origin + "/api/result?jcd=" + jcd2 + "&rno=" + +rno + "&hd=" + hd);
  const cache = caches.default;
  const hit = await cache.match(cacheKey);
  if (hit) return hit;

  let result = null;
  try {
    const res = await fetch(
      URL_RESULT.replace("{r}", String(+rno)).replace("{jcd}", jcd2).replace("{hd}", hd),
      { headers: UA }
    );
    if (res.ok) result = parseResult(await res.text());
  } catch (e) { /* 未確定/公式落ちは result:null＝クライアントがフォールバック */ }

  const resp = json({ result, fetched_at: new Date().toISOString() });
  // 未確定(null)は短命キャッシュにしたいが Cache API は個別TTL不可なので、確定時のみ保存。
  if (result) context.waitUntil(cache.put(cacheKey, resp.clone()));
  return resp;
}
