# 競艇アプリ セキュリティ＆分析レビュー（2026-07-01）

対象: `boatrace/` 一式（Python パイプライン / today.html ほか静的ページ / Cloudflare Pages Functions / GitHub Actions ワークフロー / ローカル serve_odds.py）。
リポジトリは **private**、サイトは Cloudflare Pages + パスワード認証（`functions/_middleware.js`）で配信、という前提で監査した。

総評: シークレットのコミットなし・`shell=True`/`eval` なし・race_id/date の入力検証あり・requests の TLS 検証は既定有効、と基礎はしっかりしている。
一方で **「外部から取得した文字列を無検証のまま innerHTML / `<script>` 埋め込みに流す」経路が複数あり、これが最大のリスク**。認証も「パスワードのハッシュ＝Cookie 値」という設計に構造的な弱点がある。

---

## 高リスク（先に直すべき）

### H-1. `<script>` への JSON 埋め込みで `</script>` エスケープなし（DOM 実行の恐れ）
`build_today.py:981`（build_viewer.py / build_summary.py も同様の方式）:

```python
html = HTML.replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
```

`json.dumps` は `</script>` をエスケープしない。payload には **外部由来の文字列**（B ファイルの選手名・会場名、公式 HTML から正規表現で抜いた決まり手、OpenAPI の選手名など）が入るため、上流データに `</script><script>...` が混じるとスクリプトタグが閉じられ、任意 JS が実行される。現実の選手名にタグが入る可能性は低いが、**上流（非公式 OpenAPI＝第三者の GitHub Pages）が汚染された場合に一撃でストアド XSS になる**構造。

**対策**（1行で済む）:
```python
data_js = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
html = HTML.replace("__DATA__", data_js)
```

### H-2. 第三者フィード（boatraceopenapi.github.io）→ innerHTML の無検証 XSS 経路
`today.html:972-997`（`PREVIEWS_URL` をクライアント直読み）→ `pvToEx()` → `exView()`（`today.html:896` 以降）で innerHTML に連結:

- `w.wind` / `w.wave` / `w.temp` : `'風'+w.wind+'m'` と**そのまま文字列連結**（`today.html:901-904`）
- `c`（進入コース）: `c+'c'` をそのまま挿入（`today.html:916`）

previews フィードが数値の代わりに `"<img src=x onerror=...>"` を返せば、**認証済みページ上で XSS** になる。第三者の GitHub Pages を信頼境界の内側に置いている点が問題（アカウント乗っ取り・リポジトリ改竄・提供者の悪意のいずれでも成立）。Cookie は HttpOnly なので窃取はされないが、XSS からは `/api/refresh` の叩き放題・表示改竄（偽の買い目表示＝金銭的実害）が可能。

**対策**: `pvToEx()` で型を強制する。
```js
const num = v => (typeof v === 'number' && isFinite(v)) ? v : null;
// time/st/tilt/course/wind/wave/temp/winddir すべて num() を通す
// course はさらに 1..6 の整数のみ許可
```
`WX_NUM[...]` / `WINDDIR[...]` のようにホワイトリスト引きしている項目は安全。同じ方針を全項目に広げる。update.json（自前生成）経由の `km`・`parts`・`tenki` は正規表現で固定語彙に絞られており現状安全だが、KV が汚染された場合（H-4）の多層防御として、クライアント側でも `km` を既知の決まり手 6 種に限定するとよい。

### H-3. 認証 Cookie がパスワードの決定的ハッシュ（`_middleware.js:60`）
`token = SHA256(password + "|br")` には次の問題が重なる:

1. **失効・無効化ができない**: Max-Age はクライアント側の値でしかなく、Cookie 値自体はパスワードを変えない限り永久に有効。端末紛失・共有時に個別失効できない。
2. **Cookie 漏洩 = パスワード漏洩**: 固定 salt `"|br"` の生 SHA-256 なので、Cookie 値が漏れると GPU でのオフライン総当たりでパスワード本体まで割れる（弱いパスワードならほぼ確実）。
3. 全端末で同一トークン＝端末単位の管理が不可能。

**対策**（実装コスト順）:
- 最小: `SITE_PASSWORD` とは別の `SITE_SECRET` を用意し、`token = HMAC-SHA256(SITE_SECRET, "v1|" + 有効期限ts)` を発行して署名検証する（バージョン番号 `v1` を上げれば全端末即時失効できる。パスワードも逆算不能になる）。
- 推奨: **Cloudflare Access（Zero Trust）に置き換える**。個人利用の無料枠で足り、メール OTP/端末管理/失効/レート制限が全部プラットフォーム側に載り、自作認証コードを丸ごと消せる。

---

## 中リスク

### M-1. ログインのレート制限が「任意」かつ競合状態あり（`_middleware.js:74-107`）
- `env.BR_RL` 未バインドだと**レート制限が完全にスキップ**される（現状バインド済みかは Cloudflare 側設定次第）。8桁程度の弱いパスワードだと無制限総当たりが通る。
- KV の read→put はアトミックでなく、**並列リクエストでカウンタを追い越せる**（8回制限が実質突破可能）。KV の結果整合性により多リージョン分散攻撃でも緩む。

**対策**: Cloudflare ダッシュボードの **WAF レートリミットルール**（`POST` かつ該当ホストで N 回/分）をかける。エッジで止まるので KV の競合と無関係に効く。加えて BR_RL バインドの有無を起動時に前提化（未設定なら 500 で気づけるように）するか、H-3 の Access 移行で丸ごと解決。

### M-2. serve_odds.py の LAN 公開モード（`--bind 0.0.0.0`）
`SimpleHTTPRequestHandler` 継承のため:

- **ディレクトリリスティング有効**: `http://<PC>:8787/data/` で全データファイル一覧・取得可能（serve.log、オッズ CSV、予測 CSV など）。認証なし。
- **Host ヘッダ検証なし → DNS リバインディング**: 外部サイトが罠ページ経由で `localhost:8787` の応答を読める。ローカル閲覧（127.0.0.1 既定）でも、罠サイトからの `GET /update` で**PC に公式サイトへの一斉スクレイピングを実行させられる**（GET で副作用がある CSRF）。

**対策**:
- Handler で Host ヘッダを検証: `if self.headers.get("Host","").split(":")[0] not in ("localhost","127.0.0.1", <LAN IP>): return 403`
- `/update` `/odds` に簡易トークン（起動時ランダム生成 → today.html 側 URL に付与）を要求するか、少なくとも POST 化。
- `list_directory` をオーバーライドして 403 を返す（一覧を殺す）。
- LAN 公開はファイアウォールで自宅サブネットに限定、と README/bat に明記。

### M-3. サプライチェーン
- `requirements.txt` が `requests` / `lhafile` **バージョン未固定**。特に `lhafile` はメンテの薄い LZH パーサで、公式サイト由来のアーカイブ解凍に使う。`pip install` のたびに最新が入る＝上流汚染がそのまま CI（`contents: write` 権限あり）に入る。
  - **対策**: バージョン固定＋`pip install --require-hashes`（`pip-compile --generate-hashes` で生成）。
- GitHub Actions が `actions/checkout@v4` 等**タグ pin**。タグは動くので、コミット SHA pin が堅い: `actions/checkout@<full-sha> # v4.x.x`。Dependabot の `github-actions` エコシステムを有効にすると SHA pin でも追従できる。

### M-4. トークンの権限範囲（設定側の確認事項）
- `GH_DISPATCH_TOKEN`（Pages 環境変数）: fine-grained PAT で **対象リポジトリ 1 つ・Actions: write のみ**か確認。classic PAT の `repo` スコープだとリポジトリ全体の読み書きが漏洩範囲になる。有効期限も設定する。
- `CF_API_TOKEN`（GitHub Secrets）: **該当 KV namespace 1 つの Edit のみ**に絞る。これが漏れると KV `update` キーへの書き込み＝クライアントに任意 JSON 注入（H-2/H-4 の XSS 連鎖の起点）になるため、blast radius を最小化する。
- `refresh.js:52` が GitHub API のエラー本文をクライアントへ返している。認証済みユーザーにしか見えないが、`message` は固定文言にして詳細はログだけに残すのが行儀がよい。

### M-5. セキュリティヘッダ不在
静的配信・Functions 応答ともに CSP なし。XSS 経路（H-1/H-2）への多層防御として、`_middleware.js` の `next()` 応答にヘッダを足す:

```js
const res = await next();
const h = new Headers(res.headers);
h.set("Content-Security-Policy",
  "default-src 'self'; script-src 'self' 'unsafe-inline'; " +
  "connect-src 'self' https://boatraceopenapi.github.io; " +
  "img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'");
h.set("X-Content-Type-Options", "nosniff");
h.set("Referrer-Policy", "no-referrer");
return new Response(res.body, { status: res.status, headers: h });
```

インライン script/style 依存のため `unsafe-inline` は残るが、`connect-src` と `frame-ancestors` だけでも外部送信・クリックジャッキングを塞げる。

---

## 低リスク・作法

- **ログイン CSRF**: ログイン POST に CSRF トークンなし。実害は小さい（攻撃者が自分のパスワードでログインさせる攻撃は成立しない＝パスワードは 1 つ）。Access 移行で消える。
- **ログアウト手段なし**: 共有端末で開くと 30 日残る。`/logout` で Cookie を消す 3 行のルートを足すと安心。
- `loginPage(msg)` の `msg` は現状すべて内部固定文字列なので反射 XSS はないが、将来 `msg` にユーザー入力を渡さないこと（コメントで縛っておく）。
- Actions の `contents: write` は daily/update ワークフローに必要だが、`boatrace-freshness` は `actions: write` のみで正しく最小化されている。daily/update も生成物 push 用ボットを deploy key や環境保護に分離するとさらに堅い（個人運用なら現状で許容）。
- 公式サイトへのスクレイピングは 0.6 秒 wait・窓取得・キャッシュで負荷配慮されており良い。UA が `Mozilla/5.0 (boatrace-study-script)` と正体を名乗っているのも誠実。利用規約の確認だけしておくこと。

---

## 分析（予想ロジック）側の欠陥指摘

セキュリティ外だが「厳しく」とのことなので、数字の信頼性を損なう点を挙げる。

1. **学習期間を含む成績表示**: UI 自身が「4月までは学習期間を含むため高め」と注記済みだが、注記では不十分。**サマリー・場別成績の既定表示を 5 月以降（純アウトオブサンプル）に切り替え**、全期間はトグルの裏に隠すべき。学習期間込みの的中率は実力の過大表示になる。
2. **EV = モデル確率 × 取得時オッズの過大評価**: オッズは締切直前まで動き、本命側は締切にかけて下がるのが常。発走 7 分前の取得オッズで EV≥1.5 と判定しても、約定時点では EV が割れていることが多い。**「取得時刻とオッズの締切時変化」を odds CSV から実測し、EV 閾値に平均スリッページ分のマージンを乗せる**（例: 判定閾値を実測減衰率で補正）。
3. **鉄板閾値 0.65 / EV 閾値 1.5 のイン・サンプル選択**: 閾値をバックテストと同じデータで選んでいるなら過学習。閾値選択は前半期間、検証は後半期間に分離する。
4. **「対象外レース」除外の生存バイアス**: 前提崩れ（前づけ等）のレースを的中率集計から除くのは説明として妥当だが、**除外率も併記**しないと的中率が実運用より良く見える。買う前に除外を判定できない以上、回収率集計には含めるべき。
5. **直近 2 日の場別回収率**: サンプル極小で 3 連単 1 本に支配される（UI 注記あり）。表示するなら信頼区間か「参考値」の格下げ表示が誠実。
6. **仮想 100 万円チャレンジ**: 実配当で精算しているのは良いが、残高 12%/R の投票額だと実市場では自分の投票でオッズが下がる（マーケットインパクト）。「実際に賭けた場合の再現ではない」旨を注記に一言足すべき。

---

## 対応優先順位（推奨）

| # | 対応 | 工数 | 効果 |
|---|------|------|------|
| 1 | H-1: `.replace("</", "<\\/")` を build_* 3 ファイルに | 数分 | script 脱出の根絶 |
| 2 | H-2: `pvToEx` の型強制（number 以外は null） | 30分 | 第三者フィード XSS 遮断 |
| 3 | M-4: PAT / CF トークンの権限・期限を確認 | 15分 | 漏洩時の被害最小化 |
| 4 | H-3: HMAC 署名 Cookie 化 or Cloudflare Access 移行 | 半日 | 認証の構造欠陥解消 |
| 5 | M-1: WAF レートリミットルール追加 | 15分 | 総当たり防止 |
| 6 | M-5: セキュリティヘッダ追加 | 30分 | 多層防御 |
| 7 | M-3: 依存の hash pin・Actions SHA pin | 1時間 | サプライチェーン |
| 8 | M-2: serve_odds の Host 検証＋一覧無効化 | 1時間 | ローカル/LAN 経路 |
| 9 | 分析 1〜4 の表示・検証分離 | 継続 | 数字の信頼性 |
