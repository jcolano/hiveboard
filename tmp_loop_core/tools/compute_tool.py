"""
COMPUTE_TOOL
============

Safe math expression evaluator for the Agentic Loop Framework.

Evaluates mathematical expressions without giving the LLM access to
Python's eval() or exec(). Uses ast.NodeVisitor to whitelist only safe
operations: arithmetic, comparisons, and a small set of builtins.

This prevents the LLM from performing calculations inline (which it
does poorly) and instead offloads math to a deterministic evaluator.

Supported operations:
- Arithmetic: +, -, *, /, //, %, **
- Comparisons: <, >, <=, >=, ==, !=
- Functions: round, abs, min, max, sum, len
- Constants: True, False, None

Rejected (raises error):
- Imports, exec, eval
- Attribute access (no obj.attr)
- Function calls other than whitelisted builtins
- String operations
- Variable assignment

Usage::

    compute(expression="95000 * 0.85")
    # Returns: {"result": 80750.0, "expression": "95000 * 0.85"}

    compute(expression="round(sum([100, 200, 300]) / 3, 2)")
    # Returns: {"result": 200.0, "expression": "round(sum([100, 200, 300]) / 3, 2)"}
"""

import ast
import json
import operator
from typing import Any, Dict, Optional

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


# Whitelisted builtins for safe evaluation
SAFE_BUILTINS = {
    "round": round,
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "int": int,
    "float": float,
    "bool": bool,
}

# Supported binary operators
BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# Supported unary operators
UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Supported comparison operators
COMPARE_OPS = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


class SafeEvaluator(ast.NodeVisitor):
    """AST-based safe expression evaluator.

    Walks the AST and evaluates only whitelisted node types.
    Raises ValueError for anything unsafe.
    """

    def visit(self, node: ast.AST) -> Any:
        method = f"visit_{type(node).__name__}"
        visitor = getattr(self, method, None)
        if visitor is None:
            raise ValueError(f"Unsupported expression: {type(node).__name__}")
        return visitor(node)

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, (int, float, bool, type(None))):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value).__name__}")

    def visit_Num(self, node: ast.Num) -> Any:
        # Python 3.7 compat
        return node.n

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        op_func = UNARY_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return op_func(self.visit(node.operand))

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        op_func = BINARY_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        left = self.visit(node.left)
        right = self.visit(node.right)
        # Safety: prevent excessively large exponents
        if isinstance(node.op, ast.Pow) and isinstance(right, (int, float)) and right > 1000:
            raise ValueError("Exponent too large (max 1000)")
        return op_func(left, right)

    def visit_Compare(self, node: ast.Compare) -> Any:
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            op_func = COMPARE_OPS.get(type(op))
            if op_func is None:
                raise ValueError(f"Unsupported comparison: {type(op).__name__}")
            right = self.visit(comparator)
            if not op_func(left, right):
                return False
            left = right
        return True

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls allowed (no method calls)")
        func_name = node.func.id
        if func_name not in SAFE_BUILTINS:
            raise ValueError(f"Function not allowed: {func_name}")
        func = SAFE_BUILTINS[func_name]
        args = [self.visit(arg) for arg in node.args]
        kwargs = {kw.arg: self.visit(kw.value) for kw in node.keywords}
        return func(*args, **kwargs)

    def visit_List(self, node: ast.List) -> Any:
        return [self.visit(elt) for elt in node.elts]

    def visit_Tuple(self, node: ast.Tuple) -> Any:
        return tuple(self.visit(elt) for elt in node.elts)

    def visit_IfExp(self, node: ast.IfExp) -> Any:
        test = self.visit(node.test)
        if test:
            return self.visit(node.body)
        return self.visit(node.orelse)

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id == "True":
            return True
        if node.id == "False":
            return False
        if node.id == "None":
            return None
        raise ValueError(f"Unknown variable: {node.id}")


def safe_eval(expression: str) -> Any:
    """Safely evaluate a mathematical expression.

    Args:
        expression: Math expression string

    Returns:
        Evaluation result

    Raises:
        ValueError: If expression contains unsafe operations
    """
    if len(expression) > 1000:
        raise ValueError("Expression too long (max 1000 characters)")

    # Quick rejection of obviously dangerous patterns
    dangerous = {"import", "__", "exec", "eval", "compile", "open", "os.", "sys."}
    expr_lower = expression.lower()
    for d in dangerous:
        if d in expr_lower:
            raise ValueError(f"Forbidden pattern in expression: {d}")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression syntax: {e}")

    evaluator = SafeEvaluator()
    return evaluator.visit(tree)


class ComputeTool(BaseTool):
    """Evaluate math expressions safely."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="math_eval",
            description="Evaluate math expressions safely. Supports +,-,*,/,round,abs,min,max,sum.",
            parameters=[
                ToolParameter(
                    name="expression",
                    type="string",
                    description='Math expression to evaluate (e.g. "95000 * 0.85", "round(sum([100,200,300]) / 3, 2)")',
                ),
                ToolParameter(
                    name="description",
                    type="string",
                    description="Optional description of what this computation represents",
                    required=False,
                ),
            ],
        )

    def execute(self, expression: str, description: str = None) -> ToolResult:
        try:
            result = safe_eval(expression)
            output = {"result": result, "expression": expression}
            if description:
                output["description"] = description
            return ToolResult(
                success=True,
                output=json.dumps(output),
            )
        except ValueError as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Compute error: {e}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Unexpected error: {e}",
            )
