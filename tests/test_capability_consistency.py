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


def scan_atom_counts(text, n):
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in re.finditer(r'(\d+)\s*个?原子', line):
            if int(m.group(1)) != n:
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


def test_docs_atom_counts():
    atoms = _registry_atoms()
    n = len(atoms)
    whitelist = ['README.md', 'docs/ATOMS_GUIDE.md', 'docs/INTEGRATION_GUIDE.md',
                 'docs/PROMOTION.md', 'docs/CAPABILITY.md', 'docs/SECURITY_HARDENING.md']
    bad = []
    for rel in whitelist:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        for ln, frag in scan_atom_counts(_read(rel), n):
            bad.append(f'{rel}:{ln} {frag!r} != {n}')
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