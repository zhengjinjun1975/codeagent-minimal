import os
import sys
import subprocess
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))


def _repo_root(start):
    p = start
    while p:
        if os.path.isfile(os.path.join(p, "atomic_base.py")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    return None


ROOT = _repo_root(HERE)
for _p in (HERE, ROOT):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from atomic_base import AtomicAgent, run_cli


class GitOpsAgent(AtomicAgent):
    name = "git-ops"
    version = "0.1.0"
    domain = "tools"
    description = "git 仓库操作：状态/差异/日志/提交/分支（只读为主+受控写；不含 push/reset/force）"
    open_source = True
    provides = ["git.status", "git.diff", "git.log", "git.commit", "git.branch"]
    depends_on = []
    inputs = ["path", "action", "name", "message", "n", "staged", "base"]
    outputs = ["branch", "clean", "changed", "diff", "files", "stat", "commits",
               "sha", "committed", "branches", "current", "created", "switched"]

    def _register_defaults(self):
        self.register("git.status", self._do_status)
        self.register("git.diff", self._do_diff)
        self.register("git.log", self._do_log)
        self.register("git.commit", self._do_commit)
        self.register("git.branch", self._do_branch)

    def _git(self, path, *args):
        try:
            p = subprocess.run(
                ["git"] + list(args),
                cwd=path,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            return p.returncode, p.stdout, p.stderr
        except FileNotFoundError as e:
            return 127, "", "git 不可用: %s" % e
        except subprocess.TimeoutExpired:
            return 124, "", "git 超时"
        except Exception as e:
            return 1, "", "git 调用失败: %s" % e

    def _not_repo(self, path, detail=""):
        return self._envelope(False, data={"root": path}, degraded=True,
                              error="不是 git 仓库（或 git 不可用）：" + detail)

    def _is_repo(self, path):
        rc, out, err = self._git(path, "rev-parse", "--is-inside-work-tree")
        return rc == 0 and out.strip() == "true", err

    def _do_status(self, path=".", **kw):
        ok, err = self._is_repo(path)
        if not ok:
            return self._not_repo(path, err.strip())
        rc, out, _ = self._git(path, "rev-parse", "--abbrev-ref", "HEAD")
        branch = out.strip() if rc == 0 else ""
        rc, out, _ = self._git(path, "status", "--porcelain")
        changed = []
        if rc == 0:
            for line in out.splitlines():
                if len(line) > 3:
                    changed.append(line[3:])
        clean = (changed == [])
        ahead = behind = 0
        rc, out, _ = self._git(path, "rev-list", "--left-right", "--count",
                               "HEAD...@{upstream}")
        if rc == 0:
            parts = out.strip().split()
            if len(parts) == 2:
                try:
                    ahead, behind = int(parts[0]), int(parts[1])
                except ValueError:
                    ahead = behind = 0
        return self._envelope(True, data={
            "branch": branch,
            "clean": clean,
            "changed": changed,
            "ahead": ahead,
            "behind": behind,
        })

    def _do_diff(self, path=".", staged=False, base=None, **kw):
        ok, err = self._is_repo(path)
        if not ok:
            return self._not_repo(path, err.strip())
        extra = (["--cached"] if staged else []) + ([base] if base else [])
        rc, out, err = self._git(path, "diff", *extra)
        if rc != 0:
            return self._envelope(False, data={"root": path}, degraded=True,
                                  error=err.strip() or "git diff 失败")
        diff_text = out
        rc, out, _ = self._git(path, "diff", "--name-only", *extra)
        files = out.splitlines() if rc == 0 else []
        rc, out, _ = self._git(path, "diff", "--stat", *extra)
        stat = out if rc == 0 else ""
        return self._envelope(True, data={
            "diff": diff_text,
            "files": files,
            "stat": stat,
        })

    def _do_log(self, path=".", n=10, **kw):
        ok, err = self._is_repo(path)
        if not ok:
            return self._not_repo(path, err.strip())
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 10
        if n < 1 or n > 200:
            n = 10
        rc, out, err = self._git(path, "log", "-n", str(n),
                                 "--pretty=format:%H%x1f%s%x1f%an%x1f%at")
        if rc != 0:
            return self._envelope(False, data={"commits": []}, degraded=True,
                                  error=err.strip() or "git log 失败")
        commits = []
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.split("\x1f")
            if len(parts) != 4:
                continue
            sha, subject, author, ts = parts
            try:
                ts_i = int(ts)
            except ValueError:
                ts_i = 0
            commits.append({"sha": sha, "subject": subject,
                            "author": author, "ts": ts_i})
        return self._envelope(True, data={"commits": commits})

    def _do_commit(self, path=".", message="", paths=None, **kw):
        if not message or not str(message).strip():
            return self._envelope(False, data={}, degraded=True,
                                  error="提交信息不能为空")
        ok, err = self._is_repo(path)
        if not ok:
            return self._not_repo(path, err.strip())
        if paths:
            rc, out, err = self._git(path, "add", *paths)
        else:
            rc, out, err = self._git(path, "add", "-A")
        if rc != 0:
            return self._envelope(False, data={}, degraded=True,
                                  error=err.strip() or "git add 失败")
        rc, out, err = self._git(path, "commit", "-m", str(message))
        if rc != 0:
            return self._envelope(False, data={}, degraded=True,
                                  error=err.strip() or "git commit 失败")
        rc, out, _ = self._git(path, "rev-parse", "HEAD")
        sha = out.strip() if rc == 0 else ""
        return self._envelope(True, data={"sha": sha, "committed": True})

    def _do_branch(self, path=".", action="list", name=None, **kw):
        ok, err = self._is_repo(path)
        if not ok:
            return self._not_repo(path, err.strip())
        action = action or "list"
        if action == "create":
            if not name or not str(name).strip():
                return self._envelope(False, data={}, degraded=True,
                                      error="create 需要非空 name")
            rc, out, err = self._git(path, "checkout", "-b", str(name))
            if rc != 0:
                return self._envelope(False, data={}, degraded=True,
                                      error=err.strip() or "git checkout -b 失败")
            rc, out, _ = self._git(path, "branch", "--format=%(refname:short)")
            branches = out.splitlines() if rc == 0 else []
            return self._envelope(True, data={
                "branches": branches,
                "current": str(name),
                "created": str(name),
            })
        if action == "switch":
            if not name or not str(name).strip():
                return self._envelope(False, data={}, degraded=True,
                                      error="switch 需要非空 name")
            rc, out, err = self._git(path, "checkout", str(name))
            if rc != 0:
                return self._envelope(False, data={}, degraded=True,
                                      error=err.strip() or "git checkout 失败")
            rc, out, _ = self._git(path, "branch", "--format=%(refname:short)")
            branches = out.splitlines() if rc == 0 else []
            return self._envelope(True, data={
                "branches": branches,
                "current": str(name),
                "switched": str(name),
            })
        rc, out, _ = self._git(path, "branch", "--format=%(refname:short)")
        branches = out.splitlines() if rc == 0 else []
        rc, out, _ = self._git(path, "rev-parse", "--abbrev-ref", "HEAD")
        current = out.strip() if rc == 0 else ""
        return self._envelope(True, data={
            "branches": branches,
            "current": current,
        })


if __name__ == "__main__":
    sys.exit(run_cli(GitOpsAgent(), run_args={
        "capability": {"default": GitOpsAgent.provides[0],
                       "choices": list(GitOpsAgent.provides)},
        "path": {"default": "."},
        "action": {"default": "list"},
        "name": {"default": ""},
        "message": {"default": ""},
        "n": {"default": 10, "type": int},
    }))