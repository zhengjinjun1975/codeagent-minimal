# git-ops

把 git 的日常操作（看状态/看差异/看日志/提交/切分支）做成 CodeAgent 的原子能力，纯标准库、数据不出厂。

## 能力（5 个）

| 能力名 | 必要入参 | 返回的关键字段 |
| --- | --- | --- |
| git.status | path | branch / clean / changed / ahead / behind |
| git.diff | path, staged=False, base=None | diff / files / stat |
| git.log | path, n=10 | commits[{sha,subject,author,ts}] |
| git.commit | path, message, paths=None | sha / committed |
| git.branch | path, action=list\|create\|switch, name | branches / current / created / switched |

## 安全红线（重要）

本原子**不实现也不转发** push / reset --hard / clean -fd / checkout -f / rebase / merge / force 系列；
能力面里根本不暴露这些名字，这是设计而不是缺参数。凡是远端操作，一律由人来做。

## 用法

```bash
# 原子自测入口（看当前仓库状态）
python agents/tools/git-ops/main.py --capability git.status --path .
```

上层调用：`run_capability("git.status", path="D:/某仓库")`；提交：`run_capability("git.commit", path=..., message="fix: xx")`。

## 可靠性

非 git 目录或 git 不在 PATH 时返回 degraded 信封而不抛异常；所有 git 子命令走参数列表（无 shell=True）；单次调用超时 30 秒；测试造的临时仓在 try/finally 里清干净（含只读的 .git 文件）。

## 验收

`python scripts/verify_git_ops.py`（9 条判据：注册表自省 / 5 个能力真跑真提交 / 安全红线 / 老能力回归 / 临时目录不泄漏）。