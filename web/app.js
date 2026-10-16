const MATERIAL_TYPES = [
  { id: "application", title: "原申请文件", required: true, hint: "说明书、权利要求书、附图或其合并 PDF" },
  { id: "officeAction", title: "审查意见通知书", required: true, hint: "本轮 OA、审查意见或审查报告" },
  { id: "priorArt", title: "对比文件", required: false, hint: "OA 引用的专利、论文或其他现有技术" },
  { id: "response", title: "意见陈述书", required: false, hint: "仅后续 OA：此前提交的答复或修改文件" },
];

const materials = Object.fromEntries(MATERIAL_TYPES.map((type) => [type.id, []]));
const { cacheMaterialFile, fileAsBase64 } = globalThis.OAOcrFileCache;
const issues = [];
let selected = new Set();
let focusedIssue = null;
let loginPollingTimer = null;
let loginFeedbackTimer = null;
let loginFeedbackStartedAt = 0;
let qualityCheckRunning = false;
let ocrStatus = null;
let ocrReviewRunning = false;
let ocrSetupPollingTimer = null;
let ocrSetupPollGeneration = 0;
let ocrSetupRequestRunning = false;
let lastOcrJobId = null;
let notebookSubmissionRunning = false;
let materialSubmissionOpening = false;
let lastNotebookStatus = null;
let notebookStatusVerificationTimer = null;
let completedNotebookOcrJobId = null;
let latestNotebookJob = null;
let issueAnalysisRunning = false;
let latestIssueAnalysis = null;
let issueAnalysisHistory = [];
let recoveryAttempted = false;
let priorNotebookHistory = null;
let currentApplicationNumber = null;
let issuesPanelCollapsed = false;
let oaRoundNumber = 1;
let newRoundAwaitingMaterials = sessionStorage.getItem("oaRoundAwaitingMaterials") === "true";
const workspaceQuery = new URLSearchParams(window.location.search);
const startingNewCase = workspaceQuery.has("newCase");
let activeCaseId = workspaceQuery.get("case") || null;
let activeRoundNumber = Number(workspaceQuery.get("round") || 1);
let activeCaseNotebookId = null;
let activeCaseApplicationNumber = null;
let archiveSettings = null;
let historyMaterialRounds = [];
let responsePurpose = isLaterOa() ? "history" : "optimize";

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const materialGroups = $("#materialGroups");
const issueList = $("#issueList");

function isLaterOa() {
  return Boolean(activeCaseId && activeRoundNumber > 1);
}

function materialGroupDisplay(group) {
  if (group.id !== "response") return group;
  const optimizing = responsePurpose === "optimize";
  return {
    ...group,
    title: optimizing ? "已有意见陈述书" : "历史意见陈述书",
    hint: optimizing
      ? "可选：本轮已有的意见陈述书草稿；将作为优化基线，NotebookLM 会提出段落级补充、替换与权利要求修改建议"
      : "可选：此前提交的答复、修改文本或相关陈述；作为本轮审查策略的历史背景材料",
  };
}

function materialGroupsForActiveRound() {
  if (!isLaterOa()) return MATERIAL_TYPES.map(materialGroupDisplay);
  const labels = {
    officeAction: { title: "补充审查意见通知书", hint: "本轮最新 OA、审查意见或审查报告；用于生成本轮新的答复策略" },
    priorArt: { title: "补充对比文件", hint: "本轮 OA 新引用的专利、论文或其他现有技术" },
    response: { title: "历史意见陈述书", hint: "此前实际提交的答复、修改文本或相关陈述；NotebookLM 会将其作为历史答复背景" },
  };
  return MATERIAL_TYPES.filter((group) => group.id !== "application").map((group) => materialGroupDisplay({ ...group, ...labels[group.id] }));
}

function materialKindTitle(kind) {
  return MATERIAL_TYPES.find((group) => group.id === kind)?.title || "材料";
}

function renderHistoricalMaterials() {
  const panel = $("#historyMaterials");
  const rounds = historyMaterialRounds.filter((entry) => Array.isArray(entry.materials) && entry.materials.length);
  if (!isLaterOa() || !rounds.length) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  panel.hidden = false;
  panel.innerHTML = `<div class="history-materials-heading"><div><p class="eyebrow">案件历史材料</p><h2>此前 OA 已上传材料</h2><p>以下材料已保留在本案件的 NotebookLM 中，本轮不会重复上传。请在下方仅补充本轮新增的 OA、对比文件或历史意见陈述书。</p></div><span class="count">${rounds.reduce((total, entry) => total + entry.materials.length, 0)} 份历史材料</span></div><div class="history-material-rounds">${rounds.map((entry) => `<section class="history-material-round"><h3>OA-${String(entry.roundNumber || 0).padStart(2, "0")}</h3><div class="history-material-list">${entry.materials.map((material) => `<div class="history-material-row"><span class="file-symbol">${escapeHtml(String(material.name || "文件").split(".").pop()?.toUpperCase() || "文件")}</span><div><b>${escapeHtml(material.name || "未命名材料")}</b><small>${escapeHtml(materialKindTitle(material.kind))}${material.pages ? ` · ${material.pages} 页` : ""}</small></div><span class="quality good">已保留</span></div>`).join("")}</div></section>`).join("")}</div>`;
}

function renderRoundIdentity() {
  const round = activeCaseId ? activeRoundNumber : 1;
  const label = `OA-${String(round).padStart(2, "0")}`;
  $("#roundMaterialLabel").textContent = `${label} / ${isLaterOa() ? "补充材料" : "材料"}`;
  $("#materialsTitle").textContent = isLaterOa() ? "补充材料" : "按材料类型上传";
  $("#oaKind").textContent = round > 1 ? "中国 · 后续 OA" : "中国 · 首次 OA";
  $("#caseTitle").textContent = activeCaseApplicationNumber
    ? `${activeCaseApplicationNumber} · ${label} 审通答复`
    : round > 1 ? `请上传第 ${round} 轮 OA 材料` : "请先上传本案件 OA 材料";
}

function resetWorkspaceForNewRound() {
  Object.values(materials).forEach((files) => files.splice(0, files.length));
  issues.splice(0, issues.length);
  selected = new Set();
  focusedIssue = null;
  lastOcrJobId = null;
  completedNotebookOcrJobId = null;
  latestNotebookJob = null;
  latestIssueAnalysis = null;
  issueAnalysisHistory = [];
  priorNotebookHistory = null;
  currentApplicationNumber = null;
  activeCaseId = null;
  activeRoundNumber = 1;
  activeCaseNotebookId = null;
  activeCaseApplicationNumber = null;
  historyMaterialRounds = [];
  qualityCheckRunning = false;
  ocrReviewRunning = false;
  notebookSubmissionRunning = false;
  issueAnalysisRunning = false;
  newRoundAwaitingMaterials = true;
  sessionStorage.setItem("oaRoundAwaitingMaterials", "true");
  closeCitationPopup();
  $("#evidenceTitle").textContent = "选择一个争点";
  $("#evidenceBody").className = "empty-state";
  $("#evidenceBody").innerHTML = '<span class="empty-icon">↝</span><p>上传本轮材料后，系统会生成新的 OA 审查问题清单。</p>';
  setIssuesPanelCollapsed(false);
  renderRoundIdentity();
  renderHistoricalMaterials();
  renderMaterials();
  renderIssues();
  renderTimeline();
}

async function archiveAndStartNewRound() {
  const confirmButton = $("#confirmNewRound");
  confirmButton.disabled = true;
  try {
    const response = await fetch("/api/rounds/archive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        roundNumber: activeCaseId ? activeRoundNumber : oaRoundNumber,
        caseId: activeCaseId,
        applicationNumber: currentApplicationNumber,
        ocrJobId: lastOcrJobId,
        notebookId: latestNotebookJob?.notebookId || null,
        issueAnalysisIds: issueAnalysisHistory.map((entry) => entry.id),
      }),
    });
    const archive = await response.json();
    if (!response.ok) throw new Error(archive.error || "无法归档当前 OA 轮次");
    $("#newRoundDialog").close();
    resetWorkspaceForNewRound();
    window.history.replaceState({}, "", "./?newCase=1");
    showToast("已归档并新建案件", `当前材料与答复记录已保留在本机归档 ${archive.id}；现在可上传新案件的首次 OA 材料。`);
  } catch (error) {
    showToast("无法新建 OA 轮次", error.message || "请确认本机桥接器正在运行后重试。");
  } finally {
    confirmButton.disabled = false;
  }
}

async function archiveCurrentCase() {
  const button = $("#archiveCase");
  if (button.disabled) return;
  button.disabled = true;
  try {
    const response = await fetch("/api/rounds/archive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        roundNumber: activeCaseId ? activeRoundNumber : oaRoundNumber,
        caseId: activeCaseId,
        applicationNumber: currentApplicationNumber,
        ocrJobId: lastOcrJobId,
        notebookId: latestNotebookJob?.notebookId || activeCaseNotebookId || null,
        issueAnalysisIds: issueAnalysisHistory.map((entry) => entry.id),
      }),
    });
    const record = await response.json();
    if (!response.ok) throw new Error(record.error || "无法归档当前案件");
    activeCaseId = record.id;
    activeRoundNumber = Number(record.activeRoundNumber || activeRoundNumber || 1);
    activeCaseApplicationNumber = record.applicationNumber || currentApplicationNumber || null;
    activeCaseNotebookId = latestNotebookJob?.notebookId || activeCaseNotebookId;
    window.history.replaceState({}, "", `./?case=${encodeURIComponent(activeCaseId)}&round=${encodeURIComponent(activeRoundNumber)}`);
    renderRoundIdentity();
    showToast("当前案件已归档", "已保存在本机“案件”页；当前页面保持打开，可继续编辑、补充材料或新增后续 OA。" );
  } catch (error) {
    showToast("无法归档当前案件", error.message || "请确认本机桥接器正在运行后重试。");
  } finally {
    button.disabled = false;
  }
}

async function openCases() {
  const dialog = $("#casesDialog");
  const list = $("#caseList");
  list.innerHTML = '<p class="case-empty">正在读取本机案件归档…</p>';
  dialog.showModal();
  try {
    const response = await fetch("/api/cases");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "无法读取案件归档");
    const cases = payload.cases || [];
    list.innerHTML = cases.length ? cases.map((entry) => {
      const application = entry.applicationNumber || "申请号尚未识别";
      const recordCount = (entry.issueAnalysisIds || []).length;
      const archivedAt = String(entry.archivedAt || "").replace("T", " ").replace(/\+00:00$/, " UTC");
      return `<article class="case-card"><b>${escapeHtml(application)}</b><small>已归档：${escapeHtml(archivedAt || "时间未记录")}</small><p>${entry.ocrJobId ? "材料与 OCR 留档已保留" : "尚无材料留档"} · ${entry.notebookId ? "Notebook 已保留" : "尚未创建 Notebook"} · ${recordCount} 份答复记录</p></article>`;
    }).join("") : '<p class="case-empty">尚无已归档案件。完成当前案件后，点击“新建 OA 案件”即可在这里看到它。</p>';
  } catch (error) {
    list.innerHTML = `<p class="case-empty">无法读取案件归档：${escapeHtml(error.message || "请检查本机桥接器")}</p>`;
  }
}

function setIssuesPanelCollapsed(collapsed) {
  issuesPanelCollapsed = collapsed;
  $("#workspace").classList.toggle("issues-collapsed", collapsed);
  $("#issuesPanel").classList.toggle("collapsed", collapsed);
  const button = $("#toggleIssuesPanel");
  button.textContent = collapsed ? "展开" : "收起";
  button.setAttribute("aria-expanded", String(!collapsed));
}

function fileMeta(file) {
  const extension = file.name.includes(".") ? file.name.split(".").pop().toUpperCase() : "文件";
  return { extension, type: `${extension} · 待解析`, raw: file, qualityState: "pending", progress: 0 };
}

function qualityPresentation(file) {
  if (file.qualityState === "checking") return { label: `检查中 ${file.progress}%`, state: "checking" };
  if (file.qualityState === "ocr-checking") return { label: `OCR 中 ${file.progress}%`, state: "checking" };
  if (file.qualityState === "converting") return { label: "正在转换 DOC", state: "checking" };
  if (file.qualityState === "ocr-complete") return { label: "OCR 已完成", state: "good" };
  if (file.qualityState === "complete") return { label: "文件可读取", state: "good" };
  if (file.qualityState === "review") return { label: "需要 OCR 复核", state: "review" };
  if (file.qualityState === "error") return { label: "OCR 处理失败（可重试）", state: "error" };
  return { label: "待文本质量检查", state: "review" };
}

function renderMaterials() {
  materialGroups.innerHTML = materialGroupsForActiveRound().map((group) => {
    const groupFiles = materials[group.id];
    const rows = groupFiles.length
      ? groupFiles.map((file) => {
        const quality = qualityPresentation(file);
        const progress = ["checking", "ocr-checking"].includes(file.qualityState) ? `<div class="quality-progress" aria-label="检查进度 ${file.progress}%"><i style="width:${file.progress}%"></i></div>` : "";
        const error = file.error ? `<small class="file-error" title="${file.error}">${file.error}</small>` : "";
        return `<div class="file-row"><span class="file-symbol">${file.extension}</span><div><b>${file.name}</b><small>${file.type}</small>${progress}${error}</div><span class="quality ${quality.state}">${quality.label}</span><button class="delete-file" data-file-id="${file.id}" aria-label="删除 ${file.name}" title="删除文件">×</button></div>`;
      }).join("")
      : `<p class="group-empty">尚未上传</p>`;
    const required = materialIsRequired(group.id);
    const responseMode = group.id === "response"
      ? `<label class="response-purpose"><span>本文件用途</span><select id="responsePurpose"><option value="optimize" ${responsePurpose === "optimize" ? "selected" : ""}>优化本轮已有意见陈述书</option><option value="history" ${responsePurpose === "history" ? "selected" : ""}>作为此前答复背景材料</option></select><small>选择“优化”后，NotebookLM 会按段落提出补充、修改和替换建议，而不是从零起草。</small></label>`
      : "";
    return `<section class="material-group" data-material-type="${group.id}"><div class="group-heading"><h3>${group.title}${required ? '<span class="required">必填</span>' : '<span class="optional">可选</span>'}</h3><p>${group.hint}</p></div>${responseMode}<div class="file-list">${rows}</div><label class="drop-zone material-drop" data-drop-type="${group.id}"><input class="material-input" data-input-type="${group.id}" type="file" multiple accept=".pdf,.doc,.docx,.txt" /><b>拖入文件或点击选择</b><span>PDF、DOC、DOCX 或文本</span></label></section>`;
  }).join("");

  materialGroups.querySelectorAll(".material-input").forEach((input) => {
    input.addEventListener("change", (event) => {
      addMaterialFiles(input.dataset.inputType, event.target.files);
      event.target.value = "";
    });
  });
  materialGroups.querySelectorAll(".material-drop").forEach((zone) => {
    zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("dragging"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      zone.classList.remove("dragging");
      addMaterialFiles(zone.dataset.dropType, event.dataTransfer.files);
    });
  });
  materialGroups.querySelectorAll(".delete-file").forEach((button) => {
    button.addEventListener("click", () => removeMaterial(button.dataset.fileId));
  });
  $("#responsePurpose")?.addEventListener("change", (event) => {
    responsePurpose = event.target.value === "history" ? "history" : "optimize";
    materials.response.forEach((file) => { file.responsePurpose = responsePurpose; });
    renderMaterials();
  });
  updateMaterialCount();
  updateQualityControl();
  renderWorkflowNext();
}

function addMaterialFiles(kind, fileList) {
  const incoming = [...fileList];
  if (!incoming.length) return;
  const existingNames = new Set(materials[kind].map((file) => file.name));
  const newFiles = incoming.filter((file) => !existingNames.has(file.name)).map((file) => ({
    id: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${file.name}`,
    name: file.name,
    kind,
    ...(kind === "response" ? { responsePurpose } : {}),
    ...fileMeta(file),
  }));
  materials[kind].push(...newFiles);
  renderMaterials();
}

function removeMaterial(id) {
  const group = Object.values(materials).find((files) => files.some((file) => file.id === id));
  if (!group) return;
  const index = group.findIndex((file) => file.id === id);
  const [removed] = group.splice(index, 1);
  renderMaterials();
  showToast("已移除材料", `“${removed.name}”已从本轮材料清单删除，未发送到 NotebookLM。`);
}

function updateMaterialCount() {
  const count = Object.values(materials).reduce((total, files) => total + files.length, 0);
  $("#materialCount").textContent = `${count} 份文件`;
  renderTimeline();
}

function setProgressStep(id, state, copy) {
  const step = $(id);
  if (!step) return;
  step.className = `step ${state}`.trim();
  const small = step.querySelector("small");
  if (small) small.textContent = copy;
}

function renderTimeline() {
  const materialsReady = requiredMaterialsReady();
  const hasIssueList = issues.length > 0;
  const hasIssueAnalysis = Boolean(latestIssueAnalysis?.analysis);
  setProgressStep("#progressMaterial", materialsReady ? "done" : "current", materialsReady ? "材料已解析" : `${Object.values(materials).flat().length} 份文件`);
  const issueListGenerating = notebookSubmissionRunning || ["creating_notebook", "uploading_sources", "configuring_analysis", "generating_issue_list"].includes(latestNotebookJob?.status);
  setProgressStep("#progressIssues", hasIssueList ? "done" : materialsReady ? "current" : "", hasIssueList ? "问题清单已生成" : issueListGenerating ? "NotebookLM 生成中" : materialsReady ? "待生成问题清单" : "等待材料");
  setProgressStep("#progressEvidence", hasIssueAnalysis ? "done" : hasIssueList ? "current" : "", hasIssueAnalysis ? "争点已确认" : hasIssueList ? (selected.size ? `已选择 ${selected.size} 项` : "请选择争点") : "等待清单");
  setProgressStep("#progressStrategy", issueAnalysisRunning ? "current" : hasIssueAnalysis ? "done" : "", issueAnalysisRunning ? "NotebookLM 分析中" : hasIssueAnalysis ? "答复要点已生成" : "尚未开始");
}

function updateQualityControl() {
  const allFiles = Object.values(materials).flat();
  const pending = allFiles.filter((file) => file.qualityState === "pending").length;
  const checking = allFiles.filter((file) => ["checking", "ocr-checking"].includes(file.qualityState)).length;
  const completed = allFiles.filter((file) => ["complete", "ocr-complete", "review"].includes(file.qualityState)).length;
  const ocrCompleted = allFiles.filter((file) => file.qualityState === "ocr-complete").length;
  const needsOcr = allFiles.filter((file) => ["review", "error"].includes(file.qualityState)).length;
  $("#startQualityCheck").disabled = qualityCheckRunning || !allFiles.some((file) => file.raw);
  $("#startQualityCheck").textContent = qualityCheckRunning ? "正在检查…" : completed === allFiles.length && allFiles.length ? "重新检查材料" : "启动文本质量检查";
  $("#startOcrReview").disabled = qualityCheckRunning || ocrReviewRunning || needsOcr === 0;
  $("#startOcrReview").textContent = ocrReviewRunning ? "正在 OCR…" : needsOcr ? `启动 OCR 复核（${needsOcr}）` : "启动 OCR 复核";
  $("#qualitySummary").textContent = checking ? `正在 OCR / 检查 ${checking} 份` : pending ? `${pending} 份待检查` : allFiles.some((file) => file.qualityState === "error") ? `${needsOcr} 份可重试` : needsOcr ? `${needsOcr} 份需要 OCR` : ocrCompleted ? `OCR 已完成 ${ocrCompleted} 份` : completed ? `${completed} 份已完成初检` : "尚未检查";
}

function materialIsRequired(kind) {
  return kind === "officeAction" || (kind === "application" && !(activeCaseId && activeRoundNumber > 1));
}

function requiredMaterialsReady() {
  return materialGroupsForActiveRound().filter((group) => materialIsRequired(group.id)).every((group) => materials[group.id].length > 0)
    && Object.values(materials).flat().every((file) => ["complete", "ocr-complete"].includes(file.qualityState));
}

function applicationNumberFromMaterials() {
  return currentApplicationNumber;
}

async function loadPriorNotebookHistory() {
  const applicationNumber = applicationNumberFromMaterials();
  if (!applicationNumber) return;
  try {
    const response = await fetch(`/api/notebook/history?applicationNumber=${encodeURIComponent(applicationNumber)}`);
    priorNotebookHistory = response.ok ? await response.json() : null;
    renderWorkflowNext();
  } catch {
    priorNotebookHistory = null;
  }
}

async function showPriorNotebookHistory() {
  const history = priorNotebookHistory;
  if (!history?.job?.id) return;
  try {
    const response = await fetch(`/api/notebook/jobs/${encodeURIComponent(history.job.id)}`);
    const job = await response.json();
    if (!response.ok || !job.initialAnalysis) throw new Error(job.error || "此前问题清单不可用");
    renderNotebookResult(job);
    if (history.latestIssueAnalysisId) {
      const analysisResponse = await fetch(`/api/notebook/issue-jobs/${encodeURIComponent(history.latestIssueAnalysisId)}`);
      const analysis = await analysisResponse.json();
      if (analysisResponse.ok && analysis.analysis) renderIssueAnalysis(analysis);
    }
    showToast("已加载此前审通答复", `已打开 ${job.title} 的本机记录；提交当前材料不会覆盖该记录。`);
  } catch (error) {
    showToast("无法加载此前答复", error.message || "请稍后重试。");
  }
}

function renderWorkflowNext() {
  const panel = $("#workflowNext");
  const allFiles = Object.values(materials).flat();
  if (!allFiles.length) { panel.className = "workflow-next"; panel.innerHTML = ""; return; }
  if (materialSubmissionOpening) {
    panel.className = "workflow-next pending";
    panel.innerHTML = '<b>正在检查 NotebookLM 登录状态</b><p class="progress-copy">请稍候；确认窗口会自动打开。重复点击不会创建重复提交。</p>';
    return;
  }
  if (notebookSubmissionRunning) {
    panel.className = "workflow-next pending";
    panel.innerHTML = '<b>正在提交到 NotebookLM</b><p class="progress-copy" id="notebookProgress">正在准备本机任务…</p>';
    return;
  }
  if (completedNotebookOcrJobId === lastOcrJobId) {
    panel.className = "workflow-next ready";
    panel.innerHTML = '<div><b>NotebookLM 审查问题清单已生成</b><p>本轮材料已按审查理由归类；请在下方选择一个问题继续逐项分析。</p></div><button class="secondary" id="regenerateIssueGroups">按审查类别重新生成</button>';
    $("#regenerateIssueGroups").addEventListener("click", regenerateIssueGroups);
    return;
  }
  if (!requiredMaterialsReady()) {
    panel.className = "workflow-next pending";
    const laterRound = activeCaseId && activeRoundNumber > 1;
    panel.innerHTML = laterRound
      ? '<b>下一步：完成本轮材料解析</b><p>本轮审查意见通知书为必填；首轮申请文件已保留在本案件 Notebook 中。建议同时上传此前意见陈述书和本轮引用的对比文件。</p>'
      : '<b>下一步：完成材料解析</b><p>原申请文件和审查意见通知书为必填；所有材料完成文本检查和 OCR 后，才可提交到 NotebookLM。</p>';
    return;
  }
  const applicationNumber = applicationNumberFromMaterials();
  const history = priorNotebookHistory?.job;
  const title = applicationNumber ? `${applicationNumber}-OA策略分析` : "OA策略分析";
  panel.className = "workflow-next ready";
  panel.innerHTML = `<div><b>材料解析已完成</b><p>提交后将创建 Notebook「${title}」，并先生成 OA 审查问题清单。${history ? ` 已找到此前的「${history.title}」本机记录。` : ""}</p></div>${history ? '<button class="secondary" id="loadPriorNotebook">加载此前答复</button>' : ""}<button class="primary" id="submitMaterials">提交到 NotebookLM</button>`;
  if (history) $("#loadPriorNotebook").addEventListener("click", showPriorNotebookHistory);
  $("#submitMaterials").addEventListener("click", openMaterialSubmission);
}

function pause(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function inspectMaterial(file) {
  file.qualityState = "checking";
  file.progress = 15;
  renderMaterials();
  await pause(120);
  try {
    file.progress = 45;
    renderMaterials();
    const bytes = await cacheMaterialFile(file);
    file.progress = 80;
    renderMaterials();
    await pause(120);
    if (bytes.length === 0) {
      file.qualityState = "error";
    } else if (file.extension === "PDF") {
      const header = new TextDecoder().decode(bytes.slice(0, 8));
      file.qualityState = header.startsWith("%PDF-") ? "review" : "error";
    } else if (["TXT", "MD"].includes(file.extension)) {
      file.qualityState = new TextDecoder().decode(bytes).trim().length ? "complete" : "error";
    } else {
      file.qualityState = "review";
    }
  } catch {
    file.qualityState = "error";
    file.error = "无法读取本机源文件。请删除此文件后重新添加，再启动 OCR。";
  }
  file.progress = 100;
  renderMaterials();
}

async function startQualityCheck() {
  if (qualityCheckRunning) return;
  const queued = Object.values(materials).flat().filter((file) => file.raw && file.qualityState !== "checking");
  if (!queued.length) return;
  qualityCheckRunning = true;
  updateQualityControl();
  for (const file of queued) await inspectMaterial(file);
  qualityCheckRunning = false;
  renderMaterials();
  const needsOcr = Object.values(materials).flat().filter((file) => file.qualityState === "review").length;
  if (needsOcr) showToast("文本质量初检已完成", `其中 ${needsOcr} 份需要 OCR 复核；请点击材料区的“启动 OCR 复核”。`);
  else showToast("文本质量初检已完成", "所有材料均可直接读取。可以继续进入争点分析。");
}

async function loadOcrStatus() {
  const response = await fetch("/api/ocr/status");
  if (!response.ok) throw new Error("OCR status unavailable");
  ocrStatus = await response.json();
  updateOcrSetupButton(ocrStatus);
  return ocrStatus;
}

function updateOcrSetupButton(status) {
  const button = $("#prepareOcr");
  if (!button) return;
  const setupState = status?.setup?.state;
  button.disabled = ["queued", "running"].includes(setupState);
  if (["queued", "running"].includes(setupState)) button.textContent = "正在安装 / 初始化 OCR…";
  else if (setupState === "ready") button.textContent = "OCR 模型已就绪";
  else if (status?.state === "available") button.textContent = "初始化 OCR 模型";
  else button.textContent = "安装 / 初始化 OCR";
}

function stopOcrSetupPolling() {
  ocrSetupPollGeneration += 1;
  if (ocrSetupPollingTimer) clearTimeout(ocrSetupPollingTimer);
  ocrSetupPollingTimer = null;
}

async function pollOcrSetup(generation) {
  if (generation !== ocrSetupPollGeneration) return;
  try {
    const status = await loadOcrStatus();
    if (generation !== ocrSetupPollGeneration) return;
    const setup = status.setup || {};
    if (["queued", "running"].includes(setup.state)) {
      $("#ocrStatusBox").className = "ocr-status-box";
      const progress = Number.isFinite(Number(setup.progress)) ? Number(setup.progress) : 0;
      $("#ocrStatusBox").innerHTML = `<b>OCR 安装进度：${progress}%</b>${setup.message || "正在准备本机 OCR 环境…"}`;
      $("#ocrDialogCopy").textContent = "安装任务由 OA阅答 在本机执行；可以关闭本窗口，稍后再次打开查看进度。";
      ocrSetupPollingTimer = setTimeout(() => pollOcrSetup(generation), 1200);
      return;
    }
    ocrSetupPollingTimer = null;
    if (setup.state === "ready") {
      $("#ocrStatusBox").className = "ocr-status-box";
      $("#ocrStatusBox").innerHTML = "<b>OCR 引擎和模型已就绪</b>后续点击“启动 OCR 复核”会直接开始处理材料，不再重复安装。";
      $("#ocrDialogCopy").textContent = "模型仅保存在本机；材料仍不会发送到外部服务。";
      return;
    }
    if (setup.state === "error") throw new Error(setup.error || "OCR 初始化失败");
  } catch (error) {
    $("#ocrStatusBox").className = "ocr-status-box error";
    $("#ocrStatusBox").textContent = `OCR 初始化失败：${error.message || "请重试。"}`;
  }
}

async function prepareOcr() {
  if (ocrSetupRequestRunning) {
    if (!$("#ocrDialog").open) $("#ocrDialog").showModal();
    return;
  }
  ocrSetupRequestRunning = true;
  stopOcrSetupPolling();
  const generation = ocrSetupPollGeneration;
  $("#ocrDialogTitle").textContent = "安装 / 初始化 OCR";
  $("#ocrDialogCopy").textContent = "此操作仅在本机准备 PaddleOCR 和中文模型；完成一次后，材料复核会自动加载。";
  $("#ocrStatusBox").className = "ocr-status-box";
  $("#ocrStatusBox").textContent = "正在检查本机 OCR 配置…";
  $("#ocrInstallHint").hidden = true;
  $("#installOcr").hidden = true;
  if (!$("#ocrDialog").open) $("#ocrDialog").showModal();
  try {
    const status = await loadOcrStatus();
    if (status.setup?.state === "ready") {
      $("#ocrStatusBox").innerHTML = "<b>OCR 引擎和模型已就绪</b>后续 OCR 复核会自动复用，不会重新安装。";
      return;
    }
    const response = await fetch("/api/ocr/setup", { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "无法开始 OCR 初始化");
    if (generation === ocrSetupPollGeneration && $("#ocrDialog").open) pollOcrSetup(generation);
  } catch (error) {
    $("#ocrStatusBox").className = "ocr-status-box error";
    $("#ocrStatusBox").textContent = `无法准备 OCR：${error.message || "请确认本地桥接器正在运行。"}`;
  } finally {
    ocrSetupRequestRunning = false;
  }
}

async function startOcrReview() {
  const needsOcr = Object.values(materials).flat().filter((file) => ["review", "error"].includes(file.qualityState));
  if (!needsOcr.length) return;
  $("#ocrDialogTitle").textContent = `OCR 复核（${needsOcr.length} 份材料）`;
  $("#ocrDialogCopy").textContent = "OCR 仅在本机运行。开始前会检查本机是否已配置可用引擎。";
  $("#ocrStatusBox").className = "ocr-status-box";
  $("#ocrStatusBox").textContent = "正在检查本机 OCR 引擎…";
  $("#ocrInstallHint").hidden = true;
  $("#installOcr").hidden = true;
  $("#ocrDialog").showModal();
  try {
    const status = await loadOcrStatus();
    const requiresPaddle = needsOcr.some((file) => file.extension === "PDF");
    if (!requiresPaddle) {
      $("#ocrStatusBox").className = "ocr-status-box";
      $("#ocrStatusBox").innerHTML = `<b>正在转换 ${needsOcr.length} 份 DOC 材料</b>将使用本机 LibreOffice 转为 DOCX；图表和表格会以 DOCX 形式保留，NotebookLM 不会收到原始 DOC。`;
      await submitOcrJob(needsOcr);
      return;
    }
    if (status.state === "available") {
      $("#ocrStatusBox").className = "ocr-status-box";
      $("#ocrStatusBox").innerHTML = `<b>已检测到 ${status.activeEngines.join("、")}</b>正在将 ${needsOcr.length} 份材料交给本机 OCR 队列；首次识别可能需要下载模型。`;
      $("#ocrDialogCopy").textContent = "正在本机进行逐页文本识别。窗口可保持打开以查看状态。";
      await submitOcrJob(needsOcr);
      return;
    }
    $("#ocrStatusBox").className = "ocr-status-box warn";
    $("#ocrStatusBox").innerHTML = `<b>尚未配置 OCR 引擎</b>${status.nativeTextExtractor ? "已检测到 PDF 原生文本提取工具，但它不能识别扫描页。" : "未检测到可用的 PDF 文本或 OCR 工具。"}`;
    $("#ocrDialogCopy").textContent = "点击下方按钮后，OA阅答 会把 PaddleOCR 安装到本机用户数据目录；无需打开终端。";
    $("#ocrInstallHint").hidden = true;
    $("#installOcr").textContent = "安装 / 初始化 OCR";
    $("#installOcr").hidden = false;
  } catch {
    $("#ocrStatusBox").className = "ocr-status-box error";
    $("#ocrStatusBox").innerHTML = "<b>无法检查 OCR 配置</b>请确认本地桥接器正在运行，然后重试。";
  }
}

function findMaterial(id) {
  return Object.values(materials).flat().find((file) => file.id === id);
}

function applyOcrJobState(job) {
  if (job.applicationNumber) currentApplicationNumber = job.applicationNumber;
  if (job.modelState === "loading") {
    $("#ocrStatusBox").className = "ocr-status-box";
    $("#ocrStatusBox").innerHTML = "<b>正在加载本机中文 OCR 模型</b>首次运行会下载并初始化模型；后续同一电脑上的识别会更快。";
  } else {
    const activeIndex = (job.files || []).findIndex((item) => item.state === "running");
    const active = activeIndex >= 0 ? job.files[activeIndex] : null;
    if (active) {
      const page = active.pageTotal ? `第 ${active.pageCurrent || 0}/${active.pageTotal} 页` : "正在准备文件";
      $("#ocrStatusBox").className = "ocr-status-box";
      $("#ocrStatusBox").textContent = `正在处理第 ${activeIndex + 1}/${job.files.length} 份材料：${active.name}（${page}）`;
    }
  }
  for (const result of job.files || []) {
    const file = findMaterial(result.id);
    if (!file) continue;
    if (result.state === "queued") { file.qualityState = "ocr-checking"; file.progress = 4; }
    if (result.state === "running") {
      file.qualityState = "ocr-checking";
      const current = Number(result.pageCurrent || 0);
      const total = Number(result.pageTotal || 0);
      file.progress = total ? Math.max(8, Math.min(95, Math.round((current / total) * 90) + 5)) : 8;
      file.type = total ? `${file.extension} · OCR 第 ${current || 1}/${total} 页` : `${file.extension} · 正在准备 OCR`;
    }
    if (result.state === "converting") {
      file.qualityState = "converting";
      file.progress = 35;
      file.type = `${file.extension} · 正在本机转换为 DOCX`;
    }
    if (result.state === "complete") {
      file.qualityState = "ocr-complete";
      file.progress = 100;
      file.type = result.method === "native_pdf_text"
        ? `${file.extension} · 已读取原生文本`
        : result.method === "document_text"
          ? `${file.extension} · 已读取文档文本`
          : result.method === "legacy_doc_to_docx"
            ? "DOC · 已转换为 DOCX（图表与表格保留）"
          : `${file.extension} · OCR ${result.pages || 1} 页${result.confidence ? ` · 置信度 ${Math.round(result.confidence * 100)}%` : ""}`;
    }
    if (result.state === "review") { file.qualityState = "review"; file.progress = 100; }
    if (result.state === "error") { file.qualityState = "error"; file.progress = 100; file.error = result.error || "OCR 运行失败，请重试。"; }
  }
  renderMaterials();
}

async function pollOcrJob(jobId) {
  try {
    const response = await fetch(`/api/ocr/jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "OCR job unavailable");
    lastOcrJobId = job.id;
    applyOcrJobState(job);
    if (["queued", "running"].includes(job.state)) {
      setTimeout(() => pollOcrJob(jobId), 1500);
      return;
    }
    ocrReviewRunning = false;
    updateQualityControl();
    if (job.state === "complete") {
      if (newRoundAwaitingMaterials) {
        newRoundAwaitingMaterials = false;
        sessionStorage.removeItem("oaRoundAwaitingMaterials");
      }
      $("#ocrStatusBox").className = "ocr-status-box";
      $("#ocrStatusBox").innerHTML = "<b>OCR 复核已完成</b>各材料的页数与置信度已显示在材料清单中；低质量或无文本材料会保留为需人工复核。";
      if ($("#ocrDialog").open) $("#ocrDialog").close();
      loadPriorNotebookHistory();
    } else throw new Error(job.error || "OCR task failed");
  } catch (error) {
    ocrReviewRunning = false;
    updateQualityControl();
    $("#ocrStatusBox").className = "ocr-status-box error";
    $("#ocrStatusBox").innerHTML = `<b>OCR 复核失败</b>${error.message || "请查看本机桥接器状态后重试。"}`;
  }
}

async function submitOcrJob(files) {
  ocrReviewRunning = true;
  if (!activeCaseId) currentApplicationNumber = null;
  priorNotebookHistory = null;
  files.forEach((file) => { file.qualityState = "ocr-checking"; file.progress = 5; file.error = null; });
  renderMaterials();
  try {
    const preparedFiles = [];
    for (const file of files) {
      try {
        preparedFiles.push({
          id: file.id,
          name: file.name,
          kind: file.kind,
          responsePurpose: file.responsePurpose,
          contentBase64: await fileAsBase64(file),
        });
      } catch (error) {
        file.qualityState = "error";
        file.progress = 100;
        file.error = error.message || "无法读取本机源文件。请删除后重新添加。";
        throw error;
      }
    }
    const payload = { files: preparedFiles };
    const response = await fetch("/api/ocr/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "无法创建 OCR 作业");
    applyOcrJobState(job);
    lastOcrJobId = job.id;
    pollOcrJob(job.id);
  } catch (error) {
    ocrReviewRunning = false;
    files.forEach((file) => {
      if (file.qualityState !== "error") file.qualityState = "review";
      file.progress = 100;
    });
    renderMaterials();
    $("#ocrStatusBox").className = "ocr-status-box error";
    $("#ocrStatusBox").innerHTML = `<b>无法启动 OCR 复核</b>${error.message || "请检查材料大小和本机桥接器。"}`;
  }
}

function issueCategory(issue) {
  const text = `${issue.title || ""} ${issue.ground || ""} ${issue.legalBasis || ""}`;
  if (/新颖性|创造性|22条第[二三]款|35\s*U\.S\.C\.?\s*(102|103)/i.test(text)) return { id: "substantive", label: "实质性问题（新颖性／创造性）" };
  if (/清楚性|支持|26条第[三四]款|35\s*U\.S\.C\.?\s*112/i.test(text)) return { id: "clarity", label: "清楚性／支持问题" };
  if (/附图|37\s*CFR\s*1\.(81|83)/i.test(text)) return { id: "drawings", label: "附图问题" };
  if (/单一性|第31条第1款|35\s*U\.S\.C\.?\s*(111|121)/i.test(text)) return { id: "unity", label: "单一性问题" };
  if (/专利客体|第2条第2款|第25条|第5条第1款|35\s*U\.S\.C\.?\s*101/i.test(text) && !/重复授权|Double\s+Patenting/i.test(text)) return { id: "subject-matter", label: "专利客体问题" };
  if (/重复授权|Double\s+Patenting|第9条/i.test(text)) return { id: "double-patenting", label: "防止重复授权问题" };
  if (/修改超范围|第33条|New\s+Matter|35\s*U\.S\.C\.?\s*132/i.test(text)) return { id: "new-matter", label: "修改超范围问题" };
  return { id: "other", label: "其他审查问题" };
}

function selectedIssueCategory() {
  const first = issues.find((issue) => selected.has(issue.id));
  return first ? issueCategory(first) : null;
}

function renderIssues() {
  if (!issues.length) {
    issueList.innerHTML = '<div class="issue-empty"><b>等待材料解析</b><p>上传原申请文件与审查意见通知书后，系统会生成可勾选的审查争点。</p></div>';
    updateSelection();
    return;
  }
  const activeCategory = selectedIssueCategory();
  issueList.innerHTML = issues.map((issue) => {
    const category = issueCategory(issue);
    const unavailable = activeCategory && activeCategory.id !== category.id;
    const compactTags = [issue.claims, ...(issue.priorArt || [])].filter(Boolean).slice(0, 4);
    const extraTags = Math.max(0, [issue.claims, ...(issue.priorArt || [])].filter(Boolean).length - compactTags.length);
    return `<label class="issue-card ${focusedIssue === issue.id ? "focused" : ""} ${unavailable ? "unavailable" : ""}" data-issue="${escapeHtml(issue.id)}"><input type="checkbox" ${selected.has(issue.id) ? "checked" : ""} ${unavailable ? "disabled" : ""}/><span><b>${escapeHtml(issue.title)}</b><small class="issue-category">${escapeHtml(category.label)}</small><p>${escapeHtml(issue.summary || issue.detail || "")}</p><div class="issue-tags">${compactTags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}${extraTags ? `<span class="tag">+${extraTags}</span>` : ""}</div></span></label>`;
  }).join("");
  issueList.querySelectorAll(".issue-card").forEach((card) => {
    card.addEventListener("click", (event) => {
      if (event.target.tagName === "INPUT" || card.classList.contains("unavailable")) return;
      event.preventDefault();
      const id = card.dataset.issue;
      focusedIssue = id;
      selected.has(id) ? selected.delete(id) : selected.add(id);
      renderIssues();
      renderEvidence();
    });
    card.querySelector("input").addEventListener("change", (event) => {
      const id = card.dataset.issue;
      focusedIssue = id;
      event.target.checked ? selected.add(id) : selected.delete(id);
      renderIssues();
      renderEvidence();
    });
  });
  updateSelection();
}

function renderEvidence() {
  const issue = issues.find((entry) => entry.id === focusedIssue);
  if (!issue || (selected.size === 0 && issueAnalysisHistory.length)) {
    renderIssueAnalysisHistory();
    return;
  }
  $("#evidenceTitle").textContent = issue.title;
  $("#evidenceBody").className = "evidence-list";
  const overview = [
    issue.summary ? `<section class="evidence-section"><h3>审查理由摘要</h3><div class="evidence-quote">${escapeHtml(issue.summary)}</div></section>` : "",
    issue.analysisFocus ? `<section class="evidence-section"><h3>答复核对重点</h3><div class="evidence-quote">${escapeHtml(issue.analysisFocus)}</div></section>` : "",
  ].join("");
  $("#evidenceBody").innerHTML = `${overview}${issue.evidence.map(([source, text]) => `<section class="evidence-section"><h3>${escapeHtml(source)}</h3><div class="evidence-quote">${escapeHtml(text)}</div></section>`).join("")}${issueAnalysisHistoryMarkup()}`;
  bindIssueAnalysisHistoryLinks();
}

function issueAnalysisHistoryMarkup() {
  if (!issueAnalysisHistory.length) return "";
  return `<section class="answer-history"><h3>历史回答</h3><p>历史答复会保留；调整左侧勾选不会删除或覆盖它们。</p><div class="answer-history-list">${issueAnalysisHistory.map((entry) => `<button class="history-answer" data-history-analysis-id="${escapeHtml(entry.id)}" type="button"><span>${escapeHtml(entry.title)}</span><small>${escapeHtml(entry.completedAt || "本机记录")}${entry.favorite ? " · 已收藏" : ""}</small></button>`).join("")}</div></section>`;
}

function renderIssueAnalysisHistory() {
  if (!issueAnalysisHistory.length) return;
  $("#evidenceTitle").textContent = "历史回答";
  $("#evidenceBody").className = "evidence-list";
  $("#evidenceBody").innerHTML = issueAnalysisHistoryMarkup();
  bindIssueAnalysisHistoryLinks();
}

function bindIssueAnalysisHistoryLinks() {
  document.querySelectorAll(".history-answer").forEach((button) => {
    button.addEventListener("click", () => openIssueAnalysisHistory(button.dataset.historyAnalysisId));
  });
}

function upsertIssueAnalysisHistory(job) {
  if (!job?.id || !job.analysis) return;
  const entry = {
    id: job.id,
    title: `${job.issue?.title || "审查问题"}：答复要点`,
    completedAt: job.completedAt || job.createdAt,
    favorite: Boolean(job.favorite),
  };
  issueAnalysisHistory = [entry, ...issueAnalysisHistory.filter((item) => item.id !== entry.id)];
}

async function loadIssueAnalysisHistory(notebookId) {
  if (!notebookId) return;
  try {
    const response = await fetch(`/api/notebook/issue-jobs?notebookId=${encodeURIComponent(notebookId)}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "无法读取历史回答");
    issueAnalysisHistory = Array.isArray(payload.jobs) ? payload.jobs : [];
    if (selected.size === 0) renderIssueAnalysisHistory();
  } catch (error) {
    console.warn("无法读取本机历史回答", error);
  }
}

async function openIssueAnalysisHistory(jobId) {
  if (!jobId) return;
  try {
    const response = await fetch(`/api/notebook/issue-jobs/${encodeURIComponent(jobId)}`);
    const job = await response.json();
    if (!response.ok || !job.analysis) throw new Error(job.error || "该历史回答不可用");
    renderIssueAnalysis(job, { announce: false });
  } catch (error) {
    showToast("无法打开历史回答", error.message || "请稍后重试。");
  }
}

function updateSelection() {
  const category = selectedIssueCategory();
  $("#selectedCount").textContent = category ? `已选择 ${selected.size} 项 · ${category.label}` : "0 已选择";
  $("#analyseSelected").disabled = selected.size === 0 || issueAnalysisRunning;
  $("#analyseSelected").textContent = issueAnalysisRunning ? "正在分析审查问题…" : "分析已选争点";
  renderTimeline();
}

function openConfirmation() {
  const selectedIssues = issues.filter((issue) => selected.has(issue.id));
  const category = selectedIssueCategory();
  if (!selectedIssues.length || !category || selectedIssues.some((issue) => issueCategory(issue).id !== category.id)) {
    showToast("请选择同一答复类别", "新颖性和创造性可合并分析；清楚性／支持及附图问题须分别分析。");
    return;
  }
  const requiredFiles = [...new Set(selectedIssues.flatMap((issue) => (issue.sources || issue.evidence.map(([source]) => source)).filter(Boolean)))];
  $("#confirmForm h2").textContent = `确认分析：${category.label}`;
  $("#confirmCopy").textContent = "本操作只向已建立的本轮 NotebookLM 提出一个分析问题，不会上传材料或创建新的 Notebook。";
  $("#accountOption").textContent = lastNotebookStatus?.account || "已连接的本机 Google 账号";
  $("#confirmFiles").innerHTML = requiredFiles.map((name) => `<div class="confirm-file"><span>${escapeHtml(name)}</span><small>本轮 Notebook 内已有来源</small></div>`).join("");
  $("#queueJob").textContent = "确认并分析该审查问题";
  $("#consent").checked = false;
  $("#queueJob").dataset.action = "issue-analysis";
  $("#queueJob").dataset.issueId = category.id;
  $("#queueJob").disabled = true;
  $("#confirmDialog").showModal();
}

async function openMaterialSubmission() {
  if (!lastOcrJobId || !requiredMaterialsReady() || materialSubmissionOpening || notebookSubmissionRunning) return;
  materialSubmissionOpening = true;
  renderWorkflowNext();
  try {
    // The bridge already refreshed this state during page startup.  Reuse it here
    // so opening the confirmation dialog never waits on a slow CLI status check.
    const notebook = lastNotebookStatus || await refreshNotebookStatus();
    if (notebook.state !== "authenticated") {
      showToast("请先连接 NotebookLM", "提交材料前需要在本机桥接器中完成 Google 登录。");
      openLogin();
      return;
    }
    const files = Object.values(materials).flat();
    $("#confirmForm h2").textContent = "确认提交本轮材料到 NotebookLM";
    const optimizingCurrentResponse = files.some((file) => file.kind === "response" && file.responsePurpose === "optimize");
    const responseOptimizationNote = optimizingCurrentResponse
      ? " 已有意见陈述书会作为本轮优化基线；后续分析将定位可补充、替换或需核验的段落，而不会从零起草。"
      : "";
    $("#confirmCopy").textContent = (activeCaseNotebookId
      ? "确认后，本轮材料会追加到本案件既有的 NotebookLM；首轮材料和历史答复会继续保留。可直接读取文字的 PDF、DOCX 以原件提交；扫描件仅提交本机 OCR 生成的 Markdown。"
      : "确认后，本机桥接器会创建独立 Notebook。可直接读取文字的 PDF、DOCX 将以原件提交；扫描件仅提交本机 OCR 生成的 Markdown。每份材料仅发送一个版本，实际发送文件会在原件目录下的 notebook-upload 文件夹留档。"
    ) + responseOptimizationNote;
    $("#accountOption").textContent = notebook.account || `本机 Profile ${notebook.profile || "default"}`;
    $("#confirmFiles").innerHTML = files.map((file) => {
      const direct = file.type.includes("已读取原生文本") || file.type.includes("已读取文档文本");
      const role = isLaterOa()
        ? ({ officeAction: "本轮审查意见", priorArt: "补充对比文件", response: "历史意见陈述书" }[file.kind] || "补充材料")
        : materialKindTitle(file.kind);
      return direct
        ? `<div class="confirm-file"><span>[${role}｜原件] ${escapeHtml(file.name)}</span><small>可直接读取文字</small></div>`
        : `<div class="confirm-file"><span>[${role}｜材料文本] ${escapeHtml(file.name.replace(/\.[^.]+$/, ""))}.md</span><small>扫描件 OCR Markdown</small></div>`;
    }).join("");
    $("#queueJob").textContent = "确认并提交到 NotebookLM";
    $("#queueJob").dataset.action = "submit-materials";
    $("#consent").checked = false;
    $("#queueJob").disabled = true;
    $("#confirmDialog").showModal();
  } catch (error) {
    showToast("无法检查 NotebookLM", error.message || "请稍后重试。");
  } finally {
    materialSubmissionOpening = false;
    renderWorkflowNext();
  }
}

function notebookProgress(job) {
  const uploadDetail = job.uploadRetryMessage
    ? `${job.uploadRetryMessage}（${job.uploadedSources || 0}/${job.sourceCount || 0}）`
    : `正在上传材料（${job.uploadedSources || 0}/${job.sourceCount || 0}）…`;
  const states = {
    queued: "正在排队…",
    using_existing_notebook: "正在连接本案件既有 Notebook…",
    creating_notebook: "正在创建本轮独立 Notebook…",
    uploading_sources: uploadDetail,
    configuring_analysis: "正在配置专利 OA 分析提示词…",
    generating_issue_list: "正在生成 OA 审查问题清单…",
  };
  return states[job.status] || "正在处理…";
}

function renderNotebookResult(job) {
  latestNotebookJob = job;
  loadIssueAnalysisHistory(job.notebookId);
  completedNotebookOcrJobId = job.ocrJobId || completedNotebookOcrJobId;
  renderTimeline();
  // Restored jobs arrive after the material panel was first painted. Refresh the
  // panel now so its completed-state action is visible instead of leaving the
  // pre-restoration "materials parsed" message on screen.
  renderWorkflowNext();
  const result = job.initialAnalysis || "NotebookLM 已完成初步问题清单。";
  const parsed = parseNotebookIssues(result);
  if (parsed.length) {
    issues.splice(0, issues.length, ...parsed);
    selected.clear();
    focusedIssue = null;
    renderIssues();
    $("#evidenceTitle").textContent = "选择一个争点";
    $("#evidenceBody").className = "empty-state";
    $("#evidenceBody").textContent = "勾选左侧争点后，可查看该项的 OA 定位与答复核对重点。";
    return;
  }
  issueList.innerHTML = `<div class="issue-empty"><b>NotebookLM 返回了无法识别的结果格式</b><p class="notebook-result">${escapeHtml(result)}</p></div>`;
  $("#analyseSelected").disabled = true;
}

function issuePayload(selectedIssues) {
  const category = issueCategory(selectedIssues[0]);
  const unique = (values) => [...new Set(values.filter(Boolean))];
  return {
    id: category.id,
    title: category.label,
    ground: unique(selectedIssues.map((issue) => issue.ground)).join("；"),
    legalBasis: unique(selectedIssues.map((issue) => issue.legalBasis)).join("；"),
    claims: unique(selectedIssues.map((issue) => issue.claims)).join("；"),
    priorArt: unique(selectedIssues.flatMap((issue) => issue.priorArt || [])),
    summary: selectedIssues.map((issue) => `${issue.title}：${issue.summary || ""}`).join("\n"),
    analysisFocus: selectedIssues.map((issue) => `${issue.title}：${issue.analysisFocus || ""}`).join("\n"),
    includedIssues: selectedIssues.map((issue) => ({ id: issue.id, title: issue.title })),
  };
}

function formatNotebookAnalysis(value) {
  return unpackNotebookAnalysis(value).text;
}

function unpackNotebookAnalysis(value) {
  let result = value;
  let references = [];
  for (let depth = 0; depth < 3 && typeof result === "string"; depth += 1) {
    try { result = JSON.parse(result); } catch { break; }
    if (result && typeof result === "object") {
      if (Array.isArray(result.references)) references = result.references;
      if (typeof result.answer === "string") result = result.answer;
    }
  }
  return {
    text: typeof result === "string" ? result : JSON.stringify(result, null, 2),
    references: references.filter((entry) => entry && entry.citation_number != null),
  };
}

function citationNumbers(value) {
  return String(value).split(/[,，]/).flatMap((part) => {
    const range = part.trim().match(/^(\d+)\s*[-–]\s*(\d+)$/);
    if (range) {
      const first = Number(range[1]); const last = Number(range[2]);
      return last - first >= 0 && last - first <= 12 ? Array.from({ length: last - first + 1 }, (_, index) => first + index) : [];
    }
    return /^\d+$/.test(part.trim()) ? [Number(part.trim())] : [];
  });
}

function notebookTextWithCitations(text, references, citationScope = "analysis") {
  const available = new Set(references.map((entry) => Number(entry.citation_number)));
  const escaped = escapeHtml(text);
  return escaped.replace(/\[(\d+(?:\s*(?:,|，|-|–)\s*\d+)*)\]/g, (match, values) => {
    const numbers = citationNumbers(values).filter((number) => available.has(number));
    if (!numbers.length) return match;
    return `<button class="citation-mark" type="button" data-citation-numbers="${numbers.join(",")}" data-citation-scope="${citationScope}" title="查看引文核验">${match}</button>`;
  }).replace(/\n/g, "<br>");
}

function readableCitationText(value) {
  const text = String(value || "").trim();
  if (!/[\u00c0-\u00ff]/.test(text) || /[^\x00-\xff]/.test(text)) return text;
  try {
    const bytes = Uint8Array.from([...text].map((character) => character.charCodeAt(0)));
    const decoded = new TextDecoder("gb18030").decode(bytes);
    const cjkCount = (value) => (value.match(/[\u3400-\u9fff]/g) || []).length;
    return cjkCount(decoded) > cjkCount(text) ? decoded : text;
  } catch { return text; }
}

function citationFileName(source = {}) {
  const fromPath = String(source.path || "").split(/[\\/]/).pop();
  const title = String(source.title || fromPath || "").trim();
  return title.replace(/^\[(?:原件|材料文本)\]\s*/u, "") || "未映射到本机材料文件";
}

function citationPopup() {
  let popup = $("#citationPopup");
  if (popup) return popup;
  popup = document.createElement("aside");
  popup.id = "citationPopup";
  popup.className = "citation-popup";
  popup.setAttribute("role", "dialog");
  popup.setAttribute("aria-label", "引文核验");
  popup.addEventListener("pointerdown", (event) => {
    const handle = event.target instanceof Element ? event.target.closest(".citation-popup-titlebar") : null;
    if (!handle) return;
    const box = popup.getBoundingClientRect();
    citationPopupDrag = {
      pointerId: event.pointerId,
      offsetX: event.clientX - box.left,
      offsetY: event.clientY - box.top,
    };
    popup.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  });
  document.body.append(popup);
  return popup;
}

let citationPopupDrag = null;

function closeCitationPopup() {
  citationPopupDrag = null;
  $("#citationPopup")?.classList.remove("open");
}

function positionCitationPopup(popup, anchor) {
  const gap = 12;
  const margin = 18;
  const anchorBox = anchor.getBoundingClientRect();
  const popupBox = popup.getBoundingClientRect();
  const maxLeft = Math.max(margin, window.innerWidth - popupBox.width - margin);
  const left = Math.max(margin, Math.min(anchorBox.right + gap, maxLeft));
  const maxTop = Math.max(margin, window.innerHeight - popupBox.height - margin);
  const top = Math.max(margin, Math.min(anchorBox.top, maxTop));
  popup.style.left = `${left}px`;
  popup.style.top = `${top}px`;
}

function renderCitationInspector(numbers, references, sourceCatalog = {}, anchor) {
  const byNumber = new Map(references.map((entry) => [Number(entry.citation_number), entry]));
  const selectedReferences = numbers.map((number) => byNumber.get(number)).filter(Boolean);
  if (!selectedReferences.length || !anchor) return;
  const popup = citationPopup();
  popup.innerHTML = `<header class="citation-popup-titlebar" title="按住此处拖动窗口"><h3>引文核验</h3><span>拖动</span></header>${selectedReferences.map((entry) => {
    const source = sourceCatalog[entry.source_id] || {};
    const sourceName = citationFileName(source);
    const excerpt = readableCitationText(entry.cited_text) || "NotebookLM 未返回可显示的原文摘录。";
    const mappingHint = source.title || source.path
      ? `材料文件：${escapeHtml(sourceName)}`
      : "材料文件：尚未映射；下次生成分析时会自动补齐文件名。";
    return `<article class="citation-reference"><b>[${entry.citation_number}] ${escapeHtml(sourceName)}</b><small>${mappingHint}</small><p>${escapeHtml(excerpt)}</p></article>`;
  }).join("")}`;
  popup.classList.add("open");
  positionCitationPopup(popup, anchor);
}

function bindCitationMarks(referenceSets, sourceCatalog) {
  const body = $("#evidenceBody");
  body.onclick = (event) => {
    const button = event.target instanceof Element ? event.target.closest(".citation-mark") : null;
    if (!button) return;
    event.preventDefault();
    const scope = button.dataset.citationScope || "analysis";
    renderCitationInspector(citationNumbers(button.dataset.citationNumbers), referenceSets[scope] || [], sourceCatalog, button);
  };
}

document.addEventListener("pointerdown", (event) => {
  const popup = $("#citationPopup");
  if (!popup?.classList.contains("open")) return;
  const target = event.target;
  if (!(target instanceof Element)) return;
  if (!popup.contains(target) && !target.closest(".citation-mark")) closeCitationPopup();
});

document.addEventListener("pointermove", (event) => {
  if (!citationPopupDrag || event.pointerId !== citationPopupDrag.pointerId) return;
  const popup = $("#citationPopup");
  if (!popup) return;
  const margin = 12;
  const box = popup.getBoundingClientRect();
  const left = Math.max(margin, Math.min(event.clientX - citationPopupDrag.offsetX, window.innerWidth - box.width - margin));
  const top = Math.max(margin, Math.min(event.clientY - citationPopupDrag.offsetY, window.innerHeight - box.height - margin));
  popup.style.left = `${left}px`;
  popup.style.top = `${top}px`;
});

document.addEventListener("pointerup", () => { citationPopupDrag = null; });
document.addEventListener("pointercancel", () => { citationPopupDrag = null; });

function renderIssueAnalysis(job, { announce = true } = {}) {
  closeCitationPopup();
  latestIssueAnalysis = job;
  upsertIssueAnalysisHistory(job);
  issueAnalysisRunning = false;
  renderTimeline();
  const analysisPayload = unpackNotebookAnalysis(job.analysis || "NotebookLM 未返回可显示的分析文本。");
  const extractionPayload = job.examinerIssueExtraction
    ? unpackNotebookAnalysis(job.examinerIssueExtraction)
    : null;
  const analysis = analysisPayload.text;
  const responseOptimization = job.mode === "response_optimization";
  $("#evidenceTitle").textContent = `${job.issue?.title || "审查问题"}：答复要点`;
  if (responseOptimization) $("#evidenceTitle").textContent = `${job.issue?.title || "审查问题"}：意见陈述书优化建议`;
  $("#evidenceBody").className = "evidence-list";
  const followUps = (job.followUps || []).map((entry, index) => `<section class="evidence-section followup-answer"><h3>追问 ${index + 1}</h3><p><b>问题：</b>${escapeHtml(entry.question || "")}</p><div class="evidence-quote notebook-result">${escapeHtml(formatNotebookAnalysis(entry.answer || "")).replace(/\n/g, "<br>")}</div></section>`).join("");
  const extractionSection = extractionPayload ? `<section class="evidence-section examiner-extraction"><h3>第一次输出 · 审查意见问题逐项提取</h3><p class="analysis-section-note">仅列明审查意见、依据和待核验位置；后续策略分析不会重复这些内容。</p><div class="evidence-quote notebook-result">${notebookTextWithCitations(extractionPayload.text, extractionPayload.references, "extraction")}</div></section>` : "";
  const strategyHeading = responseOptimization ? "第二次输出 · 已有意见陈述书优化" : extractionPayload ? "第二次输出 · 答复策略分析" : "NotebookLM 分项分析";
  const strategyNote = responseOptimization ? "以当前意见陈述书为基线，给出权利要求、答辩要点及段落级替换或补充建议；每一项均应可定位并保留人工核验要求。" : "聚焦审查员立场、具体技术场景差异、答复与修改方向、人工核验位置及粗略通过可能性。";
  $("#evidenceBody").innerHTML = `<div class="analysis-actions"><button class="ghost" id="backToHistory" type="button">返回历史回答</button><button class="ghost" id="favoriteAnalysis">${job.favorite ? "已收藏" : "收藏回答"}</button><a class="ghost export-link" href="/api/notebook/issue-jobs/${encodeURIComponent(job.id)}/export.docx">保存为 DOCX</a></div>${extractionSection}<section class="evidence-section strategy-analysis"><h3>${strategyHeading}</h3><p class="analysis-section-note">${strategyNote}</p><div class="evidence-quote notebook-result">${notebookTextWithCitations(analysis, analysisPayload.references, "analysis")}</div></section>${followUps}<section class="evidence-section followup-form"><h3>继续追问</h3><textarea id="followUpQuestion" rows="4" placeholder="围绕本项答复策略追问，例如：D1 与本申请在该具体技术场景中的差异如何写得更有说服力？"></textarea><button class="primary" id="sendFollowUp">提交追问</button></section>`;
  $("#backToHistory").addEventListener("click", renderIssueAnalysisHistory);
  $("#favoriteAnalysis").addEventListener("click", () => favoriteIssueAnalysis(job.id));
  $("#sendFollowUp").addEventListener("click", () => submitIssueFollowup(job.id));
  bindCitationMarks({
    extraction: extractionPayload?.references || [],
    analysis: analysisPayload.references,
  }, job.sourceCatalog || {});
  if (announce) showToast("本项分析已完成", "已生成答复要点、证据核验重点和粗略通过可能性；请由专业人员复核后使用。");
}

async function favoriteIssueAnalysis(jobId) {
  try {
    const response = await fetch(`/api/notebook/issue-jobs/${encodeURIComponent(jobId)}/favorite`, { method: "POST" });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "无法收藏本项回答");
    renderIssueAnalysis(job);
    showToast("已收藏回答", "已保存在本机工作台的收藏记录中。");
  } catch (error) {
    showToast("收藏失败", error.message || "请稍后重试。");
  }
}

async function submitIssueFollowup(jobId) {
  const question = $("#followUpQuestion").value.trim();
  if (!question) {
    showToast("请输入追问", "请输入与当前审查类别相关的具体问题。");
    return;
  }
  issueAnalysisRunning = true;
  updateSelection();
  try {
    const response = await fetch(`/api/notebook/issue-jobs/${encodeURIComponent(jobId)}/followup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "无法开始追问");
    pollIssueAnalysis(job.id);
  } catch (error) {
    issueAnalysisRunning = false;
    updateSelection();
    showToast("追问未发送", error.message || "请检查本机 NotebookLM 登录状态后重试。");
  }
}

function renderIssueAnalysisProgress(job) {
  const recoveryMessage = job.recovery?.message || "";
  const keepsPreviousAnswer = latestIssueAnalysis?.id === job.id && Boolean(latestIssueAnalysis.analysis);
  if (keepsPreviousAnswer) {
    const body = $("#evidenceBody");
    const pending = $("#followupPending");
    if (!pending) {
      body.insertAdjacentHTML("beforeend", '<section class="evidence-section followup-pending" id="followupPending"><h3>正在生成追问回答</h3><p>NotebookLM 正在基于当前材料和上方答复继续分析；已有答复会保留显示。</p></section>');
    }
    const question = $("#followUpQuestion");
    const submit = $("#sendFollowUp");
    if (question) question.disabled = true;
    if (submit) submit.disabled = true;
    return;
  }
  if (job.status === "extracting_examiner_issues") {
    $("#evidenceTitle").textContent = "正在提取审查意见问题";
    $("#evidenceBody").className = "empty-state";
    $("#evidenceBody").textContent = recoveryMessage || "NotebookLM 正在生成第一份审查意见逐项提取；完成后会自动继续生成答复策略分析。";
    return;
  }
  $("#evidenceTitle").textContent = "正在生成分项答复要点";
  $("#evidenceBody").className = "empty-state";
  $("#evidenceBody").textContent = recoveryMessage || "NotebookLM 正在生成第二份答复策略分析；本次不会上传材料。";
}

async function pollIssueAnalysis(jobId) {
  try {
    const response = await fetch(`/api/notebook/issue-jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "Issue analysis job unavailable");
    if (!["complete", "error"].includes(job.status)) {
      renderIssueAnalysisProgress(job);
      setTimeout(() => pollIssueAnalysis(jobId), 1800);
      return;
    }
    if (job.status === "complete") {
      renderIssueAnalysis(job);
      return;
    }
    throw new Error(job.error || "Issue analysis failed");
  } catch (error) {
    issueAnalysisRunning = false;
    updateSelection();
    const message = error.message || "请检查本机 NotebookLM 登录状态后重试。";
    if (message.includes("登录已过期") || message.includes("重新连接 Google")) {
      await refreshNotebookStatus().catch(() => {});
      showToast("NotebookLM 登录已过期", "请点击顶部“连接 NotebookLM”，重新登录后再次分析；本地材料和历史回答不会丢失。");
      return;
    }
    showToast("分项分析失败", message);
  }
}

async function submitSelectedIssueAnalysis() {
  const selectedIssues = issues.filter((entry) => selected.has(entry.id));
  if (!selectedIssues.length || !latestNotebookJob?.notebookId) {
    showToast("无法开始分析", "请先完成本轮 NotebookLM 审查问题清单的生成。");
    return;
  }
  issueAnalysisRunning = true;
  updateSelection();
  try {
    const response = await fetch("/api/notebook/issue-analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebookId: latestNotebookJob.notebookId, ocrJobId: lastOcrJobId, issue: issuePayload(selectedIssues), consentAcknowledged: true }),
    });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "无法开始分项分析");
    pollIssueAnalysis(job.id);
  } catch (error) {
    issueAnalysisRunning = false;
    updateSelection();
    showToast("无法开始分析", error.message || "请稍后重试。");
  }
}

async function regenerateIssueGroups() {
  if (notebookSubmissionRunning || !latestNotebookJob?.notebookId || !lastOcrJobId) return;
  notebookSubmissionRunning = true;
  renderWorkflowNext();
  const progress = $("#notebookProgress");
  if (progress) progress.textContent = "正在让 NotebookLM 按审查类别重新生成问题清单…";
  try {
    const response = await fetch("/api/notebook/recover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebookId: latestNotebookJob.notebookId, ocrJobId: lastOcrJobId }),
    });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "无法重新生成问题清单");
    pollNotebookSubmission(job.id);
  } catch (error) {
    notebookSubmissionRunning = false;
    renderWorkflowNext();
    showToast("无法重新生成问题清单", error.message || "请稍后重试。");
  }
}

async function pollNotebookSubmission(jobId) {
  try {
    const response = await fetch(`/api/notebook/jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "NotebookLM job unavailable");
    const progress = $("#notebookProgress");
    if (progress) progress.textContent = notebookProgress(job);
    if (!["complete", "error"].includes(job.status)) {
      setTimeout(() => pollNotebookSubmission(jobId), 1800);
      return;
    }
    notebookSubmissionRunning = false;
    if (job.status === "complete") {
      renderWorkflowNext();
      renderNotebookResult(job);
      showToast("NotebookLM 分析已就绪", "已创建本轮独立 Notebook，并返回 OA 审查问题清单。请在下方核对后逐项分析。");
      return;
    }
    const checkpoint = Number(job.uploadedSources || 0) > 0
      ? `已保留 ${job.uploadedSources}/${job.sourceCount || job.uploadedSources} 份材料；恢复登录后再次提交，将从未完成材料继续。`
      : "";
    throw new Error([job.error || "NotebookLM submission failed", checkpoint].filter(Boolean).join("。"));
  } catch (error) {
    notebookSubmissionRunning = false;
    renderWorkflowNext();
    refreshNotebookStatus({ verify: true }).catch(() => {});
    showToast("NotebookLM 提交失败", error.message || "请检查本机登录状态后重试。");
  }
}

async function submitMaterialsToNotebook() {
  notebookSubmissionRunning = true;
  renderWorkflowNext();
  try {
    const response = await fetch("/api/notebook/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ocrJobId: lastOcrJobId,
        consentAcknowledged: true,
        caseId: activeCaseId,
        roundNumber: activeCaseId ? activeRoundNumber : null,
        existingNotebookId: activeCaseNotebookId,
      }),
    });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "无法提交材料");
    pollNotebookSubmission(job.id);
  } catch (error) {
    notebookSubmissionRunning = false;
    renderWorkflowNext();
    refreshNotebookStatus({ verify: true }).catch(() => {});
    showToast("无法提交到 NotebookLM", error.message || "请稍后重试。");
  }
}

function showToast(title, text) {
  if ($("#toastDialog").open) $("#toastDialog").close();
  $("#toastTitle").textContent = title;
  $("#toastText").textContent = text;
  $("#toastDialog").showModal();
}

async function openArchiveSettings() {
  const dialog = $("#archiveSettingsDialog");
  $("#archiveSettingsCurrent").textContent = "正在读取当前存档位置…";
  dialog.showModal();
  try {
    const response = await fetch("/api/settings");
    const settings = await response.json();
    if (!response.ok) throw new Error(settings.error || "无法读取本地设置");
    archiveSettings = settings;
    $("#archiveRootInput").value = settings.archiveRoot || "";
    $("#archiveSettingsCurrent").textContent = `当前后续案件存档位置：${settings.archiveRoot || "未设置"}`;
  } catch (error) {
    $("#archiveSettingsCurrent").textContent = error.message || "无法读取本地设置";
  }
}

async function saveArchiveSettings(archiveRoot) {
  const input = $("#archiveRootInput");
  const button = $("#saveArchiveSettings");
  const target = String(archiveRoot ?? input.value).trim();
  if (!target) {
    showToast("请输入存档位置", "请选择一个用于后续案件的本地文件夹。");
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch("/api/settings/archive-root", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archiveRoot: target }),
    });
    const settings = await response.json();
    if (!response.ok) throw new Error(settings.error || "无法保存本地存档位置");
    archiveSettings = settings;
    input.value = settings.archiveRoot || target;
    $("#archiveSettingsCurrent").textContent = `当前后续案件存档位置：${settings.archiveRoot}`;
    $("#archiveSettingsDialog").close();
    showToast("本地存档位置已更新", "后续案件会保存到新的文件夹；现有存档仍保留在原位置。");
  } catch (error) {
    showToast("无法保存存档位置", error.message || "请检查文件夹路径后重试。");
  } finally {
    button.disabled = false;
  }
}

function restoreKind(material, index) {
  if (material.kind && Object.hasOwn(materials, material.kind)) return material.kind;
  if (index === 0) return "application";
  if (index === 1) return "officeAction";
  return "priorArt";
}

function clearWorkspaceContent() {
  Object.values(materials).forEach((files) => files.splice(0, files.length));
  issues.splice(0, issues.length);
  selected = new Set();
  focusedIssue = null;
  lastOcrJobId = null;
  completedNotebookOcrJobId = null;
  latestNotebookJob = null;
  latestIssueAnalysis = null;
  issueAnalysisHistory = [];
  historyMaterialRounds = [];
}

async function restoreCaseRound() {
  if (!activeCaseId) return false;
  try {
    const response = await fetch(`/api/cases/${encodeURIComponent(activeCaseId)}/rounds/${encodeURIComponent(activeRoundNumber)}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "案件轮次不存在");
    clearWorkspaceContent();
    historyMaterialRounds = payload.historyMaterials || [];
    activeCaseApplicationNumber = payload.case?.applicationNumber || payload.ocr?.applicationNumber || null;
    currentApplicationNumber = activeCaseApplicationNumber;
    activeCaseNotebookId = payload.round?.notebookId || null;
    newRoundAwaitingMaterials = false;
    sessionStorage.removeItem("oaRoundAwaitingMaterials");
    const resultById = new Map((payload.ocr?.job?.files || []).map((result) => [result.id, result]));
    (payload.ocr?.materials || []).forEach((material, index) => {
      const result = resultById.get(material.id);
      if (!result || result.state !== "complete") return;
      const kind = restoreKind(material, index);
      const extension = material.name.includes(".") ? material.name.split(".").pop().toUpperCase() : "文件";
      const type = result.method === "native_pdf_text"
        ? `${extension} · 已读取原生文本`
        : result.method === "document_text"
          ? `${extension} · 已读取文档文本`
          : `${extension} · OCR ${result.pages || 1} 页${result.confidence ? ` · 置信度 ${Math.round(result.confidence * 100)}%` : ""}`;
      materials[kind].push({ id: material.id, name: material.name, kind, extension, type, raw: null, restored: true, qualityState: "ocr-complete", progress: 100 });
    });
    if (payload.ocr?.job?.id) lastOcrJobId = payload.ocr.job.id;
    renderRoundIdentity();
    renderHistoricalMaterials();
    renderMaterials();
    renderIssues();
    renderTimeline();
    if (payload.notebook?.status === "complete" && payload.notebook.initialAnalysis) {
      renderNotebookResult(payload.notebook);
    } else if (activeCaseNotebookId) {
      loadIssueAnalysisHistory(activeCaseNotebookId);
    }
    return true;
  } catch (error) {
    showToast("无法打开案件", error.message || "请返回案件页后重试。");
    return true;
  }
}

async function restoreLatestLocalMaterials() {
  if (startingNewCase || recoveryAttempted || Object.values(materials).flat().length) return;
  recoveryAttempted = true;
  try {
    const response = await fetch("/api/ocr/latest");
    if (!response.ok) return;
    const payload = await response.json();
    if (newRoundAwaitingMaterials) {
      const archivesResponse = await fetch("/api/cases");
      const archives = archivesResponse.ok ? await archivesResponse.json() : { cases: [] };
      const latestIsArchived = (archives.cases || []).some((entry) => entry.ocrJobId === payload.job.id);
      if (latestIsArchived) return;
      newRoundAwaitingMaterials = false;
      sessionStorage.removeItem("oaRoundAwaitingMaterials");
    }
    const resultById = new Map((payload.job.files || []).map((result) => [result.id, result]));
    (payload.materials || []).forEach((material, index) => {
      const result = resultById.get(material.id);
      if (!result || result.state !== "complete") return;
      const kind = restoreKind(material, index);
      const extension = material.name.includes(".") ? material.name.split(".").pop().toUpperCase() : "文件";
      const type = result.method === "native_pdf_text"
        ? `${extension} · 已读取原生文本`
        : `${extension} · OCR ${result.pages || 1} 页${result.confidence ? ` · 置信度 ${Math.round(result.confidence * 100)}%` : ""}`;
      materials[kind].push({
        id: material.id,
        name: material.name,
        kind,
        extension,
        type,
        raw: null,
        restored: true,
        qualityState: "ocr-complete",
        progress: 100,
      });
    });
    if (Object.values(materials).flat().length) {
      lastOcrJobId = payload.job.id;
      currentApplicationNumber = payload.applicationNumber || null;
      renderMaterials();
      loadPriorNotebookHistory();
    }
  } catch {
    // Recovery is optional; an empty new case should remain usable offline.
  }
}

async function restoreLatestNotebookResult() {
  if (!lastOcrJobId || issues.length) return;
  try {
    const response = await fetch("/api/notebook/latest");
    if (!response.ok) return;
    const job = await response.json();
    if (job.status === "complete" && job.ocrJobId === lastOcrJobId && job.initialAnalysis) {
      renderNotebookResult(job);
    }
  } catch {
    // A notebook result is optional until the user explicitly submits materials.
  }
}

async function checkBridge() {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    $("#bridgeState").textContent = data.status === "ready" ? "运行中" : "需要注意";
    document.querySelector(".status-dot").classList.toggle("ready", data.status === "ready");
    await refreshNotebookStatus();
    loadOcrStatus().catch(() => {});
    const restoredCase = await restoreCaseRound();
    if (!restoredCase) {
      await restoreLatestLocalMaterials();
      await restoreLatestNotebookResult();
    }
  } catch {
    $("#bridgeState").textContent = "未连接";
    $("#notebookState").textContent = "NotebookLM 未检查";
  }
}

function renderNotebookStatus(data) {
  const isAuthenticated = data.state === "authenticated" && data.live === true;
  const isConnecting = data.state === "login_in_progress";
  const isChecking = data.state === "checking" || data.checking === true;
  const account = data.account || "";
  $("#notebookState").textContent = isAuthenticated
    ? `NotebookLM 在线已验证${account ? `：${account}` : ""}`
    : isConnecting ? "NotebookLM 正在连接"
      : isChecking ? "NotebookLM 正在验证登录"
        : data.stage === "expired" ? "NotebookLM 登录已失效"
          : data.state === "unavailable" ? "NotebookLM 暂时无法验证" : "NotebookLM 未登录";
  $("#notebookLogin").textContent = isAuthenticated
    ? "NotebookLM 已连接"
    : isConnecting || isChecking ? "正在验证 NotebookLM"
      : "连接 NotebookLM";
  if (isAuthenticated) $("#account").options[0].textContent = account || "已连接的本机 Google 账号";
}

function scheduleNotebookStatusVerification(delay = 1500) {
  if (notebookStatusVerificationTimer !== null) clearTimeout(notebookStatusVerificationTimer);
  notebookStatusVerificationTimer = setTimeout(async () => {
    notebookStatusVerificationTimer = null;
    try {
      await refreshNotebookStatus();
    } catch {
      // One transient local request must not break the whole verification chain.
      scheduleNotebookStatusVerification(1500);
    }
  }, delay);
}

async function refreshNotebookStatus({ duringLogin = false, verify = false } = {}) {
  const query = duringLogin ? "?login=1" : verify ? "?verify=1" : "";
  const response = await fetch(`/api/notebook/status${query}`);
  const data = await response.json();
  lastNotebookStatus = data;
  renderNotebookStatus(data);
  if (!duringLogin) {
    if (notebookStatusVerificationTimer !== null) clearTimeout(notebookStatusVerificationTimer);
    notebookStatusVerificationTimer = null;
    if (data.state === "login_in_progress" || data.state === "checking" || data.checking === true) {
      scheduleNotebookStatusVerification();
    }
  }
  return data;
}

async function openLogin() {
  $("#loginDialog").showModal();
  $("#loginStatus").textContent = "正在读取 OA阅答 内置桥接器的本机登录状态。";
  $("#beginLogin").textContent = "在本机打开 Google 登录";
  $("#beginLogin").disabled = false;
  $("#installHint").hidden = true;
  try {
    const data = await refreshNotebookStatus();
    if (data.state === "authenticated" && data.live === true) {
      $("#loginStatus").textContent = `已连接 ${data.account || "本机 Google 账号"}（Profile：${data.profile || "default"}）。登录凭据保留在本机。`;
      $("#beginLogin").textContent = "重新登录或切换账号";
      $("#beginLogin").disabled = false;
    } else if (data.state === "login_in_progress") {
      $("#loginStatus").textContent = data.message || "正在打开浏览器并等待 Google 登录。";
      startLoginPolling();
    } else {
      $("#loginStatus").textContent = "点击后会使用电脑上安装的 Chrome 或 Edge 打开隔离登录窗口；不会读取你的日常浏览器资料。";
      $("#beginLogin").textContent = "在本机打开 Google 登录";
      $("#beginLogin").disabled = false;
    }
  } catch {
    $("#loginStatus").textContent = "未检测到本机桥接器。请先运行 py bridge/bridge.py。";
  }
}

function stopLoginFeedback() {
  if (loginFeedbackTimer !== null) clearInterval(loginFeedbackTimer);
  loginFeedbackTimer = null;
  loginFeedbackStartedAt = 0;
}

function updateLoginFeedback() {
  const seconds = Math.max(0, Math.floor((Date.now() - loginFeedbackStartedAt) / 1000));
  const button = $("#beginLogin");
  if (seconds < 5) {
    $("#loginStatus").textContent = `正在启动 OA阅答 的本机登录窗口（${seconds} 秒）…`;
    button.textContent = "正在启动本机登录…";
  } else if (seconds < 15) {
    $("#loginStatus").textContent = `正在启动电脑上安装的 Chrome 或 Edge（${seconds} 秒）。请稍候，窗口可能需要一点时间出现。`;
    button.textContent = "正在等待浏览器打开…";
  } else {
    $("#loginStatus").textContent = `请在刚打开的浏览器窗口完成 Google 登录（已等待 ${seconds} 秒）。`;
    button.textContent = "正在等待登录完成…";
  }
}

function startLoginFeedback() {
  stopLoginFeedback();
  loginFeedbackStartedAt = Date.now();
  updateLoginFeedback();
  loginFeedbackTimer = setInterval(updateLoginFeedback, 1000);
}

async function beginLogin() {
  $("#beginLogin").disabled = true;
  startLoginFeedback();
  try {
    const response = await fetch("/api/notebook/login", { method: "POST" });
    const data = await response.json();
    if (!response.ok) {
      $("#loginStatus").textContent = data.message || "无法启动登录。";
      $("#installHint").textContent = data.installHint || "";
      $("#installHint").hidden = !data.installHint;
      return;
    }
    $("#loginStatus").textContent = data.message || "正在启动浏览器并等待你完成 Google 登录。";
    startLoginPolling();
  } catch {
    stopLoginFeedback();
    $("#loginStatus").textContent = "无法连接本机桥接器。请确认它正在运行。";
  } finally {
    if (!loginFeedbackTimer) $("#beginLogin").disabled = false;
  }
}

function stopLoginPolling() {
  if (loginPollingTimer !== null) clearTimeout(loginPollingTimer);
  loginPollingTimer = null;
}

async function pollLoginStatus() {
  try {
    const data = await refreshNotebookStatus({ duringLogin: true });
    if (data.state === "authenticated" && data.live === true) {
      stopLoginPolling();
      stopLoginFeedback();
      if ($("#loginDialog").open) $("#loginDialog").close();
      showToast("NotebookLM 已连接", `已使用本机 Profile ${data.profile || "default"} 登录。`);
      return;
    }
    if (data.state === "not_authenticated" && data.stage === "failed") {
      stopLoginPolling();
      stopLoginFeedback();
      $("#loginStatus").textContent = data.message || "登录没有完成，请重试。";
      $("#beginLogin").disabled = false;
      $("#beginLogin").textContent = "重新打开 Google 登录";
      return;
    }
    $("#loginStatus").textContent = data.message || "仍在等待浏览器完成登录。完成后本窗口会自动更新。";
  } catch {
    if (!loginFeedbackTimer) $("#loginStatus").textContent = "暂时无法核验登录状态；将自动重试。";
  }
  loginPollingTimer = setTimeout(pollLoginStatus, 3000);
}

function startLoginPolling() {
  stopLoginPolling();
  loginPollingTimer = setTimeout(pollLoginStatus, 1500);
}

$("#analyseSelected").addEventListener("click", openConfirmation);
$("#toggleIssuesPanel").addEventListener("click", () => setIssuesPanelCollapsed(!issuesPanelCollapsed));
$("#startQualityCheck").addEventListener("click", startQualityCheck);
$("#startOcrReview").addEventListener("click", startOcrReview);
$("#closeOcrDialog").addEventListener("click", () => $("#ocrDialog").close());
$("#ocrDialog").addEventListener("close", stopOcrSetupPolling);
$("#prepareOcr").addEventListener("click", prepareOcr);
$("#installOcr").addEventListener("click", prepareOcr);
$("#notebookLogin").addEventListener("click", openLogin);
$("#archiveSettings").addEventListener("click", (event) => { event.preventDefault(); openArchiveSettings(); });
$("#closeArchiveSettings").addEventListener("click", () => $("#archiveSettingsDialog").close());
$("#saveArchiveSettings").addEventListener("click", () => saveArchiveSettings());
$("#resetArchiveSettings").addEventListener("click", () => {
  const defaultRoot = archiveSettings?.defaultArchiveRoot;
  if (!defaultRoot) {
    openArchiveSettings();
    return;
  }
  $("#archiveRootInput").value = defaultRoot;
  $("#archiveSettingsCurrent").textContent = `将恢复默认位置：${defaultRoot}`;
});
$("#closeLogin").addEventListener("click", () => $("#loginDialog").close());
$("#loginDialog").addEventListener("close", () => {
  stopLoginPolling();
  stopLoginFeedback();
  stopNotebookSetupPolling();
  // Closing the dialog must only hide it; login verification continues in the
  // workbench and updates the sidebar as soon as the browser worker finishes.
  const verified = lastNotebookStatus?.state === "authenticated" && lastNotebookStatus?.live === true;
  if (!verified) {
    refreshNotebookStatus({ verify: true }).catch(() => scheduleNotebookStatusVerification());
  }
});
$("#beginLogin").addEventListener("click", beginLogin);
$("#consent").addEventListener("change", (event) => { $("#queueJob").disabled = !event.target.checked; });
$("#queueJob").addEventListener("click", async (event) => {
  event.preventDefault();
  $("#confirmDialog").close();
  if (event.currentTarget.dataset.action === "submit-materials") {
    submitMaterialsToNotebook();
    return;
  }
  if (event.currentTarget.dataset.action === "issue-analysis") {
    submitSelectedIssueAnalysis();
  }
});
$("#toastDialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
$("#archiveCase").addEventListener("click", archiveCurrentCase);
$("#addRound").addEventListener("click", () => $("#newRoundDialog").showModal());
$("#cancelNewRound").addEventListener("click", () => $("#newRoundDialog").close());
$("#confirmNewRound").addEventListener("click", archiveAndStartNewRound);
$("#showAudit").addEventListener("click", () => showToast("审计记录", "当前案件尚未上传材料，也没有对外发送记录。"));

sessionStorage.removeItem("oaRoundNumber");
renderRoundIdentity();
renderMaterials();
renderIssues();
checkBridge();
if (window.location.hash === "#settings") openArchiveSettings();
setInterval(() => { refreshNotebookStatus().catch(() => {}); }, 60000);
