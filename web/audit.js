const $ = (selector) => document.querySelector(selector);
let audit = null;
let refreshTimer = null;

function remaining(used, limit) { return Math.max(0, limit - used); }

function render() {
  if (!audit) return;
  const { limits, usage } = audit;
  const cards = [
    { label: "笔记本数限制", used: usage.notebooks, limit: limits.notebooks, note: audit.notebookCountScope === "account" ? "已从当前 NotebookLM 账户同步" : "本机已知笔记本；请同步以读取账户总数" },
    { label: "每日对话限制", used: usage.dailyChats, limit: limits.dailyChats, note: "仅统计 OA阅答今天在本机发起的查询" },
    { label: "每个笔记本来源数限制", used: usage.sourcesPerNotebook, limit: limits.sourcesPerNotebook, note: audit.activeNotebook?.title ? `当前：${audit.activeNotebook.title}` : "尚未选择或创建 OA Notebook" },
  ];
  $("#auditPlan").value = audit.plan;
  $("#auditCards").innerHTML = cards.map((card) => {
    const left = remaining(card.used, card.limit);
    const percent = Math.min(100, Math.round((card.used / card.limit) * 100));
    return `<article class="audit-card"><p>${card.label}</p><strong>${card.used}<small> / ${card.limit}</small></strong><b>剩余 ${left}</b><div class="audit-bar"><i style="width:${percent}%"></i></div><span>${card.note}</span></article>`;
  }).join("");
  const sync = audit.sync || {};
  const refreshButton = $("#refreshAudit");
  const syncing = sync.state === "syncing";
  refreshButton.disabled = syncing;
  refreshButton.setAttribute("aria-busy", syncing ? "true" : "false");
  refreshButton.textContent = syncing
    ? "正在同步…"
    : sync.state === "ready" && Number.isInteger(sync.notebookCount)
      ? `同步笔记本数（当前 ${sync.notebookCount}）`
      : "同步笔记本数";
  if (sync.state === "syncing") $("#auditSyncState").textContent = "正在通过本机 NotebookLM 桥接器同步账户笔记本数量…";
  else if (sync.state === "error") $("#auditSyncState").textContent = `暂未取得账户笔记本总数：${sync.error || "桥接器未返回结果"}。当前仍显示本机已知笔记本数。`;
  else if (sync.syncedAt) $("#auditSyncState").textContent = `已同步账户笔记本数；最近同步：${new Date(sync.syncedAt).toLocaleString()}。`;
  else $("#auditSyncState").textContent = "尚未同步账户笔记本数；可点击右上角“同步笔记本数”。";
}

async function loadAudit(refresh = false) {
  const response = await fetch(`/api/audit${refresh ? "?refresh=1" : ""}`);
  if (!response.ok) throw new Error("无法读取审计数据");
  audit = await response.json();
  render();
  if (audit.sync?.state === "syncing") {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => loadAudit(false).catch(showError), 1500);
  }
}

function showError(error) { $("#auditSyncState").textContent = error.message || "无法读取本机审计数据。"; }

$("#refreshAudit").addEventListener("click", async () => {
  const button = $("#refreshAudit");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = "正在连接 NotebookLM…";
  $("#auditSyncState").textContent = "正在连接当前 NotebookLM 账户并读取笔记本列表…";
  try {
    await loadAudit(true);
  } catch (error) {
    button.disabled = false;
    button.setAttribute("aria-busy", "false");
    button.textContent = "重新同步笔记本数";
    showError(error);
  }
});
$("#auditPlan").addEventListener("change", async (event) => {
  const response = await fetch("/api/audit/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan: event.target.value }) });
  if (!response.ok) { showError(new Error("无法保存套餐选择")); return; }
  audit = await response.json();
  render();
});
loadAudit().catch(showError);
