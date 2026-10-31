"""検証シミュレーション層（SB層）— 生成コードと部品式の AST 許可リスト検査（SB3）。

設計: ``docs/features/simulation_bridge_design.md`` §2 SB3 / §5.5。

- :func:`check_program` は LLM が生成した理論プログラム（``simulate(inputs, params)``）を、
- :func:`check_expression` は L層部品の ``transfer`` 式（式モード）を検査する。

stdlib ``ast`` だけを使う（FastAPI / sqlalchemy / LLM / numpy 非 import）。ここはサンドボックスの
**前段**で、最後の防波堤は sim-runner コンテナ側（二重防御）。api-server プロセス内で
コードを ``exec`` することはしない。
"""

from __future__ import annotations

import ast

from core.simulation_bridge import schema

__all__ = ["check_program", "check_expression"]

#: 式モードで属性アクセスを許すオブジェクト名。
_EXPRESSION_ATTR_OWNERS = frozenset({"np", "math", "params"})
#: 式モードで関数呼び出しを許すオブジェクト名（``np.exp(x)`` 等）。
_EXPRESSION_CALL_OWNERS = frozenset({"np", "math"})


def _uniq(items: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def _forbidden_attribute(attr: str) -> bool:
    return attr in schema.FORBIDDEN_ATTRIBUTES or attr.startswith("__")


def _common_reasons(node: ast.AST) -> list[str]:
    """プログラム・式の両モードで共通の禁止（禁止名・禁止属性）。"""
    reasons: list[str] = []
    if isinstance(node, ast.Name) and node.id in schema.FORBIDDEN_NAMES:
        reasons.append(f"使用が許可されていない名前があります: {node.id}")
    elif isinstance(node, ast.Attribute):
        if _forbidden_attribute(node.attr):
            reasons.append(f"許可されていない属性にアクセスしています: {node.attr}")
        if node.attr in schema.FORBIDDEN_NAMES:
            reasons.append(f"使用が許可されていない名前があります: {node.attr}")
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
        if text.startswith("__") and text.endswith("__") and len(text) > 4:
            reasons.append(f"特殊名を文字列で参照しています: {text}")
    return reasons


def _entrypoint_found(tree: ast.Module, entrypoint: str) -> bool:
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == entrypoint:
            positional = len(stmt.args.posonlyargs) + len(stmt.args.args)
            if positional >= 2:
                return True
    return False


def check_program(source: str, *, entrypoint: str = schema.PROGRAM_ENTRYPOINT) -> schema.AstCheckResult:
    """生成コード全体を検査する。``ok`` は禁止事項ゼロ、``entrypoint_found`` は入口関数の有無。

    入口関数は top-level の ``def {entrypoint}(...)`` で位置引数 2 つ以上。
    """
    text = source if isinstance(source, str) else ""
    if not text.strip():
        return schema.AstCheckResult(ok=False, reasons=("コードが空です。",), entrypoint_found=False)
    line_count = len(text.splitlines())
    reasons: list[str] = []
    if line_count > schema.PROGRAM_MAX_LINES:
        reasons.append(f"コードの行数が上限（{schema.PROGRAM_MAX_LINES} 行）を超えています。")
    try:
        tree = ast.parse(text, mode="exec")
    except SyntaxError as exc:
        reasons.append(f"コードを構文解析できません: {exc.msg}")
        return schema.AstCheckResult(ok=False, reasons=_uniq(reasons), entrypoint_found=False)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in schema.ALLOWED_IMPORTS:
                    reasons.append(f"許可されていないモジュールを import しています: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".")[0]
            if node.level or root not in schema.ALLOWED_IMPORTS:
                reasons.append(f"許可されていないモジュールを import しています: {module or '(相対 import)'}")
            for alias in node.names:
                if alias.name == "*":
                    reasons.append(f"ワイルドカード import（*）は許可されていません: {module}")
                elif _forbidden_attribute(alias.name) or alias.name in schema.FORBIDDEN_ATTRIBUTES:
                    reasons.append(f"許可されていない名前を import しています: {alias.name}")
        elif isinstance(node, ast.ClassDef):
            reasons.append(f"クラス定義は許可されていません: {node.name}")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            reasons.append("非同期構文は許可されていません。")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            reasons.append(f"global / nonlocal 宣言は許可されていません: {', '.join(node.names)}")
        elif isinstance(node, ast.With):
            reasons.append("with 文は許可されていません（ファイル等の資源を開く経路を作らない）。")
        reasons.extend(_common_reasons(node))

    found = _entrypoint_found(tree, entrypoint)
    if not found:
        reasons.append(f"入口関数 {entrypoint}(inputs, params) が top-level に定義されていません。")
    return schema.AstCheckResult(ok=not reasons, reasons=_uniq(reasons), entrypoint_found=found)


def _owner_name(node: ast.AST) -> str | None:
    """``np.exp`` / ``np.random.normal`` / ``params["k"]`` の起点の名前。"""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    return None


def check_expression(expr: str) -> schema.AstCheckResult:
    """部品の ``transfer`` 式（1 行の式）を検査する。

    参照してよい名前は :data:`schema.EXPRESSION_ALLOWED_NAMES`、属性アクセスは
    ``np`` / ``math`` / ``params`` の上だけ、呼び出しは ``np`` / ``math`` の関数だけ。
    lambda・内包表記は不可。``entrypoint_found`` は式モードでは常に True（入口を要求しない）。
    """
    text = expr if isinstance(expr, str) else ""
    if not text.strip():
        return schema.AstCheckResult(ok=False, reasons=("式が空です。",), entrypoint_found=False)
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        return schema.AstCheckResult(ok=False, reasons=(f"式を構文解析できません: {exc.msg}",), entrypoint_found=False)

    reasons: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id not in schema.EXPRESSION_ALLOWED_NAMES:
                reasons.append(f"式で使えない名前があります: {node.id}")
        elif isinstance(node, ast.Attribute):
            owner = _owner_name(node)
            if owner not in _EXPRESSION_ATTR_OWNERS:
                reasons.append(f"属性アクセスは np / math / params の上だけです: {node.attr}")
        elif isinstance(node, ast.Subscript):
            owner = _owner_name(node)
            if owner != "params":
                reasons.append("添字アクセスは params[...] だけです。")
        elif isinstance(node, ast.Call):
            func = node.func
            if not (isinstance(func, ast.Attribute) and _owner_name(func) in _EXPRESSION_CALL_OWNERS):
                label = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "?")
                reasons.append(f"呼び出せるのは np / math の関数だけです: {label}")
        elif isinstance(node, ast.Lambda):
            reasons.append("式で lambda は使えません。")
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            reasons.append("式で内包表記は使えません。")
        elif isinstance(node, (ast.NamedExpr, ast.Await, ast.Yield, ast.YieldFrom)):
            reasons.append("式で代入・非同期・yield は使えません。")
        reasons.extend(_common_reasons(node))
    return schema.AstCheckResult(ok=not reasons, reasons=_uniq(reasons), entrypoint_found=True)


def required_keys(source: str) -> dict[str, dict[str, bool]]:
    """生成コードが ``inputs`` / ``params`` から読む文字列キーを集める（決定論・実行しない）。

    戻り値は ``{"inputs": {key: required}, "params": {key: required}}``。``x["k"]`` は必須、
    ``x.get("k", default)`` は任意（``x.get("k")`` だけなら必須扱い）。io_contract が
    コードと食い違って入力欄に現れないキー（実機 2026-10-03 の ``KeyError: 'b_1'``）を、
    契約の補完と実行前の検査の両方で使う。構文エラーなら空。
    """
    out: dict[str, dict[str, bool]] = {"inputs": {}, "params": {}}
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in out:
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                out[node.value.id].setdefault(key.value, True)
                out[node.value.id][key.value] = True
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in out
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            name = node.args[0].value
            optional = len(node.args) >= 2 or bool(node.keywords)
            bucket = out[node.func.value.id]
            if name not in bucket:
                bucket[name] = not optional
    return out

