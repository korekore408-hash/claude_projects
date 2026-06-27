// Cloudflare Pages Function: 全リクエストの前段でパスワード認証をかける。
// ─────────────────────────────────────────────────────────────────────
// ・パスワードは Cloudflare Pages の環境変数 SITE_PASSWORD（シークレット）に設定する。
//   → パスワードを知っている人だけが開ける。最初は自分だけが知る＝実質「自分専用」。
// ・認証済みは HttpOnly Cookie で30日保持（iPhoneホーム画面アイコンでも毎回入力不要）。
// ・Cookie の値はパスワードの SHA-256（パスワード自体はクライアントに乗らない）。
// ・このファイルは Pages の「ルートディレクトリ＝boatrace」設定下で functions/ に置く前提。
//
// ★セキュリティ強化（施策2）:
//   ・パスワード照合を「定数時間比較」にして総当たりへのタイミング情報漏れを防止。
//   ・ログイン失敗のレート制限/ロックアウト（IP単位）。Cloudflare KV を使う。
//     - KV Namespace を作成し、Pages に「変数名 BR_RL」でバインドすると有効化。
//     - バインドが無い場合（env.BR_RL 未設定）はレート制限を自動スキップ＝サイトは正常動作。

const COOKIE = "br_auth";

// レート制限パラメータ
const RL_MAX = 8;       // この回数だけ連続失敗するとロック
const RL_WINDOW = 900;  // 失敗カウントの有効期間（秒）= 15分。ロック時間もこの長さ。

async function sha256hex(s) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// 定数時間比較: 入力をSHA-256(32byte)化してXOR累積で照合（入力長や一致位置で分岐しない）。
async function ctEqual(a, b) {
  const ha = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(a)));
  const hb = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(b)));
  let diff = 0;
  for (let i = 0; i < ha.length; i++) diff |= ha[i] ^ hb[i];
  return diff === 0;
}

function loginPage(msg, status) {
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
    { status: status || 401, headers: { "content-type": "text/html; charset=utf-8" } }
  );
}

export async function onRequest(context) {
  const { request, env, next } = context;
  const expected = env.SITE_PASSWORD;
  if (!expected) return new Response("SITE_PASSWORD 未設定（Cloudflare Pages の環境変数に追加してください）", { status: 500 });

  const token = await sha256hex(expected + "|br");
  const url = new URL(request.url);
  const cookie = request.headers.get("cookie") || "";
  const authed = cookie.split(";").some((c) => c.trim() === `${COOKIE}=${token}`);

  // API（/api/*）: フォームログインは行わず、認証済みCookieのみ通す。
  //   → 「更新」ボタンの POST /api/refresh が（ログイン送信と誤認されず）Function に届く。
  if (url.pathname.startsWith("/api/")) {
    if (!authed) return new Response(JSON.stringify({ status: "unauthorized" }), { status: 401, headers: { "content-type": "application/json" } });
    return next();
  }

  // 通常ページ: POST はログイン送信として扱う。
  if (request.method === "POST") {
    const rl = env.BR_RL; // KVバインド（未設定ならレート制限スキップ）
    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    const key = `fail:${ip}`;

    // ① ロック判定（パスワード照合より前に弾く）
    if (rl) {
      const n = parseInt((await rl.get(key)) || "0", 10);
      if (n >= RL_MAX) {
        return loginPage("試行回数が多すぎます。15分ほど待って再度お試しください。", 429);
      }
    }

    const form = await request.formData();
    const pw = (form.get("pw") || "").toString();

    // ② 定数時間で照合
    if (await ctEqual(pw, expected)) {
      if (rl) await rl.delete(key); // 成功 → 失敗カウントをクリア
      return new Response(null, {
        status: 302,
        headers: {
          location: url.pathname,
          "set-cookie": `${COOKIE}=${token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=2592000`,
        },
      });
    }

    // ③ 失敗 → カウント++（窓のTTLで自動失効）
    if (rl) {
      const n = parseInt((await rl.get(key)) || "0", 10) + 1;
      await rl.put(key, String(n), { expirationTtl: RL_WINDOW });
      const left = RL_MAX - n;
      if (left <= 0) return loginPage("試行回数が多すぎます。15分ほど待って再度お試しください。", 429);
      if (left <= 3) return loginPage(`パスワードが違います（あと${left}回でロック）`, 401);
    }
    return loginPage("パスワードが違います", 401);
  }

  // Cookie 検証 → 認証済みなら本来の静的ファイルを配信
  if (authed) return next();

  return loginPage("", 401);
}
