import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8', errors='replace') as f:
        return f.read()


def _registry_atoms():
    with open(os.path.join(ROOT, 'registry.json'), encoding='utf-8') as f:
        reg = json.load(f)
    order = reg.get('order') or []
    if order:
        return sorted(order)
    agents = reg.get('agents') or {}
    return sorted(agents.keys())


COUNT_RE = re.compile(r'(\d+)\s*个?\s*原子|(\d+)\s*个?\s*核心(?=\s*(?:原子|\+))')


def scan_atom_counts(text, n):
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in COUNT_RE.finditer(line):
            if int(m.group(1) or m.group(2)) != n:
                out.append((i, m.group(0)))
    return out


def scan_guide_index(text, atoms):
    seen = []
    rows = 0
    for line in text.splitlines():
        m = re.match(r'^\|\s*\d+\s*\|\s*`([^`]+)`', line)
        if m:
            rows += 1
            seen.append(m.group(1))
    seen_set = set(seen)
    atom_set = set(atoms)
    return (sorted(atom_set - seen_set), sorted(seen_set - atom_set), rows)


def scan_atomcn(js, atoms):
    start = js.find('const ATOM_CN = {')
    if start < 0:
        return sorted(atoms)
    end = js.find('};', start)
    block = js[start:end if end >= 0 else len(js)]
    keys = set(re.findall(r'"([a-z0-9-]+)"\s*:', block))
    return sorted(set(atoms) - keys)


def scan_server_cmds(text, allowed):
    cmds = set(re.findall(r'cmd\s*==\s*"([a-z_]+)"', text))
    return sorted(cmds - set(allowed))


def test_registry_self_consistent():
    atoms = _registry_atoms()
    assert atoms, 'registry.json 未解析出任何原子'
    assert len(atoms) == len(set(atoms)), f'registry 原子名重复: {atoms}'


def test_readme_counts():
    atoms = _registry_atoms()
    n = len(atoms)
    text = _read('README.md')
    m1 = re.search(r'共\s*\*\*(\d+)\s*个', text)
    assert m1, 'README.md 未找到 "共 **N 个"'
    assert int(m1.group(1)) == n, f'README 共 **{m1.group(1)} 个 != registry {n}'
    m2 = re.search(r'注册\s*\*\*(\d+)\s*原子\s*/\s*(\d+)\s*能力\*\*', text)
    assert m2, 'README.md 未找到 "注册 **N 原子 / M 能力**"'
    assert int(m2.group(1)) == n, f'README 注册原子 {m2.group(1)} != registry {n}'


def _scan_surfaces(n):
    """全仓扫计数：所有 .md/.html/.js（CHANGELOG 历史段除外）+ 用户可见代码面；tests/ 除外（用例注释合法描述子集）。"""
    bad = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ('.git', '__pycache__', '.pytest_cache', 'tests')]
        for fn in filenames:
            if not fn.endswith(('.md', '.html', '.js')):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT).replace('\\', '/')
            if rel.lower().startswith('changelog'):
                continue
            with open(full, encoding='utf-8', errors='replace') as f:
                for ln, frag in scan_atom_counts(f.read(), n):
                    bad.append(f'{rel}:{ln} {frag!r} != {n}')
    for rel in ('agent_runtime.py', 'web/server.py', 'lab/lab_app.py', 'lab/pipeline.py',
                'lab/atom_extension.py', 'lab/report_gen.py', 'tools/gen_arch_svg.py',
                'examples/web_api_client.py'):
        for ln, frag in scan_atom_counts(_read(rel), n):
            bad.append(f'{rel}:{ln} {frag!r} != {n}')
    return bad


def test_docs_atom_counts():
    n = len(_registry_atoms())
    bad = _scan_surfaces(n)
    assert not bad, '原子数不一致:\n' + '\n'.join(bad)


def test_atoms_guide_index():
    atoms = _registry_atoms()
    missing, extra, rows = scan_guide_index(_read('docs/ATOMS_GUIDE.md'), atoms)
    assert rows == len(atoms), f'指南表格行数 {rows} != 原子数 {len(atoms)}'
    assert not missing, f'指南缺原子: {missing}'
    assert not extra, f'指南多原子: {extra}'


def test_frontend_atomcn_covers():
    atoms = _registry_atoms()
    missing = scan_atomcn(_read('lab/frontend/lab.js'), atoms)
    assert not missing, f'ATOM_CN 缺原子: {missing}'


def test_server_cmd_whitelist():
    allowed = {'status', 'review', 'test', 'chain', 'guard', 'evolve'}
    extra = scan_server_cmds(_read('web/server.py'), allowed)
    assert not extra, f'server.py 命令超出白名单: {extra}'


def test_checkers_have_power():
    atoms = _registry_atoms()
    n = len(atoms)

    readme = _read('README.md')
    mutated = readme + '\n共 **999 个原子**\n'
    assert scan_atom_counts(mutated, n), 'scan_atom_counts 未检出注入的 999 原子'
    assert scan_atom_counts('调色板 (999核心+扩展)', n), 'scan_atom_counts 未检出 999核心+ 形式'

    guide = _read('docs/ATOMS_GUIDE.md')
    lines = guide.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r'^\|\s*\d+\s*\|\s*`([^`]+)`', ln):
            del lines[i]
            break
    else:
        assert False, '指南中未找到表格数据行，无法注入'
    missing, extra, rows = scan_guide_index('\n'.join(lines), atoms)
    assert rows != len(atoms) or missing or extra, 'scan_guide_index 未检出删行'

    js = _read('lab/frontend/lab.js')
    start = js.find('const ATOM_CN = {')
    end = js.find('};', start)
    block = js[start:end]
    km = re.search(r'"([a-z0-9-]+)"\s*:', block)
    assert km, 'lab.js ATOM_CN 中未找到键，无法注入'
    mutated_js = js[:start] + block.replace(km.group(0), '', 1) + js[end:]
    assert scan_atomcn(mutated_js, atoms), 'scan_atomcn 未检出删键'