// Cloudflare Pages Function: GET /api/update
// ─────────────────────────────────────────────────────────────────────
// 速度A: KV(BR_DATA) に保存された最新の update.json を「リビルド不要・即時」で配信する。
//   ・ci_update.py（GitHub Actions）が収集のたび KV キー "update" に JSON 文字列を書き込む。
//   ・ここはそれをそのまま返すだけ。Cloudflare Pages のビルド枠（500/月）を一切消費しない。
//   ・KV 未バインド（env.BR_DATA 無し）or キー未設定なら 404 → ページは静的 update.json に
//     自動フォールバックする（=未設定環境では従来どおり静的配信で動作する）。
// 認証は _middleware.js が /api/* に対し実施済み（認証Cookie必須）。
//
// 手動設定（有効化に必要）:
//   1) KV Namespace を作成（例 boatrace-data）。
//   2) Pages(claude-projects) → Settings → Functions → KV bindings で
//      変数名 BR_DATA → その namespace をバインド（Production）。
//   3) GitHub Secrets に CF_ACCOUNT_ID / CF_KV_NAMESPACE_ID / CF_API_TOKEN(KV Edit) を登録。

function json(body, status) {
  return new Response(body, {
    status: status || 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

export async function onRequestGet(context) {
  const kv = context.env.BR_DATA;
  if (!kv) return new Response("no kv binding", { status: 404 });
  const v = await kv.get("update"); // 保存は JSON 文字列そのまま
  if (!v) return new Response("no data", { status: 404 });
  return json(v, 200);
}
