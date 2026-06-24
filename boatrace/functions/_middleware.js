// Cloudflare Pages Function: 全リクエストの前段でパスワード認証をかける。
// ─────────────────────────────────────────────────────────────────────
// ・パスワードは Cloudflare Pages の環境変数 SITE_PASSWORD（シークレット）に設定する。
//   → パスワードを知っている人だけが開ける。最初は自分だけが知る＝実質「自分専用」。
// ・認証済みは HttpOnly Cookie で30日保持（iPhoneホーム画面アイコンでも毎回入力不要）。
// ・Cookie の値はパスワードの SHA-256（パスワード自体はクライアントに乗らない）。
// ・このファイルは Pages の「ルートディレクトリ＝boatrace」設定下で functions/ に置く前提。

const COOKIE = "br_auth";

async function sha256hex(s) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function loginPage(msg) {
  return new Response(
    `<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>認証</title>
<style>body{font-family:system-ui,sans-serif;background:#0e3a47;color:#eee;display:grid;place-items:center;height:100vh;margin:0}
form{background:rgba(20,80,97,.5);padding:24px;border-radius:12px;width:280px}
h3{margin:0 0 8px}input{width:100%;box-sizing:border-box;padding:10px;margin:8px 0;border-radius:8px;border:0}
button{width:100%;padding:10px;border:0;border-radius:8px;background:#ef7a27;color:#fff;font-weight:bold;cursor:pointer}
.msg{color:#ffb4a1;font-size:13px;min-height:16px}</style>
<form method=POST>
<h3>競艇 当日予想</h3>
<div class=msg>${msg || ""}</div>
<input type=password name=pw placeholder="パスワード" autofocus autocomplete="current-password">
<button>開く</button>
</form>`,
    { status: 401, headers: { "content-type": "text/html; charset=utf-8" } }
  );
}

export async function onRequest(context) {
  const { request, env, next } = context;
  const expected = env.SITE_PASSWORD;
  if (!expected) return new Response("SITE_PASSWORD 未設定（Cloudflare Pages の環境変数に追加してください）", { status: 500 });

  const token = await sha256hex(expected + "|br");

  // ログイン送信
  if (request.method === "POST") {
    const form = await request.formData();
    const pw = (form.get("pw") || "").toString();
    if (pw === expected) {
      return new Response(null, {
        status: 302,
        headers: {
          location: new URL(request.url).pathname,
          "set-cookie": `${COOKIE}=${token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=2592000`,
        },
      });
    }
    return loginPage("パスワードが違います");
  }

  // Cookie 検証 → 認証済みなら本来の静的ファイルを配信
  const cookie = request.headers.get("cookie") || "";
  const ok = cookie.split(";").some((c) => c.trim() === `${COOKIE}=${token}`);
  if (ok) return next();

  return loginPage("");
}
