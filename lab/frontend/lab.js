/* CodeAgent Lab — 本地完整代码智能体前端(原生 JS, 零依赖, 离线可用) */
"use strict";

/* ═══════════ 基础工具 ═══════════ */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const escAttr = (s) => esc(s).replace(/\n/g, " ");
let __seq = 0;
const uid = (p) => p + "-" + (++__seq) + "-" + Date.now().toString(36).slice(-4);

/* ═══════════ API 封装(统一 {ok,data,error} 契约) ═══════════ */
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  try {
    const resp = await fetch(path, opts);
    const ctype = resp.headers.get("Content-Type") || "";
    if (!ctype.includes("application/json")) {
      const text = await resp.text();
      if (!resp.ok) return { ok: false, error: `HTTP ${resp.status}` };
      return { ok: true, data: text, raw: true };
    }
    const j = await resp.json();
    if (!resp.ok) return { ok: false, error: j.error || `HTTP ${resp.status}` };
    return j;
  } catch (e) {
    return { ok: false, error: "网络错误: " + e.message };
  }
}
const GET = (p) => api("GET", p);
const POST = (p, b) => api("POST", p, b);

/* ═══════════ 全局状态 ═══════════ */
const S = {
  atoms: [],            // 原子列表
  atomMap: {},
  tree: null,
  activeFile: null,     // {path, content, methods, dead, rel}
  editorDirty: false,
  graphKind: null,
  canvas: { nodes: [], edges: [], loopEdges: [], selected: null, linkDraft: null },
  pipes: {},
  debugRuns: {},
  lastEvSeq: 0,
  chat: [],
  models: null,
  providers: [],
  chain: [],
  repTarget: "",
  view: "presets",
};

/* ═══════════ 视图切换 ═══════════ */
function switchView(v) {
  S.view = v;
  document.body.classList.toggle("in-canvas", v === "canvas");
  document.querySelectorAll("#tabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  document.querySelectorAll("#main .view").forEach((sec) => sec.classList.toggle("active", sec.id === "view-" + v));
  if (v === "canvas") { if (!S.canvas.nodes.length && !S.canvas._blank) { S.canvas._defaultLoaded = false; ensureDefaultPipeline(); } renderOrchPicker(); renderCanvas(); renderProps(); }
  if (v === "presets") { renderPresets(); refreshPresetHistory(); }
  if (v === "debug") loadDebugHistory();
  if (v === "report") loadReportHistory();
  if (v === "models") loadModels();
  if (v === "ext") loadExtList();
  if (v === "arch") renderArch();
  if (v === "events") loadEventsFull();
}
document.querySelectorAll("#tabs .tab").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));

/* ═══════════ 顶栏信息 + 事件轮询 ═══════════ */
async function boot() {
  const h = await GET("/api/health");
  if (!h.ok) { $("conn-pill").textContent = "服务 ✗"; $("conn-pill").className = "pill err"; return; }
  $("conn-pill").textContent = "服务 ✓";
  const c = (h.data && h.data.config) || {};
  S.targetRoot = c.target_root || "";
  if ($("target-input")) $("target-input").value = S.targetRoot;
  loadTargetRecents();
  await loadAtoms();
  loadTree();
  loadEvents(true);
  renderPresets();
  setInterval(() => { loadEvents(false); }, 1500);
  setInterval(() => { pollRunning(); }, 1500);
}
/* ── 审查对象选择器（P0-1：切换任意目标仓库/目录/文件 + 一键审查）── */
const TARGET_RECENT_KEY = "codeagent_lab_target_recents";
function loadTargetRecents() {
  const dl = $("target-recents"); if (!dl) return;
  let recents = [];
  try { recents = JSON.parse(localStorage.getItem(TARGET_RECENT_KEY) || "[]"); } catch (e) {}
  dl.innerHTML = recents.map((r) => `<option value="${escAttr(r)}"></option>`).join("");
}
function rememberTarget(p) {
  let recents = [];
  try { recents = JSON.parse(localStorage.getItem(TARGET_RECENT_KEY) || "[]"); } catch (e) {}
  recents = [p, ...recents.filter((r) => r !== p)].slice(0, 12);
  try { localStorage.setItem(TARGET_RECENT_KEY, JSON.stringify(recents)); } catch (e) {}
  loadTargetRecents();
}
function setTargetUI(targetRoot) {
  S.targetRoot = targetRoot || "";
  if ($("target-input")) $("target-input").value = targetRoot;
}
function flashHint(msg, cls) {
  const h = $("target-hint"); if (!h) return;
  h.textContent = msg; h.className = "hint" + (cls ? " " + cls : "");
  setTimeout(() => { h.textContent = ""; h.className = "hint"; }, 5000);
}
async function applyTarget() {
  const raw = ($("target-input").value || "").trim();
  if (!raw) { flashHint("请输入目标仓库/目录/文件路径"); return; }
  const isFile = /\.py$/i.test(raw) || /\.[\w]+$/.test(raw);
  const dirPath = isFile ? raw.replace(/[\\/]+[^\\/]+$/, "") : raw;
  const r = await POST("/api/config/target", { path: dirPath });
  if (!r.ok) { flashHint("切换失败: " + (r.error || ""), "bad"); return; }
  const newRoot = r.data.target_root;
  rememberTarget(dirPath);
  setTargetUI(newRoot);
  flashHint("✅ 目标已切换: " + newRoot);
  await loadTree();
  if (isFile) {
    const fname = raw.replace(/\\/g, "/").split("/").pop();
    openFile(fname);   // 相对新目标根的 basename
  }
}
async function oneClickReview() {
  // 先确保审查对象已应用（目录切换）
  const raw = ($("target-input").value || "").trim();
  if (raw && raw.replace(/[\\/]+$/, "") !== (S.targetRoot || "").replace(/[\\/]+$/, "")) {
    await applyTarget();
  }
  switchView("canvas");
  await ensureDefaultPipeline(true);   // 强制重建，确保文件型原子路径适配当前目标
  // P0-2 运行前必填校验(默认图节点均带 path → 恒通过；防自定义缺参)
  const missOc = validateRequiredParams(S.canvas.nodes);
  if (missOc.length) { flashHint("一键审查拦截缺参: " + missOc[0].label + " 缺 " + missOc[0].missing.join(","), "bad"); return; }
  const r = await POST("/api/pipeline/run", { name: "一键审查-" + (S.targetRoot||"").split(/[\/]/).pop(), graph: S.canvas });
  if (!r.ok) { flashHint("一键审查启动失败: " + (r.error || ""), "bad"); return; }
  S.canvas.nodes.forEach((n) => { n.status = "running"; });
  drawCanvas();
  appendPipeLog(`▶ 一键审查已启动 ${r.data.run_id} — ${S.canvas.nodes.length} 节点，目标 ${S.targetRoot}`);
  flashHint("✅ 一键审查已启动，节点运行进度见画布/事件流");
}
/* 审查对象选择器接线：输入 → 切换目标 → 一键审查（运行默认编排图） */
$("btn-target-apply").addEventListener("click", applyTarget);
$("btn-review-now").addEventListener("click", oneClickReview);

/* ═══════════ 统一结果区（预设卡片的结果落 #run-log：进行中 / 成功 / 失败原因）═══════════ */
function runLogLine(label, cls, detail) {
  const box = $("run-log"); if (!box) return null;
  box.classList.remove("hidden");
  const el = document.createElement("div");
  el.className = "run-line " + cls;
  el.innerHTML = '<span class="rl-lbl"></span><span class="rl-t"></span>' + (detail ? '<pre class="rl-body"></pre>' : "");
  el.querySelector(".rl-lbl").textContent = label;
  el.querySelector(".rl-t").textContent = (cls === "busy" ? "进行中…  " : "") + new Date().toLocaleTimeString("zh-CN", { hour12: false });
  if (detail) el.querySelector(".rl-body").textContent = detail;
  box.insertBefore(el, box.firstChild);
  while (box.children.length > 10) box.removeChild(box.lastChild);
  return el;
}
function runLogUpdate(el, label, r) {
  if (!el) return;
  const ok = !!(r && r.ok);
  el.className = "run-line " + (ok ? "ok" : "err");
  el.querySelector(".rl-lbl").textContent = (ok ? "✅ " : "❌ ") + label;
  el.querySelector(".rl-t").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false })
    + (ok ? "" : "  失败原因：" + ((r && r.error) || "未知"));
  if (!ok) {
    const tip = document.createElement("pre");
    tip.className = "rl-body";
    tip.textContent = "提示：先确认顶部「审查对象」是你要处理的目标；需要单文件时先在文件树里选文件，再点动作。";
    el.appendChild(tip);
    return;
  }
  const payload = (r.data && r.data.result !== undefined) ? r.data.result : r.data;
  const pre = document.createElement("pre");
  pre.className = "rl-body";
  pre.textContent = JSON.stringify(payload, null, 1).slice(0, 1600);
  el.appendChild(pre);
}


/* ═══════════ 预设模式：点一下就跑（每张卡 = 一条真流程，不是单步动作）═══════════ */
function targetEntryFile() { return (S.targetRoot || ".").replace(/[\\/]+$/, "") + "/agent_runtime.py"; }
function mkNode(atom, cap, label, x, y, params) {
  const a = S.atomMap[atom] || {};
  const caps = a.provides || [];
  const p = Object.assign({}, params || {});
  // 交付报告(deliver.report)要求 outputs（缺则该节点 FAIL："能力 deliver.report 缺参数 outputs"）
  if (atom === "code-deliver" && p.outputs === undefined) p.outputs = {};
  return { id: uid("n"), atom, label, x, y, capability: caps.includes(cap) ? cap : (caps[0] || cap), params: p, status: null };
}
function chainEdges(nodes) {
  const e = [];
  for (let i = 0; i + 1 < nodes.length; i++) e.push({ from: nodes[i].id, to: nodes[i + 1].id });
  return e;
}
function graphVia(builder) {   // 复用既有图构造函数（它们直接写 S.canvas，这里临时换出来）
  const saved = S.canvas;
  S.canvas = { nodes: [], edges: [], loopEdges: [], selected: null, linkDraft: null, _defaultLoaded: false };
  builder();
  const g = { nodes: S.canvas.nodes, edges: S.canvas.edges, loopEdges: S.canvas.loopEdges, handLayout: true };
  S.canvas = saved;
  return g;
}
/* 自动布局：按最长路径分层，列=层（主干从左到右横排），同层并行原子竖着叠；列内超过 4 个拆子列 */
function layoutOrch(g) {
  const ns = g.nodes, es = g.edges, depth = {};
  ns.forEach((n) => (depth[n.id] = 0));
  for (let it = 0; it < ns.length; it++) {          // ponytail: O(n路e) 松弛，节点 ≤ 25 够用
    let ch = false;
    es.forEach((e) => { if (depth[e.from] !== undefined && depth[e.to] !== undefined && depth[e.to] < depth[e.from] + 1) { depth[e.to] = depth[e.from] + 1; ch = true; } });
    if (!ch) break;
  }
  const cols = {};
  ns.forEach((n) => { (cols[depth[n.id]] = cols[depth[n.id]] || []).push(n); });
  let x = 30;
  Object.keys(cols).map(Number).sort((a, b) => a - b).forEach((d) => {
    const list = cols[d], sub = Math.ceil(list.length / 4);
    list.forEach((n, j) => { n.x = x + 180 * Math.floor(j / 4); n.y = 40 + 110 * (j % 4); });
    x += 180 * sub;
  });
  return g;
}
/* 载入/运行前的最小补齐：缺「交付报告」就补一个（保证有产出）；连边/布局/循环回路在这里统一生成。 */
function ensureDeliver(g) {
  const nodes = g.nodes;
  const t = S.targetRoot || ".";
  let dv = nodes.find((n) => n.atom === "code-deliver");
  // 交付报告的 chain 一律按图自动算（避免与图脱节）
  if (dv) dv.params.chain = nodes.filter((n) => n !== dv).map((n) => n.capability);
  else {
    dv = mkNode("code-deliver", "deliver.report", "交付报告", 30, 30 + 110 * nodes.length, { path: t, chain: nodes.map((n) => n.capability) });
    nodes.push(dv);
  }
  g.edges = evChain(nodes);
  if (!g.handLayout) layoutOrch(g);
  // 循环要看得见：给循环节点画一条回到它目标的反馈回路（虚线）
  if (!g.loopEdges || !g.loopEdges.length) {
    const lp = nodes.find((n) => n.atom === "lab-loop");
    const tg = lp && nodes.find((n) => n.atom === ((lp.params || {}).target_atom || ""));
    if (lp && tg) g.loopEdges = [{ from: lp.id, to: tg.id, kind: "重试反馈" }];
  }
  return g;
}
/* 卡上印的步骤：从真实编排图取，保证卡面与图一致 */
function orchSteps(p) {
  try {
    const ns = ensureDeliver(p.graph("")).nodes;
    // 原子表未就绪时默认图可能被过滤成空 → 退回卡片自带的步骤文案
    if (ns.length >= 2) return ns.map((n) => n.label).join(" → ");
  } catch (e) {}
  return (p.steps || []).join(" → ");
}
/* 编排卡边：前导「扫描型」原子（连续 ≥2 个）真并行（各自喂给后继）；其余顺序；门控用 evidence 注入收齐上游 */
const SCAN_ATOMS = new Set(["code-review", "security-scan", "deadcode", "doc-freshness", "dep-scan", "arch-review", "method-impact", "dep-impact", "atomicity-audit", "domain-review", "localized", "ontology-review", "minimalist-style"]);
function evChain(ns) {
  const es = [];
  let head = 0;
  while (head < ns.length && SCAN_ATOMS.has(ns[head].atom)) head++;
  const par = head >= 2 ? ns.slice(0, head) : [];
  const line = ns.slice(par.length);
  const gi = line.findIndex((n) => n.cap === "harness.gate");
  par.forEach((h) => es.push({ from: h.id, to: line[0].id, map: "summary", input_name: (line[0] && line[0].cap === "harness.gate") ? "evidence" : "input" }));
  for (let i = 0; i < line.length - 1; i++) es.push({ from: line[i].id, to: line[i + 1].id, map: "summary", input_name: (i + 1 === gi) ? "evidence" : "input" });
  if (gi > 1) {
    for (let i = 0; i < gi - 1; i++) es.push({ from: line[i].id, to: line[gi].id, map: "summary", input_name: "evidence" });
    par.forEach((h) => es.push({ from: h.id, to: line[gi].id, map: "summary", input_name: "evidence" }));
  }
  return es;
}
/* 预设编排：每张卡 = 一套完整、典型的代码智能体编排（多步原子 + 控制/门控 + 交付）。
   steps 印在卡上：点▶开始跑的就是这套，点✎改图到画布上改的是同一套。 */
/* 旧的逐卡手写图（保留作参考，不再使用） */
const PRESETS_LEGACY = [
  { id: "audit_full", ic: "🧾", title: "全量代码审查",
    desc: "10 项检查 → 编排控制 → 门控 → 交付报告，一次跑完整个仓",
    steps: ["代码审查", "安全扫描", "单元测试", "方法级影响", "死代码", "文档新鲜度", "依赖SCA", "架构审查", "Git 状态", "项目级验收", "编排控制", "门控", "交付报告"],
    graph: () => graphVia(defaultReviewPipeline) },
  { id: "audit_fast", ic: "⚡", title: "快速体检",
    desc: "只看两样要紧的：代码审查 + 安全扫描，过门控出报告",
    steps: ["代码审查", "安全扫描", "门控", "交付报告"],
    graph: () => { const f = targetEntryFile();
      const n = [ mkNode("code-review", "codereview.review", "代码审查", 30, 30, { path: f }),
                  mkNode("security-scan", "security.scan", "安全扫描", 30, 150, { path: f }),
                  mkNode("lab-harness", "harness.gate", "门控:放行判定", 30, 270, { condition: "all_ok" }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 390, { path: f, chain: ["codereview.review", "security.scan"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "fix_bug", ic: "🐞", title: "修 bug / 修回归", ask: "要修的问题（现象 / 报错原文）",
    desc: "定方案 → 改代码 → 跑测试 → 失败自动重试 → 过审查 → 门控 → 交付",
    steps: ["定方案", "改代码", "跑测试", "循环:失败重试(2轮)", "过审查", "门控", "交付报告"],
    graph: (task) => { const f = targetEntryFile();
      const n = [ mkNode("code-plan", "plan.think", "定方案", 30, 30, { task, language: "python" }),
                  mkNode("code-implement", "code.implement", "改代码", 30, 150, { task }),
                  mkNode("code-test", "test.run", "跑测试", 30, 270, { path: f }),
                  mkNode("lab-loop", "loop.retry", "循环:失败重试", 30, 390, { target_atom: "code-test", target_cap: "test.run", attempts: 2, params: { path: f } }),
                  mkNode("code-review", "codereview.review", "过审查", 30, 510, { path: f }),
                  mkNode("lab-harness", "harness.gate", "门控:放行判定", 30, 630, { condition: "all_ok" }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 750, { path: f, chain: ["plan.think", "code.implement", "test.run", "loop.retry", "codereview.review"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "write_new", ic: "✍️", title: "写个新功能 / 小脚本", ask: "要实现什么（一句话说清）",
    desc: "定方案 → 写代码 → 生成测试 → 跑测试 → 过审查 → 门控 → 交付",
    steps: ["定方案", "写代码", "生成测试", "跑测试", "过审查", "门控", "交付报告"],
    graph: (task) => { const f = targetEntryFile();
      const n = [ mkNode("code-plan", "plan.think", "定方案", 30, 30, { task, language: "python" }),
                  mkNode("code-implement", "code.write", "写代码", 30, 150, { task }),
                  mkNode("code-test", "test.gen", "生成测试", 30, 270, { path: f }),
                  mkNode("code-test", "test.run", "跑测试", 30, 390, { path: f }),
                  mkNode("lab-loop", "loop.retry", "循环:失败重试", 30, 510, { target_atom: "code-test", target_cap: "test.run", attempts: 2, params: { path: f } }),
                  mkNode("code-review", "codereview.review", "过审查", 30, 630, { path: f }),
                  mkNode("lab-harness", "harness.gate", "门控:放行判定", 30, 750, { condition: "all_ok" }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 870, { path: f, chain: ["plan.gen", "code.write", "test.gen", "test.run", "loop.retry", "codereview.review"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "tests", ic: "🧪", title: "测试补强 / 覆盖率",
    desc: "看覆盖缺口 → 生成测试 → 跑测试 → 模糊测试 → 项目级验收 → 交付",
    steps: ["覆盖率分析", "生成测试", "跑测试", "模糊测试", "项目级验收", "交付报告"],
    graph: () => { const f = targetEntryFile(); const t = S.targetRoot || ".";
      const n = [ mkNode("code-test", "test.coverage_analysis", "覆盖率分析", 30, 30, { path: t }),
                  mkNode("code-test", "test.gen", "生成测试", 30, 150, { path: f }),
                  mkNode("code-test", "test.run", "跑测试", 30, 270, { path: f }),
                  mkNode("lab-loop", "loop.retry", "循环:失败重试", 30, 390, { target_atom: "code-test", target_cap: "test.run", attempts: 2, params: { path: f } }),
                  mkNode("code-fuzz", "fuzz.run", "模糊测试", 30, 510, { path: f }),
                  mkNode("code-test", "test.project", "项目级验收", 30, 630, { path: t }),
                  mkNode("lab-harness", "harness.gate", "门控:放行判定", 30, 750, { condition: "all_ok" }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 870, { path: t, chain: ["test.coverage_analysis", "test.gen", "test.run", "loop.retry", "fuzz.run", "test.project"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "impact", ic: "🧠", title: "影响面分析",
    desc: "方法级影响 + 依赖影响 + 死代码 → 编排控制 → 交付报告",
    steps: ["方法级影响", "依赖影响", "死代码", "编排控制", "交付报告"],
    graph: () => { const t = S.targetRoot || ".";
      const n = [ mkNode("method-impact", "impact.method", "方法级影响", 30, 30, { path: targetEntryFile() }),
                  mkNode("dep-impact", "impact.analyze", "依赖影响", 30, 150, { path: t }),
                  mkNode("deadcode", "deadcode.scan", "死代码", 30, 270, { path: t }),
                  mkNode("lab-harness", "harness.control", "编排控制", 30, 390, { mode: "seq", retries: 1, steps: [] }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 510, { path: t, chain: ["impact.method", "impact.analyze", "deadcode.scan"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "refactor", ic: "🧹", title: "重构 / 精简",
    desc: "极简风格 + 依赖精简 + 影响面 → 编排控制 → 门控 → 交付",
    steps: ["极简风格", "依赖精简", "方法级影响", "编排控制", "门控", "交付报告"],
    graph: () => { const f = targetEntryFile();
      const n = [ mkNode("minimalist-style", "minimal.style", "极简风格", 30, 30, { path: f }),
                  mkNode("minimalist-style", "minimal.deps", "依赖精简", 30, 150, { path: f }),
                  mkNode("method-impact", "impact.method", "方法级影响", 30, 270, { path: f }),
                  mkNode("lab-harness", "harness.control", "编排控制", 30, 390, { mode: "seq", retries: 1, steps: [] }),
                  mkNode("lab-harness", "harness.gate", "门控:放行判定", 30, 510, { condition: "all_ok" }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 630, { path: f, chain: ["minimal.style", "minimal.deps", "impact.method"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "guard", ic: "🛡", title: "质量闸门",
    desc: "安全/依赖/密钥/命令审批四道门，一个不过就不放行交付",
    steps: ["护栏门禁", "安全扫描", "依赖SCA", "密钥泄露扫描", "命令审批", "门控", "交付报告"],
    graph: () => { const f = targetEntryFile(); const t = S.targetRoot || ".";
      const n = [ mkNode("guard", "guard.check", "护栏门禁", 30, 30, { path: f }),
                  mkNode("security-scan", "security.scan", "安全扫描", 30, 150, { path: f }),
                  mkNode("dep-scan", "depscan.scan", "依赖SCA", 30, 270, { path: t }),
                  mkNode("secret-vault", "secrets.mask", "密钥泄露扫描", 30, 390, { path: f }),
                  mkNode("command-approvals", "approval.check", "命令审批", 30, 510, { path: f, cmd: "git status" }),
                  mkNode("lab-harness", "harness.gate", "门控:放行判定", 30, 630, { condition: "all_ok" }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 750, { path: t, chain: ["guard.check", "security.scan", "depscan.scan", "secrets.mask", "approval.check"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "deliver", ic: "📦", title: "交付验收",
    desc: "跑测试 → 项目级验收 → 门控 → 出一份交付报告",
    steps: ["跑测试", "项目级验收", "门控", "交付报告"],
    graph: () => { const t = S.targetRoot || ".";
      const n = [ mkNode("code-test", "test.run", "跑测试", 30, 30, { path: t }),
                  mkNode("code-test", "test.project", "项目级验收", 30, 150, { path: t }),
                  mkNode("lab-harness", "harness.gate", "门控:放行判定", 30, 270, { condition: "all_ok" }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 390, { path: t, chain: ["test.run", "test.project"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "chain", ic: "🔗", title: "按任务自动选链", ask: "任务描述（例：给 utils.py 加一个去重函数）",
    desc: "派单编排先按任务自动挑能力序列，再走门控交付",
    steps: ["派单编排(自动选链)", "门控", "交付报告"],
    graph: (task) => { const f = targetEntryFile();
      const n = [ mkNode("code-dispatch", "dispatch.chain_select", "派单编排", 30, 30, { task, path: f }),
                  mkNode("lab-harness", "harness.gate", "门控:放行判定", 30, 150, { condition: "all_ok" }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 270, { path: f, chain: ["dispatch.chain_select"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "deliver_trail", ic: "🧾", title: "交付验收 · 事件留痕",
    desc: "跑测试 → 项目级验收 → 事件账本留痕 → 门控 → 出一份交付报告",
    steps: ["跑测试", "项目级验收", "事件账本检查点", "门控", "交付报告"],
    graph: () => { const t = S.targetRoot || ".";
      const n = [ mkNode("code-test", "test.run", "跑测试", 30, 30, { path: t }),
                  mkNode("code-test", "test.project", "项目级验收", 30, 150, { path: t }),
                  mkNode("event-log", "event.checkpoint", "事件账本检查点", 30, 270, { path: t }),
                  mkNode("lab-harness", "harness.gate", "门控:放行判定", 30, 390, { condition: "all_ok" }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 510, { path: t, chain: ["test.run", "test.project", "event.checkpoint"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "bug_root", ic: "🐛", title: "深挖 Bug 根因", ask: "现象 / 报错原文（越具体越好）",
    desc: "深挖根因 → 迭代修复 → 回归测试 → 过审查 → 交付",
    steps: ["深挖根因", "迭代修复", "回归测试", "过审查", "交付报告"],
    graph: (task) => { const f = targetEntryFile();
      const n = [ mkNode("bug-deep", "bugdeep.adv", "深挖根因", 30, 30, { task, path: f }),
                  mkNode("code-runloop", "code.runloop", "迭代修复", 30, 150, { task, path: f }),
                  mkNode("code-test", "test.run", "回归测试", 30, 270, { path: f }),
                  mkNode("lab-loop", "loop.retry", "循环:失败重试", 30, 390, { target_atom: "code-test", target_cap: "test.run", attempts: 2, params: { path: f } }),
                  mkNode("code-review", "codereview.review", "过审查", 30, 510, { path: f }),
                  mkNode("lab-harness", "harness.gate", "门控:放行判定", 30, 630, { condition: "all_ok" }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 750, { path: f, chain: ["bugdeep.adv", "code.runloop", "test.run", "loop.retry", "codereview.review"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "evolve", ic: "🧬", title: "自我进化 · 技能沉淀", task: "从这次任务里提炼可复用经验",
    desc: "自进化提炼 → 技能沉淀 → 记忆入库 → 交付（越用越强）",
    steps: ["自进化提炼", "技能沉淀", "记忆入库", "交付报告"],
    graph: () => { const t = S.targetRoot || ".";
      const n = [ mkNode("code-evolve", "evolve.refine", "自进化提炼", 30, 30, { path: t, task: "从最近任务里提炼可复用规则" }),
                  mkNode("code-skill", "skill.sediment", "技能沉淀", 30, 150, { task: "把本次可复用能力沉淀成技能" }),
                  mkNode("code-memory", "memory.sediment", "记忆入库", 30, 270, { path: t, task: "沉淀到记忆库" }),
                  mkNode("lab-harness", "harness.control", "编排控制", 30, 390, { mode: "seq", retries: 1, steps: [] }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 510, { path: t, chain: ["evolve.refine", "skill.sediment", "memory.sediment"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "tools", ic: "🔌", title: "外部工具 · 模型分工",
    desc: "MCP 工具清单 → 可用模型清单 → 模型候选/降级 → 编排控制 → 交付",
    steps: ["MCP 能力清单", "模型清单", "模型候选/降级", "编排控制", "交付报告"],
    graph: () => { const t = S.targetRoot || ".";
      const n = [ mkNode("mcp-client", "mcp.list", "MCP 能力清单", 30, 30, {}),
                  mkNode("llm-router", "llm.list_models", "模型清单", 30, 150, {}),
                  mkNode("model-fallback", "model.candidates", "模型候选/降级", 30, 270, { task: "按任务挑主模型与降级链" }),
                  mkNode("lab-harness", "harness.control", "编排控制", 30, 390, { mode: "seq", retries: 1, steps: [] }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 510, { path: t, chain: ["mcp.list", "llm.list_models", "model.candidates"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "smoke", ic: "🖥", title: "前端冒烟验收",
    desc: "浏览器逐路由真跑 → 沙箱校验 → 门控 → 交付（前端改动后跑这个）",
    steps: ["浏览器冒烟", "沙箱校验", "门控", "交付报告"],
    graph: () => { const t = S.targetRoot || ".";
      const n = [ mkNode("browser-smoke", "browsersmoke.run", "浏览器冒烟", 30, 30, { path: t }),
                  mkNode("process-sandbox", "sandbox.validate", "沙箱校验", 30, 150, { path: t }),
                  mkNode("lab-harness", "harness.gate", "门控:放行判定", 30, 270, { condition: "all_ok" }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 390, { path: t, chain: ["browsersmoke.run", "sandbox.validate"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "resume", ic: "🗂", title: "长任务 · 跨会话续跑",
    desc: "会话状态 → 任务状态 → 上下文压缩 → 交付（关机不断线）",
    steps: ["会话状态", "任务状态", "上下文压缩", "交付报告"],
    graph: () => { const t = S.targetRoot || ".";
      const n = [ mkNode("session", "session.status", "会话状态", 30, 30, {}),
                  mkNode("task-state", "taskstate.track", "任务状态", 30, 150, { task: "跟踪当前长任务进度" }),
                  mkNode("context-compact", "context.budget", "上下文压缩", 30, 270, { path: t }),
                  mkNode("lab-harness", "harness.control", "编排控制", 30, 390, { mode: "seq", retries: 1, steps: [] }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 510, { path: t, chain: ["session.status", "taskstate.track", "context.budget"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
  { id: "onboard", ic: "🧭", title: "上手新项目",
    desc: "项目骨架扫描 → 分层架构 → 依赖影响 + 可复用既有实现 → 交付",
    steps: ["项目骨架扫描", "分层架构", "依赖影响", "复用建议", "交付报告"],
    graph: () => { const t = S.targetRoot || ".";
      const n = [ mkNode("code-project", "project.scan", "项目骨架扫描", 30, 30, { path: t }),
                  mkNode("arch-review", "archreview.layers", "分层架构", 30, 150, { path: t }),
                  mkNode("dep-impact", "impact.analyze", "依赖影响", 30, 270, { path: t }),
                  mkNode("code-reuse", "reuse.local", "复用建议", 30, 390, { path: t }),
                  mkNode("lab-harness", "harness.control", "编排控制", 30, 510, { mode: "seq", retries: 1, steps: [] }),
                  mkNode("code-deliver", "deliver.report", "交付报告", 30, 630, { path: t, chain: ["project.scan", "archreview.layers", "impact.analyze", "reuse.local"] }) ];
      return { nodes: n, edges: evChain(n), loopEdges: [] }; } },
];
/* ── 用「阶段」描述编排：单元素=串行一步；数组=一组并行原子（与下一步全连，形成真并行分支）── */
function stageGraph(groups) {
  const nodes = [], edges = [], rows = [];
  groups.forEach((g) => {
    const items = (Array.isArray(g) && Array.isArray(g[0])) ? g : [g];
    const row = items.map((it) => { const n = mkNode(it[0], it[1], it[2], 0, 0, it[3] || {}); nodes.push(n); return n; });
    rows.push(row);
  });
  for (let i = 0; i < rows.length - 1; i++) {
    rows[i].forEach((a) => rows[i + 1].forEach((b) => edges.push({ from: a.id, to: b.id, map: "summary", input_name: (b.capability === "harness.gate") ? "evidence" : "input" })));
  }
  const gi = rows.findIndex((r) => r.some((n) => n.capability === "harness.gate"));
  if (gi > 0) {
    const gate = rows[gi].find((n) => n.capability === "harness.gate");
    for (let i = 0; i < gi; i++) rows[i].forEach((n) => { if (!edges.some((e) => e.from === n.id && e.to === gate.id)) edges.push({ from: n.id, to: gate.id, map: "summary", input_name: "evidence" }); });
  }
  return { nodes, edges, loopEdges: [] };
}
/* 预设编排（统一用阶段描述，每套都是多阶段完整编排：并行分支 + 按需循环/门控 + 交付） */
const PRESETS = [
  { id: "audit_full", ic: "🧾", title: "全量代码审查",
    desc: "14 项检查并行扫 → 编排控制 → 门控 → 双循环 → 交付",
    graph: () => graphVia(defaultReviewPipeline) },
  { id: "audit_fast", ic: "⚡", title: "快速体检",
    desc: "四项检查并行（审查/安全/死代码/文档） → 门控 → 交付",
    graph: () => { const f = targetEntryFile(); const t = S.targetRoot || "."; return stageGraph([
      [["code-review", "codereview.review", "代码审查", { path: f }], ["security-scan", "security.scan", "安全扫描", { path: f }], ["deadcode", "deadcode.scan", "死代码", { path: t }], ["doc-freshness", "doc.stale", "文档新鲜度", { path: t, root: t }]],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: f }]]); } },
  { id: "fix_bug", ic: "🐞", title: "修 bug / 修回归", ask: "要修的问题（现象 / 报错原文）",
    desc: "深挖根因 → 影响面+依赖并行 → 定方案 → 改代码 → 跑测试 → 循环重试 → 审查+安全并行 → 门控 → 交付",
    graph: (task) => { const f = targetEntryFile(); const t = S.targetRoot || "."; return stageGraph([
      ["bug-deep", "bugdeep.adv", "深挖根因", { task, path: f }],
      [["method-impact", "impact.method", "影响面", { path: t }], ["dep-scan", "depscan.scan", "依赖检查", { path: t }]],
      ["code-plan", "plan.think", "定方案", { task, language: "python" }],
      ["code-implement", "code.implement", "改代码", { task, path: f }],
      ["code-test", "test.run", "跑测试", { path: f }],
      ["lab-loop", "loop.retry", "循环:失败重试", { target_atom: "code-test", target_cap: "test.run", attempts: 2, params: { path: f } }],
      [["code-review", "codereview.review", "过审查", { path: f }], ["security-scan", "security.scan", "安全扫描", { path: f }]],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "write_new", ic: "✍️", title: "写个新功能 / 小脚本", ask: "要实现什么（一句话说清）",
    desc: "项目骨架+查可复用并行 → 定方案 → 写代码 → 生成测试 → 跑测试 → 循环重试 → 审查+安全并行 → 门控 → 交付",
    graph: (task) => { const f = targetEntryFile(); const t = S.targetRoot || "."; return stageGraph([
      [["code-project", "project.scan", "项目骨架", { path: t }], ["code-reuse", "reuse.local", "查可复用", { path: t }]],
      ["code-plan", "plan.think", "定方案", { task, language: "python" }],
      ["code-implement", "code.write", "写代码", { task, path: f }],
      ["code-test", "test.gen", "生成测试", { path: f }],
      ["code-test", "test.run", "跑测试", { path: f }],
      ["lab-loop", "loop.retry", "循环:失败重试", { target_atom: "code-test", target_cap: "test.run", attempts: 2, params: { path: f } }],
      [["code-review", "codereview.review", "过审查", { path: f }], ["security-scan", "security.scan", "安全扫描", { path: f }]],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "tests", ic: "🧪", title: "测试补强 / 覆盖率",
    desc: "覆盖率+死代码并行 → 生成测试 → 跑测试 → 循环重试 → 模糊测试 → 项目级验收 → 门控 → 交付",
    graph: () => { const f = targetEntryFile(); const t = S.targetRoot || "."; return stageGraph([
      [["code-test", "test.coverage_analysis", "覆盖率分析", { path: t }], ["deadcode", "deadcode.scan", "死代码", { path: t }]],
      ["code-test", "test.gen", "生成测试", { path: f }],
      ["code-test", "test.run", "跑测试", { path: f }],
      ["lab-loop", "loop.retry", "循环:失败重试", { target_atom: "code-test", target_cap: "test.run", attempts: 2, params: { path: f } }],
      ["code-fuzz", "fuzz.run", "模糊测试", { path: f }],
      ["code-test", "test.project", "项目级验收", { path: t }],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "impact", ic: "🧠", title: "影响面分析",
    desc: "方法级影响 + 依赖影响 + 死代码 + 可复用 并行 → 编排控制 → 门控 → 交付",
    graph: () => { const t = S.targetRoot || "."; return stageGraph([
      [["method-impact", "impact.method", "方法级影响", { path: t }], ["dep-impact", "impact.analyze", "依赖影响", { path: t }], ["deadcode", "deadcode.scan", "死代码", { path: t }], ["code-reuse", "reuse.local", "可复用", { path: t }]],
      ["lab-harness", "harness.control", "编排控制", { mode: "seq", retries: 1, steps: [] }],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "refactor", ic: "🧹", title: "重构 / 精简", ask: "重构目标（哪一块、想达到什么效果）",
    desc: "四项检查并行 → 重构方案 → 重构改写 → 回归测试 → 循环重试 → 过审查 → 门控 → 交付",
    graph: (task) => { const f = targetEntryFile(); const t = S.targetRoot || "."; return stageGraph([
      [["minimalist-style", "minimal.style", "极简风格", { path: f }], ["minimalist-style", "minimal.deps", "依赖精简", { path: f }], ["method-impact", "impact.method", "方法级影响", { path: t }], ["deadcode", "deadcode.scan", "死代码", { path: t }]],
      ["code-plan", "plan.think", "重构方案", { task, language: "python" }],
      ["code-implement", "code.implement", "重构改写", { task, path: f }],
      ["code-test", "test.run", "回归测试", { path: f }],
      ["lab-loop", "loop.retry", "循环:失败重试", { target_atom: "code-test", target_cap: "test.run", attempts: 2, params: { path: f } }],
      ["code-review", "codereview.review", "过审查", { path: f }],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "guard", ic: "🛡", title: "质量闸门",
    desc: "护栏+安全+依赖+密钥+命令审批 五项并行 → 门控 → 交付（一个不过就不放行）",
    graph: () => { const f = targetEntryFile(); const t = S.targetRoot || "."; return stageGraph([
      [["guard", "guard.check", "护栏门禁", { path: f }], ["security-scan", "security.scan", "安全扫描", { path: f }], ["dep-scan", "depscan.scan", "依赖SCA", { path: t }], ["secret-vault", "secrets.mask", "密钥泄露扫描", { path: f }], ["command-approvals", "approval.check", "命令审批", { path: f, cmd: "git status" }]],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "deliver", ic: "📦", title: "交付验收 · 事件留痕",
    desc: "跑测试+安全并行 → 项目级验收 → 事件账本留痕 → 门控 → 交付",
    graph: () => { const t = S.targetRoot || "."; return stageGraph([
      [["code-test", "test.run", "跑测试", { path: t }], ["security-scan", "security.scan", "安全扫描", { path: targetEntryFile() }]],
      ["code-test", "test.project", "项目级验收", { path: t }],
      ["event-log", "event.checkpoint", "事件账本检查点", { path: t }],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "chain", ic: "🔗", title: "按任务自动选链", ask: "任务描述（例：给 utils.py 加一个去重函数）",
    desc: "上下文预算 + 方法级影响 + 派单自动选链 并行 → 编排控制 → 门控 → 交付",
    graph: (task) => { const f = targetEntryFile(); const t = S.targetRoot || "."; return stageGraph([
      [["context-compact", "context.budget", "上下文预算", { path: t }], ["method-impact", "impact.method", "方法级影响", { path: t }], ["code-dispatch", "dispatch.chain_select", "派单自动选链", { task, path: f }]],
      ["lab-harness", "harness.control", "编排控制", { mode: "seq", retries: 1, steps: [] }],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "bug_root", ic: "🐛", title: "深挖 Bug 根因", ask: "现象 / 报错原文（越具体越好）",
    desc: "深挖根因 → 复现PoC+沙箱验证并行 → 迭代修复 → 回归测试 → 循环重试 → 审查+安全并行 → 门控 → 交付",
    graph: (task) => { const f = targetEntryFile(); const t = S.targetRoot || "."; return stageGraph([
      ["bug-deep", "bugdeep.adv", "深挖根因", { task, path: f }],
      [["bug-deep", "bugdeep.poc", "复现 PoC", { task, path: f }], ["process-sandbox", "sandbox.validate", "沙箱验证", { path: f }]],
      ["code-runloop", "code.runloop", "迭代修复", { task, path: f }],
      ["code-test", "test.run", "回归测试", { path: f }],
      ["lab-loop", "loop.retry", "循环:失败重试", { target_atom: "code-test", target_cap: "test.run", attempts: 2, params: { path: f } }],
      [["code-review", "codereview.review", "过审查", { path: f }], ["security-scan", "security.scan", "安全扫描", { path: f }]],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "evolve", ic: "🧬", title: "自我进化 · 技能沉淀",
    desc: "自进化提炼 + 记忆回顾 并行 → 技能沉淀 → 记忆入库 → 编排控制 → 交付",
    graph: () => { const t = S.targetRoot || "."; return stageGraph([
      [["code-evolve", "evolve.refine", "自进化提炼", { path: t, task: "从最近任务里提炼可复用规则" }], ["code-memory", "memory.recall", "记忆回顾", { path: t }]],
      ["code-skill", "skill.sediment", "技能沉淀", { task: "把本次可复用能力沉淀成技能" }],
      ["code-memory", "memory.sediment", "记忆入库", { path: t, task: "沉淀到记忆库" }],
      ["lab-harness", "harness.control", "编排控制", { mode: "seq", retries: 1, steps: [] }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "tools", ic: "🔌", title: "外部工具 · 模型分工",
    desc: "MCP 清单 + 模型清单 + 降级候选 + 上下文预算 并行 → 编排控制 → 门控 → 交付",
    graph: () => { const t = S.targetRoot || "."; return stageGraph([
      [["mcp-client", "mcp.list", "MCP 能力清单", {}], ["llm-router", "llm.list_models", "模型清单", {}], ["model-fallback", "model.candidates", "降级候选", { task: "按任务挑主模型与降级链" }], ["context-compact", "context.budget", "上下文预算", { path: t }]],
      ["lab-harness", "harness.control", "编排控制", { mode: "seq", retries: 1, steps: [] }],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "smoke", ic: "🖥", title: "前端冒烟验收",
    desc: "浏览器冒烟 + 单元测试 并行 → 沙箱校验 → 事件留痕 → 门控 → 交付",
    graph: () => { const t = S.targetRoot || "."; return stageGraph([
      [["browser-smoke", "browsersmoke.run", "浏览器冒烟", { url: (location.protocol + "//" + location.host), routes: JSON.stringify([{ path: "/", expectText: ["CodeAgent Lab"] }, { path: "/no-such-route-zzz", expectText: ["这个页面不存在"] }]), wait_sec: "8", min_body: "40" }], ["code-test", "test.run", "单元测试", { path: t }]],
      ["process-sandbox", "sandbox.validate", "沙箱校验", { path: t }],
      ["event-log", "event.checkpoint", "事件留痕", { path: t }],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "resume", ic: "🗂", title: "长任务 · 跨会话续跑",
    desc: "会话状态 + 任务状态 并行 → 上下文压缩 → 事件检查点 → 编排控制 → 交付",
    graph: () => { const t = S.targetRoot || "."; return stageGraph([
      [["session", "session.status", "会话状态", {}], ["task-state", "taskstate.track", "任务状态", { task: "跟踪当前长任务进度" }]],
      ["context-compact", "context.budget", "上下文压缩", { path: t }],
      ["event-log", "event.checkpoint", "事件检查点", { path: t }],
      ["lab-harness", "harness.control", "编排控制", { mode: "seq", retries: 1, steps: [] }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
  { id: "onboard", ic: "🧭", title: "上手新项目",
    desc: "六项侦察并行（骨架/分层/依赖/可复用/本地化/本体） → 编排控制 → 门控 → 交付",
    graph: () => { const t = S.targetRoot || "."; return stageGraph([
      [["code-project", "project.scan", "项目骨架", { path: t }], ["arch-review", "archreview.layers", "分层架构", { path: t }], ["dep-impact", "impact.analyze", "依赖影响", { path: t }], ["code-reuse", "reuse.local", "可复用", { path: t }], ["localized", "local.audit", "本地化", { path: t }], ["ontology-review", "ontology.quality", "本体审查", { path: t }]],
      ["lab-harness", "harness.control", "编排控制", { mode: "seq", retries: 1, steps: [] }],
      ["lab-harness", "harness.gate", "门控:放行判定", { condition: "all_ok" }],
      ["code-deliver", "deliver.report", "交付报告", { path: t }]]); } },
];
function renderPresets() {
  const box = $("preset-cards"); if (!box || box.dataset.done) return;
  box.dataset.done = "1";
  box.innerHTML = PRESETS.map((p, i) =>
    '<div class="pcard' + (p.dashed ? " dashed" : "") + '">' +
      '<div class="pt"><span class="ic">' + p.ic + '</span>' + esc(p.title) + '</div>' +
      '<div class="pd">' + esc(p.desc) + '</div>' +
      '<div class="psteps">' + esc(orchSteps(p)) + '</div>' +
      '<div class="pa"><button class="go" data-run="' + i + '">' + (p.gotoLabel || "▶ 开始") + '</button>' +
      (p.graph ? '<button data-edit="' + i + '">✎ 改图</button>' : "") +
      '<span class="tag" data-last="' + p.id + '">未跑过</span></div></div>').join("");
  box.querySelectorAll("[data-run]").forEach((b) => b.addEventListener("click", () => startPreset(PRESETS[+b.dataset.run])));
  box.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => loadPresetToCanvas(PRESETS[+b.dataset.edit])));
  refreshPresetHistory();
  renderPresetProgress();
}
async function startPreset(p) {
  if (p.goto) { switchView(p.goto); if (p.id === "debug_loop") flashHint("填好目标文件与报错描述，再点「▶ 启动调试回路」"); return; }
  const extra = {};
  if (p.ask) {
    const v = window.prompt(p.ask, "");
    if (!v) { flashHint("已取消：先写清要做什么再来"); return; }
    if (p.action) extra.task = v;
  }
  if (p.action) {
    const el = runLogLine(p.title + " @ " + (S.targetRoot || "."), "busy");
    const r = await POST("/api/action", Object.assign({}, p.action, extra));
    runLogUpdate(el, p.title, r); refreshPresetHistory(); return;
  }
  const g = ensureDeliver(p.graph(extra.task));
  const miss = validateRequiredParams(g.nodes);
  if (miss.length) { flashHint("这张图还缺参数：" + miss[0].label + " 缺 " + miss[0].missing.join(","), "bad"); return; }
  const el = runLogLine("▶ " + p.title + " @ " + (S.targetRoot || "."), "busy");
  const r = await POST("/api/pipeline/run", { name: p.title, graph: g });
  if (!r.ok) { runLogUpdate(el, p.title, { ok: false, error: r.error || "启动失败" }); return; }
  S.presetRun = { title: p.title, nodeIds: g.nodes.map((n) => n.id), labels: {}, el };
  g.nodes.forEach((n) => { S.presetRun.labels[n.id] = n.label || n.atom; });
  S.presetStatus = {}; g.nodes.forEach((n) => { S.presetStatus[n.id] = "todo"; });
  renderPresetProgress();
  const b = $("run-log"); if (b) b.classList.remove("hidden");
}
/* 画布上挑编排：10 套预设编排 + 空白自搭。切换 = 把那一套铺到画布上（改的就是同一套）。 */
function renderOrchPicker() {
  const sel = $("orch-pick"); if (!sel || sel.dataset.done) return;
  sel.dataset.done = "1";
  sel.innerHTML = PRESETS.filter((p) => p.graph).map((p) =>
    '<option value="' + p.id + '">' + esc(p.ic + " " + p.title + "（" + orchSteps(p).split(" → ").length + " 步）") + '</option>').join("") +
    '<option value="__blank">空白 · 从零自己搭</option>';
  sel.addEventListener("change", () => {
    const v = sel.value;
    if (v === "__blank") {
      S.canvas.nodes = []; S.canvas.edges = []; S.canvas.loopEdges = []; S.canvas.selected = null;
      S.canvas._defaultLoaded = true; S.canvas._blank = true;
      renderCanvas(); renderProps();
      const lg = $("pipe-log"); if (lg) lg.innerHTML = '<div>[📋] 已清空：从左侧点原子开始搭（点一下即入编排，会自动串起来）。</div>';
      return;
    }
    const p = PRESETS.find((x) => x.id === v);
    if (!p) return;
    try { loadPresetToCanvas(p, true); }
    catch (e) {
      // 不静默失败：把真实原因显示出来，画布保持原样
      const msg = String((e && e.message) || e);
      flashHint("载入这套编排失败：" + msg, "bad");
      const lg = $("pipe-log");
      if (lg) { lg.classList.remove("hidden"); lg.innerHTML = '<div>[❌] 载入「' + esc(p.title) + '」失败：' + esc(msg) + '（画布未改动）</div>'; }
    }
  });
  syncOrchPicker();
}
function syncOrchPicker() {
  const sel = $("orch-pick"); if (!sel) return;
  const first = S.canvas.nodes[0];
  let hit = null;
  if (first) for (const p of PRESETS) {
    if (!p.graph) continue;
    let g = null;
    try { g = p.graph(""); } catch (e) { continue; }   // 单张卡构图失败不影响选择器
    if (g && g.nodes.length === S.canvas.nodes.length && g.nodes[0].atom === first.atom) { hit = p; break; }
  }
  sel.value = hit ? hit.id : "__blank";
}
function loadPresetToCanvas(p, keepView) {
  if (!p.graph) return;
  const g = ensureDeliver(p.graph(null));
  S.canvas.nodes = g.nodes; S.canvas.edges = g.edges; S.canvas.loopEdges = g.loopEdges || [];
  S.canvas.selected = null; S.canvas._defaultLoaded = true; S.canvas._blank = false;
  if ($("pipe-name")) $("pipe-name").value = p.title;   // 运行名跟随所选编排
  if (!keepView) switchView("canvas"); else syncOrchPicker();
  renderCanvas(); renderProps();
  const lg = $("pipe-log");
  if (lg) lg.innerHTML = '<div>[📋] 已把「' + esc(p.title) + '」的 ' + g.nodes.length + ' 个节点铺到画布 — 改完点「▶ 运行管道」。</div>';
  flashHint("已铺到画布，可以拖拽改了");
}
function renderPresetProgress() {
  const box = $("preset-progress"); if (!box) return;
  if (!S.presetRun) { box.innerHTML = '<div class="placeholder">还没有在跑的任务<br>点左边任意一张卡片就开始</div>'; return; }
  const ids = S.presetRun.nodeIds, st = S.presetStatus || {};
  const done = ids.filter((i) => st[i] === "done" || st[i] === "fail").length;
  const pct = Math.round((done / Math.max(1, ids.length)) * 100);
  box.innerHTML = '<div class="pbar"><i style="width:' + pct + '%"></i></div>' + ids.map((i) => {
    const s = st[i] || "todo";
    const ic = s === "done" ? "✔" : s === "fail" ? "✖" : s === "run" ? "⏳" : "○";
    return '<div class="ps ' + s + '"><span class="pst">' + ic + '</span>' + esc(S.presetRun.labels[i] || i) + '</div>';
  }).join("");
  const t = $("preset-run-title"); if (t) t.textContent = "第 " + done + " / " + ids.length + " 步";
}
function presetEvent(e) {
  const p = e.payload || {};
  const st = S.presetStatus || {};
  if (e.type === "pipeline.node_started" && p.node in st) { st[p.node] = "run"; renderPresetProgress(); return; }
  if (e.type === "pipeline.node_done" && p.node in st) { st[p.node] = "done"; renderPresetProgress(); return; }
  if (e.type === "pipeline.node_failed" && p.node in st) { st[p.node] = "fail"; renderPresetProgress(); return; }
  if ((e.type === "pipeline.finished" || e.type === "pipeline.failed") && S.presetRun) {
    const title = S.presetRun.title;
    if (S.presetRun.el && S.presetRun.el.parentNode) S.presetRun.el.parentNode.removeChild(S.presetRun.el);
    const failed = Object.keys(st).filter((k) => st[k] === "fail");
    if (e.type === "pipeline.failed" || failed.length) {
      runLogLine("❌ " + title + " — 有步骤没跑成" + (p.error ? "：" + String(p.error).slice(0, 140) : ""), "err");
    } else {
      runLogLine("✅ " + title + " 跑完了（共 " + Object.keys(st).length + " 步）", "ok");
    }
    const b = $("run-log"); if (b) b.classList.remove("hidden");
    refreshPresetHistory();
  }
}
async function refreshPresetHistory() {
  const box = $("preset-history");
  const r = await GET("/api/pipelines"); if (!r.ok) return;
  const runs = (r.data && r.data.runs) || [];
  const byName = {};
  runs.forEach((x) => { if (!(x.name in byName)) byName[x.name] = x; });
  document.querySelectorAll("#preset-cards [data-last]").forEach((el) => {
    const p = PRESETS.filter((q) => q.id === el.dataset.last)[0]; if (!p) return;
    const hit = byName[p.title];
    if (!hit) { el.textContent = "未跑过"; el.className = "tag"; return; }
    el.textContent = hit.ok ? "上次 ✓ 通过" : "上次 ✗ 失败";
    el.className = "tag" + (hit.ok ? " ok" : "");
  });
  if (box) box.innerHTML = runs.slice(0, 7).map((x) =>
    '<div style="font-size:11.5px;line-height:1.8;color:var(--muted)">' + (x.ok ? "✅" : "❌") + " " +
    esc(String(x.name || "").slice(0, 24)) + ' <span style="color:#94a3b8">#' + x.id + "</span></div>").join("") ||
    '<div class="placeholder">还没有历史</div>';
}
$("btn-tools") && $("btn-tools").addEventListener("click", (ev) => {
  ev.stopPropagation();
  $("tools-list").classList.toggle("hidden");
});
$("tools-list") && $("tools-list").addEventListener("click", () => $("tools-list").classList.add("hidden"));
document.addEventListener("click", (ev) => {
  const l = $("tools-list");
  if (l && !l.classList.contains("hidden") && !ev.target.closest("#tools-wrap")) l.classList.add("hidden");
});

/* ═══════════ 浏览器原生文件/目录选择器（现代软件体验：点"浏览/选目录/选文件"弹系统对话框）═══
   沙箱约束：<input type=file> 只给 basename、<input type=file webkitdirectory> 只给目录名/相对结构，
   不暴露绝对路径。选中的结果统一交给后端 /api/fs/locate 在 路径边界白名单/当前目标仓库内 有界解析
   回真实相对/绝对路径，再填入对应输入框。 */
let PICK = { dest: "target", kind: "file" };
const pickFor = (dest, kind) => { PICK.dest = dest; PICK.kind = kind; };
function triggerPicker(input){ input.value = ""; input.click(); }
function setPickTargets(){
  const on = (id, fn) => { const el = $(id); if (el) el.addEventListener("click", fn); };
  on("btn-browse-target-dir", () => { pickFor("target", "dir"); triggerPicker($("pick-dir")); });
  on("btn-browse-target-file", () => { pickFor("target", "file"); triggerPicker($("pick-file")); });
  on("btn-browse-dbg", () => { pickFor("dbg", "file"); triggerPicker($("pick-file")); });
  on("btn-browse-rep-dir", () => { pickFor("rep", "dir"); triggerPicker($("pick-dir")); });
  on("btn-browse-rep-file", () => { pickFor("rep", "file"); triggerPicker($("pick-file")); });
  const pf = $("pick-file"); if (pf) pf.addEventListener("change", async () => {
    const f = pf.files && pf.files[0];
    if (!f) return;
    await applyPicked({ kind: "file", name: f.name });
  });
  const pd = $("pick-dir"); if (pd) pd.addEventListener("change", async () => {
    const f = pd.files && pd.files[0];
    if (!f) return;
    const rp = f.webkitRelativePath || "";
    const folder = rp.split("/")[0] || "";
    await applyPicked({ kind: "dir", name: folder || f.name });
  });
}
async function applyPicked(p){
  const dest = PICK.dest || "target";
  if (p.kind === "dir") {
    if (!p.name) { flashHint("未获取到所选目录名，请重试", "bad"); return; }
    const r = await POST("/api/fs/locate", { mode: "dir", name: p.name });
    if (!r.ok) { flashHint("目录定位失败: " + (r.error || ""), "bad"); return; }
    const d = r.data;
    if (d.ambiguous || !d.abs) {
      setTargetUI(p.name);
      flashHint("发现多个同名目录，未自动切换。候选: " + (d.candidates || []).slice(0, 3).join(" / ") + " — 请直接填完整路径", "bad");
      return;
    }
    if (dest === "target") {
      setTargetUI(d.abs);
      flashHint("✅ 已从系统选择器填入目录: " + d.abs + "（点『切换目标』应用）");
    } else { // rep 报告目标目录：转 target 内相对路径
      const tgt = (S.targetRoot || "").replace(/\\/g, "/").replace(/\/+$/, "");
      const ab = d.abs.replace(/\\/g, "/").replace(/\/+$/, "");
      if (tgt && (ab === tgt || ab.startsWith(tgt + "/"))) {
        const rel = ab.slice(tgt.length).replace(/^\/+/, "") || ".";
        $("rep-target").value = rel;
        flash("✅ 已填入报告目标目录相对路径: " + rel);
      } else {
        $("rep-target").value = p.name;
        flash("所选目录不在当前审查对象内，已填入目录名（请填 target 内相对路径）");
      }
    }
  } else { // file
    if (!p.name) return;
    const r = await POST("/api/fs/locate", { mode: "file", name: p.name });
    if (!r.ok) {
      if (dest === "target") flashHint("审查对象内未找到该文件: " + (r.error || ""), "bad");
      else flash("审查对象内未找到该文件: " + (r.error || ""));
      return;
    }
    const d = r.data;
    if (dest === "target") {
      setTargetUI(d.abs);   // 填入文件绝对路径，点『切换目标』后其父目录成为审查对象
      flashHint("✅ 已填入目标文件绝对路径: " + d.abs + "（点『切换目标』应用）");
    } else if (dest === "dbg") {
      $("dbg-file").value = d.rel;
      flash("✅ 已填入调试目标文件相对路径: " + d.rel);
    } else { // rep
      $("rep-target").value = d.rel;
      flash("✅ 已填入报告目标相对路径: " + d.rel);
    }
  }
}
setPickTargets();
async function loadAtoms() {
  const r = await GET("/api/atoms");
  if (!r.ok) { $("atom-count").textContent = "原子 ✗"; return; }
  S.atoms = r.data.atoms || [];
  S.atomMap = {}; S.atoms.forEach((a) => (S.atomMap[a.name] = a));
  $("atom-count").textContent = `原子 ${r.data.core_count}核心 + ${r.data.ext_count}扩展`;
  const ver = r.data.version || "";
  if ($("ver-pill")) $("ver-pill").textContent = ver ? ("v" + ver) : "v—";
  if (ver) document.title = `CodeAgent Lab v${ver} — 本地完整代码智能体`;
  renderPalette();
  ensureDefaultPipeline();   // 打开即加载默认"代码审查"编排图(仅首次/画布为空时)
}
async function loadTree() {
  const r = await GET("/api/tree?recursive=1&badges=1");
  if (r.ok) { S.tree = r.data; renderTree(); }
}
$("btn-refresh-tree").addEventListener("click", loadTree);

/* ═══════════ 事件轮询 ═══════════ */
let eventsLoaded = false;
async function loadEvents(initial) {
  const r = await GET("/api/events?since=" + S.lastEvSeq);
  if (!r.ok) return;
  if (initial) { S.lastEvSeq = r.data.last_seq; return; }   // 首轮只对齐序号
  const evs = r.data.events || [];
  S.lastEvSeq = r.data.last_seq;
  evs.forEach(onEvent);
  if (S.view === "events") appendEventsToBox(evs);
  const now = Date.now();
  if (now - (S._lastEvUi || 0) > 3000) { S._lastEvUi = now; refreshRunningUI(); }
}
function onEvent(e) {
  const p = e.payload || {};
  if (e.type === "pipeline.node_done") paintCanvasNode(p.node, "done");
  if (e.type === "pipeline.node_failed") paintCanvasNode(p.node, "fail");
  if (e.type === "pipeline.finished") {
    paintCanvasAll();
    appendPipeLog(`✅ 管道完成: ${p.status} (${p.run_id})`);
    loadPipelines();
  }
  if (e.type === "pipeline.failed") appendPipeLog(`❌ 管道失败: ${(p.error || "").slice(0, 300)}`);
  if (e.type === "pipeline.node_started") appendPipeLog(`▶ 节点 ${p.atom} 开始`);
  if (e.type === "debug.started") appendDbgStep(null, { state: "IDLE", detail: "调试回路启动: " + p.run_id, evidence: "" });
  if (e.type === "debug.state" && S.debugRuns[p.run_id]) addDbgStep(p.run_id, p);
  if (e.type === "debug.done") refreshDebug(p.run_id);
  if (e.type === "report.atom_done") updateReportProgress(p);
  if (e.type === "report.finished" || e.type === "report.failed") finishReportUi();
  if (e.type === "models.test_ok") { flash("模型连通 ✓ " + p.detail); }
  if (e.type === "models.test_failed") { flash("模型连通 ✗ " + p.detail); }
  if (e.type === "atom.scaffolded") { flash("原子模板已生成: " + p.name); loadExtList(); }
  if (e.type === "atom.registered") { flash("原子已注册: " + p.name); loadAtoms(); loadExtList(); }
  if (e.type === "atom.enabled") { flash("扩展状态已更新: " + p.name); loadAtoms(); loadExtList(); }
  if (e.type === "chat.reply") {}
  if (e.type === "pipeline.node_started" || e.type === "pipeline.node_done" || e.type === "pipeline.node_failed"
      || e.type === "pipeline.finished" || e.type === "pipeline.failed") presetEvent(e);
}
let lastPipeLogAt = 0;
function appendPipeLog(line) {
  const box = $("pipe-log");
  const t = new Date().toLocaleTimeString();
  box.insertAdjacentHTML("beforeend", `<div>[${t}] ${esc(line)}</div>`);
  box.scrollTop = box.scrollHeight;
}
function appendEventsToBox(evs) {
  const box = $("events-box");
  for (const e of evs) {
    const col = e.type.includes("failed") || e.type.includes("error") ? "var(--err)" : "var(--accent)";
    box.insertAdjacentHTML("beforeend",
      `<div style="color:${col}">[${e.seq}] ${esc(e.type)} ${esc(JSON.stringify(e.payload || {}).slice(0, 220))}</div>`);
  }
  box.scrollTop = box.scrollHeight;
}
async function loadEventsFull() {
  const r = await GET("/api/events?since=0");
  if (r.ok) { $("events-box").innerHTML = ""; appendEventsToBox(r.data.events); }
}
function flash(t) { const s = $("models-status"); if (s) s.textContent = t; }
function refreshRunningUI() {}

/* ═══════════ 文件树 ═══════════ */
function renderTree() {
  const t = S.tree;
  const box = $("tree-box");
  if (!t) { box.innerHTML = "<div class='placeholder'>加载中...</div>"; return; }
  $("tree-root-label").textContent = t.root || t.path || "-";
  box.innerHTML = "";
  buildTreeItems(box, t.dirs, true);
  buildTreeItems(box, t.files, false);
}
function buildTreeItems(box, items, isDir) {
  for (const it of items || []) {
    const el = document.createElement("div");
    el.className = "tree-item " + (isDir ? "dir" : "file");
    el.textContent = (isDir ? "📁 " : "📄 ") + it.name;
    if (!isDir) {
      // P1-7 树徽标契约修复：后端 _attach_badges 返回 {kind,count,label} 对象数组，
      // kind ∈ {syntax(语法错误), ok(可编译), todo(含TODO), doc(缺模块文档)}。
      // 前端按 kind 渲染彩色徽标；ok(可编译) 常态不占位，仅在有问题的信号时显示徽标。
      const badges = it.badges || [];
      const showMap = { syntax: ["badge syntax", "✗ 语法错"], todo: ["badge todo", "☰ TODO"], doc: ["badge doc", "▤ 缺文档"] };
      for (const b of badges) {
        if (!b || !b.kind) continue;
        const pair = showMap[b.kind];
        if (!pair) continue;                 // ok/syntax 等无需显示的跳过
        const n = b.count || 1;
        el.insertAdjacentHTML("beforeend", `<span class="${pair[0]}" title="${escAttr(b.label || b.kind)}">${pair[1]}${n > 1 ? "×" + n : ""}</span>`);
      }
      el.addEventListener("click", () => openFile(it.path));
    } else {
      el.addEventListener("click", () => toggleDir(box, el, it));
    }
    box.appendChild(el);
  }
}
function toggleDir(box, el, dir) {
  const sub = el.nextSibling;
  if (sub && sub.classList && sub.classList.contains("dir-children")) {
    sub.remove(); el.textContent = el.textContent.replace("📁", "📂"); return;
  }
  el.textContent = el.textContent.replace("📂", "📁");
  const ch = document.createElement("div");
  ch.className = "dir-children";
  ch.style.paddingLeft = "14px";
  ch.innerHTML = "<div class='note'>加载中…</div>";
  el.after(ch);
  GET("/api/tree?root=" + encodeURIComponent(dir.path) + "&badges=1").then((r) => {
    if (!r.ok) { ch.innerHTML = "<div class='note'>加载失败</div>"; return; }
    ch.innerHTML = "";
    buildTreeItems(ch, r.data.dirs, true);
    buildTreeItems(ch, r.data.files, false);
  });
}

/* ═══════════ 文件查看/编辑器/方法级/图 ═══════════ */
/* ── 专业代码编辑器(纯原生JS·轻量tokenizer高亮·零依赖) ── */
let OPEN_TABS = [];                 // [{rel, dirty}] 打开文件标签页
let Editor = {
  ta: null, pre: null, gutter: null, // 编辑区元素
  lang: "py", editable: false,
  curLine: 1, curCol: 1,
  dirty: false,
  findOn: false, findStr: "", findMarks: [], findCur: 0,
  panel: "methods",                   // 右面板: methods|errors|output
  errByFile: {},                      // rel -> [{line, detail}]
  output: ""                          // 测试/运行输出
};
function tabDirty(rel){ const t = OPEN_TABS.find(x => x.rel === rel); return t ? t.dirty : false; }
function setTabDirty(rel, d){ const t = OPEN_TABS.find(x => x.rel === rel); if (t && t.dirty !== d){ t.dirty = d; refreshTabBar(); } }
function basename(p){ const a = (p || "").split("/"); return a[a.length - 1] || p; }

/* ── 轻量 tokenizer：关键字/字符串/注释/数字/函数名/装饰器(零依赖) ── */
const KW_PY = new Set(["False","None","True","and","as","assert","async","await","break","class","continue","def","del","elif","else","except","finally","for","from","global","if","import","in","is","lambda","nonlocal","not","or","pass","raise","return","try","while","with","yield","match","case","self","cls"]);
const KW_JS = new Set(["var","let","const","function","return","if","else","for","while","do","switch","case","break","continue","default","new","delete","typeof","instanceof","in","of","class","extends","super","this","try","catch","finally","throw","async","await","yield","import","export","from","as","null","undefined","true","false","void","static","get","set","arguments"]);
const KW_HTML = new Set(["html","head","body","div","span","p","a","img","script","style","link","meta","title","table","tr","td","th","ul","ol","li","button","input","form","select","option","textarea","header","footer","main","nav","section","aside","h1","h2","h3","h4","h5","h6","strong","em","b","i","u","br","hr","pre","code","video","audio","iframe","label","blockquote","canvas","svg","g","rect","circle","line","text","path","defs","marker","figure","figcaption","details","summary","dl","dt","dd"]);
const KW_CSS = new Set(["important","inherit","initial","unset"]);
const KW_SH = new Set(["if","then","else","elif","fi","for","do","done","while","until","case","esac","function","in","return","exit","echo","export","local","readonly","set","unset","shift","select","time","coproc","break","continue"]);
const KW_JSON = new Set(["true","false","null"]);
const KW_YAML = new Set(["true","false","null","yes","no","on","off","and","or","not","is"]);
const BUILTIN_PY = new Set(["print","len","range","type","str","int","float","bool","list","dict","set","tuple","frozenset","bytes","bytearray","open","input","enumerate","zip","map","filter","sum","min","max","abs","round","sorted","reversed","next","iter","repr","format","isinstance","issubclass","hasattr","getattr","setattr","delattr","property","staticmethod","classmethod","super","vars","dir","id","hash","callable","object","compile","eval","exec","globals","locals","bin","hex","oct","ord","chr","ascii","any","all","divmod","pow","slice","complex","memoryview","Exception","BaseException","SystemExit","KeyboardInterrupt","GeneratorExit","StopIteration","StopAsyncIteration","ArithmeticError","AssertionError","AttributeError","BufferError","EOFError","FloatingPointError","LookupError","MemoryError","NameError","NotImplementedError","OSError","OverflowError","ReferenceError","RuntimeError","SyntaxError","SystemError","TabError","TypeError","UnboundLocalError","UnicodeError","ValueError","ZeroDivisionError","EnvironmentError","IOError","FileNotFoundError","PermissionError","TimeoutError","RecursionError","ImportError","ModuleNotFoundError","IndexError","KeyError","IndentationError","NotImplemented"]);
function langOf(rel){ const e = (rel || "").toLowerCase(); if (e.endsWith(".py")) return "py"; if (e.endsWith(".js") || e.endsWith(".mjs") || e.endsWith(".cjs") || e.endsWith(".ts")) return "js"; if (e.endsWith(".html") || e.endsWith(".htm")) return "html"; if (e.endsWith(".css")) return "css"; if (e.endsWith(".sh") || e.endsWith(".bash")) return "sh"; if (e.endsWith(".json")) return "json"; if (e.endsWith(".yaml") || e.endsWith(".yml")) return "yaml"; return "txt"; }
function isKw(w, lang){ if (lang === "py") return KW_PY.has(w); if (lang === "js") return KW_JS.has(w); if (lang === "html") return KW_HTML.has(w) || w === "doctype"; if (lang === "css") return KW_CSS.has(w); if (lang === "sh") return KW_SH.has(w); if (lang === "json") return KW_JSON.has(w); if (lang === "yaml") return KW_YAML.has(w); return false; }
function tokenize(src, lang){
  const n = src.length, toks = [];
  const push = (s, e, cls) => { if (e > s) toks.push({ s, e, cls }); };
  const idStart = c => /[A-Za-z_]/.test(c);
  const idChar = c => /[A-Za-z0-9_]/.test(c);
  const digit = c => c >= "0" && c <= "9";
  let i = 0;
  while (i < n){
    const c = src[i];
    if (c === " " || c === "\t" || c === "\r" || c === "\n"){ i++; continue; }
    if (lang === "py" && c === "#"){ let j = i; while (j < n && src[j] !== "\n") j++; push(i, j, "com"); i = j; continue; }
    if (lang === "sh" && c === "#"){ let j = i; while (j < n && src[j] !== "\n") j++; push(i, j, "com"); i = j; continue; }
    if ((lang === "js" || lang === "css") && c === "/" && src[i + 1] === "/"){ let j = i; while (j < n && src[j] !== "\n") j++; push(i, j, "com"); i = j; continue; }
    if (lang === "js" && c === "/" && src[i + 1] === "*"){ let j = i + 2; while (j < n && !(src[j] === "*" && src[j + 1] === "/")) j++; j = Math.min(n, j + 2); push(i, j, "com"); i = j; continue; }
    if (lang === "html" && src.startsWith("<!--", i)){ let j = i + 4; const e = src.indexOf("-->", j); j = e === -1 ? n : e + 3; push(i, j, "com"); i = j; continue; }
    if (lang === "py" && (src.startsWith('"""', i) || src.startsWith("'''", i))){ const q = src.substr(i, 3); let j = i + 3; const e = src.indexOf(q, j); j = e === -1 ? n : e + 3; push(i, j, "str"); i = j; continue; }
    if (c === '"' || c === "'"){ const q = c; let j = i + 1, esc = false; while (j < n){ const ch = src[j]; if (esc) esc = false; else if (ch === "\\") esc = true; else if (ch === q){ j++; break; } j++; } j = Math.min(j, n); push(i, j, "str"); i = j; continue; }
    if (digit(c) || (c === "." && digit(src[i + 1]))){ let j = i;
      if (c === "0" && /[xXbBoO]/.test(src[i + 1] || "")){ j = i + 2; while (j < n && /[0-9a-fA-F_]/.test(src[j])) j++; }
      else { while (j < n && /[0-9_]/.test(src[j])) j++; if (src[j] === "." && digit(src[j + 1] || "")){ j++; while (j < n && /[0-9_]/.test(src[j])) j++; } if (/[eE]/.test(src[j] || "")){ j++; if (src[j] === "+" || src[j] === "-") j++; while (j < n && /[0-9_]/.test(src[j])) j++; } if (/[jJ]/.test(src[j] || "")) j++; }
      push(i, j, "num"); i = j; continue; }
    if (idStart(c)){ let j = i; while (j < n && idChar(src[j])) j++; const w = src.slice(i, j);
      if (lang === "py" && i > 0 && src[i - 1] === "@"){ push(i, j, "dec"); i = j; continue; }
      if (lang === "py" && (w === "def" || w === "class")){ push(i, j, "kw"); let k = j; while (k < n && /\s/.test(src[k])) k++; if (idStart(src[k])){ let k2 = k; while (k2 < n && idChar(src[k2])) k2++; push(k, k2, "fname"); i = k2; continue; } i = j; continue; }
      if (isKw(w, lang)){ push(i, j, "kw"); i = j; continue; }
      if (lang === "py" && BUILTIN_PY.has(w)){ push(i, j, "blt"); i = j; continue; }
      if ((lang === "py" || lang === "js") && src[j] === "("){ push(i, j, "fn"); i = j; continue; }
      push(i, j, ""); i = j; continue; }
    i++;
  }
  return toks;
}
function escHl(src, s, e, cls, marks){
  const out = []; let pos = s;
  if (marks && marks.length){
    for (const mk of marks){
      if (mk[1] <= s || mk[0] >= e) continue;
      const ms = Math.max(mk[0], s), me = Math.min(mk[1], e);
      if (ms > pos) out.push(esc(src.slice(pos, ms)));
      out.push(`<mark class="ed-mk${mk[2] ? " ed-mk-cur" : ""}">${esc(src.slice(ms, me))}</mark>`);
      pos = me;
    }
  }
  if (pos < e) out.push(esc(src.slice(pos, e)));
  const inner = out.join("");
  return cls ? `<span class="tk-${cls}">${inner}</span>` : inner;
}
function renderEditorHTML(src, lang, marks, curLine, errInfo){
  const toks = tokenize(src, lang);
  const lineStarts = [0]; for (let i = 0; i < src.length; i++) if (src[i] === "\n") lineStarts.push(i + 1);
  const lc = lineStarts.length; let out = "";
  const errSet = errInfo ? errInfo.set : null;
  for (let li = 0; li < lc; li++){
    const ls = lineStarts[li], le = (li + 1 < lc) ? lineStarts[li + 1] - 1 : src.length;
    let cls = "ed-line"; if (li + 1 === curLine) cls += " ed-curline"; if (errSet && errSet.has(li + 1)) cls += " ed-errline";
    let html = "", pos = ls;
    for (const tk of toks){ if (tk.e <= ls) continue; if (tk.s >= le) break; const s = Math.max(tk.s, ls), e = Math.min(tk.e, le); if (s > pos) html += esc(src.slice(pos, s)); html += escHl(src, s, e, tk.cls, marks); pos = e; }
    if (pos < le) html += esc(src.slice(pos, le));
    const ttl = errSet && errSet.has(li + 1) ? ` title="${escAttr((errInfo.msg && errInfo.msg[li + 1]) || "")}"` : "";
    out += `<div class="${cls}"${ttl}>${html}</div>`;
  }
  return out;
}
function renderGutter(lineCount, curLine, errSet, errInfo){
  let h = "";
  for (let i = 1; i <= lineCount; i++){
    const cur = i === curLine ? " g-cur" : "";
    const isErr = errSet && errSet.has(i);
    const err = isErr ? " g-err" : "";
    const ttl = isErr ? ` title="${escAttr((errInfo && errInfo.msg && errInfo.msg[i]) || "")}"` : "";
    h += `<div class="g-line${cur}${err}"${ttl}>${i}</div>`;
  }
  return h;
}
function errInfoFor(rel){
  const list = Editor.errByFile[rel];
  if (!list || !list.length) return null;
  const set = new Set(), msg = {};
  list.forEach(er => { if (er.line > 0){ set.add(er.line); msg[er.line] = er.detail; } });
  return { set, msg };
}
function curLineCol(ta){
  const v = ta.value, sel = ta.selectionStart;
  const before = v.slice(0, sel); let line = 1, col = 1;
  for (let i = 0; i < before.length; i++){ if (before[i] === "\n"){ line++; col = 1; } else col++; }
  return { line, col };
}
function renderOpenTabsHtml(){
  if (!OPEN_TABS.length) return "";
  return `<div class="ed-tabs" id="ed-tabs">` + tabsHtml() + `</div>`;
}
function tabsHtml(){
  const cur = S.activeFile ? S.activeFile.rel : null;
  return OPEN_TABS.map(t =>
    `<span class="ed-tab${t.rel === cur ? " active" : ""}" data-rel="${escAttr(t.rel)}" title="${escAttr(t.rel)}">` +
    `<span class="ed-name">${esc(basename(t.rel))}</span>` + (t.dirty ? `<span class="ed-dirty">●</span>` : "") +
    `<span class="ed-close" title="关闭">×</span></span>`).join("");
}
function refreshTabBar(){
  const tb = $("ed-tabs"); if (!tb) return;
  tb.innerHTML = tabsHtml();
  bindTabs();
}
function bindTabs(){
  const box = $("file-body"); if (!box) return;
  box.querySelectorAll(".ed-tab").forEach(t => t.addEventListener("click", (e) => { if (e.target.closest(".ed-close")) return; openFile(t.dataset.rel); }));
  box.querySelectorAll(".ed-close").forEach(x => x.addEventListener("click", (e) => { e.stopPropagation(); closeTab(x.closest(".ed-tab").dataset.rel); }));
}
function buildPanelTabs(){
  const head = document.querySelector("#info-panel .panel-head");
  if (!head) return;
  head.innerHTML = `<span>信息面板</span><span class="head-actions">` +
    ["methods", "errors", "output"].map(t =>
      `<button class="mini p-tab${t === Editor.panel ? " active" : ""}" data-panel="${t}">` +
      `${t === "methods" ? "方法" : t === "errors" ? "错误" : "输出"}</button>`).join("") + `</span>`;
  head.querySelectorAll(".p-tab").forEach(b => b.addEventListener("click", () => setPanel(b.dataset.panel)));
}
function syncScroll(){
  if (Editor.pre) Editor.pre.scrollTop = Editor.ta.scrollTop;
  if (Editor.pre) Editor.pre.scrollLeft = Editor.ta.scrollLeft;
  if (Editor.gutter) Editor.gutter.scrollTop = Editor.ta.scrollTop;
}
function updateStatus(f){
  const st = $("ed-status"); if (!st) return;
  const langName = { py: "Python", js: "JavaScript", html: "HTML", css: "CSS", sh: "Shell", json: "JSON", yaml: "YAML", txt: "纯文本" }[Editor.lang] || Editor.lang;
  const errInfo = errInfoFor(f.rel);
  const errTxt = errInfo ? `<span class="err">⚠ ${errInfo.set.size} 处语法错误</span>` : "";
  const dirty = Editor.dirty || tabDirty(f.rel);
  st.innerHTML = `<span><b>${esc(f.rel)}</b></span>` +
    `<span>${langName}</span>` +
    `<span>行 ${Editor.curLine}, 列 ${Editor.curCol}</span>` +
    `<span>UTF-8</span>` + errTxt +
    `<span class="${dirty ? "dirty" : "ok"}">${dirty ? "● 未保存" : "✓ 已保存"}</span>` +
    `<span>${Editor.editable ? "可编辑" : "只读"}</span>`;
}
function renderEditorViews(f){
  const ta = Editor.ta; if (!ta) return;
  const src = ta.value;
  const errInfo = errInfoFor(f.rel);
  const errSet = errInfo ? errInfo.set : null;
  const hl = src.length <= 120000;
  let html;
  if (hl) html = renderEditorHTML(src, Editor.lang, Editor.findOn ? Editor.findMarks : null, Editor.curLine, errInfo);
  else {
    const lines = src.split("\n"); html = "";
    for (let li = 0; li < lines.length; li++){ const c = li + 1 === Editor.curLine ? " ed-curline" : ""; const e = errSet && errSet.has(li + 1) ? " ed-errline" : ""; html += `<div class="ed-line${c}${e}">${esc(lines[li])}</div>`; }
  }
  Editor.pre.innerHTML = html;
  Editor.pre.style.width = Editor.ta.clientWidth + "px";
  Editor.gutter.innerHTML = renderGutter(src.split("\n").length, Editor.curLine, hl ? errSet : null, errInfo);
  syncScroll();
  updateStatus(f);
}
function buildEditor(f){
  const box = $("file-body");
  const lang = langOf(f.rel);
  const editable = lang === "py";
  Editor.lang = lang; Editor.editable = editable; Editor.curLine = 1; Editor.curCol = 1; Editor.dirty = false; Editor.findOn = false; Editor.findMarks = []; Editor.findCur = 0;
  $("btn-save").disabled = true; $("btn-run-test").disabled = false;
  const tabsHtml = renderOpenTabsHtml();
  const findBar = `<div class="ed-find hidden" id="ed-find">
      <input id="ed-find-input" placeholder="查找… (Ctrl+F)">
      <span id="ed-find-count" class="ed-find-count"></span>
      <button class="mini" id="ed-find-prev" title="上一个">↑</button>
      <button class="mini" id="ed-find-next" title="下一个">↓</button>
      <button class="mini" id="ed-find-close" title="关闭">×</button>
    </div>`;
  const preserved = (S.activeFile && S.activeFile.rel === f.rel && Editor.ta && (Editor.dirty || tabDirty(f.rel))) ? Editor.ta.value : null;
  box.innerHTML = `${tabsHtml}${findBar}
    <div class="ed-wrap">
      <div class="ed-gutter" id="ed-gutter"></div>
      <div class="ed-stack">
        <pre class="ed-hl" id="ed-hl" aria-hidden="true"></pre>
        <textarea class="ed-ta" id="ed-ta" spellcheck="false" autocomplete="off" autocapitalize="off" autocorrect="off" wrap="off"></textarea>
      </div>
    </div>
    <div class="ed-status" id="ed-status"></div>`;
  const ta = $("ed-ta"), pre = $("ed-hl"), gutter = $("ed-gutter");
  Editor.ta = ta; Editor.pre = pre; Editor.gutter = gutter;
  ta.readOnly = !editable;
  ta.value = (preserved !== null) ? preserved : (f.content || "");
  renderEditorViews(f);
  $("btn-editor").textContent = "↻ 重载文件";
  ta.addEventListener("input", () => { Editor.dirty = true; setTabDirty(f.rel, true); renderEditorViews(f); $("btn-save").disabled = false; });
  ta.addEventListener("scroll", syncScroll);
  const refreshCur = () => { const p = curLineCol(ta); Editor.curLine = p.line; Editor.curCol = p.col; renderEditorViews(f); };
  ta.addEventListener("keyup", refreshCur);
  ta.addEventListener("click", refreshCur);
  ta.addEventListener("keydown", (e) => onEditorKey(e, f));
  const inp = $("ed-find-input");
  inp.addEventListener("input", onFindInput);
  $("ed-find-next").addEventListener("click", (e) => { e.preventDefault(); stepFind(1); });
  $("ed-find-prev").addEventListener("click", (e) => { e.preventDefault(); stepFind(-1); });
  $("ed-find-close").addEventListener("click", (e) => { e.preventDefault(); closeFind(); ta.focus(); });
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter"){ e.preventDefault(); stepFind(e.shiftKey ? -1 : 1); } if (e.key === "Escape"){ closeFind(); ta.focus(); } });
  bindTabs();
}
function onEditorKey(e, f){
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "s"){ e.preventDefault(); saveActiveFile(); return; }
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "f"){ e.preventDefault(); showFind(); return; }
  if (e.key === "Tab"){ e.preventDefault(); handleTab(); return; }
}
function handleTab(){
  const ta = Editor.ta; const s = ta.selectionStart, en = ta.selectionEnd, val = ta.value;
  if (s === en){
    const lineStart = val.lastIndexOf("\n", s - 1) + 1;
    const ins = " ".repeat(4 - (s - lineStart) % 4 || 4);
    ta.value = val.slice(0, s) + ins + val.slice(en);
    ta.selectionStart = ta.selectionEnd = s + ins.length;
  } else {
    const sel = val.slice(s, en).replace(/^(?!$)/gm, "    ");
    ta.value = val.slice(0, s) + sel + val.slice(en);
    ta.selectionStart = s + 4; ta.selectionEnd = s + 4 + sel.length;
  }
  Editor.dirty = true; setTabDirty(S.activeFile.rel, true);
  const p = curLineCol(ta); Editor.curLine = p.line; Editor.curCol = p.col;
  renderEditorViews(S.activeFile); $("btn-save").disabled = false;
}
function findMatches(src, q){
  if (!q) return [];
  const s = src.toLowerCase(), ql = q.toLowerCase(), out = []; let idx = 0;
  while ((idx = s.indexOf(ql, idx)) !== -1){ out.push([idx, idx + ql.length, false]); idx += Math.max(ql.length, 1); }
  return out;
}
function runFind(){
  const src = Editor.ta.value;
  Editor.findMarks = findMatches(src, Editor.findStr);
  if (Editor.findMarks.length){ Editor.findCur = 0; Editor.findMarks[0][2] = true; }
  else Editor.findCur = -1;
  renderEditorViews(S.activeFile);
  const c = $("ed-find-count"); if (c) c.textContent = Editor.findStr ? `${Editor.findMarks.length ? Editor.findCur + 1 : 0}/${Editor.findMarks.length}` : "";
  if (Editor.findMarks.length) selectFindMatch(0);
}
function onFindInput(){ Editor.findStr = $("ed-find-input").value; Editor.findCur = 0; runFind(); }
function showFind(){
  if (!Editor.ta) return;
  const bar = $("ed-find"); bar.classList.remove("hidden");
  Editor.findOn = true;
  const inp = $("ed-find-input"); inp.value = Editor.findStr; inp.focus(); inp.select();
  runFind();
}
function closeFind(){
  Editor.findOn = false; Editor.findStr = ""; Editor.findMarks = []; Editor.findCur = 0;
  const bar = $("ed-find"); if (bar) bar.classList.add("hidden");
  if (Editor.ta) renderEditorViews(S.activeFile);
}
function selectFindMatch(i){
  if (i < 0 || i >= Editor.findMarks.length) return;
  const mk = Editor.findMarks[i];
  const ta = Editor.ta; ta.focus(); ta.setSelectionRange(mk[0], mk[1]); syncScroll();
}
function stepFind(dir){
  if (!Editor.findMarks.length) return;
  Editor.findCur = (Editor.findCur + dir + Editor.findMarks.length) % Editor.findMarks.length;
  Editor.findMarks.forEach((m, idx) => { m[2] = idx === Editor.findCur; });
  renderEditorViews(S.activeFile);
  const c = $("ed-find-count"); if (c) c.textContent = `${Editor.findCur + 1}/${Editor.findMarks.length}`;
  selectFindMatch(Editor.findCur);
}
function jumpToLine(ln){
  if (Editor.ta){
    const v = Editor.ta.value; let pos = 0, line = 1;
    while (line < ln){ const ni = v.indexOf("\n", pos); if (ni === -1) break; pos = ni + 1; line++; }
    Editor.ta.focus(); Editor.ta.setSelectionRange(pos, pos); Editor.curLine = ln; Editor.curCol = 1;
    renderEditorViews(S.activeFile);
    const lh = parseFloat(getComputedStyle(Editor.ta).lineHeight) || 19;
    Editor.ta.scrollTop = (ln - 1) * lh; syncScroll();
  } else {
    const rows = document.querySelectorAll("#file-body .ed-line"); if (rows[ln - 1]) rows[ln - 1].scrollIntoView({ block: "center" });
  }
}
function setPanel(tab){
  Editor.panel = tab;
  document.querySelectorAll("#info-panel .p-tab").forEach(b => b.classList.toggle("active", b.dataset.panel === tab));
  if (tab === "methods") renderMethods();
  else if (tab === "errors") renderErrorsPanel();
  else if (tab === "output") renderOutputPanel();
}
function renderErrorsPanel(){
  const box = $("info-box"); const f = S.activeFile;
  const list = (f && Editor.errByFile[f.rel]) || [];
  if (!list.length){ box.innerHTML = "<div class='note'>无语法错误 — 保存后自动校验</div>"; return; }
  box.innerHTML = list.map(er => `<div class="method-item err-item" data-line="${er.line || 1}"><span class="method-name">L${er.line || 1}</span><div class="detail">${esc(er.detail || "语法错误")}</div></div>`).join("");
  box.querySelectorAll(".err-item").forEach(el => el.addEventListener("click", () => jumpToLine(Number(el.dataset.line))));
}
function renderOutputPanel(){
  const box = $("info-box");
  if (!Editor.output){ box.innerHTML = "<div class='note'>暂无输出 — 运行测试后在此显示</div>"; return; }
  box.innerHTML = `<div class="ev" style="white-space:pre-wrap;font-family:Consolas,monospace">${esc(Editor.output)}</div>`;
}
async function saveActiveFile(){
  const f = S.activeFile; if (!f) return;
  const ta = Editor.ta; if (!ta) return;
  const r = await POST("/api/file/save", { path: f.rel, content: ta.value });
  if (r && r.compile_ok === false){
    const er = r.first_error || {};
    Editor.errByFile[f.rel] = [{ line: er.line || 1, detail: r.error || er.detail || "语法错误" }];
    Editor.dirty = true; setTabDirty(f.rel, true); $("btn-save").disabled = false;
    renderEditorViews(f); setPanel("errors");
    flash(`语法校验失败: line ${er.line || 1}: ${String(er.detail || r.error || "").slice(0, 120)}`);
    return;
  }
  if (!r.ok){ flash("保存失败: " + (r.error || "")); return; }
  delete Editor.errByFile[f.rel];
  Editor.dirty = false; setTabDirty(f.rel, false); $("btn-save").disabled = true;
  f.content = ta.value;
  S.fileCache = S.fileCache || {}; if (S.fileCache[f.rel]) S.fileCache[f.rel].content = ta.value;
  renderEditorViews(f); setPanel("methods");
  flash(`已保存 ${f.rel} — 语法校验通过`);
  loadTree();
}
function closeTab(rel){
  const idx = OPEN_TABS.findIndex(x => x.rel === rel); if (idx === -1) return;
  OPEN_TABS.splice(idx, 1); S.fileCache = S.fileCache || {}; delete S.fileCache[rel];
  if (S.activeFile && S.activeFile.rel === rel){
    if (OPEN_TABS.length) openFile(OPEN_TABS[Math.max(0, idx - 1)].rel);
    else { S.activeFile = null; $("file-title").textContent = "选择文件查看 / 编辑"; $("file-body").innerHTML = '<div class="placeholder">← 从左侧文件树选择源码文件<br>支持: 代码编辑 / 语法高亮 / 查找 / 行内错误 / 文件标签</div>'; }
  } else if (S.activeFile) buildEditor(S.activeFile);
}
async function openFile(rel){
  S.fileCache = S.fileCache || {};
  let data = S.fileCache[rel];
  if (!data){
    const r = await GET("/api/file?path=" + encodeURIComponent(rel) + "&view=methods");
    if (!r.ok){ flash(r.error || "读取失败"); return; }
    data = r.data; S.fileCache[rel] = data;
  }
  S.activeFile = { rel, ...data };
  $("file-title").textContent = "📄 " + rel;
  if (!OPEN_TABS.find(x => x.rel === rel)) OPEN_TABS.push({ rel, dirty: false });
  $("graph-box").classList.add("hidden");
  $("file-body").classList.remove("hidden");
  buildEditor(S.activeFile);
  buildPanelTabs();
  setPanel("methods");
}
$("btn-editor").addEventListener("click", async () => {
  const f = S.activeFile; if (!f) return;
  S.fileCache = S.fileCache || {}; delete S.fileCache[f.rel];
  await openFile(f.rel);
});
$("btn-save").addEventListener("click", saveActiveFile);
$("btn-run-test").addEventListener("click", async () => {
  const f = S.activeFile; if (!f) return;
  const r = await POST("/api/test", { path: f.rel });
  if (!r.ok) { flash("测试失败: " + r.error); return; }
  const d = r.data;
  const rg = (d.data && d.data.red_green) || {};
  Editor.output = JSON.stringify(d.data || d, null, 2);
  setPanel("output");
  flash(rg.green ? "✅ 测试全绿" : "❌ 测试红: " + ((d.data && d.data.summary) || d.error || "").slice(0, 200));
  appendChat("assistant", "测试结果: " + (rg.green ? "✅ 全绿" : "❌ 红") + " — 详见事件流", "test");
});
function renderMethods() {
  const f = S.activeFile; if (!f) return;
  const box = $("info-box");
  const ms = (f.methods || []).filter((m) => { const k = m.type || m.kind; return k === "function" || k === "class"; });
  if (!ms.length) { box.innerHTML = "<div class='note'>无顶层函数/类(AST 方法级视图)</div>"; return; }
  box.innerHTML = ms.map((m) => {
    const k = m.type || m.kind;
    return `<div class="method-item" data-line="${m.line}"><span class="method-name">${esc(k === "class" ? "◈ " : "ƒ ")}${esc(m.name)}</span>` +
    `<span class="method-line"> L${m.line} args=${esc((m.args || []).join(","))}</span></div>`;
  }).join("");
  box.querySelectorAll(".method-item").forEach((el) => el.addEventListener("click", () => {
    jumpToLine(Number(el.dataset.line));
  }));
}
$("btn-methods").addEventListener("click", () => { if (S.activeFile) { setPanel("methods"); jumpToLine(Editor.curLine); } });
$("btn-graph").addEventListener("click", () => { if (S.activeFile) showGraph("deps", S.activeFile.rel); });
$("btn-impact").addEventListener("click", () => { if (S.activeFile) showGraph("impact", S.activeFile.rel); });
$("btn-layer").addEventListener("click", () => { if (S.activeFile) showGraph("layers", S.activeFile.rel); });
async function showGraph(kind, rel) {
  const r = await GET(`/api/graph?kind=${kind}&path=${encodeURIComponent(rel || "")}`);
  if (!r.ok) { flash("图生成失败: " + r.error); return; }
  $("file-body").classList.add("hidden");
  const gb = $("graph-box");
  gb.classList.remove("hidden");
  gb.innerHTML = "";
  const d = r.data;
  const svg = makeSvgGraph(d, kind, rel);
  gb.appendChild(svg);
  if (d.error) { const e = document.createElement("div"); e.className = "note"; e.textContent = d.error; gb.appendChild(e); }
}
function makeSvgGraph(d, kind, label) {
  const nodes = d.nodes || [], edges = d.edges || [];
  const W = 1000, H = Math.max(320, Math.ceil(nodes.length / 8) * 46 + 40);
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", "100%");
  const defs = document.createElementNS(svgNS, "defs");
  defs.innerHTML = `<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#94a3b8"/></marker>`;
  svg.appendChild(defs);
  const pos = {};
  nodes.forEach((n, i) => { pos[n.id] = { x: 24 + (i % 8) * 122, y: 18 + Math.floor(i / 8) * 46 }; });
  for (const e of edges) {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) continue;
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", a.x + 100); line.setAttribute("y1", a.y + 9);
    line.setAttribute("x2", b.x); line.setAttribute("y2", b.y + 9);
    line.setAttribute("class", "svg-link");
    line.setAttribute("marker-end", "url(#arrow)");
    svg.appendChild(line);
  }
  for (const n of nodes) {
    const p = pos[n.id];
    const g2 = document.createElementNS(svgNS, "g");
    const r2 = document.createElementNS(svgNS, "rect");
    r2.setAttribute("x", p.x); r2.setAttribute("y", p.y);
    r2.setAttribute("width", 100); r2.setAttribute("height", 20); r2.setAttribute("rx", 4);
    r2.setAttribute("fill", kind === "layers" ? "#e0e7ff" : (n.kind === "module" ? "#eff6ff" : "#ecfdf5"));
    r2.setAttribute("stroke", "#d5dbe3");
    g2.appendChild(r2);
    const t1 = document.createElementNS(svgNS, "text");
    t1.setAttribute("x", p.x + 8); t1.setAttribute("y", p.y + 14); t1.setAttribute("font-size", "10px");
    t1.setAttribute("fill", "#1e293b");
    t1.textContent = String(n.label || n.id).slice(0, 14);
    g2.appendChild(t1);
    svg.appendChild(g2);
  }
  const cap = document.createElementNS(svgNS, "text");
  cap.setAttribute("x", 8); cap.setAttribute("y", H - 6); cap.setAttribute("font-size", "11px");
  cap.setAttribute("fill", "#64748b");
  cap.textContent = `${label} — ${nodes.length} 节点 / ${edges.length} 边`;
  svg.appendChild(cap);
  return svg;
}

/* ═══════════ 原子中文功能名映射 (显示中文, 调用仍用后端原子名) ═══════════ */
const ATOM_CN = {
  "command-approvals": "命令审批", "arch-review": "架构审查", "atomicity-audit": "原子化审计",
  "bug-deep": "深挖Bug", "code-review": "代码审查", "minimalist-style": "极简风格",
  "context-compact": "上下文压缩", "code-deliver": "交付报告", "dep-scan": "依赖SCA",
  "code-dispatch": "派单编排", "doc-freshness": "文档新鲜度", "domain-review": "领域审查",
  "code-evolve": "自进化", "code-fuzz": "模糊测试", "guard": "护栏门禁", "deadcode": "死代码",
  "dep-impact": "依赖影响", "method-impact": "方法级影响", "llm-router": "模型路由",
  "localized": "本地化审查", "mcp-client": "MCP客户端", "code-memory": "代码记忆",
  "model-fallback": "模型降级", "ontology-review": "本体审查", "code-plan": "编码规划",
  "code-project": "项目骨架", "code-reuse": "复用建议", "process-sandbox": "沙箱执行",
  "security-scan": "安全扫描", "code-skill": "技能管理", "task-state": "任务状态",
  "code-test": "单元测试",
  "lab-harness": "执行编排控制器", "lab-loop": "循环控制",
  "browser-smoke": "浏览器冒烟",
  "git-ops": "Git 只读工具", "code-implement": "编码实现", "code-runloop": "迭代实现",
  "event-log": "事件账本", "secret-vault": "密钥保管", "session": "会话管理"
};
const cnName = (atom) => ATOM_CN[atom] || atom;

/* 默认「最全全原子 + 循环 + harness」编排图 —— 页面打开即见(节点/边已连接, 可直接运行)。
   设计(检索自 Airflow/Temporal/Prefect DAG 调度器 + LangGraph 循环 最佳实践):
   - 可执行数据流为 DAG(调度器拒绝依赖环), 循环/反馈用「循环控制原子(重试/迭代)」实现——
     重试/迭代的循环体在 loop 原子内部真实执行, 避免在图里画硬环导致调度失败。
   - 实线边 = 可执行数据流(并行扫描汇入 harness → 交付报告; 循环控制 → 交付报告)。
   - 虚线边(loopEdges) = 循环/反馈语义可视化(重试反馈回测试、迭代反馈回审查、harness 门控
     调度进入重试/迭代), 只做语义标注不参与拓扑排序, 保证整图可执行。
   - harness 控制器(执行编排控制器) 聚合各审查原子 + 顺序编排步骤 + 门控判定(ALL_OK 放行)。
   全部节点用中文功能名(调用走后端原子名)。 */
function defaultReviewPipeline() {
  const tgt = (S.targetRoot || ".");   // 默认审查整个目标仓库(目录, 供目录型原子)
  // 文件型原子(代码审查/安全扫描/单元测试)接口需"单个文件"而非目录:
  // 默认指向目标仓库入口文件 agent_runtime.py(加壳不改核心; 若换目标仓库请在属性面板改 path)
  const FILE_TGT = (tgt === "." ? "." : tgt).replace(/[\\/]+$/, "") + "/agent_runtime.py";
  const defs = [
    // ── 核心审查原子(并行扫描目标) ──
    { atom: "code-review",   cap: "codereview.review",  label: "代码审查",   x: 30,  y: 30,  params: { path: FILE_TGT } },
    { atom: "security-scan", cap: "security.scan",      label: "安全扫描",   x: 30,  y: 140, params: { path: FILE_TGT } },
    { atom: "code-test",     cap: "test.run",           label: "单元测试",   x: 30,  y: 250, params: { path: FILE_TGT } },
    { atom: "method-impact", cap: "impact.method",      label: "方法级影响", x: 30,  y: 360, params: { path: tgt } },
    { atom: "deadcode",      cap: "deadcode.scan",      label: "死代码",     x: 300, y: 30,  params: { path: tgt } },
    { atom: "doc-freshness", cap: "doc.stale",          label: "文档新鲜度", x: 300, y: 140, params: { path: tgt, root: tgt } },
    { atom: "dep-scan",      cap: "depscan.scan",       label: "依赖SCA",    x: 300, y: 250, params: { path: tgt } },
    { atom: "arch-review",   cap: "archreview.layers",  label: "架构审查",   x: 300, y: 360, params: { path: tgt } },
    { atom: "atomicity-audit", cap: "atomicity.breaks", label: "原子化审计", x: 30, y: 580, params: { path: tgt } },
    { atom: "domain-review",  cap: "domain.imports",     label: "领域审查",   x: 300, y: 580, params: { path: tgt } },
    { atom: "localized",      cap: "local.audit",        label: "本地化审查", x: 570, y: 580, params: { path: tgt } },
    { atom: "ontology-review", cap: "ontology.quality",  label: "本体审查",   x: 840, y: 580, params: { path: tgt } },
    { atom: "git-ops",       cap: "git.status",         label: "Git 状态",   x: 570, y: 430, params: { path: tgt } },
    { atom: "code-test",     cap: "test.project",       label: "项目级验收", x: 300, y: 470, params: { path: tgt } },
    // ── harness 执行编排控制器(执行器/调度器): 默认单轮(retries=1) + 聚合上游 evidence（steps 空=只聚合） ──
    { atom: "lab-harness", cap: "harness.control", label: "执行编排控制器", x: 570, y: 150,
      params: { mode: "seq", retries: 1, steps: [] } },
    // ── 门控(放行判定): harness.gate, 以「上游证据的裁决」为准, condition=all_ok 才放行交付 ──
    { atom: "lab-harness", cap: "harness.gate", label: "门控:放行判定", x: 570, y: 300,
      params: { condition: "all_ok" } },
    // ── 循环控制原子(内部真实多轮+失败反馈): 目标用已注册的真实原子(默认可红可绿) ──
    { atom: "lab-loop", cap: "loop.retry",  label: "循环:重试控制", x: 900, y: 60,
      params: { target_atom: "code-test", target_cap: "test.run",
                attempts: 2, params: { path: FILE_TGT } } },
    { atom: "lab-loop", cap: "loop.iterate", label: "循环:迭代控制", x: 900, y: 300,
      params: { target_atom: "security-scan", target_cap: "security.scan",
                items: [FILE_TGT, FILE_TGT], item_param: "path" } },
    // ── 交付报告(聚合汇总) ──
    { atom: "code-deliver", cap: "deliver.report", label: "交付报告", x: 1170, y: 205,
      params: { chain: ["codereview.review", "security.scan", "test.run", "impact.method",
                        "deadcode.scan", "doc.stale", "depscan.scan", "archreview.layers",
                        "atomicity.breaks", "domain.imports", "local.audit", "ontology.quality",
                        "git.status", "test.project",
                        "harness.control", "harness.gate", "loop.retry", "loop.iterate"],
                outputs: {} } }
  ];
  S.canvas.nodes = defs.filter((d) => S.atomMap[d.atom]).map((d) => {
    const a = S.atomMap[d.atom] || {};
    return { id: uid("n"), atom: d.atom, label: d.label, x: d.x, y: d.y,
             capability: d.cap || (a.provides && a.provides[0]) || "",
             params: d.params || {}, status: null };
  });
  const byLabel = (l) => S.canvas.nodes.find((n) => n.label === l);
  // 两端节点都在才连线：缺哪个原子就少哪条边，整图仍可执行（按本地已注册原子自适应）
  const link = (from, to) => {
    const a = byLabel(from), b = byLabel(to);
    return (a && b) ? { from: a.id, to: b.id, map: "summary", input_name: "evidence" } : null;
  };
  const loopLink = (from, to, kind) => {
    const a = byLabel(from), b = byLabel(to);
    return (a && b) ? { from: a.id, to: b.id, kind } : null;
  };
  // 可执行数据流(实线): 各审查原子 → 执行编排控制器 → 门控(放行判定) → 交付报告
  const scan = ["代码审查", "安全扫描", "单元测试", "方法级影响",
                "死代码", "文档新鲜度", "依赖SCA", "架构审查",
                "原子化审计", "领域审查", "本地化审查", "本体审查",
                "Git 状态", "项目级验收"];
  S.canvas.edges = scan.map((l) => link(l, "执行编排控制器")).filter(Boolean)
    .concat([
      link("执行编排控制器", "门控:放行判定"),
      link("门控:放行判定", "交付报告"),
      link("循环:重试控制", "交付报告"),
      link("循环:迭代控制", "交付报告")
    ].filter(Boolean));
  // 循环/反馈语义(虚线可视化; 不参与拓扑排序 → 保证可执行)
  S.canvas.loopEdges = [
    loopLink("循环:重试控制", "单元测试", "重试反馈"),
    loopLink("循环:迭代控制", "安全扫描", "迭代反馈"),
    loopLink("门控:放行判定", "循环:重试控制", "门控调度"),
    loopLink("门控:放行判定", "循环:迭代控制", "门控调度")
  ].filter(Boolean);
  S.canvas.selected = null;
}
function ensureDefaultPipeline(force) {
  // force=true（一键审查）→ 强制重建默认「代码审查」编排图，确保文件型原子路径适配当前审查对象
  if (force) { S.canvas._defaultLoaded = false; S.canvas.nodes = []; S.canvas.edges = []; S.canvas.loopEdges = []; }
  if (S.canvas._defaultLoaded) return;      // 只加载一次
  S.canvas._defaultLoaded = true;
  if (S.canvas.nodes.length) return;        // 用户已有内容则不清空
  defaultReviewPipeline();
  renderCanvas();
  renderProps();
  $("pipe-log").innerHTML = `<div>[🕘] 已加载默认「最全全原子 + 循环 + harness」编排图（${S.canvas.nodes.length} 节点 / ${S.canvas.edges.length} 实线边 / ${(S.canvas.loopEdges||[]).length} 循环反馈边）— 点「▶ 运行管道」即可执行。实线=可执行数据流, 虚线=循环/反馈语义, 循环体在「循环控制/执行编排控制器」原子内部真实执行。可清空后自行拖拽原子重组。</div>`;
}

/* （已移除：旧的「UI 冒烟预设」按钮——它的能力已经是「🖥 前端冒烟验收」这张编排卡）*/

/* ═══════════ 原子调色板 ═══════════ */
function renderPalette() {
  const box = $("palette-box");
  box.innerHTML = "";
  const q = ($("palette-q").value || "").toLowerCase();
  for (const a of S.atoms) {
    const cn = cnName(a.name);
    if (q && !(a.name.toLowerCase().includes(q) || cn.toLowerCase().includes(q) || a.description.toLowerCase().includes(q))) continue;
    const el = document.createElement("div");
    el.className = "atom-chip" + (a.origin === "ext" ? " ext" : "");
    el.draggable = true;
    el.title = `后端原子: ${a.name} · ${a.domain} · 点一下即加入编排（也可拖到画布任意位置）`;
    el.innerHTML = `<div class="a-name">${esc(cn)}</div><div class="a-domain">${esc(a.name)} · ${esc(a.version)} · ${a.origin === "ext" ? "扩展" : "核心"}</div>`;
    el.addEventListener("dragstart", (ev) => { ev.dataTransfer.setData("text/plain", a.name); });
    el.addEventListener("click", () => quickAddAtom(a.name));
    box.appendChild(el);
  }
}
$("palette-q").addEventListener("input", renderPalette);

/* ═══════════ 拖拽编排画布 ═══════════ */
const VBW = 1500, VBH = 760;
function renderCanvas() {
  const box = $("canvas-box");
  const svg = $("canvas-svg");
  if (!S.canvas._bound) {
    S.canvas._bound = true;
    svg.addEventListener("dragover", (ev) => { ev.preventDefault(); $("canvas-box").classList.add("drag-over"); });
    svg.addEventListener("dragleave", (ev) => { if (!svg.contains(ev.relatedTarget)) $("canvas-box").classList.remove("drag-over"); });
    svg.addEventListener("drop", (ev) => {
      ev.preventDefault();
      $("canvas-box").classList.remove("drag-over");
      const atom = ev.dataTransfer.getData("text/plain");
      if (atom) addCanvasNode(atom, toVbX(ev), toVbY(ev));
    });
    svg.addEventListener("mousedown", (ev) => onSvgDown(ev));
    svg.addEventListener("mousemove", (ev) => onSvgMove(ev));
    svg.addEventListener("mouseup", (ev) => onSvgUp(ev));
    svg.addEventListener("dblclick", (ev) => {
      const t = ev.target;
      if (t.classList && t.classList.contains("loop-edge")) {
        const idx = [...svg.querySelectorAll(".loop-edge")].indexOf(t);
        if (idx >= 0) S.canvas.loopEdges.splice(idx, 1);
        renderCanvas();
      } else if (t.classList && t.classList.contains("svg-link")) {
        const idx = [...svg.querySelectorAll(".svg-link")].indexOf(t);
        if (idx >= 0) S.canvas.edges.splice(idx, 1);
        renderCanvas();
      }
    });
  }
  drawCanvas();
}
function toVbX(ev) {
  const rect = $("canvas-svg").getBoundingClientRect();
  return ((ev.clientX - rect.left) / rect.width) * VBW;
}
function toVbY(ev) {
  const rect = $("canvas-svg").getBoundingClientRect();
  return ((ev.clientY - rect.top) / rect.height) * VBH;
}
function addCanvasNode(atom, x, y) {
  const a = S.atomMap[atom]; if (!a) return;
  const id = uid("n");
  const node = { id, atom, label: cnName(atom), x: Math.max(30, x - 55), y: Math.max(16, y - 14), capability: a.provides[0] || "", params: {}, status: null };
  S.canvas.nodes.push(node);
  renderCanvas();
  selectNode(id);
  appendPipeLog(`＋ 已添加节点 [${cnName(atom)}] (后端: ${atom})`);
}
function freeSlot() {
  // 找一个不与现有节点重叠的空位（左→右、上→下扫描）：新节点一定看得见，不会“藏在”已有节点下面
  for (let row = 0; row < 8; row++) {
    for (let col = 0; col < 8; col++) {
      const x = 44 + col * 205, y = 40 + row * 92;
      if (x > VBW - 160 || y > VBH - 80) continue;
      const busy = S.canvas.nodes.some((n) => Math.abs(n.x - x) < 140 && Math.abs(n.y - y) < 62);
      if (!busy) return { x, y };
    }
  }
  return { x: 44, y: 40 };
}
function quickAddAtom(atom) {
  // 点一下左侧原子 → 右侧编排立刻多一个节点（落在空位并闪一下），并与上一个自动串线
  const slot = freeSlot();
  addCanvasNode(atom, slot.x + 55, slot.y + 14);
  const added = S.canvas.nodes[S.canvas.nodes.length - 1];
  if (added) {
    added._flash = true;
    renderCanvas();
    setTimeout(() => { if (added) { added._flash = false; renderCanvas(); } }, 2600);
  }
  const prev = S._lastQuickNode;
  if (added && prev && S.canvas.nodes.indexOf(prev) >= 0) {
    const dup = S.canvas.edges.some((e) => e.from === prev.id && e.to === added.id);
    if (!dup) {
      S.canvas.edges.push({ from: prev.id, to: added.id, map: "summary", input_name: "input" });
      renderCanvas();
      appendPipeLog("↔ 已自动串线: " + prev.label + " → " + added.label);
    }
  }
  S._lastQuickNode = added;
  flashHint("已加入编排（蓝色闪动那个就是新节点）；再点下一个会自动串起来，右侧可直接改参数");
}
function selectNode(id) {
  S.canvas.selected = id;
  drawCanvas();
  renderProps();
}
function onSvgDown(ev) {
  const svg = $("canvas-svg");
  const t = ev.target;
  if (t.classList && t.classList.contains("node-title")) {
    const g = t.closest("g.node-g");
    if (g) { S.canvas._dragNode = g.dataset.id; S.canvas._dx = toVbX(ev); S.canvas._dy = toVbY(ev); ev.preventDefault(); }
  } else if (t.classList && t.classList.contains("port-out")) {
    const g = t.closest("g.node-g");
    S.canvas.linkDraft = { from: g.dataset.id, x: toVbX(ev), y: toVbY(ev) };
    ev.preventDefault();
  } else if (t.classList && t.classList.contains("node-g")) {
    selectNode(t.dataset.id);
  } else if (t.tagName === "rect" && t.closest && t.closest("g.node-g")) {
    // 点击节点矩形体 → 选中该节点(打开右侧属性面板)
    const g = t.closest("g.node-g");
    if (g) selectNode(g.dataset.id);
  } else if (t.tagName === "text" && t.closest && t.closest("g.node-g")) {
    // 点击节点能力文字 → 选中
    const g = t.closest("g.node-g");
    if (g) selectNode(g.dataset.id);
  } else {
    S.canvas.selected = null; drawCanvas(); renderProps();
  }
}
function onSvgMove(ev) {
  if (S.canvas._dragNode) {
    const n = S.canvas.nodes.find((n2) => n2.id === S.canvas._dragNode);
    if (!n) return;
    const dx = toVbX(ev), dy = toVbY(ev);
    n.x += dx - S.canvas._dx; n.y += dy - S.canvas._dy;
    S.canvas._dx = dx; S.canvas._dy = dy;
    drawCanvas();
  } else if (S.canvas.linkDraft) {
    S.canvas.linkDraft.x = toVbX(ev); S.canvas.linkDraft.y = toVbY(ev);
    drawCanvas();
  }
}
function onSvgUp(ev) {
  if (S.canvas._dragNode) { S.canvas._dragNode = null; return; }
  if (S.canvas.linkDraft) {
    const t = ev.target;
    if (t.classList && t.classList.contains("port-in")) {
      const g = t.closest("g.node-g");
      if (g && g.dataset.id !== S.canvas.linkDraft.from) {
        S.canvas.edges.push({ from: S.canvas.linkDraft.from, to: g.dataset.id, map: "data", input_name: "input" });
      }
    }
    S.canvas.linkDraft = null;
    drawCanvas();
  }
}
function paintCanvasNode(nid, status) {
  const n = S.canvas.nodes.find((n2) => n2.id === nid);
  if (n) { n.status = status; drawCanvas(); }
}
function paintCanvasAll() {
  S.canvas.nodes.forEach((n) => { n.status = null; });
  drawCanvas();
}
function drawCanvas() {
  const svg = $("canvas-svg");
  // 自适应虚拟区：按内容包围盒取（窄编排→放大显示，长编排→保证都装得下）
  const ext = S.canvas.nodes.reduce((a, n) => ({ w: Math.max(a.w, n.x + 200), h: Math.max(a.h, n.y + 116) }), { w: 0, h: 0 });
  const W = Math.max(430, Math.min(VBW, ext.w)), H = Math.max(300, Math.min(VBH, ext.h));
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  let html = `<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#94a3b8"/></marker><marker id="arrowLoop" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#f59e0b"/></marker></defs>`;
  const { nodes, edges } = S.canvas;
  for (const e of edges) {
    const a = nodes.find((n) => n.id === e.from), b = nodes.find((n) => n.id === e.to);
    if (!a || !b) continue;
    const cx = a.x + 150, cy = a.y + 28, cx2 = b.x, cy2 = b.y + 28;
    let cls = "svg-link";
    if (b.status === "done") cls += " active";
    if (b.status === "fail") cls += " fail";
    html += `<path class="${cls}" d="M${cx},${cy} C${cx + 50},${cy} ${cx2 - 50},${cy2} ${cx2},${cy2}"/>`;
  }
  // 循环/反馈语义边(虚线橙色, 可视化; 不参与拓扑排序)
  for (const le of (S.canvas.loopEdges || [])) {
    const a = nodes.find((n) => n.id === le.from), b = nodes.find((n) => n.id === le.to);
    if (!a || !b) continue;
    const cx = a.x + 150, cy = a.y + 28, cx2 = b.x, cy2 = b.y + 28;
    html += `<path class="svg-link loop-edge" style="stroke:#f59e0b;stroke-dasharray:7 5" marker-end="url(#arrowLoop)" title="${esc(le.kind || "循环/反馈")}" d="M${cx},${cy} C${cx + 70},${cy + 90} ${cx2 - 70},${cy2 + 90} ${cx2},${cy2}"/>`;
  }
  if (S.canvas.linkDraft) {
    const a = nodes.find((n) => n.id === S.canvas.linkDraft.from);
    if (a) html += `<path class="svg-link" style="stroke-dasharray:6 4" d="M${a.x + 120},${a.y + 22} C${a.x + 170},${a.y + 22} ${S.canvas.linkDraft.x - 50},${S.canvas.linkDraft.y} ${S.canvas.linkDraft.x},${S.canvas.linkDraft.y}"/>`;
  }
  for (const n of nodes) {
    const sel = n.id === S.canvas.selected;
    const color = n.status === "done" ? "#d1fae5" : n.status === "fail" ? "#fee2e2" : n.status === "running" ? "#fef9c3" : "#ffffff";
    const border = sel ? "#2563eb" : n.status === "fail" ? "#dc2626" : n.status === "done" ? "#16a34a" : n.status === "running" ? "#d97706" : "#94a3b8";
    const a = S.atomMap[n.atom] || {};
    const capTxt = (n.capability || a.provides?.[0] || "").slice(0, 26);
    html += `<g class="node-g${n._flash ? " flash" : ""}" data-id="${n.id}" transform="translate(${n.x},${n.y})">` +
      `<rect x="0" y="0" width="150" height="56" rx="8" fill="${color}" stroke="${border}" stroke-width="${sel ? 3 : 1.6}"/>` +
      `<circle class="port-in" cx="0" cy="28" r="5.5" fill="#3b82f6" stroke="#fff" title="输入端口"/>` +
      `<circle class="port-out" cx="150" cy="28" r="6" fill="#2563eb" stroke="#fff" title="输出端口"/>` +
      `<text class="node-title" x="12" y="25" font-size="19" font-weight="700" fill="#1e293b" style="cursor:move">${esc((n.label || cnName(n.atom)).slice(0, 22))}</text>` +
      `<text x="12" y="45" font-size="12.5" fill="#64748b">${esc(capTxt)}</text>` +
      `</g>`;
  }
  svg.innerHTML = html;
}
function renderProps() {
  const box = $("prop-box");
  const n = S.canvas.nodes.find((n2) => n2.id === S.canvas.selected);
  if (!n) { box.innerHTML = "<div class='placeholder'>点画布上的节点<br>配置输入参数 / 选择能力</div>"; return; }
  const a = S.atomMap[n.atom] || {};
  let opts = `<option value="">(默认能力)</option>`;
  (a.provides || []).forEach((c) => { opts += `<option value="${esc(c)}" ${c === n.capability ? "selected" : ""}>${esc(c)}</option>`; });
  let paramsHtml = "";
  // P0-2 非审查原子 schema 支持：优先读后端 inputs_schema [{name,required,type,default,edge_ok}]，
  // 回退到 a.inputs(字段名)。显示必填/类型/默认值；缺失必填红框提示；text/json 用多行编辑。
  const schemaList = (a.inputs_schema && a.inputs_schema.length)
    ? a.inputs_schema
    : (a.inputs || []).map((nm) => ({ name: nm, required: false, type: "str", default: "", edge_ok: true }));
  const hasPathInput = schemaList.some((s) => s.name === "path");
  const pathVal = n.params["path"];
  const isSatisfied = (s) => {
    // 路径型原子已给 path 时，code/content/task 由 path 文件推导，视为已满足(不误拦默认绿图)。
    if (hasPathInput && pathVal && s.name !== "path") return true;
    const v = n.params[s.name];
    return !(v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0));
  };
  const missingReq = schemaList.filter((s) => s.required && !isSatisfied(s)).map((s) => s.name);
  if (schemaList.length) {
    paramsHtml = schemaList.map((s) => {
      const inp = s.name;
      let v = n.params[inp];
      if (v === undefined || v === null) v = s.default ?? "";
      const isReq = s.required;
      const unmet = isReq && !isSatisfied(s);
      const star = isReq ? `<span class="req-star" title="必填"> *</span>` : "";
      const typeTag = `<span class="type-tag" title="类型: ${escAttr(s.type)}${s.edge_ok ? ", 可承接上游边数据" : ""}">${escAttr(s.type)}${s.edge_ok ? " ⇐边" : ""}</span>`;
      const warn = unmet ? `<span class="req-warn">缺必填</span>` : "";
      const ph = s.default ? `默认: ${escAttr(s.default)}` : (inp === "path" ? "如: bad_sample.py (target内相对)" : "");
      const jsonType = s.type === "json" || inp === "steps" || inp === "chain" || inp === "items" || inp === "params" || inp === "outputs";
      const multi = s.type === "text" || s.type === "code" || s.type === "content" || inp === "code" || inp === "data" || inp === "failure";
      const valTxt = (typeof v === "object") ? JSON.stringify(v) : String(v ?? "");
      const boxHtml = (multi || jsonType)
        ? `<textarea rows="1" data-p="${escAttr(inp)}" ${unmet ? `style="border-color:var(--err);background:var(--err-soft)"` : ""} placeholder="${escAttr(ph)}">${esc(valTxt)}</textarea>`
        : `<input data-p="${escAttr(inp)}" value="${escAttr(valTxt)}" ${unmet ? `style="border-color:var(--err);background:var(--err-soft)"` : ""} placeholder="${escAttr(ph)}">`;
      return `<div class="form-row"><label style="min-width:70px" title="${escAttr(inp)}${s.edge_ok ? " 可承接上游边数据" : ""}">${esc(inp)}${star} ${typeTag} ${warn}</label>${boxHtml}</div>`;
    }).join("");
    if (missingReq.length) {
      paramsHtml += `<div class="form-row note req-warn-note" style="color:var(--err)">⚠ 运行前请补必填: ${esc(missingReq.join(", "))}</div>`;
    }
  } else {
    paramsHtml = "<div class='note'>该原子无声明输入(或走默认)</div>";
  }
  box.innerHTML =
    `<div><b>${esc(cnName(n.atom))}</b> <span class="note">(后端: ${esc(n.atom)}, ${a.origin === "ext" ? "扩展" : "核心"})</span></div>` +
    `<div class="form-row"><label>能力</label><select id="prop-cap">${opts}</select></div>` +
    `<div class="form-row"><label>标签</label><input id="prop-label" value="${escAttr(n.label)}"></div>` +
    `<div class="form-row"><label>输入参数</label></div>` + paramsHtml +
    `<div class="form-row"><button id="prop-del" class="mini danger">删除节点</button>` +
    `<button id="prop-ok" class="mini primary">应用</button></div>`;
  box.querySelectorAll("[data-p]").forEach((el) => el.addEventListener("input", () => { n.params[el.dataset.p] = el.value; }));
  $("prop-cap") && $("prop-cap").addEventListener("change", (e) => { n.capability = e.target.value; drawCanvas(); });
  $("prop-label") && $("prop-label").addEventListener("input", (e) => { n.label = e.target.value || cnName(n.atom); });
  $("prop-del") && $("prop-del").addEventListener("click", () => {
    S.canvas.nodes = S.canvas.nodes.filter((x) => x.id !== n.id);
    S.canvas.edges = S.canvas.edges.filter((e2) => e2.from !== n.id && e2.to !== n.id);
    S.canvas.loopEdges = (S.canvas.loopEdges || []).filter((le) => le.from !== n.id && le.to !== n.id);
    S.canvas.selected = null; renderCanvas(); renderProps();
  });
  $("prop-ok") && $("prop-ok").addEventListener("click", () => { drawCanvas(); flash("节点参数已应用"); });
}
$("btn-clear-canvas").addEventListener("click", () => {
  S.canvas.nodes = []; S.canvas.edges = []; S.canvas.loopEdges = []; S.canvas.selected = null;
  renderCanvas(); renderProps(); $("pipe-log").innerHTML = "";
});
$("btn-validate").addEventListener("click", () => {
  const v = S.canvas.nodes.length ? "画布有 " + S.canvas.nodes.length + " 节点 / " + S.canvas.edges.length + " 实线边 / " + (S.canvas.loopEdges || []).length + " 循环反馈边(运行时会做拓扑校验)" : "画布为空";
  appendPipeLog("ℹ " + v);
});
/* P0-2 运行时必填校验：返回 [{id,label,atom,missing:[...]}]。路径型原子已给 path 则 code/content 视为可推导。
   供 btn-run-pipe 运行前拦截明显缺参(如 code-plan 缺 task / guard 缺 code)，避免静默 FAIL。 */
function validateRequiredParams(nodes) {
  const out = [];
  for (const n of nodes) {
    const a = S.atomMap[n.atom] || {};
    const schemaList = (a.inputs_schema && a.inputs_schema.length)
      ? a.inputs_schema
      : (a.inputs || []).map((nm) => ({ name: nm, required: false, type: "str", default: "", edge_ok: true }));
    const hasPath = schemaList.some((s) => s.name === "path");
    const pathVal = n.params["path"];
    const need = schemaList.filter((s) => {
      if (!s.required) return false;
      if (hasPath && pathVal && s.name !== "path") return false;   // path 已给 → 文件型推导
      const v = n.params[s.name];
      const filled = !(v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0));
      if (filled) return false;
      // 有上游边注入该输入名视为已满足
      if (S.canvas.edges.some((e) => e.to === n.id && (e.input_name || "input") === s.name)) return false;
      // schema 有非空默认值视为可满足
      if (s.default !== undefined && s.default !== null && s.default !== "") return false;
      return true;
    }).map((s) => s.name);
    if (need.length) out.push({ id: n.id, label: n.label, atom: n.atom, missing: need });
  }
  return out;
}
$("btn-run-pipe").addEventListener("click", async () => {
  if (!S.canvas.nodes.length) { flash("画布为空: 先拖入原子"); return; }
  // P0-2 运行时校验必填：阻断明显缺参的非路径型原子(如 code-plan 需 task)，避免单点跑必 FAIL。
  // 路径型原子(code-review/code-test 等) 已给 path 则由文件推导 code/content，不误拦。
  const missing = validateRequiredParams(S.canvas.nodes);
  if (missing.length) {
    const brief = missing.slice(0, 4).map((m) => `${m.label}(${m.atom}) 缺 ${m.missing.join("、")}`).join("；") + (missing.length > 4 ? ` …共${missing.length}项` : "");
    appendPipeLog("❌ 有节点缺必填参数(已拦截): " + brief);
    flash("缺必填参数: " + brief);
    missing.forEach((m) => selectNode(m.id));
    return;
  }
  const r = await POST("/api/pipeline/run", { name: $("pipe-name").value || "管道", graph: S.canvas });
  if (!r.ok) {
    appendPipeLog("❌ " + (r.error || "校验失败"));
    flash("管道校验失败: " + r.error); return;
  }
  appendPipeLog(`▶ 管道已启动 ${r.data.run_id} — ${S.canvas.nodes.length} 节点`);
  S.canvas.nodes.forEach((n) => { n.status = "running"; });
  drawCanvas();
});
async function loadPipelines() {
  const r = await GET("/api/pipelines");
  if (r.ok && r.data.runs) {
    const last = r.data.runs[0];
    if (last) appendPipeLog(`🕘 历史管道 #${last.id}: ${last.name} — ${last.summary}`.slice(0, 200));
  }
}

/* ═══════════ 黑箱调试 ═══════════ */
$("btn-debug").addEventListener("click", async () => {
  const failure = $("dbg-failure").value.trim();
  const file = $("dbg-file").value.trim();
  if (!failure && !file) { flash("请提供失败描述或目标文件"); return; }
  // 前置校验：目标文件必须存在于当前审查对象(target_root)内，避免无谓启动调试回路
  if (file) {
    const chk = await GET("/api/file?path=" + encodeURIComponent(file) + "&view=code");
    if (!chk.ok) { flash("目标内不存在该文件: " + file + " — " + (chk.error || "请先确认路径在审查对象内")); return; }
  }
  const r = await POST("/api/debug", { failure, file, test_cmd: $("dbg-test").value.trim() });
  if (!r.ok) { flash("调试启动失败: " + r.error); return; }
  S.debugRuns[r.data.run_id] = { steps: [] };
  $("dbg-steps").innerHTML = "<div class='step-card'><b>回路已启动</b> run_id=" + esc(r.data.run_id) + "</div>";
  refreshDebug(r.data.run_id);
});
function addDbgStep(runId, p) {
  S.debugRuns[runId] = S.debugRuns[runId] || { steps: [] };
  S.debugRuns[runId].steps.push(p);
  renderDbgSteps(runId);
}
function appendDbgStep(runId, p) {
  S.debugRuns[runId] = S.debugRuns[runId] || { steps: [] };
  S.debugRuns[runId].steps.push(p);
  renderDbgSteps(runId);
}
function renderDbgSteps(runId) {
  const rs = S.debugRuns[runId];
  if (!rs) return;
  const box = $("dbg-steps");
  const states = ["IDLE", "REPRO", "MINIMIZE", "RETRIEVE", "LOCATE", "FIX", "REGRESS", "SEDIMENT", "DONE"];
  let progress = "";
  const curIdx = Math.max(0, states.findIndex((s) => s === (rs.steps[rs.steps.length - 1] || {}).state));
  progress = `<div class="prog-bar"><i style="width:${Math.round(((curIdx + 1) / states.length) * 100)}%"></i></div>` +
    `<div style="font-size:11px;color:var(--muted)">状态机: ${states.map((s, i) => (i < curIdx ? "✅ " : i === curIdx ? "▶ " : "") + s).join(" → ")}</div>`;
  box.innerHTML = progress +
    rs.steps.slice(-14).map((st) => {
      const cls = st.state === "DONE" ? " done" : st.detail && st.detail.includes("失败") ? " err" : "";
      return `<div class="step-card${cls}"><span class="st">${esc(st.state)}</span>` +
        (st.detail ? `<div class="detail">${esc(st.detail)}</div>` : "") +
        (st.evidence ? `<div class="ev">${esc(st.evidence)}</div>` : "") + `</div>`;
    }).join("");
}
async function refreshDebug(runId) {
  const r = await GET("/api/debug/" + runId);
  if (!r.ok) return;
  const st = r.data;
  S.debugRuns[runId] = { steps: (st.steps || []).map((s) => ({ state: s.state, detail: s.detail, evidence: s.evidence })), state: st };
  renderDbgSteps(runId);
  if (st.status === "success" || st.status === "failed" || st.status === "done" || st.ok !== null && st.ok !== undefined) {
    const ok = st.ok;
    const tail = document.createElement("div");
    tail.className = "step-card " + (ok ? "done" : "err");
    tail.innerHTML = (ok ? "✅ 调试成功: 补丁已应用且回归全绿" : "❌ 调试失败(诚实报告)") +
      (ok && st.patch ? `<div class="ev">${esc(st.patch)}</div>` : "") +
      (st.evidence ? `<div class="ev">${esc(st.evidence)}</div>` : "") +
      (st.hit ? `<div class="detail">历史命中: 相似度 ${st.hit}</div>` : "");
    $("dbg-steps").appendChild(tail);
    loadDebugHistory();
  }
}
function pollRunning() {
  Object.keys(S.debugRuns).forEach((rid) => {
    if (S.debugRuns[rid].state && S.debugRuns[rid].state.ok !== null) return;
    refreshDebug(rid);
  });
}
async function loadDebugHistory() {
  const r = await GET("/api/debug/history?q=");
  if (!r.ok) return;
  const box = $("dbg-history");
  const rows = r.data.hits || [];
  if (!rows.length) { box.innerHTML = "<div class='note'>调试历史为空 — 跑一次调试后失败/修复对将沉淀于此</div>"; return; }
  box.innerHTML = rows.map((h) => {
    const steps = (h.steps || []).map((s) => s.state).join("→");
    return `<div class="hist-item ${h.ok ? "ok" : "fail"}">` +
      `<div class="h-f">${h.ok ? "✅" : "❌"} ${esc((h.failure || "").slice(0, 60))}</div>` +
      `<div class="h-meta">${esc(h.file || "?")} · ${esc(steps)} · 轮次 ${h.rounds}</div>` +
      (h.patch ? `<div class="ev" style="background:#f4f7fb;border-radius:4px;padding:4px;margin-top:4px;font-family:Consolas;font-size:11px;white-space:pre-wrap">${esc(h.patch.slice(0, 220))}</div>` : "") +
      `<button class="mini retry" data-id="${h.id}">重试上次调试</button>` +
      `<button class="mini use-patch" data-id="${h.id}">复用补丁</button></div>`;
  }).join("");
  box.querySelectorAll(".retry").forEach((b) => b.addEventListener("click", async () => {
    const r = await POST("/api/debug/retry", { id: b.dataset.id });
    if (!r.ok) { flash("重试失败: " + r.error); return; }
    S.debugRuns[r.data.run_id] = { steps: [] };
    refreshDebug(r.data.run_id);
  }));
  box.querySelectorAll(".use-patch").forEach((b) => b.addEventListener("click", () => {
    const h = rows.find((x) => String(x.id) === b.dataset.id);
    if (!h || !h.patch) return;
    const ta = $("dbg-failure");
    ta.value = "复用历史补丁:\n" + h.patch;
    flash("已把历史补丁填入失败描述(可直接启动调试后由 REPRO 验证)");
  }));
}
async function callDebugHistoryHit(errsig) {
  const r = await GET("/api/debug/history/hit?errsig=" + encodeURIComponent(errsig || ""));
  return r.ok ? r.data : null;
}

/* ═══════════ 审查报告 ═══════════ */
$("btn-report").addEventListener("click", async () => {
  const t = $("rep-target").value.trim();
  const r = await POST("/api/report", { path: t || "." });
  if (!r.ok) { flash("报告启动失败: " + r.error); return; }
  $("rep-body").innerHTML = `<div class="prog-bar"><i style="width:4%"></i></div><div>报告生成中 report_id=${esc(r.data.report_id)}（多原子并行，进度见事件流）</div>`;
  $("rep-actions").classList.add("hidden");
  pollReport(r.data.report_id);
});
let activeReportId = null;
async function pollReport(rid) {
  activeReportId = rid;
  const r = await GET("/api/report/" + rid);
  if (!r.ok) return;
  const st = r.data;
  if (st.status === "running") {
    const n = (st.done_atoms || []).length;
    const total = st.total_atoms || 8;   // 用后端 total_atoms 精确进度（不写死 8）
    const bar = $("rep-body").querySelector(".prog-bar i");
    if (bar) bar.style.width = Math.min(95, Math.round((n / total) * 100)) + "%";
    setTimeout(() => pollReport(rid), 1200);
    return;
  }
  renderReport(st, rid);
}
function updateReportProgress(p){
  // 报告进度事件处理(原代码缺失导致 ReferenceError; 补安全占位, 报告正文由 pollReport 驱动)
  const b = $("rep-body");
  if (b && p) {
    b.insertAdjacentHTML("beforeend", `<div class="sec-card"><b>[${esc(p.atom || "")}] 完成</b> <span class="hint">${esc(String(p.summary || p.status || p.node || "").slice(0, 140))}</span></div>`);
    b.scrollTop = b.scrollHeight;
  }
}
function finishReportUi() { if (activeReportId) pollReport(activeReportId); }
function renderReport(st, rid) {
  $("rep-actions").classList.remove("hidden");
  $("rep-export-html").href = "/api/report/" + rid + ".html";
  $("rep-export-md").href = "/api/report/" + rid + ".md";
  let html = `<div class="sec-card"><b>${esc(st.title || "审查报告")}</b> <span class="note">target: ${esc(st.target || "")} · 状态: ${st.ok ? "✅ 完成" : "❌ " + (st.summary || st.error || "失败")}</span>` +
    (st.summary ? `<div>概要: ${esc(st.summary)}</div>` : "") + `</div>`;
  html += "<h4 style='margin:8px 0'>📌 发现（P0/P1/P2 分级）</h4>";
  const findings = st.findings || [];
  if (!findings.length) html += "<div class='note'>暂无发现</div>";
  for (const f of findings) {
    const lv = String(f.level || "P2").toUpperCase();
    html += `<div class="find-item ${lv === "P0" ? "p0" : lv === "P1" ? "p1" : "p2"}">` +
      `<b>${esc(lv)}</b> ${esc(f.file || "")}${f.line ? ":" + f.line : ""} — ${esc(f.msg || "")}` +
      (f.advice ? `<div class="detail">建议: ${esc(f.advice)}</div>` : "") + `</div>`;
  }
  html += "<h4 style='margin:8px 0'>🏗 代码结构</h4>";
  const st2 = st.structure || {};
  if (st2.layers && st2.layers.length) html += `<div class="sec-card"><b>分层</b><div class="sec-body">${esc(JSON.stringify(st2.layers))}</div></div>`;
  if (st2.graph) html += `<div class="sec-card"><b>依赖拓扑</b><div class="sec-body">${esc(JSON.stringify(st2.graph).slice(0, 1500))}</div></div>`;
  if (st2.complexity) html += `<div class="sec-card"><b>复杂度</b><div class="sec-body">${esc(JSON.stringify(st2.complexity))}</div></div>`;
  html += "<h4 style='margin:8px 0'>🔬 原子章节</h4>";
  for (const sc of st.sections || []) {
    html += `<div class="sec-card"><h4>${esc(sc.atom)} ${sc.ok ? "✅" : "❌"}</h4>` +
      (sc.summary ? `<div>${esc(sc.summary)}</div>` : "") +
      (sc.error ? `<div style="color:var(--err)">${esc(sc.error)}</div>` : "") +
      (sc.data ? `<div class="sec-body">${esc(typeof sc.data === "string" ? sc.data : JSON.stringify(sc.data, null, 1).slice(0, 1800))}</div>` : "") +
      `</div>`;
  }
  $("rep-body").innerHTML = html;
  loadReportHistory();
}
async function loadReportHistory() {
  const r = await GET("/api/reports");
  if (!r.ok) return;
  const box = $("rep-history");
  const rows = r.data.reports || [];
  if (!rows.length) { box.innerHTML = "<div class='note'>报告历史为空</div>"; return; }
  box.innerHTML = rows.map((h) =>
    `<div class="hist-item ${h.ok ? "ok" : "fail"}"><div class="h-f">📋 ${esc((h.title || "").slice(0, 50))}</div>` +
    `<div class="h-meta">${esc(h.target || "")} · ${h.ok ? "✅" : "❌"}</div>` +
    `<button class="mini" data-id="${h.id}">打开</button></div>`).join("");
  box.querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
    const r = await GET("/api/report/" + b.dataset.id);
    if (r.ok) renderReport(r.data, b.dataset.id);
  }));
}

/* ═══════════ 模型配置 ═══════════ */
async function loadModels() {
  const r = await GET("/api/models");
  if (!r.ok) { flash("模型配置读取失败: " + r.error); return; }
  S.models = r.data;
  S.providers = r.data.providers || [];
  S.chain = r.data.chain || [];
  renderProviders();
  renderChain();
}
function renderProviders() {
  const box = $("providers-box");
  if (!S.providers.length) { box.innerHTML = "<div class='note'>尚未配置 provider — 点『新增 Provider』添加本地 Ollama 或云端端点</div>"; return; }
  box.innerHTML = S.providers.map((p, i) =>
    `<div class="provider-card" data-id="${esc(p.id)}">` +
    `<div class="p-row"><input class="p-id" value="${escAttr(p.id)}" style="max-width:140px" placeholder="id">` +
    `<input class="p-name" value="${escAttr(p.name)}" style="max-width:150px" placeholder="名称">` +
    `<select class="p-type"><option value="local" ${p.type === "local" ? "selected" : ""}>本地</option><option value="cloud" ${p.type === "cloud" ? "selected" : ""}>云端</option></select>` +
    `<label><input type="checkbox" class="p-active" ${p.active ? "checked" : ""}> 激活</label>` +
    `<button class="mini p-test">测试连通</button><button class="mini danger p-del">删</button></div>` +
    `<div class="p-row"><input class="p-url" value="${escAttr(p.base_url || "")}" placeholder="base_url (OpenAI兼容, 如 http://127.0.0.1:11434)" style="flex:2">` +
    `<input class="p-key" value="${escAttr(p.api_key_status || "")}" placeholder="api_key(留空/含*保留旧值, 脱敏)" style="flex:1"></div>` +
    `<div class="p-row"><input class="p-models" value="${escAttr((p.models || []).join(","))}" placeholder="模型列表(逗号分隔, 首个为默认)" style="flex:2">` +
    `<span class="p-models">key: ${esc(p.api_key_status || "未配置")}</span></div></div>`).join("");
  box.querySelectorAll(".p-test").forEach((b) => b.addEventListener("click", async () => {
    const card = b.closest(".provider-card");
    const pid = card.dataset.id;
    flash("连通测试中...");
    const r = await POST("/api/models/test", { provider_id: pid });
    flash(r.ok ? "✅ " + r.data.detail : "❌ " + (r.data && r.data.error || r.error));
  }));
  box.querySelectorAll(".p-del").forEach((b) => b.addEventListener("click", () => {
    const card = b.closest(".provider-card");
    S.providers = S.providers.filter((p) => p.id !== card.dataset.id);
    S.chain = S.chain.filter((c) => c !== card.dataset.id);
    renderProviders(); renderChain();
  }));
}
$("btn-add-provider").addEventListener("click", () => {
  S.providers.push({ id: "provider-" + (S.providers.length + 1), name: "新Provider", type: "local", base_url: "http://127.0.0.1:11434", api_key_status: "", models: [], active: false });
  renderProviders();
});
function renderChain() {
  const box = $("chain-box");
  box.innerHTML =
    `<div class="form-row"><label style="min-width:70px">降级链</label>` +
    `<input id="chain-input" value="${escAttr(S.chain.join(","))}" placeholder="provider id 逗号分隔, 留空=按激活声明序自动" style="flex:1">` +
    `<span class="note" title="点击×移除某 provider">(×移除)</span></div>` +
    `<div style="margin-top:6px">` +
    (S.chain.length
      ? S.chain.map((c) => `<span class="chain-item">${esc(c)} <b onclick="removeChain('${esc(c)}')" style="cursor:pointer">×</b></span>`).join("")
      : "<span class='note'>留空保存 = 激活的 provider 按声明序自动入链</span>") +
    `</div>`;
}
window.removeChain = (id) => {
  S.chain = S.chain.filter((c) => c !== id);
  const ci = $("chain-input"); if (ci) ci.value = S.chain.join(",");
  renderChain();
};
$("btn-save-models").addEventListener("click", async () => {
  const providers = [];
  document.querySelectorAll(".provider-card").forEach((card) => {
    const id = card.querySelector(".p-id").value.trim();
    if (!id) return;
    const modelsStr = card.querySelector(".p-models").value;
    providers.push({
      id, name: card.querySelector(".p-name").value.trim() || id,
      type: card.querySelector(".p-type").value,
      base_url: card.querySelector(".p-url").value.trim(),
      api_key: card.querySelector(".p-key").value.trim(),
      models: modelsStr.split(",").map((s) => s.trim()).filter(Boolean),
      active: card.querySelector(".p-active").checked,
    });
  });
  const chainInput = $("chain-input") ? $("chain-input").value.trim() : S.chain.join(",");
  S.chain = chainInput.split(",").map((s) => s.trim()).filter(Boolean);
  const r = await POST("/api/models", { providers, chain: S.chain });
  if (!r.ok) { flash("保存失败: " + r.error); return; }
  flash("✅ 模型配置已保存(脱敏生效, 即时生效)");
  loadModels();
});
$("btn-test-all").addEventListener("click", async () => {
  for (const p of S.providers) {
    flash("测试 " + p.id + " ...");
    const r = await POST("/api/models/test", { provider_id: p.id });
    if (r.ok && r.data) flash("测试 " + p.id + ": " + (r.data.detail || "ok"));
    else flash("测试 " + p.id + " 失败");
    await new Promise((res) => setTimeout(res, 300));
  }
});

/* ═══════════ 原子扩展 ═══════════ */
$("btn-scaffold").addEventListener("click", async () => {
  const body = {
    name: $("ext-name").value.trim(), domain: $("ext-domain").value.trim(),
    capability: $("ext-cap").value.trim(),
    inputs: $("ext-inputs").value.split(",").map((s) => s.trim()).filter(Boolean),
    description: $("ext-desc").value.trim(),
  };
  if (!body.name || !body.capability) { flash("扩展名与能力必填"); return; }
  const r = await POST("/api/atoms/scaffold", body);
  if (!r.ok) { flash("模板生成失败: " + r.error); return; }
  $("ext-log").insertAdjacentHTML("beforeend", `<div>✅ 模板已生成 ${esc(body.name)} → ${esc(r.data.path || "")}<br><pre style="font-size:11px">${esc(JSON.stringify(r.data.manifest, null, 1))}</pre></div>`);
  flash("扩展模板已生成: " + body.name);
  loadExtList();
});
$("btn-register").addEventListener("click", async () => {
  const r = await POST("/api/atoms/register", { name: $("ext-name").value.trim() });
  if (!r.ok) { flash("注册失败: " + r.error); return; }
  $("ext-log").insertAdjacentHTML("beforeend", `<div>✅ 已注册 ${esc($("ext-name").value.trim())}（加壳不改核心）</div>`);
  loadExtList(); loadAtoms();
});
$("btn-unregister").addEventListener("click", async () => {
  const r = await POST("/api/atoms/unregister", { name: $("ext-name").value.trim() });
  if (!r.ok) { flash("注销失败: " + r.error); return; }
  $("ext-log").insertAdjacentHTML("beforeend", `<div>已注销 ${esc($("ext-name").value.trim())}</div>`);
  loadExtList(); loadAtoms();
});
$("btn-selfcheck").addEventListener("click", async () => {
  const r = await GET("/api/atoms/selfcheck");
  if (!r.ok) { flash("自检失败: " + r.error); return; }
  const d = r.data;
  $("ext-log").insertAdjacentHTML("beforeend",
    `<div>🔍 自检: 扩展 ${d.ext_count || 0}, 错误 ${(d.errors || []).length}<pre style="font-size:11px">${esc(JSON.stringify(d.errors || [], null, 1))}</pre></div>`);
});
async function loadExtList() {
  const r = await GET("/api/atoms");
  if (!r.ok) return;
  // 用后端 extensions_all（含已停用），展示启停开关；缺省回退到 origin==="ext"
  const all = (r.data && r.data.extensions_all) || (r.data.atoms || []).filter((a) => a.origin === "ext");
  const box = $("ext-list");
  if (!all.length) { box.innerHTML = "<div class='note'>尚未挂接扩展（点左侧『生成原子模板』开发新原子）</div>"; return; }
  box.innerHTML = all.map((a) => {
    const on = a.enabled !== false;
    return `<div class="hist-item ${on ? "ok" : "fail"}">` +
      `<div class="h-f">🧬 ${esc(a.name)} <span class="note">v${esc(a.version)}</span> ` +
      `<span class="note" style="color:${on ? "var(--accent)" : "var(--err)"}">${on ? "✓ 启用" : "⏸ 停用"}</span></div>` +
      `<div class="h-meta">${esc(a.domain)} · ${esc((a.provides || []).join(", "))}</div>` +
      `<button class="mini ext-toggle" data-name="${escAttr(a.name)}" data-enable="${on ? "0" : "1"}">${on ? "停用" : "启用"}</button></div>`;
  }).join("");
  box.querySelectorAll(".ext-toggle").forEach((b) => b.addEventListener("click", async () => {
    const enable = b.dataset.enable === "1";
    const name = b.dataset.name;
    const rr = await POST("/api/atoms/enabled", { name, enabled: enable });
    if (!rr.ok) { flash("切换失败: " + rr.error); return; }
    flash("扩展 " + name + (enable ? " 已启用" : " 已停用"));
    loadExtList(); loadAtoms();
  }));
}

/* ═══════════ 架构图(39原子一张图) ═══════════ */
function renderArch() {
  const box = $("arch-svg-box");
  if (!S.atoms.length) { box.innerHTML = "<div class='placeholder'>原子库未加载</div>"; return; }
  const W = 1240;
  const groups = {};
  for (const a of S.atoms) {
    (groups[a.domain] = groups[a.domain] || []).push(a);
  }
  const domains = Object.keys(groups).sort();
  const colW = 236, rowH = 96, cols = 5;
  const rows = Math.ceil(domains.length / cols);
  const H = 150 + rows * rowH;
  let svg = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" font-family="Microsoft YaHei,PingFang SC,sans-serif">`;
  svg += `<rect x="0" y="0" width="${W}" height="34" rx="6" fill="#2563eb"/><text x="14" y="23" fill="#fff" font-size="14" font-weight="600">CodeAgent Lab — 原子化完整代码智能体架构一张图</text>`;
  svg += `<rect x="0" y="40" width="${W}" height="26" rx="4" fill="#e0e7ff"/><text x="14" y="58" fill="#3730a3" font-size="12">Interface 壳层(lab/): 可视化客户端 · 拖拽编排 · 黑箱调试状态机 · 模型配置 · 报告聚合 · 对话路由 — 加壳不改核心</text>`;
  svg += `<rect x="0" y="72" width="${W}" height="26" rx="4" fill="#eff6ff"/><text x="14" y="90" fill="#2563eb" font-size="12">Application 层: codeagent.py 统一入口 · agent_runtime 能力路由 · agent_loader 拓扑加载 · chain/guard/evolve 组装链</text>`;
  let y0 = 104, x0 = 10;
  const pad = 8, cellW = (W - 2 * x0 - (cols - 1) * pad) / cols;
  domains.forEach((d, di) => {
    const gx = x0 + (di % cols) * (cellW + pad);
    const gy = y0 + Math.floor(di / cols) * rowH;
    svg += `<rect x="${gx}" y="${gy}" width="${cellW}" height="${rowH - 6}" rx="6" fill="#fbfcfe" stroke="#d5dbe3"/>`;
    svg += `<text x="${gx + 8}" y="${gy + 15}" font-size="11" font-weight="600" fill="#2563eb">${esc(d)}</text>`;
    groups[d].forEach((a, ai) => {
      const ay = gy + 20 + ai * 15;
      svg += `<text x="${gx + 8}" y="${ay + 9}" font-size="9.5" fill="#1e293b">${esc(a.origin === "ext" ? "🧬" : "⚙")} ${esc(cnName(a.name))}</text>`;
    });
  });
  // 图例
  svg += `<text x="10" y="${H - 26}" font-size="11" fill="#64748b">图例: ⚙ 核心原子(只读复用) · 🧬 扩展原子(壳层挂接, 不改核心) · 数据全在本机(SQLite/JSON), 模型仅连用户配置端点</text>`;
  if (S.atoms.length) svg += `<text x="10" y="${H - 10}" font-size="11" fill="#64748b">合计 ${S.atoms.length} 原子 · ${domains.length} 领域 · 中文功能名见原子编排调色板(显示中文, 调用走后端原子名)</text>`;
  svg += "</svg>";
  box.innerHTML = svg;
}

/* ═══════════ 对话流 ═══════════ */
function appendChat(role, content, intent) {
  S.chat.push({ role, content, intent });
  const box = $("chat-box");
  const el = document.createElement("div");
  el.className = "msg " + role;
  el.innerHTML = content.split("\n").map((l) => esc(l)).join("<br>") +
    (intent ? `<div class="intent">[意图: ${esc(intent)}]</div>` : "");
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}
async function sendChat() {
  const msg = $("chat-input").value.trim();
  if (!msg) return;
  $("chat-input").value = "";
  appendChat("user", msg);
  $("chat-panel").classList.remove("hidden");
  const r = await POST("/api/chat", { message: msg, history: S.chat.slice(-8) });
  if (!r.ok) { appendChat("assistant", "对话处理失败: " + r.error, "error"); return; }
  appendChat("assistant", r.data.reply, r.data.intent + "(" + r.data.source + ")");
  // 动作联动视图
  const act = r.data.action;
  if (act === "debug") { switchView("debug"); $("dbg-failure").value = msg; }
  if (act === "report") switchView("report");
  if (act === "models") switchView("models");
  if (act === "atoms" || act === "tree") { switchView("workspace"); loadTree(); }
  if (act === "review_file" || act === "test_file" || act === "impact" || act === "graph") {
    if (S.activeFile) { switchView("workspace"); }
  }
  if (r.data.action_data && r.data.action_data.ok !== undefined && act === "review_file") {
    flash(r.data.reply);
  }
}
$("btn-chat-send").addEventListener("click", sendChat);
$("chat-input").addEventListener("keydown", (ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendChat(); } });
$("btn-chat-toggle").addEventListener("click", () => $("chat-panel").classList.toggle("hidden"));
$("btn-chat-close").addEventListener("click", () => $("chat-panel").classList.add("hidden"));

/* ═══════════ 启动 ═══════════ */
boot();