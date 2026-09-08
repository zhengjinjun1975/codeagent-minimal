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
  view: "workspace",
};

/* ═══════════ 视图切换 ═══════════ */
function switchView(v) {
  S.view = v;
  document.querySelectorAll("#tabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  document.querySelectorAll("#main .view").forEach((sec) => sec.classList.toggle("active", sec.id === "view-" + v));
  if (v === "canvas") renderCanvas();
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
  loadAtoms();
  loadTree();
  loadEvents(true);
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
  "code-test": "单元测试", "lab-echo96914": "回显测试", "lab-echo96962": "回显测试",
  "lab-echo97004": "回显测试3", "lab-echo97043": "回显测试4", "lab-echo97171": "回显测试5",
  "lab-harness": "执行编排控制器", "lab-loop": "循环控制",
  "browser-smoke": "浏览器冒烟"
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
    // ── 扩展原子 ──
    { atom: "lab-echo96914", cap: "echo.summarize96914", label: "扩展:回显测试", x: 570, y: 60, params: {} },
    // ── harness 执行编排控制器(执行器/调度器/门控) ──
    { atom: "lab-harness", cap: "harness.control", label: "执行编排控制器", x: 570, y: 270,
      params: { mode: "seq", retries: 1, steps: [
        { atom: "lab-echo96914", capability: "echo.summarize96914", params: { input: "harness二次编排" } },
        { atom: "lab-echo96962", capability: "echo.summarize96962", params: { input: "harness门控校验" } }
      ] } },
    // ── 循环控制原子(重试/迭代, 内部真实循环+反馈) ──
    { atom: "lab-loop", cap: "loop.retry",  label: "循环:重试控制", x: 900, y: 90,
      params: { target_atom: "lab-echo96914", target_cap: "echo.summarize96914",
                attempts: 2, params: { input: "重试控制" } } },
    { atom: "lab-loop", cap: "loop.iterate", label: "循环:迭代控制", x: 900, y: 320,
      params: { target_atom: "lab-echo96914", target_cap: "echo.summarize96914",
                items: ["轮次1", "轮次2"], params: { input: "迭代控制" } } },
    // ── 交付报告(聚合汇总) ──
    { atom: "code-deliver", cap: "deliver.report", label: "交付报告", x: 1170, y: 205,
      params: { chain: ["codereview.review", "security.scan", "test.run", "impact.method",
                        "deadcode.scan", "doc.stale", "depscan.scan", "archreview.layers",
                        "harness.control", "loop.retry", "loop.iterate"],
                outputs: {} } }
  ];
  S.canvas.nodes = defs.map((d) => {
    const a = S.atomMap[d.atom] || {};
    return { id: uid("n"), atom: d.atom, label: d.label, x: d.x, y: d.y,
             capability: d.cap || (a.provides && a.provides[0]) || "",
             params: d.params || {}, status: null };
  });
  const byLabel = (l) => S.canvas.nodes.find((n) => n.label === l);
  const HARNESS = byLabel("执行编排控制器"), DELIVER = byLabel("交付报告"),
        RETRY = byLabel("循环:重试控制"), ITER = byLabel("循环:迭代控制");
  // 可执行数据流(实线): 各审查原子+扩展 → harness 聚合 → 交付报告; 循环控制 → 交付报告
  const scan = ["代码审查", "安全扫描", "单元测试", "方法级影响",
                "死代码", "文档新鲜度", "依赖SCA", "架构审查", "扩展:回显测试"];
  S.canvas.edges = scan.map((l) => ({ from: byLabel(l).id, to: HARNESS.id, map: "summary", input_name: "evidence" }))
    .concat([
      { from: HARNESS.id, to: DELIVER.id, map: "summary", input_name: "evidence" },
      { from: RETRY.id,   to: DELIVER.id, map: "summary", input_name: "evidence" },
      { from: ITER.id,    to: DELIVER.id, map: "summary", input_name: "evidence" }
    ]);
  // 循环/反馈语义(虚线可视化; 不参与拓扑排序 → 保证可执行)
  S.canvas.loopEdges = [
    { from: RETRY.id,   to: byLabel("单元测试").id, kind: "重试反馈" },
    { from: ITER.id,    to: byLabel("代码审查").id, kind: "迭代反馈" },
    { from: HARNESS.id, to: RETRY.id, kind: "门控调度" },
    { from: HARNESS.id, to: ITER.id,  kind: "门控调度" }
  ];
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

/* 编排预设：真实浏览器 UI 冒烟（browser-smoke）—— 对目标 Web 前端逐路由做无白屏/无错误边界/关键文本/console 检查。
   目标默认指向本 Lab 自己的前端(当前 origin)做自检；可在节点属性面板改 url。
   预设图 = 单个 browser-smoke 节点(无需连线, 单节点即可运行)，点按钮载入画布后按「▶ 运行管道」执行。 */
function uiSmokePreset() {
  const base = (location.protocol) + "//" + (location.host);   // 默认冒烟本 Lab 自身前端
  const routes = [
    { path: "/", expectText: ["CodeAgent Lab", "本地完整代码智能体"] },
    { path: "/", expectText: ["文件树"] },
    { path: "/no-such-route-zzz", expectText: ["这个页面不存在"] }  // 期望 FAIL：不存在路由
  ];
  const a = S.atomMap["browser-smoke"] || {};
  const id = uid("n");
  S.canvas.nodes = [{
    id, atom: "browser-smoke", label: cnName("browser-smoke"),
    x: 120, y: 80, capability: (a.provides && a.provides[0]) || "browsersmoke.run",
    params: { url: base, routes: JSON.stringify(routes, null, 1), wait_sec: "8", min_body: "40" },
    status: null
  }];
  S.canvas.edges = [];
  S.canvas.loopEdges = [];
  S.canvas.selected = id;
  S.canvas._defaultLoaded = true;   // 已载入预设 → 不自动覆盖为默认审查图
  renderCanvas();
  renderProps();
  selectNode(id);
  const lg = $("pipe-log"); if (lg) lg.innerHTML = `<div>[🧪] 已载入编排预设「浏览器 UI 冒烟」(browser-smoke)：对 <b>${esc(base)}</b> 逐路由冒烟（期望 2 PASS + 1 FAIL:不存在路由）— 点「▶ 运行管道」真起 headless Chrome 执行。目标/路由可在右侧属性面板改。</div>`;
}
$("btn-ui-smoke-preset") && $("btn-ui-smoke-preset").addEventListener("click", uiSmokePreset);

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
    el.title = `后端原子: ${a.name} · ${a.domain} · 拖到画布或双击添加`;
    el.innerHTML = `<div class="a-name">${esc(cn)}</div><div class="a-domain">${esc(a.name)} · ${esc(a.version)} · ${a.origin === "ext" ? "扩展" : "核心"}</div>`;
    el.addEventListener("dragstart", (ev) => { ev.dataTransfer.setData("text/plain", a.name); });
    el.addEventListener("dblclick", () => addCanvasNode(a.name, 60 + Math.random() * 500, 60 + Math.random() * 300));
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
  svg.setAttribute("viewBox", `0 0 ${VBW} ${VBH}`);
  let html = `<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#94a3b8"/></marker><marker id="arrowLoop" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#f59e0b"/></marker></defs>`;
  const { nodes, edges } = S.canvas;
  for (const e of edges) {
    const a = nodes.find((n) => n.id === e.from), b = nodes.find((n) => n.id === e.to);
    if (!a || !b) continue;
    const cx = a.x + 120, cy = a.y + 22, cx2 = b.x, cy2 = b.y + 22;
    let cls = "svg-link";
    if (b.status === "done") cls += " active";
    if (b.status === "fail") cls += " fail";
    html += `<path class="${cls}" d="M${cx},${cy} C${cx + 50},${cy} ${cx2 - 50},${cy2} ${cx2},${cy2}"/>`;
  }
  // 循环/反馈语义边(虚线橙色, 可视化; 不参与拓扑排序)
  for (const le of (S.canvas.loopEdges || [])) {
    const a = nodes.find((n) => n.id === le.from), b = nodes.find((n) => n.id === le.to);
    if (!a || !b) continue;
    const cx = a.x + 120, cy = a.y + 22, cx2 = b.x, cy2 = b.y + 22;
    html += `<path class="svg-link loop-edge" style="stroke:#f59e0b;stroke-dasharray:7 5" marker-end="url(#arrowLoop)" title="${esc(le.kind || "循环/反馈")}" d="M${cx},${cy} C${cx + 80},${cy} ${cx2 - 80},${cy2} ${cx2},${cy2}"/>`;
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
    html += `<g class="node-g" data-id="${n.id}" transform="translate(${n.x},${n.y})">` +
      `<rect x="0" y="0" width="120" height="44" rx="6" fill="${color}" stroke="${border}" stroke-width="${sel ? 3 : 1.6}"/>` +
      `<circle class="port-in" cx="0" cy="22" r="5.5" fill="#3b82f6" stroke="#fff" title="输入端口"/>` +
      `<circle class="port-out" cx="120" cy="22" r="6" fill="#2563eb" stroke="#fff" title="输出端口"/>` +
      `<text class="node-title" x="10" y="19" font-size="13.5" font-weight="700" fill="#1e293b" style="cursor:move">${esc((n.label || cnName(n.atom)).slice(0, 18))}</text>` +
      `<text x="10" y="34" font-size="10" fill="#64748b">${esc(capTxt)}</text>` +
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
    const brief = missing.slice(0, 4).join("；") + (missing.length > 4 ? ` …共${missing.length}项` : "");
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

/* ═══════════ 架构图(32原子一张图) ═══════════ */
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
  svg += `<text x="10" y="${H - 26}" font-size="11" fill="#64748b">图例: ⚙ 核心原子(32, 只读复用) · 🧬 扩展原子(壳层挂接, 不改核心) · 数据全在本机(SQLite/JSON), 模型仅连用户配置端点</text>`;
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