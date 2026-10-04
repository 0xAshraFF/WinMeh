"""Safe arithmetic for "what is 12*7" / "15% of 80" - small LLMs get arithmetic wrong, this doesn't."""

from __future__ import annotations

import ast
import operator
import re

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Mod: operator.mod, ast.Pow: operator.pow, ast.FloorDiv: operator.floordiv}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
            raise ValueError("number too large")
        return _OPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    raise ValueError("not plain arithmetic")


def evaluate(expr: str) -> float:
    e = expr.lower().replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", "")
    e = re.sub(r"(?<=[\d)])\s*x\s*(?=[\d(])", "*", e)            # 3 x 4
    m = re.fullmatch(r"\s*([\d.]+)\s*(%|percent)\s+of\s+([\d.]+)\s*", e)
    if m:
        return float(m.group(1)) * float(m.group(3)) / 100
    if len(e) > 200:
        raise ValueError("too long")
    return _eval(ast.parse(e, mode="eval"))


def fmt(x: float) -> str:
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        x = int(x)
    return f"{x:,.10g}" if isinstance(x, float) else f"{x:,}"
