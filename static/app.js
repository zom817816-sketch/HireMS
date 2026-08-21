const API = "/api/v1";
const $ = (id) => document.getElementById(id);
let lastQueryId = null;
let workflowCandidates = [];
let systemSettings = {};
let currentResults = [];
let toastTimer = null;

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#039;",'"':"&quot;"}[char]));
const stripMarkdown = (value) => String(value || "").replace(/[#*_`>-]/g, "").replace(/\n+/g, " ");
const importDate = (value) => {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-CN", {year: "numeric", month: "2-digit", day: "2-digit"}).format(date);
};

function notify(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 3600);
}

function setLog(message, active = false) {
  const log = $("intake-log");
  log.textContent = message;
  log.classList.toggle("active", active);
}

function setScreenState(message, running = false) {
  $("screen-status-text").textContent = message;
  $("screen-shell").classList.toggle("running", running);
}

function animateNumber(element, target) {
  const end = Number(target) || 0;
  const start = Number(String(element.textContent).replace(/\D/g, "")) || 0;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { element.textContent = end; return; }
  const started = performance.now();
  const frame = (now) => {
    const progress = Math.min((now - started) / 520, 1);
    element.textContent = Math.round(start + (end - start) * (1 - Math.pow(1 - progress, 3)));
    if (progress < 1) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

async function request(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || `请求失败 (${res.status})`);
  return data;
}

async function checkHealth() {
  try {
    if ((await request(`${API}/health`)).status === "ok") $("health-text").textContent = "本地服务运行正常";
  } catch (_) { $("health-text").textContent = "本地服务无法连接"; }
}

async function loadResumes() {
  try { animateNumber($("resume-count"), (await request(`${API}/resumes`)).total ?? 0); } catch (_) {}
}

function renderLogs(logs) {
  $("activity-log").innerHTML = logs.length ? logs.map((log) => `<div class="activity-entry"><span>${escapeHtml(log.created_at)}</span><span class="${log.status === "success" ? "ok" : "fail"}">${escapeHtml(log.kind)} / ${escapeHtml(log.status)}</span><span>${escapeHtml(log.detail)}</span></div>`).join("") : '<div class="empty-state">还没有运行记录。</div>';
}

async function loadStatus() {
  try {
    const data = await request(`${API}/operations/status`);
    const mail = data.mail.configured, bitable = data.bitable.configured;
    $("mail-status").textContent = mail ? "已连接" : "待配置";
    $("bitable-status").textContent = bitable ? "已连接" : "待配置";
    $("mail-status").classList.toggle("configured", mail);
    $("bitable-status").classList.toggle("configured", bitable);
    $("mail-dot").classList.toggle("on", mail);
    renderLogs(data.logs || []);
  } catch (_) {}
}

const parseSettingsList = (value) => [...new Set(value.split(/[\n,，;；]+/).map((item) => item.trim()).filter(Boolean))];
const settingsListText = (values) => (values || []).join("\n");

function renderSettingsConnections(connections = {}) {
  const values = [connections.feishu_messages, connections.candidate_email, connections.calendar];
  [...$("settings-connections").children].forEach((item, index) => {
    item.classList.toggle("on", Boolean(values[index]));
    item.title = values[index] ? "已配置" : "需在 .env 中补充凭据";
  });
}

async function loadSystemSettings(fillForm = false) {
  const data = await request(`${API}/settings`);
  systemSettings = data;
  if (fillForm) {
    $("settings-hr-openids").value = settingsListText(data.hr_open_ids);
    $("settings-hr-emails").value = settingsListText(data.hr_emails);
    $("settings-interviewer-openids").value = settingsListText(data.default_interviewer_ids);
    $("settings-location").value = data.default_interview_location || "线上";
    $("settings-from-name").value = data.mail_from_name || "招聘团队";
    $("settings-overdue-hours").value = data.overdue_hours || 48;
    renderSettingsConnections(data.connections);
  }
  return data;
}

async function openSettings() {
  try { await loadSystemSettings(true); $("settings-btn").classList.add("active"); $("settings-dialog").showModal(); }
  catch (error) { notify(`设置加载失败：${error.message}`); }
}

async function settingsForm(event) {
  event.preventDefault();
  const payload = {
    hr_open_ids: parseSettingsList($("settings-hr-openids").value),
    hr_emails: parseSettingsList($("settings-hr-emails").value),
    default_interviewer_ids: parseSettingsList($("settings-interviewer-openids").value),
    default_interview_location: $("settings-location").value.trim() || "线上",
    mail_from_name: $("settings-from-name").value.trim() || "招聘团队",
    overdue_hours: Number($("settings-overdue-hours").value),
  };
  try {
    systemSettings = await request(`${API}/settings`, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    $("settings-dialog").close(); notify("系统设置已保存并立即生效"); await loadStatus();
  } catch (error) { notify(`设置保存失败：${error.message}`); }
}

async function syncMailbox() {
  const btn = $("sync-mail-btn");
  btn.disabled = true;
  setLog("正在连接邮箱，并扫描最近的简历附件…", true);
  try {
    const data = await request(`${API}/operations/mail-sync`, {method: "POST"});
    setLog(`同步完成：扫描 ${data.scanned} 个附件，入库 ${data.imported} 个，跳过重复 ${data.skipped} 个，失败 ${data.failed} 个。`);
    notify(`邮箱同步完成，新增 ${data.imported} 份简历`);
  } catch (error) { setLog(`邮箱同步未完成：${error.message}`); notify("邮箱同步失败，请检查连接配置"); }
  finally { btn.disabled = false; await Promise.all([loadResumes(), loadStatus()]); }
}

async function uploadResumes() {
  const input = $("resume-files"), files = [...input.files];
  if (!files.length) { notify("请先选择至少一份简历文件"); return; }
  $("upload-btn").disabled = true;
  setLog(`正在导入 ${files.length} 份简历…`, true);
  let ok = 0, skipped = 0, lines = [];
  for (const file of files) {
    const form = new FormData(); form.append("file", file);
    try {
      const result = await request(`${API}/resumes`, {method: "POST", body: form});
      if (result.status === "duplicate") skipped++; else ok++;
      lines.push(`${result.status === "duplicate" ? "↷" : "✓"} ${file.name}: ${result.message}`);
    }
    catch (error) { lines.push(`× ${file.name}: ${error.message}`); }
  }
  const failed = files.length - ok - skipped;
  setLog(`处理完成：入库/更新 ${ok}，重复跳过 ${skipped}，失败 ${failed}\n${lines.join("\n")}`);
  notify(ok ? `${ok} 份简历已入库或更新` : (skipped ? `${skipped} 份重复简历已跳过` : "没有简历被成功导入"));
  input.value = ""; updateSelectedFiles(); await loadResumes();
}

function updateSelectedFiles(files = $("resume-files").files) {
  const selection = $("selected-files");
  const uploadButton = $("upload-btn");
  const selected = [...files];
  uploadButton.disabled = !selected.length;
  selection.classList.toggle("ready", Boolean(selected.length));
  if (!selected.length) { selection.textContent = "尚未选择文件"; return; }
  const names = selected.slice(0, 2).map((file) => file.name).join("、");
  const rest = selected.length > 2 ? ` 等 ${selected.length} 份文件` : "";
  selection.textContent = `已选择：${names}${rest}，可开始导入`;
}

function setupFileIntake() {
  const input = $("resume-files");
  const chooser = $("choose-files-btn");
  chooser.addEventListener("click", () => input.click());
  input.addEventListener("change", () => updateSelectedFiles());
  ["dragenter", "dragover"].forEach((eventName) => chooser.addEventListener(eventName, (event) => {
    event.preventDefault(); chooser.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((eventName) => chooser.addEventListener(eventName, (event) => {
    event.preventDefault(); chooser.classList.remove("dragging");
  }));
  chooser.addEventListener("drop", (event) => {
    if (!event.dataTransfer?.files?.length) return;
    input.files = event.dataTransfer.files;
    updateSelectedFiles(event.dataTransfer.files);
  });
}

function resumeButton(candidate, label = "查看简历") {
  if (!candidate.has_resume_file) return `<button class="mini-button resume" disabled title="历史候选人尚未保存原始文件，请重新导入简历">${label}</button>`;
  return `<button class="mini-button resume" data-candidate="${candidate.id}" data-action="view">${label}</button>`;
}

function actionsFor(candidate) {
  return `<div class="candidate-actions">${resumeButton(candidate)}<button class="mini-button pass" data-candidate="${candidate.id}" data-action="pass">通过</button><button class="mini-button reject" data-candidate="${candidate.id}" data-action="reject">淘汰</button><button class="mini-button" data-candidate="${candidate.id}" data-action="schedule">安排面试</button><button class="mini-button delete" data-candidate="${candidate.id}" data-action="delete">删除</button></div>`;
}

function renderResults(data) {
  const candidates = data.candidates || [];
  currentResults = candidates;
  const scope = data.recall_scope || {};
  const sync = data.bitable_sync || {};
  const scopeText = scope.job_category ? ` · ${scope.job_category} · 近 ${scope.lookback_days || 60} 天` : "";
  const syncText = sync.status === "success" ? ` · 已自动落表 ${sync.exported}`
    : sync.status === "up_to_date" ? " · 多维表格已同步"
    : sync.status === "failed" ? " · 自动落表失败，可重试"
    : sync.status === "no_eligible_candidates" ? " · 暂无候选人达到落表阈值" : "";
  $("result-meta").textContent = `${candidates.length} 位匹配候选人${scopeText}${syncText}`;
  $("export-btn").disabled = !candidates.length;
  $("results").innerHTML = candidates.length ? candidates.map((candidate, index) => {
    const score = Math.round(Number(candidate.overall_score || 0) * 100);
    const tags = [candidate.job_category || "其他", ...(candidate.skills || [])].slice(0, 6).map((skill) => `<span class="tag">${escapeHtml(skill)}</span>`).join("");
    const imported = importDate(candidate.imported_at);
    return `<article class="candidate enter" style="animation-delay:${index * 80}ms" id="candidate-${candidate.id}"><div class="candidate-rank">RANK<b>${String(candidate.rank || 0).padStart(2, "0")}</b></div><div><div class="candidate-name">${escapeHtml(candidate.name || "未识别姓名")}</div><div class="candidate-contact">${escapeHtml(candidate.email || "未提供邮箱")}${candidate.phone ? ` · ${escapeHtml(candidate.phone)}` : ""}${imported ? ` · 导入 ${escapeHtml(imported)}` : ""}</div><div class="tags">${tags}</div></div><div class="candidate-summary">${escapeHtml(stripMarkdown(candidate.analysis) || "已完成基础匹配，等待 HR 复核简历细节。")}</div><div class="score-orbit" style="--score:${score}%"><strong>${score}</strong><small>MATCH / 100</small></div>${actionsFor(candidate)}</article>`;
  }).join("") : '<div class="empty-state">本次没有候选人通过条件。<span>系统只召回最近两个月导入且岗位类别一致的简历；可检查类别、时间窗口或先同步更多简历。</span></div>';
}

async function runQuery() {
  const text = $("query-text").value.trim();
  if (!text) { notify("请先描述岗位要求"); $("query-text").focus(); return; }
  const btn = $("query-btn"); btn.disabled = true;
  $("results").innerHTML = '<div class="empty-state">正在让岗位要求穿过规则与语义两层筛选…<span>候选人信号将按匹配度抵达。</span></div>';
  setScreenState("正在提取岗位规则", true);
  const stages = ["正在匹配硬性条件", "正在计算语义匹配度", "正在生成候选人摘要"];
  let stage = 0;
  const stageTimer = setInterval(() => { setScreenState(stages[stage++ % stages.length], true); }, 1100);
  try {
    const query = await request(`${API}/queries`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({query_text: text})});
    lastQueryId = query.query_id;
    const result = await request(`${API}/results/${lastQueryId}`);
    renderResults(result);
    setScreenState(`筛选完成 · ${result.total_candidates} 位候选人抵达`);
    const sync = result.bitable_sync || {};
    if (sync.status === "success") notify(`筛选完成，${sync.exported} 位高分候选人已自动写入多维表格`);
    else if (sync.status === "failed") notify(`筛选完成；自动落表失败，可点击按钮重试：${sync.message || "请检查飞书配置"}`);
    else notify(`筛选完成，发现 ${result.total_candidates} 位候选人`);
    await Promise.all([loadPipeline(), loadStatus()]);
  } catch (error) {
    $("results").innerHTML = `<div class="empty-state">筛选失败：${escapeHtml(error.message)}<span>请检查 LLM 配置或稍后重试。</span></div>`;
    setScreenState("筛选未完成，请检查配置"); notify("筛选未完成，请检查服务配置");
  } finally { clearInterval(stageTimer); $("screen-shell").classList.remove("running"); btn.disabled = false; }
}

const lane = (status) => status === "待复核" ? "待复核" : ["通过", "安排面试", "面试中"].includes(status) ? "面试流程" : status.startsWith("Offer") ? "Offer" : "已淘汰";
function pipelineActions(candidate) {
  let actions = "";
  if (candidate.status === "待复核") actions = `<button class="mini-button pass" data-candidate="${candidate.id}" data-action="pass">通过</button><button class="mini-button reject" data-candidate="${candidate.id}" data-action="reject">淘汰</button><button class="mini-button" data-candidate="${candidate.id}" data-action="schedule">安排面试</button>`;
  else if (candidate.status === "通过") actions = `<button class="mini-button pass" data-candidate="${candidate.id}" data-action="schedule">安排面试</button><button class="mini-button reject" data-candidate="${candidate.id}" data-action="reject">淘汰</button>`;
  else if (candidate.status === "安排面试" && candidate.active_interview) actions = `<button class="mini-button pass" data-candidate="${candidate.id}" data-action="feedback">填写评价</button><button class="mini-button" data-candidate="${candidate.id}" data-action="reschedule">改期</button><button class="mini-button reject" data-candidate="${candidate.id}" data-action="cancel_interview">取消面试</button>`;
  else if (candidate.status === "面试中" && candidate.pending_feedback) actions = `<button class="mini-button pass" data-candidate="${candidate.id}" data-action="feedback">更新待定评价</button>`;
  else if (candidate.status === "面试中" && candidate.next_round) actions = `<button class="mini-button pass" data-candidate="${candidate.id}" data-action="schedule">安排下一轮</button>`;
  else if (candidate.status === "Offer待发") actions = `<button class="mini-button pass" data-candidate="${candidate.id}" data-action="offer_sent">已发 Offer</button>`;
  else if (candidate.status === "Offer已发") actions = `<button class="mini-button pass" data-candidate="${candidate.id}" data-action="offer_accepted">接受</button><button class="mini-button reject" data-candidate="${candidate.id}" data-action="offer_rejected">拒绝</button>`;
  return `${resumeButton(candidate, "简历")}${actions}<button class="mini-button delete" data-candidate="${candidate.id}" data-action="delete">删除</button>`;
}

function renderPipeline() {
  const groups = {"待复核": [], "面试流程": [], "Offer": [], "已淘汰": []};
  workflowCandidates.forEach((candidate) => groups[lane(candidate.status)].push(candidate));
  $("pipeline-meta").textContent = `${workflowCandidates.length} 位候选人`;
  $("pipeline-board").innerHTML = Object.entries(groups).map(([name, items]) => `<section class="pipeline-column"><h3>${name}<span>${items.length}</span></h3>${items.map((candidate) => { const interview = candidate.active_interview || candidate.pending_feedback; const interviewLine = interview ? `<span class="interview-signal">${escapeHtml(interview.round_name)} · ${escapeHtml(interview.status)} · ${escapeHtml(importDate(interview.start_at) || interview.start_at)}</span>` : ""; return `<article class="pipeline-item"><div class="pipeline-person"><strong>${escapeHtml(candidate.name || "未识别姓名")}</strong><em>${escapeHtml(candidate.status)}</em></div><p>${escapeHtml(candidate.job_name || "")} · ${Math.round((candidate.overall_score || 0) * 100)} 分</p>${interviewLine}<div class="mini-actions">${pipelineActions(candidate)}</div></article>`; }).join("")}</section>`).join("");
}

async function loadPipeline() {
  try { const data = await request(`${API}/workflow/candidates`); workflowCandidates = data.candidates || []; renderPipeline(); }
  catch (error) { $("pipeline-board").innerHTML = `<div class="empty-state">候选人工作流加载失败：${escapeHtml(error.message)}</div>`; }
}

async function candidateAction(id, action) {
  if (action === "view") {
    window.open(`${API}/resumes/${encodeURIComponent(id)}/file`, "_blank", "noopener,noreferrer");
    return;
  }
  if (action === "schedule") return openSchedule(id);
  if (action === "feedback") return openFeedback(id);
  if (action === "reschedule") return openSchedule(id, true);
  if (action === "cancel_interview") return cancelInterview(id);
  if (["offer_sent", "offer_accepted", "offer_rejected"].includes(action)) return offerAction(id, action);
  if (action === "delete") return deleteCandidate(id);
  try { await request(`${API}/workflow/candidates/${id}/action`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({action})}); notify("候选人状态已推进"); await Promise.all([loadPipeline(), loadStatus()]); }
  catch (error) { notify(`更新失败：${error.message}`); }
}

async function deleteCandidate(id) {
  const candidate = workflowCandidates.find((item) => item.id === id) || currentResults.find((item) => item.id === id);
  const name = candidate?.name || "该候选人";
  const message = `确定从本地候选人库删除“${name}”吗？\n\n这会清除简历向量、流程、面试与通知记录，且无法恢复。已写入飞书多维表格的历史行不会自动删除。`;
  if (!window.confirm(message)) return;
  try {
    await request(`${API}/workflow/candidates/${id}`, {method: "DELETE"});
    currentResults = currentResults.filter((item) => item.id !== id);
    renderResults({candidates: currentResults});
    notify(`已从本地删除 ${name}`);
    await Promise.all([loadPipeline(), loadStatus(), loadResumes()]);
  } catch (error) { notify(`删除失败：${error.message}`); }
}

function localDatetime(date) { const p = (n) => String(n).padStart(2, "0"); return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}T${p(date.getHours())}:${p(date.getMinutes())}`; }
function openSchedule(id, reschedule = false) {
  const candidate = workflowCandidates.find((item) => item.id === id);
  if (!candidate) { notify("请先从招聘流程中加载候选人"); return; }
  const interview = reschedule ? candidate.active_interview : null;
  if (reschedule && !interview) { notify("没有可以改期的已安排面试"); return; }
  $("schedule-candidate-id").value = id;
  $("schedule-interview-id").value = interview?.interview_id || "";
  $("schedule-candidate-name").textContent = `候选人：${candidate.name || "未识别姓名"} · ${candidate.job_name || ""}`;
  $("schedule-title").textContent = interview ? "调整面试日程" : "安排面试";
  $("schedule-submit").textContent = interview ? "确认改期" : "确认安排";
  const start = interview ? new Date(interview.start_at) : new Date(Date.now() + 86400000);
  if (!interview) start.setHours(10, 0, 0, 0);
  const end = interview ? new Date(interview.end_at) : new Date(start.getTime() + 3600000);
  $("interview-round").value = interview?.round_name || candidate.next_round || "一面";
  $("interview-round").disabled = Boolean(interview);
  $("interview-start").value = localDatetime(start); $("interview-end").value = localDatetime(end);
  $("interview-location").value = interview?.location || systemSettings.default_interview_location || "线上";
  $("interviewer-ids").value = (interview?.interviewer_ids || systemSettings.default_interviewer_ids || []).join(", ");
  $("interview-note").value = interview?.note || "";
  $("schedule-dialog").showModal();
}

async function scheduleForm(event) {
  event.preventDefault();
  const interviewId = $("schedule-interview-id").value;
  const payload = {candidate_id: $("schedule-candidate-id").value, round_name: $("interview-round").value, interviewer_ids: $("interviewer-ids").value.split(",").map((x) => x.trim()).filter(Boolean), start_at: new Date($("interview-start").value).toISOString(), end_at: new Date($("interview-end").value).toISOString(), location: $("interview-location").value, note: $("interview-note").value};
  const url = interviewId ? `${API}/workflow/interviews/${interviewId}` : `${API}/workflow/interviews`;
  try { const result = await request(url, {method: interviewId ? "PATCH" : "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}); $("schedule-dialog").close(); notify(`${interviewId ? "面试已改期" : "面试已安排"}${result.sync_status === "calendar_synced" ? "，飞书日历已同步" : "，仅保存本地日程"}${result.email_status === "sent" ? "，候选人邮件已发送" : ""}`); await Promise.all([loadPipeline(), loadStatus()]); }
  catch (error) { notify(`面试安排失败：${error.message}`); }
}

function openFeedback(id) {
  const candidate = workflowCandidates.find((item) => item.id === id);
  const interview = candidate?.active_interview || candidate?.pending_feedback;
  if (!candidate || !interview) { notify("没有待评价的面试"); return; }
  $("feedback-interview-id").value = interview.interview_id;
  $("feedback-candidate-name").textContent = `候选人：${candidate.name || "未识别姓名"} · ${candidate.job_name || ""}`;
  $("feedback-round").textContent = interview.round_name;
  $("feedback-status").value = interview.status === "待定" ? "待定" : "通过";
  $("feedback-next-step").value = "继续面试";
  $("feedback-text").value = interview.feedback || "";
  updateFeedbackNextStep();
  $("feedback-dialog").showModal();
}

function updateFeedbackNextStep() {
  const needsNextStep = $("feedback-status").value === "通过";
  $("feedback-next-step-field").hidden = !needsNextStep;
  $("feedback-next-step").disabled = !needsNextStep;
}

async function feedbackForm(event) {
  event.preventDefault();
  const id = $("feedback-interview-id").value;
  const status = $("feedback-status").value;
  const payload = {status, feedback: $("feedback-text").value.trim(), next_step: status === "通过" ? $("feedback-next-step").value : null};
  try { const result = await request(`${API}/workflow/interviews/${id}/feedback`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}); $("feedback-dialog").close(); notify(`面试评价已保存，候选人进入“${result.candidate.status}”`); await Promise.all([loadPipeline(), loadStatus()]); }
  catch (error) { notify(`评价提交失败：${error.message}`); }
}

async function cancelInterview(id) {
  const candidate = workflowCandidates.find((item) => item.id === id);
  const interview = candidate?.active_interview;
  if (!interview) { notify("没有可以取消的面试"); return; }
  const reason = window.prompt(`取消 ${candidate.name || "候选人"} 的${interview.round_name}，请输入原因：`, "招聘安排调整");
  if (reason === null) return;
  try { const result = await request(`${API}/workflow/interviews/${interview.interview_id}/cancel`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({reason})}); notify(`面试已取消${result.email_status === "sent" ? "，候选人邮件已发送" : ""}`); await Promise.all([loadPipeline(), loadStatus()]); }
  catch (error) { notify(`取消面试失败：${error.message}`); }
}

async function offerAction(id, action) {
  const values = {offer_sent: "已发", offer_accepted: "已接受", offer_rejected: "已拒绝"};
  try { const result = await request(`${API}/workflow/candidates/${id}/offer`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({status: values[action]})}); notify(`Offer 状态已更新为“${result.candidate.status}”`); await Promise.all([loadPipeline(), loadStatus()]); }
  catch (error) { notify(`Offer 更新失败：${error.message}`); }
}

async function runNotification(kind) {
  try { const result = await request(`${API}/workflow/notifications/${kind}`, {method: "POST"}); notify(result.summary); await loadStatus(); }
  catch (error) { notify(`提醒任务失败：${error.message}`); }
}

async function exportBitable() {
  if (!lastQueryId) return;
  const btn = $("export-btn"); btn.disabled = true;
  try { const data = await request(`${API}/operations/bitable-export`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({query_id: lastQueryId, job_name: $("query-text").value.trim().slice(0, 80) || "未命名岗位"})}); notify(data.message || `已写入 ${data.exported} 条候选人记录`); }
  catch (error) { notify(`多维表格写入失败：${error.message}`); }
  finally { btn.disabled = false; await loadStatus(); }
}

function setupMotion() {
  const progress = () => { const top = document.documentElement.scrollTop; const height = document.documentElement.scrollHeight - window.innerHeight; document.documentElement.style.setProperty("--scroll-progress", `${height ? (top / height) * 100 : 0}%`); };
  addEventListener("scroll", progress, {passive: true}); progress();
  const revealObserver = new IntersectionObserver((entries) => entries.forEach((entry) => { if (entry.isIntersecting) { entry.target.classList.add("in-view"); revealObserver.unobserve(entry.target); } }), {threshold: .13});
  document.querySelectorAll(".reveal").forEach((element) => revealObserver.observe(element));
  const sections = [...document.querySelectorAll(".story-section")];
  const navObserver = new IntersectionObserver((entries) => entries.forEach((entry) => {
    if (!entry.isIntersecting) return;
    document.querySelectorAll(".nav-item").forEach((link) => link.classList.toggle("active", link.dataset.section === entry.target.id));
    const index = Math.max(0, Math.min(3, sections.indexOf(entry.target)));
    document.querySelectorAll(".journey-step").forEach((step, stepIndex) => step.classList.toggle("is-active", stepIndex <= index));
  }), {rootMargin: "-32% 0px -48% 0px", threshold: .01});
  sections.forEach((section) => navObserver.observe(section));
  document.querySelectorAll(".magnetic-card").forEach((card) => card.addEventListener("pointermove", (event) => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const rect = card.getBoundingClientRect(); const x = (event.clientX - rect.left) / rect.width - .5; const y = (event.clientY - rect.top) / rect.height - .5;
    card.style.transform = `translate(${x * 3}px, ${y * 3 - 7}px)`;
  }));
  document.querySelectorAll(".magnetic-card").forEach((card) => card.addEventListener("pointerleave", () => { card.style.transform = ""; }));
}

$("sync-mail-btn").addEventListener("click", syncMailbox);
$("upload-btn").addEventListener("click", uploadResumes);
$("query-btn").addEventListener("click", runQuery);
$("export-btn").addEventListener("click", exportBitable);
$("summary-btn").addEventListener("click", () => runNotification("daily_summary"));
$("overdue-btn").addEventListener("click", () => runNotification("overdue"));
$("settings-btn").addEventListener("click", openSettings);
$("close-settings").addEventListener("click", () => $("settings-dialog").close());
$("settings-dialog").addEventListener("close", () => $("settings-btn").classList.remove("active"));
$("settings-form").addEventListener("submit", settingsForm);
$("close-schedule").addEventListener("click", () => $("schedule-dialog").close());
$("schedule-form").addEventListener("submit", scheduleForm);
$("close-feedback").addEventListener("click", () => $("feedback-dialog").close());
$("feedback-status").addEventListener("change", updateFeedbackNextStep);
$("feedback-form").addEventListener("submit", feedbackForm);
document.addEventListener("click", (event) => { const button = event.target.closest("[data-candidate][data-action]"); if (button) candidateAction(button.dataset.candidate, button.dataset.action); });
$("today").textContent = new Intl.DateTimeFormat("zh-CN", {year: "numeric", month: "long", day: "numeric", weekday: "short"}).format(new Date());
setupMotion();
setupFileIntake();
Promise.all([checkHealth(), loadResumes(), loadStatus(), loadPipeline(), loadSystemSettings()]);
setInterval(checkHealth, 30000);
