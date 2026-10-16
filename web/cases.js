const caseList = document.querySelector("#casePageList");
const escapeHtml = (value) => String(value || "").replace(/[&<>\"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

function timestamp(value) {
  return String(value || "").replace("T", " ").replace(/\+00:00$/, " UTC") || "时间未记录";
}

function caseWorkspaceUrl(id, round) {
  return `./?case=${encodeURIComponent(id)}&round=${encodeURIComponent(round)}`;
}

async function addLaterOa(caseId, button) {
  button.disabled = true;
  button.textContent = "正在建立…";
  try {
    const response = await fetch(`/api/cases/${encodeURIComponent(caseId)}/rounds`, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "无法新增 OA 轮次");
    window.location.href = `${caseWorkspaceUrl(caseId, payload.round.number)}&newRound=1`;
  } catch (error) {
    button.disabled = false;
    button.textContent = "新增 OA 轮次";
    window.alert(error.message || "无法新增 OA 轮次，请确认本机桥接器正在运行。");
  }
}

function renderCases(cases) {
  if (!cases.length) {
    caseList.innerHTML = '<p class="case-empty">尚无已保存的案件。请先在工作台完成一个案件；之后它会出现在这里并可继续编辑。</p>';
    return;
  }
  caseList.innerHTML = cases.map((entry) => {
    const application = entry.applicationNumber || "申请号尚未识别";
    const activeRound = Number(entry.activeRoundNumber || 1);
    const editableRound = Number(entry.editableRoundNumber || activeRound);
    const openLabel = editableRound === activeRound ? "打开并继续编辑" : `打开 OA-${String(editableRound).padStart(2, "0")} 并继续编辑`;
    const copy = editableRound === activeRound
      ? "可继续编辑本轮材料、查看既有回答或补充追问。"
      : `当前 OA-${String(activeRound).padStart(2, "0")} 尚未上传材料；将先打开最近有材料的 OA-${String(editableRound).padStart(2, "0")} 供继续编辑。`;
    return `<article class="case-page-card"><div class="case-page-card-heading"><div><p class="eyebrow">${escapeHtml(application)}</p><h2>${escapeHtml(application)} · OA 策略分析</h2></div><span class="pill">当前 OA-${String(activeRound).padStart(2, "0")}</span></div><p class="case-page-meta">${entry.roundCount || 1} 个 OA 轮次 · ${entry.answerCount || 0} 份答复记录 · 最近更新：${escapeHtml(timestamp(entry.updatedAt || entry.createdAt))}</p><p class="case-page-copy">${copy}</p><div class="case-page-actions"><a class="primary" href="${caseWorkspaceUrl(entry.id, editableRound)}">${openLabel}</a><button class="ghost" type="button" data-add-round="${escapeHtml(entry.id)}">新增 OA 轮次</button></div></article>`;
  }).join("");
  caseList.querySelectorAll("[data-add-round]").forEach((button) => button.addEventListener("click", () => addLaterOa(button.dataset.addRound, button)));
}

async function loadCases() {
  try {
    const response = await fetch("/api/cases");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "无法读取案件");
    renderCases(payload.cases || []);
  } catch (error) {
    caseList.innerHTML = `<p class="case-empty">无法读取案件：${escapeHtml(error.message || "请确认本机桥接器正在运行。")}</p>`;
  }
}

loadCases();
