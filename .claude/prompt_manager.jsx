import { useState, useEffect } from "react";

const STORAGE_KEY = "prompts-v1";

const P1 = "#役割\nあなたはプロの〇〇です。\n\n#依頼内容\n私は今、〇〇という状況です。\n今回の目的は〇〇です。\n\n#参考情報\n必要な情報があればここに入れる\n\n#要件\n・全体像を整理する\n・重要なポイントを分解する\n・具体案を３つ出す\n・初心者にもわかりやすくする\n・すぐ使えるレベルまで具体化する\n\n#制約条件\n・専門用語は避ける\n・根拠が弱いことは断定しない\n・ありきたりな回答で終わらせない\n・煽りすぎた表現は避ける\n\n#出力形式\n1.結論\n2.理由\n3.具体案\n4.すぐ使える完成版";

const P2 = "#役割\nあなたはアルミダイカスト製造の専門コンサルタントです。\n\n#依頼内容\n私は今、愛知県の製造現場で〇〇という問題が起きています。\n今回の目的は〇〇を改善することです。\n\n#参考情報\n・製品名・型番：\n・発生工程：\n・不良率・頻度：\n\n#要件\n・原因を工程別に整理する\n・重要なポイントを分解する\n・改善案を３つ出す\n・現場担当者にわかりやすくする\n・明日から使えるレベルまで具体化する\n\n#制約条件\n・過度な専門用語は避ける\n・根拠が弱いことは断定しない\n・一般論で終わらせない\n・現場の実情に即した内容にする\n\n#出力形式\n1.結論（何が原因か）\n2.理由（なぜそうなるか）\n3.改善案（3つ）\n4.すぐ使えるアクションプラン";

const DEFAULT_PROMPTS = [
  { id: "1", title: "汎用テンプレート", category: "基本", content: P1, createdAt: 1 },
  { id: "2", title: "ダイカスト現場改善", category: "製造", content: P2, createdAt: 2 },
];

const CATEGORIES = ["基本", "製造", "競艇", "分析", "その他"];
const CAT_COLOR = { "基本": "#38bdf8", "製造": "#f59e0b", "競艇": "#10b981", "分析": "#a78bfa", "その他": "#94a3b8" };
const FONT = "'Hiragino Kaku Gothic ProN','Noto Sans JP',sans-serif";

export default function App() {
  const [prompts, setPrompts] = useState([]);
  const [view, setView] = useState("list");
  const [selected, setSelected] = useState(null);
  const [editData, setEditData] = useState({ title: "", category: "基本", content: "" });
  const [search, setSearch] = useState("");
  const [catFilter, setCatFilter] = useState("すべて");
  const [toast, setToast] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await window.storage.get(STORAGE_KEY);
        const data = JSON.parse(r.value);
        setPrompts(Array.isArray(data) && data.length ? data : DEFAULT_PROMPTS);
      } catch {
        setPrompts(DEFAULT_PROMPTS);
      }
      setLoaded(true);
    })();
  }, []);

  const persist = async (list) => {
    try { await window.storage.set(STORAGE_KEY, JSON.stringify(list)); } catch {}
  };

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2200);
  };

  const filtered = prompts.filter((p) => {
    const mc = catFilter === "すべて" || p.category === catFilter;
    const ms = !search || p.title.includes(search) || p.content.includes(search);
    return mc && ms;
  });

  const openNew = () => {
    setEditData({ title: "", category: "基本", content: "" });
    setView("new");
  };

  const openEdit = (p) => {
    setSelected(p);
    setEditData({ title: p.title, category: p.category, content: p.content });
    setView("edit");
  };

  const openDetail = (p) => {
    setSelected(p);
    setView("detail");
  };

  const handleSave = () => {
    if (!editData.title.trim() || !editData.content.trim()) {
      showToast("タイトルと内容を入力してください");
      return;
    }
    let updated;
    if (view === "new") {
      updated = [{ ...editData, id: String(Date.now()), createdAt: Date.now() }, ...prompts];
    } else {
      updated = prompts.map((p) => (p.id === selected.id ? { ...p, ...editData } : p));
    }
    setPrompts(updated);
    persist(updated);
    showToast("保存しました");
    setView("list");
  };

  const handleDelete = (id) => {
    const updated = prompts.filter((p) => p.id !== id);
    setPrompts(updated);
    persist(updated);
    showToast("削除しました");
    setView("list");
  };

  const handleCopy = (content) => {
    navigator.clipboard.writeText(content).then(() => showToast("コピーしました！"));
  };

  if (!loaded) {
    return (
      <div style={{ minHeight: "100vh", background: "#0a0f1e", display: "flex", alignItems: "center", justifyContent: "center", color: "#38bdf8", fontFamily: FONT }}>
        読み込み中...
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", background: "#0a0f1e", color: "#e2e8f0", fontFamily: FONT, paddingBottom: 40 }}>

      {toast ? (
        <div style={{ position: "fixed", top: 20, left: "50%", transform: "translateX(-50%)", background: "#1e293b", border: "1px solid #38bdf8", color: "#38bdf8", padding: "10px 24px", borderRadius: 99, fontSize: 13, fontWeight: 700, zIndex: 999, whiteSpace: "nowrap" }}>
          {toast}
        </div>
      ) : null}

      <div style={{ background: "rgba(10,15,30,0.95)", borderBottom: "1px solid #1e293b", padding: "14px 18px", display: "flex", alignItems: "center", justifyContent: "space-between", position: "sticky", top: 0, zIndex: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {view !== "list" ? (
            <button onClick={() => setView("list")} style={{ background: "none", border: "none", color: "#94a3b8", fontSize: 22, cursor: "pointer", padding: 0 }}>
              {"←"}
            </button>
          ) : null}
          <div>
            <div style={{ fontWeight: 900, fontSize: 16, background: "linear-gradient(90deg,#38bdf8,#a78bfa)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              {view === "list" ? "プロンプト集" : view === "detail" ? (selected ? selected.title : "") : view === "new" ? "新規作成" : "編集"}
            </div>
            {view === "list" ? <div style={{ color: "#475569", fontSize: 11, marginTop: 1 }}>{prompts.length}件保存中</div> : null}
          </div>
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          {view === "list" ? (
            <button onClick={openNew} style={{ background: "linear-gradient(135deg,#38bdf8,#a78bfa)", border: "none", color: "#fff", borderRadius: 10, padding: "8px 16px", fontWeight: 700, fontSize: 13, cursor: "pointer" }}>
              + 追加
            </button>
          ) : null}
          {view === "detail" ? (
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={() => openEdit(selected)} style={{ background: "rgba(167,139,250,0.15)", border: "1px solid rgba(167,139,250,0.4)", color: "#c4b5fd", borderRadius: 8, padding: "6px 14px", fontSize: 12, cursor: "pointer" }}>
                編集
              </button>
              <button onClick={() => handleCopy(selected.content)} style={{ background: "linear-gradient(135deg,#38bdf8,#10b981)", border: "none", color: "#fff", borderRadius: 8, padding: "6px 14px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                コピー
              </button>
            </div>
          ) : null}
          {view === "new" || view === "edit" ? (
            <button onClick={handleSave} style={{ background: "linear-gradient(135deg,#10b981,#38bdf8)", border: "none", color: "#fff", borderRadius: 10, padding: "8px 18px", fontWeight: 700, fontSize: 13, cursor: "pointer" }}>
              保存
            </button>
          ) : null}
        </div>
      </div>

      <div style={{ maxWidth: 680, margin: "0 auto", padding: "18px 16px" }}>

        {view === "list" ? (
          <div>
            <div style={{ position: "relative", marginBottom: 12 }}>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="検索..."
                style={{ width: "100%", boxSizing: "border-box", background: "rgba(255,255,255,0.05)", border: "1px solid #1e293b", borderRadius: 12, padding: "10px 14px", color: "#e2e8f0", fontSize: 14, outline: "none" }}
              />
            </div>

            <div style={{ display: "flex", gap: 6, overflowX: "auto", paddingBottom: 4, marginBottom: 16 }}>
              {["すべて"].concat(CATEGORIES).map((cat) => (
                <button
                  key={cat}
                  onClick={() => setCatFilter(cat)}
                  style={{
                    flexShrink: 0, padding: "5px 14px", borderRadius: 99, cursor: "pointer", fontSize: 12,
                    border: catFilter === cat ? ("1.5px solid " + (CAT_COLOR[cat] || "#38bdf8")) : "1.5px solid #1e293b",
                    background: catFilter === cat ? ((CAT_COLOR[cat] || "#38bdf8") + "20") : "transparent",
                    color: catFilter === cat ? (CAT_COLOR[cat] || "#38bdf8") : "#64748b",
                    fontWeight: catFilter === cat ? 700 : 400,
                  }}
                >
                  {cat}
                </button>
              ))}
            </div>

            {filtered.length === 0 ? (
              <div style={{ textAlign: "center", color: "#334155", padding: "60px 0", fontSize: 14 }}>
                プロンプトがありません
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {filtered.map((p) => (
                  <div
                    key={p.id}
                    onClick={() => openDetail(p)}
                    style={{ background: "rgba(255,255,255,0.03)", border: "1px solid #1e293b", borderRadius: 14, padding: "14px 16px", cursor: "pointer", borderLeft: "3px solid " + (CAT_COLOR[p.category] || "#94a3b8") }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 5 }}>
                      <div style={{ fontWeight: 700, fontSize: 14, color: "#f1f5f9" }}>{p.title}</div>
                      <span style={{ background: (CAT_COLOR[p.category] || "#94a3b8") + "22", color: CAT_COLOR[p.category] || "#94a3b8", fontSize: 11, padding: "2px 10px", borderRadius: 99, fontWeight: 700, flexShrink: 0, marginLeft: 8 }}>
                        {p.category}
                      </span>
                    </div>
                    <div style={{ color: "#64748b", fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", marginBottom: 10 }}>
                      {p.content.replace(/\n/g, " ").slice(0, 55)}
                    </div>
                    <div style={{ display: "flex", justifyContent: "flex-end" }}>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleCopy(p.content); }}
                        style={{ background: "rgba(56,189,248,0.1)", border: "1px solid rgba(56,189,248,0.3)", color: "#38bdf8", borderRadius: 8, padding: "5px 16px", fontSize: 12, cursor: "pointer", fontWeight: 600 }}
                      >
                        コピー
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : null}

        {view === "detail" && selected ? (
          <div>
            <span style={{ background: (CAT_COLOR[selected.category] || "#94a3b8") + "22", color: CAT_COLOR[selected.category] || "#94a3b8", fontSize: 12, padding: "3px 14px", borderRadius: 99, fontWeight: 700, display: "inline-block", marginBottom: 16 }}>
              {selected.category}
            </span>
            <div style={{ background: "rgba(255,255,255,0.03)", border: "1px solid #1e293b", borderRadius: 14, padding: "18px 20px", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 2, color: "#cbd5e1", marginBottom: 16 }}>
              {selected.content}
            </div>
            <button onClick={() => handleDelete(selected.id)} style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", color: "#f87171", borderRadius: 10, padding: 11, fontSize: 13, cursor: "pointer", width: "100%" }}>
              削除する
            </button>
          </div>
        ) : null}

        {view === "new" || view === "edit" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div>
              <label style={{ fontSize: 12, color: "#64748b", display: "block", marginBottom: 6 }}>タイトル</label>
              <input
                value={editData.title}
                onChange={(e) => setEditData({ ...editData, title: e.target.value })}
                placeholder="例：ダイカスト不良原因分析"
                style={{ width: "100%", boxSizing: "border-box", background: "rgba(255,255,255,0.05)", border: "1px solid #1e293b", borderRadius: 10, padding: "11px 14px", color: "#e2e8f0", fontSize: 14, outline: "none" }}
              />
            </div>
            <div>
              <label style={{ fontSize: 12, color: "#64748b", display: "block", marginBottom: 8 }}>カテゴリ</label>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {CATEGORIES.map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setEditData({ ...editData, category: cat })}
                    style={{
                      padding: "6px 16px", borderRadius: 99, cursor: "pointer", fontSize: 12,
                      border: editData.category === cat ? ("1.5px solid " + CAT_COLOR[cat]) : "1.5px solid #1e293b",
                      background: editData.category === cat ? (CAT_COLOR[cat] + "20") : "transparent",
                      color: editData.category === cat ? CAT_COLOR[cat] : "#64748b",
                      fontWeight: editData.category === cat ? 700 : 400,
                    }}
                  >
                    {cat}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label style={{ fontSize: 12, color: "#64748b", display: "block", marginBottom: 6 }}>プロンプト内容</label>
              <textarea
                value={editData.content}
                onChange={(e) => setEditData({ ...editData, content: e.target.value })}
                placeholder="#役割&#10;あなたは〇〇の専門家です。"
                rows={14}
                style={{ width: "100%", boxSizing: "border-box", background: "rgba(255,255,255,0.05)", border: "1px solid #1e293b", borderRadius: 10, padding: "12px 14px", color: "#e2e8f0", fontSize: 13, outline: "none", resize: "vertical", lineHeight: 1.9, fontFamily: FONT }}
              />
            </div>
            {view === "edit" ? (
              <button onClick={() => handleDelete(selected.id)} style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)", color: "#f87171", borderRadius: 10, padding: 10, fontSize: 13, cursor: "pointer" }}>
                このプロンプトを削除
              </button>
            ) : null}
          </div>
        ) : null}

      </div>
    </div>
  );
}
