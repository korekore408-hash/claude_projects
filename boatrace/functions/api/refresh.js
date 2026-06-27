// Cloudflare Pages Function: 「更新」ボタンの POST /api/refresh を受け、
// GitHub Actions(boatrace-update) を workflow_dispatch で起動する（オンデマンド収集）。
// 認証は _middleware.js が /api/* に対し実施済み（認証Cookie必須）。
//
// 必要な環境変数（Cloudflare Pages → Settings → Variables）:
//   GH_DISPATCH_TOKEN … repo=claude_projects の Actions:write 権限を持つ PAT（fine-grained 推奨）。シークレットで登録。
//   (任意) GH_REPO     … 既定 "korekore408-hash/claude_projects"
//   (任意) GH_WORKFLOW … 既定 "boatrace-update.yml"
//   (任意) GH_REF      … 既定 "master"
//
// 二重起動防止: 直近runが実行中/待機中なら dispatch せず {status:"already_running"} を返す
//   → Cloudflare Pages ビルド(500/月)とActions分の無駄打ちを抑える。
const ACTIVE = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);

function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: { "content-type": "application/json" },
  });
}

export async function onRequestPost(context) {
  const { env } = context;
  const token = env.GH_DISPATCH_TOKEN;
  if (!token) return json({ status: "error", message: "GH_DISPATCH_TOKEN 未設定" }, 500);

  const repo = env.GH_REPO || "korekore408-hash/claude_projects";
  const wf = env.GH_WORKFLOW || "boatrace-update.yml";
  const ref = env.GH_REF || "master";
  const base = `https://api.github.com/repos/${repo}/actions/workflows/${encodeURIComponent(wf)}`;
  const headers = {
    authorization: `Bearer ${token}`,
    accept: "application/vnd.github+json",
    "user-agent": "boatrace-refresh",
    "x-github-api-version": "2022-11-28",
  };

  try {
    const runs = await fetch(`${base}/runs?per_page=1`, { headers });
    if (runs.ok) {
      const data = await runs.json();
      const last = (data.workflow_runs || [])[0];
      if (last && ACTIVE.has(last.status)) return json({ status: "already_running" });
    }
    const disp = await fetch(`${base}/dispatches`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ ref }),
    });
    if (disp.status === 204) return json({ status: "queued" });
    const txt = await disp.text();
    return json({ status: "error", message: `dispatch ${disp.status}: ${txt.slice(0, 200)}` }, 502);
  } catch (e) {
    return json({ status: "error", message: String(e) }, 502);
  }
}

export async function onRequestGet() {
  return json({ status: "error", message: "POST only" }, 405);
}
