function parseNotebookIssues(raw) {
  let value = raw;
  for (let depth = 0; depth < 3; depth += 1) {
    if (typeof value === "string") {
      try { value = JSON.parse(value); } catch { break; }
    }
    if (value && typeof value === "object" && typeof value.answer === "string") {
      value = value.answer;
      continue;
    }
    break;
  }
  if (!value || typeof value !== "object" || !Array.isArray(value.issues)) return [];
  return value.issues.map((issue, index) => {
    const evidence = Array.isArray(issue.evidence) ? issue.evidence.map((item) => [
      item.source || "材料定位",
      [item.location, item.excerpt].filter(Boolean).join("："),
    ]) : [];
    const sources = [...new Set(evidence.map(([source]) => source).filter(Boolean))];
    const tags = [issue.ground, issue.legal_basis, issue.claim_or_requirement, ...(Array.isArray(issue.prior_art) ? issue.prior_art : [])].filter(Boolean);
    return {
      id: issue.id || `I-${String(index + 1).padStart(2, "0")}`,
      title: issue.title || `审查问题 ${index + 1}`,
      ground: issue.ground || "",
      legalBasis: issue.legal_basis || "",
      claims: issue.claim_or_requirement || "",
      priorArt: Array.isArray(issue.prior_art) ? issue.prior_art : [],
      summary: issue.summary || "",
      analysisFocus: issue.analysis_focus || "",
      detail: [issue.summary, issue.analysis_focus ? `答复核对重点：${issue.analysis_focus}` : ""].filter(Boolean).join("\n"),
      tags,
      evidence,
      sources,
    };
  }).filter((issue) => issue.title || issue.detail);
}
