# CodeAgent 安全加固 + 边界防护说明

> 范围：`codeagent-minimal`（19 原子 + bug_deep PoC 沙箱执行 + security-scan + 审查）。
> 目标：加固 CodeAgent **自身安全**（沙箱边界 / 子进程 / 路径 / 输入校验 / 数据不出厂），
> 全部**真实验证**（恶意样本实测），**本地不 push**。
> 回归：全量 pytest **133 passed**（基线 112 + 安全加固单测 21 全绿）。

---

## 1. 沙箱边界加固（bug_deep PoC 沙箱执行）

**文件**：`bug_deep.py` — `run_poc_sandbox()` + 新增沙箱安全工具。

**加固点**（防恶意代码逃逸）：

| 防护 | 实现 | 实测 |
|------|------|------|
| 子进程隔离 | 每份 PoC 跑在**独立临时工作目录** `sandbox_dir`，不再复用共享系统临时目录；`cwd=sandbox_dir` 使相对路径写被限制在沙箱内 | 相对写留在沙箱目录，宿主 CWD 无残留 |
| 超时强杀 | 超时后**强杀整棵进程树**（POSIX `killpg` SIGKILL / Windows `taskkill /T`），防死循环/逃逸 | `while True: pass` → `verdict=timeout` |
| 资源限制 | POSIX `preexec_fn` + `resource.setrlimit`：CPU 30s / 虚拟内存 512MB / 单文件写 1MB / 进程数 64 / 禁 core dump（Windows 依赖超时+输出封顶兜底） | fork 炸弹 → timeout/overflow/failed |
| 输出封顶 | 子进程输出达上限（默认 1MB）即强杀，防无限输出/内存耗尽 DoS | 无限 `print` → `verdict=overflow` |
| 可执行白名单 | `_validate_interpreter` 仅放行**真实存在且可执行**的 Python 解释器，禁任意命令执行 | 无效解释器路径 → ValueError/rejected |
| 环境净化 | 剔除含 token/secret/key/password/proxy 等敏感 env 再注入子进程（数据不出厂） | 子进程读不到 `SECRET_API_KEY`/`HTTP_PROXY` |
| 进程组隔离 | POSIX `start_new_session=True` / Windows `CREATE_NEW_PROCESS_GROUP`，可独立整树回收 | 沙箱异常不影响宿主进程 |

**诚实边界（如实标注）**：stdlib 沙箱**不承诺**拦截「绝对路径写」与「网络外呼」（恶意 PoC 可写任意绝对路径/发起外呼）。真要做 OS 级文件系统/网络隔离需 Docker/VM/seccomp，属 stdlib 范围外，已如实记录于 `test_documented_sandbox_residual_limitation`，不夸大。

## 2. 子进程安全（原子执行 subprocess）

- 沙箱以**参数列表** `[interp, tmp]` 启动，`shell=False`，杜绝把 PoC 当 shell 命令注入；
- `agents/` 下各原子壳（code-review / code-dispatch / mcp-client）均为 `shell=False` + 参数列表 + 白名单受信命令；
- **测试锚定**：`test_no_shell_true_in_subprocess_calls` 全仓扫描 `subprocess.*` 无 `shell=True`；
- 新增 `_run_limited`：统一封装超时 / 输出封顶 / 进程组 / 捕获 stdout/stderr/rc。

## 3. 路径安全（文件读取路径穿越防护）

**新增**：`pathguard.py`（`safe_resolve` / `safe_read_text` / `assert_within`）。

- `normpath` + `realpath` **双归一化**，可选 `base` 根目录做包含性校验；
- 拒绝 `../`、`..\..\`、绝对路径逃逸、符号链接逃逸；
- **接线**：`bug_deep.threat_model_file/project`、`security_scan.scan_security_file/project/detect_secrets_file`、bug-deep 原子壳 `_content` 全部改走安全读取；
- 项目扫描对每个文件 `assert_within(根)` 防穿越逃逸；
- **实测**：`../../../Windows/win.ini`、`E:/secrets/key.txt` → 拒绝；`bug_deep.py`（根内）→ 放行。

## 4. 输入校验（防崩溃/注入）

**新增**：`validate_poc()` + `run_poc_sandbox` 入口强校验。

- 非字符串（None/int/list/dict）→ `rejected`；
- 超长（>200KB）→ `rejected`；
- 空/纯空白 → `rejected`；
- timeout 非法（负数/0/超上限 60/非数值/bool）→ `rejected`；
- **实测**：全部恶意/异常输入返回 `ran=False, verdict=rejected`，不崩溃、不执行。

## 5. 数据不出厂（结果样本不外泄，本地无外网）

- 沙箱子进程 **env 净化**：剔除凭据/proxy env，恶意 PoC 读 `os.environ` 拿不到主机密钥/代理（实测 `SECRET_API_KEY`/`HTTP_PROXY`/`AWS_SECRET_ACCESS_KEY` 均 `HIDDEN`）；
- **AST 审计**：`bug_deep.py` / `security_scan.py` / `pathguard.py` **无** `socket`/`requests`/`urllib`/`http` import、无 `urlopen`/`requests.get`/`socket.socket` 调用（扫描器关键字列表不计）；
- 全流程本地执行，零第三方依赖，无外网调用。

## 6. 安全测试（恶意样本实测）

`tests/test_security_hardening.py`（21 项，全绿）覆盖：
- 正常 PoC → `exploitable`（回归不退化）
- 死循环 → `timeout`（逃逸计算被含）
- 无限输出 → `overflow`（DoS 被含）
- fork 炸弹 → 被拦截
- 相对路径写 → 被隔离在沙箱目录
- 命令注入面 → 全仓无 `shell=True`
- 路径穿越（`../` / 绝对路径）→ 拒绝；根内放行
- 输入校验（非字符串/超长/异常 timeout/空）→ 全部 `rejected`
- env 净化（secret/proxy/AWS key 不外泄）
- 本地无网络依赖（AST 审计）
- 沙箱目录清理无残留
- 诚实边界标注

## 7. 回归

- 基线：112 passed（加固前）
- 加固后全量：**133 passed**（`pytest tests/`，112 + 21 安全加固单测）

## 8. 交付物清单

| 文件 | 说明 |
|------|------|
| `pathguard.py` | 新增：路径穿越防护守卫（normpath+realpath+根白名单） |
| `bug_deep.py` | 加固：沙箱子进程隔离/超时/资源限制/输出封顶/可执行白名单/env 净化 + 输入校验 + 安全路径读取 |
| `security_scan.py` | 加固：文件读取改走 pathguard，项目扫描防穿越 |
| `agents/bugdeep/bug-deep/main.py` | 原子壳 `_content` 走安全读取 |
| `tests/test_security_hardening.py` | 新增：21 项安全加固单测（恶意样本实测） |
| `docs/SECURITY_HARDENING.md` | 本说明 |

**git 状态**：以上改动全部**本地未提交、未 push**。
