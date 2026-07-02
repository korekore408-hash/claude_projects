// Cloudflare Pages Function: GET /api/v2
// ─────────────────────────────────────────────────────────────────────
// boatrace_v2（Nishio-boooto）の today.json を KV から即時配信する。
//   ・GitHub Actions(boatrace-v2-update) が30分ごとに KV キー "v2_today" へ書き込む
//     （boatrace_v2/push_kv.py）。Pages のビルド枠は消費しない（/api/update と同方式）。
//   ・ビューアは /v2.html（クライアント側で描画・60秒毎に再取得）。
// 認証は _middleware.js が /api/* に対し実施済み（認証Cookie必須）。
// KV バインド（BR_DATA）は /api/update と共用＝追加設定は不要。

export async function onRequestGet(context) {
  const kv = context.env.BR_DATA;
  if (!kv) return new Response("no kv binding", { status: 404 });
  const v = await kv.get("v2_today");
  if (!v) return new Response("no data", { status: 404 });
  return new Response(v, {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}
