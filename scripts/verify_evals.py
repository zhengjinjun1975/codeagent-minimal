"""验收评测题集与跑题器: 纯标准库、离线、秒级。"""
import glob
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, 'evals', 'cases')
RUNNER = os.path.join(ROOT, 'scripts', 'run_evals.py')


def _get(data, path):
    cur = data
    for part in path.split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def judge(data, expect):
    """极简判定器: 点号路径取值 + equals/contains/at_least。"""
    val = _get(data, expect.get('path', ''))
    if 'equals' in expect:
        return val == expect['equals']
    if 'contains' in expect:
        try:
            return expect['contains'] in val
        except TypeError:
            return False
    if 'at_least' in expect:
        try:
            return val >= expect['at_least']
        except TypeError:
            return False
    return False


def check_1_nonempty():
    files = sorted(glob.glob(os.path.join(CASES, '*.json')))
    ids = []
    for fp in files:
        try:
            with open(fp, encoding='utf-8') as f:
                obj = json.load(f)
            ids.append(obj.get('id', os.path.basename(fp)))
        except Exception:
            ids.append(os.path.basename(fp) + '(解析失败)')
    n = len(files)
    print('  题集条数: %d' % n)
    for i in ids:
        print('    - %s' % i)
    if n >= 10:
        return True, ''
    return False, '题集不足: 实际 %d 条, 还差 %d 条' % (n, 10 - n)


def check_2_shape():
    files = sorted(glob.glob(os.path.join(CASES, '*.json')))
    missing = []
    for fp in files:
        name = os.path.basename(fp)
        try:
            with open(fp, encoding='utf-8') as f:
                obj = json.load(f)
        except Exception as e:
            missing.append('%s: 解析失败 %s' % (name, e))
            continue
        for key in ('id', 'title', 'call', 'expect'):
            if key not in obj:
                missing.append('%s: 缺 %s' % (name, key))
        call = obj.get('call')
        if not isinstance(call, dict) or 'capability' not in call:
            missing.append('%s: call 缺 capability' % name)
        expect = obj.get('expect')
        if not isinstance(expect, dict) or not any(
                k in expect for k in ('path', 'equals', 'contains')):
            missing.append('%s: expect 缺 path/equals/contains' % name)
    if not missing:
        return True, ''
    return False, '形状问题 %d 处: %s' % (len(missing), '; '.join(missing))


def check_3_judge():
    a = judge({'a': 1}, {'path': 'a', 'equals': 999})
    b = judge({'a': 'hello'}, {'path': 'a', 'contains': 'zzz'})
    c = judge({'a': 1}, {'path': 'a', 'equals': 1})
    if a is False and b is False and c is True:
        return True, ''
    return False, 'judge 反例自检失败: equals999=%r contains_zzz=%r equals1=%r' % (a, b, c)


def _run_runner():
    return subprocess.run(
        [sys.executable, 'scripts/run_evals.py', '--k', '3', '--json'],
        cwd=ROOT, timeout=180, capture_output=True, text=True)


def check_4_runner_runs():
    if not os.path.exists(RUNNER):
        return False, '跑题器不存在: %s' % RUNNER
    try:
        proc = _run_runner()
    except subprocess.TimeoutExpired:
        return False, '跑题器超时(180s)'
    if proc.returncode == 0:
        return True, ''
    tail = (proc.stderr or '')[-800:]
    return False, 'returncode=%d, stderr 尾部: %s' % (proc.returncode, tail)


def _last_json(stdout):
    text = stdout.strip()
    idx = text.rfind('{')
    while idx != -1:
        try:
            return json.loads(text[idx:])
        except Exception:
            idx = text.rfind('{', 0, idx)
    return None


def check_5_parse():
    try:
        proc = _run_runner()
    except Exception as e:
        return False, '无法运行跑题器: %s' % e
    obj = _last_json(proc.stdout or '')
    if obj is None:
        return False, 'stdout 无 JSON: %s' % (proc.stdout or '')[-300:]
    for key in ('总题数', '通过数', 'pass_k'):
        if key not in obj:
            return False, 'JSON 缺字段 %s: %s' % (key, obj)
    if obj['通过数'] != obj['总题数']:
        return False, '通过数 %r != 总题数 %r' % (obj['通过数'], obj['总题数'])
    return True, ''


def check_6_pass_k():
    try:
        proc = _run_runner()
    except Exception as e:
        return False, '无法运行跑题器: %s' % e
    obj = _last_json(proc.stdout or '')
    if obj is None or 'pass_k' not in obj:
        return False, '拿不到 pass_k'
    if obj['pass_k'] == 1.0:
        return True, ''
    return False, 'pass_k 实际值 %r != 1.0' % (obj['pass_k'],)


def check_7_offline():
    if not os.path.exists(RUNNER):
        return False, '跑题器不存在: %s' % RUNNER
    with open(RUNNER, encoding='utf-8', errors='ignore') as f:
        text = f.read()
    hits = [w for w in ('DEEPSEEK', 'api_key', 'openai') if w in text]
    if hits:
        return False, '命中云端词: %s' % ', '.join(hits)
    return True, ''


def _count_tmp():
    return len(glob.glob(os.path.join(tempfile.gettempdir(), 'codeagent-evals-*')))


def check_8_cleanup():
    before = _count_tmp()
    try:
        _run_runner()
    except Exception:
        pass
    after = _count_tmp()
    print('  temp 前=%d 后=%d' % (before, after))
    if after <= before:
        return True, ''
    return False, 'temp 目录增长: %d -> %d' % (before, after)


CHECKS = [
    ('1', '题集非空', check_1_nonempty),
    ('2', '题目形状', check_2_shape),
    ('3', '判定器反例自检', check_3_judge),
    ('4', '跑题器真跑', check_4_runner_runs),
    ('5', '输出可解析', check_5_parse),
    ('6', 'pass^k 为 1.0', check_6_pass_k),
    ('7', '跑题器离线', check_7_offline),
    ('8', '临时文件清理', check_8_cleanup),
]


def main():
    passed = 0
    failed = 0
    for num, desc, fn in CHECKS:
        try:
            ok, reason = fn()
        except Exception as e:
            ok, reason = False, '异常: %r' % (e,)
        if ok:
            passed += 1
            print('  PASS  %s %s' % (num, desc))
        else:
            failed += 1
            print('  FAIL  %s %s :: %s' % (num, desc, reason))
    print('evals 验收：通过 %d / 未通过 %d' % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())