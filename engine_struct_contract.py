import ast
import re
import sys
import os

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def parse_contract(text):
    # 契约块边界靠行首标记界定，避免正文里偶然出现的同名行被误收
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        # 允许写成 markdown 标题（## 【结构契约】）。规格文件多半带这个前缀，
        # 只认行首【 会让整块解析不到，而解析不到就等于约束不存在。
        if ln.strip().lstrip('#').strip().startswith('【结构契约】'):
            start = i + 1
            break
    if start is None:
        return {'functions': [], 'constants': {}, 'has_contract': False,
                'min_lines': None, 'max_lines': None, 'require_main': False}

    # 块结束条件只认“未知标记”。行数、自检、结构契约是同一份规格里的兄弟段落，
    # 若把它们也当结束符，【行数】永远进不了块，行数区间就永远解析不出来
    # （实测：规格里写了 650-950，解析结果却是 None-None，等于这项约束根本不存在）。
    _kinds = ('【结构契约】', '【行数】', '【自检】', '【探针】',
              '【算法】', '【禁止】', '【目标】', '【约定】', '【常量】', '【表】')

    def _strip_md(ln):
        """剥掉 markdown 前缀：规格里常见 `## 【行数】150-280` 这种写法。

        不剥的话 startswith('【') 判不中，行数区间就永远是 None，
        等于这条约束根本没生效（实测多份规格栽在这里）。
        """
        return ln.lstrip('#').strip() if ln.lstrip().startswith('#') else ln

    block = []
    for ln in lines[start:]:
        norm = _strip_md(ln)
        if norm.startswith('【') and not norm.startswith(_kinds):
            break
        block.append(norm)

    functions = []
    constants = {}
    min_lines = None
    max_lines = None
    require_main = False
    probe = None

    # def 行只取名字与参数名，默认值和注解对契约核对无意义
    def_re = re.compile(r'^\s*def\s+([A-Za-z_]\w*)\s*\((.*?)\)')
    range_re = re.compile(r'^【行数】\s*(\d+)\s*(?:-\s*(\d+))?')
    probe_re = re.compile(r'^【探针】\s*(.+?)\s*$')
    # 常量声明：收全大写名（允许前导下划线），值必须能字面量求值
    const_re = re.compile(r'^(_?[A-Z][A-Z0-9_]*)\s*=\s*(.+)$')

    for ln in block:
        m = def_re.match(ln)
        if m:
            name = m.group(1)
            raw = m.group(2).strip()
            params = []
            if raw:
                for part in raw.split(','):
                    part = part.strip()
                    if not part:
                        continue
                    # 去默认值
                    if '=' in part:
                        part = part.split('=', 1)[0].strip()
                    # 去注解
                    if ':' in part:
                        part = part.split(':', 1)[0].strip()
                    # 去 * / ** 前缀，只留参数名
                    part = part.lstrip('*').strip()
                    if part:
                        params.append(part)
            functions.append({'name': name, 'params': params})
            continue

        m = range_re.match(ln)
        if m:
            min_lines = int(m.group(1))
            max_lines = int(m.group(2)) if m.group(2) else None
            continue

        if ln.strip().startswith('【自检】'):
            require_main = True
            continue

        # 层名、来源白名单这类语义常量一旦被改名（episodic 写成 L1），
        # 函数签名照样对、算法却整体跑偏，光核签名抓不住。
        m = const_re.match(ln.strip())
        if m:
            try:
                constants[m.group(1)] = ast.literal_eval(m.group(2).strip())
            except (ValueError, SyntaxError):  # fail-open: 不是字面量就当说明文字忽略
                pass

        # 行为探针路径：母体靠第四道门真跑它，副本也要能解析出来才对得上
        m = probe_re.match(ln)
        if m:
            probe = m.group(1)

    return {
        'functions': functions,
        'constants': constants,
        'has_contract': True,
        'min_lines': min_lines,
        'max_lines': max_lines,
        'require_main': require_main,
        'probe': probe,
    }


def check_file(path, contract):
    """读盘后校验。"""
    if not os.path.exists(path):
        return {'passed': False, 'missing': [], 'param_mismatch': [],
                'lines': 0, 'issues': ['文件不存在: %s' % path]}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception as e:  # fail-open: 读不了一般按不通过处理并报原因
        return {'passed': False, 'missing': [], 'param_mismatch': [],
                'lines': 0, 'issues': ['文件读取失败: %s' % e]}

    return check_source(text, contract)


def check_source(text, contract):
    """直接校验内存里的源码文本。

    产物还没落盘时用这个判结构。意义在于：结构对不对要能进版本排序，
    否则模型自评高但接口错的版本会锁死 best，后面更好的版本永远不被检查。
    """
    result = {
        'passed': False,
        'missing': [],
        'param_mismatch': [],
        'lines': 0,
        'issues': [],
    }

    result['lines'] = len(text.splitlines())

    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        result['issues'].append('语法错误(第 %s 行): %s' % (e.lineno, e.msg))
        return result
    except Exception as e:
        result['issues'].append('解析失败: %s' % e)
        return result

    # 收集产物里的函数签名：顶层函数与类方法都算
    # 契约只写方法名（insert / select），不写类名，所以按名字平铺收集。
    # 早先只遍历 tree.body 顶层，导致写在 class 里的方法一律判"缺少"。
    actual = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            actual[node.name] = [a.arg for a in node.args.args]
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    actual[sub.name] = [a.arg for a in sub.args.args]

    for fn in contract.get('functions', []):
        name = fn['name']
        expected = fn['params']
        if name not in actual:
            result['missing'].append(name)
            continue
        got = actual[name]
        if got != expected:
            result['param_mismatch'].append({
                'name': name,
                'expected': expected,
                'actual': got,
            })

    if result['missing']:
        result['issues'].append('缺少必需函数: %s' % ' / '.join(result['missing']))

    for mm in result['param_mismatch']:
        result['issues'].append(
            '%s 的参数序列不符，应为 (%s) 实际为 (%s)' % (
                mm['name'],
                ', '.join(mm['expected']),
                ', '.join(mm['actual']),
            )
        )

    min_lines = contract.get('min_lines')
    max_lines = contract.get('max_lines')
    if min_lines is not None and result['lines'] < min_lines:
        result['issues'].append('行数 %d 低于下限 %d' % (result['lines'], min_lines))
    if max_lines is not None and result['lines'] > max_lines:
        result['issues'].append('行数 %d 高于上限 %d' % (result['lines'], max_lines))

    if contract.get('require_main') and '__main__' not in text:
        result['issues'].append('缺少 __main__ 自检入口')

    # 常量核对：签名对但语义常量被改名，是最隐蔽的一种跑偏
    actual_consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name):
                try:
                    actual_consts[tgt.id] = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):  # fail-open: 非常量表达式不参与核对
                    pass
    for name, expected_val in (contract.get('constants') or {}).items():
        if name not in actual_consts:
            result['issues'].append('缺少必需常量: %s（应为 %r）' % (name, expected_val))
        elif actual_consts[name] != expected_val:
            result['issues'].append('常量 %s 不符，应为 %r 实际为 %r'
                                    % (name, expected_val, actual_consts[name]))

    # 有契约块却什么都没解析到，等于没有约束；必须判失败，不能静默放行
    if contract.get('has_contract') and not contract.get('functions') \
            and not contract.get('constants'):
        result['issues'].append('契约块存在但未解析出任何约束，检查【结构契约】块格式')

    result['passed'] = not result['issues']
    return result


def format_issues(result):
    if result.get('passed'):
        return ''
    out = ['结构契约未通过：']
    for issue in result.get('issues', []):
        out.append('- %s' % issue)
    return '\n'.join(out)


if __name__ == '__main__':
    import tempfile

    failures = []

    def _ok(cond, label):
        if not cond:
            failures.append(label)

    tmpdir = tempfile.mkdtemp()
    try:
        # 1 解析出三个函数
        spec = (
            '【结构契约】\n'
            'def consolidate(items, key=0) -> list\n'
            'def remember(name: str, value) -> None\n'
            'def recall(name) -> object\n'
            '【行数】10-100\n'
            '【自检】\n'
        )
        c = parse_contract(spec)
        _ok([f['name'] for f in c['functions']] == ['consolidate', 'remember', 'recall'], '1 三函数解析')
        _ok(c['functions'][0]['params'] == ['items', 'key'], '1 参数去默认值')
        _ok(c['functions'][1]['params'] == ['name', 'value'], '1 参数去注解')

        # 2 无契约块为空
        c2 = parse_contract('随便一段文本\n没有契约\n')
        _ok(c2['functions'] == [], '2 无契约空')

        # 3 完全符合时 passed
        good = (
            'def consolidate(items, key):\n    return []\n'
            'def remember(name, value):\n    return None\n'
            'def recall(name):\n    return None\n'
            'if __name__ == "__main__":\n    pass\n'
        )
        p_good = os.path.join(tmpdir, 'good.py')
        with open(p_good, 'w', encoding='utf-8') as f:
            f.write(good)
        contract_good = {
            'functions': [
                {'name': 'consolidate', 'params': ['items', 'key']},
                {'name': 'remember', 'params': ['name', 'value']},
                {'name': 'recall', 'params': ['name']},
            ],
            'min_lines': 1,
            'max_lines': 100,
            'require_main': True,
        }
        r_good = check_file(p_good, contract_good)
        _ok(r_good['passed'] is True, '3 完全符合 passed')

        # 4 缺函数被抓
        bad_missing = 'def consolidate(items, key):\n    return []\n'
        p_miss = os.path.join(tmpdir, 'miss.py')
        with open(p_miss, 'w', encoding='utf-8') as f:
            f.write(bad_missing)
        r_miss = check_file(p_miss, contract_good)
        _ok(r_miss['passed'] is False and 'remember' in r_miss['missing'], '4 缺函数被抓')

        # 5 参数改名被抓且指明应为实为
        bad_param = (
            'def consolidate(items, k):\n    return []\n'
            'def remember(name, value):\n    return None\n'
            'def recall(name):\n    return None\n'
        )
        p_param = os.path.join(tmpdir, 'param.py')
        with open(p_param, 'w', encoding='utf-8') as f:
            f.write(bad_param)
        r_param = check_file(p_param, contract_good)
        _ok(len(r_param['param_mismatch']) == 1, '5 参数改名被抓')
        _ok('应为 (items, key)' in format_issues(r_param) and '实际为 (items, k)' in format_issues(r_param), '5 指明应为实为')

        # 6 行数低于下限被抓
        contract_lines = dict(contract_good)
        contract_lines['min_lines'] = 999
        r_lines = check_file(p_good, contract_lines)
        _ok(r_lines['passed'] is False and any('低于下限' in i for i in r_lines['issues']), '6 行数下限被抓')

        # 7 语法错不抛异常
        p_syn = os.path.join(tmpdir, 'syn.py')
        with open(p_syn, 'w', encoding='utf-8') as f:
            f.write('def broken(:\n    pass\n')
        try:
            r_syn = check_file(p_syn, contract_good)
            _ok(r_syn['passed'] is False and any('语法错误' in i for i in r_syn['issues']), '7 语法错不抛')
        except Exception:
            _ok(False, '7 语法错不抛')

        # 8 format_issues 前缀正确
        _ok(format_issues(r_miss).startswith('结构契约未通过：'), '8 前缀正确')
        _ok(format_issues(r_good) == '', '8 passed 空串')

        # 9 文件不存在 passed=False
        r_none = check_file(os.path.join(tmpdir, 'nope.py'), contract_good)
        _ok(r_none['passed'] is False and r_none['issues'], '9 文件不存在')
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    if failures:
        for f in failures:
            print('FAIL:', f)
        sys.exit(1)
    print('SELFCHECK PASS')
    sys.exit(0)