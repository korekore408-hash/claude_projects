// Cloudflare Pages Function: GET /api/tenji?jcd=NN&rno=R&hd=YYYYMMDD
// ─────────────────────────────────────────────────────────────────────
// 公式 beforeinfo（直前情報）を直接取得し、クライアントの r.ex 形式で返す。
//   ・previews フィード(boatraceopenapi)は実測2〜4時間おきの更新で発走前に間に合わない
//     → 締切が近いレースだけクライアントがここを叩き、展示を公表後数分で反映する。
//   ・パーサは fetch_before.py parse_beforeinfo の JS 移植（2024〜2026 構造で確認済み）。
//   ・Cache API で90秒キャッシュ＝多端末・繰返しポーリングの重複取得を公式サイトへ流さない。
//   ・公式が落ちている/ブロック時は {ex:null} → クライアントは previews/update.json に
//     自動フォールバック（従来どおり動く・縮退安全）。
// 認証は _middleware.js が /api/* に対して実施済み（認証Cookie必須）。

const URL_BEFORE = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={r}&jcd={jcd}&hd={hd}";
const UA = { "user-agent": "Mozilla/5.0 (boatrace-study-script)" };

// '.09'→0.09 / 'F.01'→-0.01 / 'L','-'→null（fetch_before.py _parse_st と同じ）
function parseSt(s) {
  s = (s || "").trim();
  if (!s || s === "L" || s === "-") return null;
  const f = s.startsWith("F");
  s = s.replace(/^F/, "");
  if (!/^-?\.?\d+(?:\.\d+)?$/.test(s)) return null;
  const v = parseFloat(s.startsWith(".") ? "0" + s : s);
  if (isNaN(v)) return null;
  return f ? -v : v;
}

// is-boatColor{1..6} の出現位置で本文を6ブロックに割る。
// 行頭セルは rowspan 付き <td class="is-boatColorN ..." rowspan="4">。行中に前走情報の
// 別艇色セル(rowspanなし)が混ざるため rowspan 付きだけを行頭に採用（fetch_before.py と同じ）。
// 最終枠はページ末尾の凡例を飲み込まないよう典型行長で上限。
function boatBlocks(html) {
  let marks = [];
  let re = /is-boatColor([1-6])(?=[^>]*rowspan)/g;
  let m;
  while ((m = re.exec(html))) marks.push([m.index, +m[1]]);
  if (new Set(marks.map((x) => x[1])).size < 6) {   // rowspan構造でない場合は従来の全出現
    marks = [];
    re = /is-boatColor([1-6])/g;
    while ((m = re.exec(html))) marks.push([m.index, +m[1]]);
  }
  if (!marks.length) return null;
  const gaps = [];
  for (let i = 0; i + 1 < marks.length; i++) gaps.push(marks[i + 1][0] - marks[i][0]);
  const rowLen = gaps.length ? Math.max.apply(null, gaps) : 2000;
  const blocks = {};
  marks.forEach(([pos, w], i) => {
    const end = i + 1 < marks.length ? marks[i + 1][0] : Math.min(html.length, pos + rowLen);
    if (!(w in blocks)) blocks[w] = html.slice(pos, end);
  });
  return blocks;
}

function parseWeather(html) {
  let seg = html;
  const ms = html.match(/class="weather1[^"]*"/);
  if (ms) seg = html.slice(ms.index, ms.index + 4000);
  let tenki = null;
  const mt = seg.match(/weather1_bodyUnitLabelTitle[^>]*>\s*(晴|曇り|曇|雨|雪|風|霧)/);
  if (mt) tenki = mt[1] === "曇" ? "曇り" : mt[1];
  const num = (mm) => (mm ? parseFloat(mm[1]) : null);
  const wind = num(seg.match(/([0-9]+(?:\.[0-9]+)?)\s*m/));
  const wave = num(seg.match(/([0-9]+)\s*cm/));
  const temp = num(seg.match(/([0-9]+(?:\.[0-9]+)?)\s*℃/));
  const md = seg.match(/is-wind(\d+)/);
  return { tenki, winddir: md ? +md[1] : null, wind, wave, temp };
}

// beforeinfo HTML → {time,st,tilt,course,parts,weather}（クライアント r.ex 形式）。
// 展示前（データが何も無い）は null。export はローカル node テスト用。
export function parseBeforeinfo(html) {
  if (!html) return null;
  const time = [null, null, null, null, null, null];
  const tilt = time.slice(), parts = time.slice();
  const blocks = boatBlocks(html);
  if (!blocks) return null;
  for (let w = 1; w <= 6; w++) {
    const b = blocks[w] || "";
    // 展示タイム＝小数2桁（6.78等）。体重/チルトは小数1桁なので区別可。
    const mt = b.match(/\b([4-7]\.\d{2})\b/);
    if (mt) {
      time[w - 1] = parseFloat(mt[1]);
      const mc = b.slice(mt.index + mt[0].length).match(/>\s*(-?[0-3]\.\d)\s*</);
      if (mc) tilt[w - 1] = parseFloat(mc[1]);
    }
    const pj = b.match(/(ピストン|リング|ギ[ヤア]ケース|キャブレター|電気系|シリンダー?|プロペラ|ボ[ーア]ト|その他)/g);
    if (pj) parts[w - 1] = Array.from(new Set(pj));
  }
  // スタート展示（進入コース順に 枠番 と ST）
  const nums = Array.from(html.matchAll(/table1_boatImage1Number[^>]*>\s*([1-6])\s*</g), (m) => m[1]);
  const sts = Array.from(html.matchAll(/table1_boatImage1Time(?:Inner)?[^>]*>\s*(F?\.?-?[0-9]+)\s*</g), (m) => m[1]);
  const course = [null, null, null, null, null, null];
  const st = course.slice();
  for (let i = 0; i < Math.min(nums.length, sts.length); i++) {
    const w = +nums[i];
    course[w - 1] = i + 1;
    st[w - 1] = parseSt(sts[i]);
  }
  const weather = parseWeather(html);
  if (!time.some((x) => x != null) && !course.some((x) => x != null) && !weather.tenki) return null;
  return { time, st, tilt, course, parts, weather };
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
  const cacheKey = new Request(u.origin + "/api/tenji?jcd=" + jcd2 + "&rno=" + +rno + "&hd=" + hd);
  const cache = caches.default;
  const hit = await cache.match(cacheKey);
  if (hit) return hit;

  let ex = null;
  try {
    const res = await fetch(
      URL_BEFORE.replace("{r}", String(+rno)).replace("{jcd}", jcd2).replace("{hd}", hd),
      { headers: UA }
    );
    if (res.ok) ex = parseBeforeinfo(await res.text());
  } catch (e) { /* 公式落ち/ブロックは ex:null＝クライアントがフォールバック */ }

  const resp = json({ ex, fetched_at: new Date().toISOString() });
  context.waitUntil(cache.put(cacheKey, resp.clone()));
  return resp;
}
