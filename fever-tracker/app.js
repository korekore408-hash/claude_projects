(() => {
  "use strict";

  const STORAGE_KEY = "feverNote.v1";

  const SYMPTOMS = [
    "発熱", "咳", "鼻水", "のどの痛み", "嘔吐", "下痢",
    "発疹", "耳の痛み", "元気がない", "食欲不振", "機嫌が悪い", "その他"
  ];

  const MED_LABELS = {
    acetaminophen: "アセトアミノフェン",
    ibuprofen: "イブプロフェン",
    other: "その他のお薬"
  };

  const DEFAULT_HOURS = { acetaminophen: 6, ibuprofen: 8, other: 6 };

  /** @type {{tempRecords: Array, medRecords: Array}} */
  let state = loadState();

  function loadState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) {
      console.warn("failed to load state", e);
    }
    return { tempRecords: [], medRecords: [] };
  }

  function saveState() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function uid() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  function fmtDateTime(iso) {
    const d = new Date(iso);
    const mm = d.getMonth() + 1;
    const dd = d.getDate();
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${mm}/${dd} ${hh}:${mi}`;
  }

  function nowLocalInputValue() {
    const d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 16);
  }

  // ---------- symptom chips ----------
  function renderSymptomGrid() {
    const grid = document.getElementById("symptomGrid");
    grid.innerHTML = "";
    SYMPTOMS.forEach((s) => {
      const label = document.createElement("label");
      label.className = "symptom-chip";
      label.innerHTML = `<input type="checkbox" value="${s}"><span>${s}</span>`;
      const input = label.querySelector("input");
      input.addEventListener("change", () => {
        label.classList.toggle("checked", input.checked);
      });
      grid.appendChild(label);
    });
  }

  // ---------- medication status ----------
  function renderMedStatus() {
    const list = document.getElementById("medStatusList");
    if (state.medRecords.length === 0) {
      list.innerHTML = '<p class="empty-hint">まだお薬の記録はありません</p>';
      return;
    }

    // 最新のお薬種類ごとに、最後に飲んだ記録を集める
    const latestByType = new Map();
    for (const rec of state.medRecords) {
      const key = rec.type === "other" ? "other:" + (rec.name || "") : rec.type;
      const existing = latestByType.get(key);
      if (!existing || new Date(rec.time) > new Date(existing.time)) {
        latestByType.set(key, rec);
      }
    }

    const now = new Date();
    const items = [...latestByType.values()].sort(
      (a, b) => new Date(b.time) - new Date(a.time)
    );

    list.innerHTML = "";
    items.forEach((rec) => {
      const lastTime = new Date(rec.time);
      const nextTime = new Date(lastTime.getTime() + rec.intervalHours * 3600 * 1000);
      const diffMs = nextTime - now;
      const label = rec.type === "other" ? (rec.name || "その他のお薬") : MED_LABELS[rec.type];

      const item = document.createElement("div");
      item.className = "med-status-item";

      let badgeHtml;
      if (diffMs <= 0) {
        badgeHtml = `<div class="med-badge ok">今使えます<span class="badge-sub">OK</span></div>`;
      } else {
        const totalMin = Math.ceil(diffMs / 60000);
        const h = Math.floor(totalMin / 60);
        const m = totalMin % 60;
        const remain = h > 0 ? `あと${h}時間${m}分` : `あと${m}分`;
        badgeHtml = `<div class="med-badge wait">${remain}<span class="badge-sub">目安 ${fmtDateTime(nextTime.toISOString())}〜</span></div>`;
      }

      item.innerHTML = `
        <div>
          <div class="med-name">${escapeHtml(label)}</div>
          <div class="med-last">前回: ${fmtDateTime(rec.time)}（${rec.intervalHours}時間あける）</div>
        </div>
        ${badgeHtml}
      `;
      list.appendChild(item);
    });
  }

  // ---------- chart ----------
  let chart = null;
  function renderChart() {
    const canvas = document.getElementById("tempChart");
    const emptyHint = document.getElementById("chartEmptyHint");
    const sorted = [...state.tempRecords].sort(
      (a, b) => new Date(a.time) - new Date(b.time)
    );

    if (sorted.length === 0) {
      emptyHint.style.display = "block";
      canvas.style.display = "none";
      if (chart) { chart.destroy(); chart = null; }
      return;
    }
    emptyHint.style.display = "none";
    canvas.style.display = "block";

    const labels = sorted.map((r) => fmtDateTime(r.time));
    const data = sorted.map((r) => r.temp);

    if (chart) {
      chart.data.labels = labels;
      chart.data.datasets[0].data = data;
      chart.update();
      return;
    }

    chart = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "体温 (℃)",
            data,
            borderColor: "#ff8fa3",
            backgroundColor: "rgba(255,143,163,0.15)",
            tension: 0.3,
            fill: true,
            pointRadius: 4,
            pointBackgroundColor: "#ef5a72",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: {
            min: 35,
            max: 41,
            ticks: { stepSize: 1 },
            grid: { color: "#f1e3e5" },
          },
          x: {
            ticks: { maxRotation: 60, minRotation: 45, autoSkip: true, maxTicksLimit: 8 },
            grid: { display: false },
          },
        },
        plugins: {
          legend: { display: false },
          annotation: undefined,
        },
      },
    });
  }

  // ---------- record list ----------
  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function renderRecordList() {
    const list = document.getElementById("recordList");
    const all = [
      ...state.tempRecords.map((r) => ({ ...r, kind: "temp" })),
      ...state.medRecords.map((r) => ({ ...r, kind: "med" })),
    ].sort((a, b) => new Date(b.time) - new Date(a.time));

    if (all.length === 0) {
      list.innerHTML = '<p class="empty-hint">まだ記録がありません</p>';
      return;
    }

    list.innerHTML = "";
    all.forEach((rec) => {
      const item = document.createElement("div");
      item.className = `record-item ${rec.kind}`;

      if (rec.kind === "temp") {
        const tagsHtml = (rec.symptoms || [])
          .map((s) => `<span class="record-tag">${escapeHtml(s)}</span>`)
          .join("");
        item.innerHTML = `
          <div class="record-icon">🌡️</div>
          <div class="record-body">
            <div class="record-time">${fmtDateTime(rec.time)}</div>
            <div class="record-main">${rec.temp.toFixed(1)}℃</div>
            <div class="record-tags">${tagsHtml}</div>
          </div>
          <button class="record-delete" data-kind="temp" data-id="${rec.id}">✕</button>
        `;
      } else {
        const label = rec.type === "other" ? (rec.name || "その他のお薬") : MED_LABELS[rec.type];
        item.innerHTML = `
          <div class="record-icon">💊</div>
          <div class="record-body">
            <div class="record-time">${fmtDateTime(rec.time)}</div>
            <div class="record-main">${escapeHtml(label)}</div>
            <div class="record-tags"><span class="record-tag">次回まで${rec.intervalHours}時間</span></div>
          </div>
          <button class="record-delete" data-kind="med" data-id="${rec.id}">✕</button>
        `;
      }
      list.appendChild(item);
    });

    list.querySelectorAll(".record-delete").forEach((btn) => {
      btn.addEventListener("click", () => {
        const { kind, id } = btn.dataset;
        if (kind === "temp") {
          state.tempRecords = state.tempRecords.filter((r) => r.id !== id);
        } else {
          state.medRecords = state.medRecords.filter((r) => r.id !== id);
        }
        saveState();
        renderAll();
      });
    });
  }

  function renderAll() {
    renderMedStatus();
    renderChart();
    renderRecordList();
  }

  // ---------- modals ----------
  function openModal(overlay) {
    overlay.classList.add("open");
  }
  function closeModal(overlay) {
    overlay.classList.remove("open");
  }

  function setupModal(overlayId, openBtnId, onOpen) {
    const overlay = document.getElementById(overlayId);
    document.getElementById(openBtnId).addEventListener("click", () => {
      onOpen && onOpen();
      openModal(overlay);
    });
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) closeModal(overlay);
    });
    overlay.querySelectorAll("[data-close]").forEach((btn) => {
      btn.addEventListener("click", () => closeModal(overlay));
    });
  }

  // ---------- init ----------
  function init() {
    renderSymptomGrid();

    setupModal("tempModalOverlay", "openTempModalBtn", () => {
      document.getElementById("tempForm").reset();
      document.getElementById("tempTime").value = nowLocalInputValue();
      document.querySelectorAll("#symptomGrid .symptom-chip").forEach((c) =>
        c.classList.remove("checked")
      );
    });

    setupModal("medModalOverlay", "openMedModalBtn", () => {
      document.getElementById("medForm").reset();
      document.getElementById("medTime").value = nowLocalInputValue();
      document.getElementById("medInterval").value = DEFAULT_HOURS.acetaminophen;
      document.getElementById("medOtherNameField").hidden = true;
    });

    document.getElementById("medType").addEventListener("change", (e) => {
      const type = e.target.value;
      document.getElementById("medInterval").value = DEFAULT_HOURS[type];
      document.getElementById("medOtherNameField").hidden = type !== "other";
    });

    document.getElementById("tempForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const timeVal = document.getElementById("tempTime").value;
      const tempVal = parseFloat(document.getElementById("tempValue").value);
      const symptoms = [...document.querySelectorAll("#symptomGrid input:checked")].map(
        (i) => i.value
      );
      if (!timeVal || Number.isNaN(tempVal)) return;

      state.tempRecords.push({
        id: uid(),
        time: new Date(timeVal).toISOString(),
        temp: tempVal,
        symptoms,
      });
      saveState();
      renderAll();
      closeModal(document.getElementById("tempModalOverlay"));
    });

    document.getElementById("medForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const type = document.getElementById("medType").value;
      const name = document.getElementById("medOtherName").value.trim();
      const intervalHours = parseFloat(document.getElementById("medInterval").value);
      const timeVal = document.getElementById("medTime").value;
      if (!timeVal || Number.isNaN(intervalHours)) return;

      state.medRecords.push({
        id: uid(),
        time: new Date(timeVal).toISOString(),
        type,
        name: type === "other" ? name : undefined,
        intervalHours,
      });
      saveState();
      renderAll();
      closeModal(document.getElementById("medModalOverlay"));
    });

    document.getElementById("resetBtn").addEventListener("click", () => {
      if (confirm("すべての記録を消去します。よろしいですか？（熱が下がって発熱が治ったときに使ってください）")) {
        state = { tempRecords: [], medRecords: [] };
        saveState();
        renderAll();
      }
    });

    renderAll();

    // お薬の残り時間表示を1分ごとに更新
    setInterval(renderMedStatus, 60 * 1000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
