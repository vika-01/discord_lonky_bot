import os
import re
import ast
import math
import cmath
import discord
from discord.ext import commands

GUILD_ID = int(os.getenv("GUILD_ID", "0"))

# -------------------------
# supports: + - * / ** ( ) and numbers
# also support ^ as power (converted to **)
# -------------------------
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)


def safe_eval(expr: str) -> float:
    expr = expr.strip().replace("^", "**")
    # quick allowlist of characters
    if not re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s\^%]*", expr):
        raise ValueError("Invalid characters")

    node = ast.parse(expr, mode="eval")

    def _eval(n):
        if isinstance(n, ast.Expression):
            return _eval(n.body)

        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)

        if isinstance(n, ast.UnaryOp) and isinstance(n.op, _ALLOWED_UNARYOPS):
            val = _eval(n.operand)
            return +val if isinstance(n.op, ast.UAdd) else -val

        if isinstance(n, ast.BinOp) and isinstance(n.op, _ALLOWED_BINOPS):
            left = _eval(n.left)
            right = _eval(n.right)
            if isinstance(n.op, ast.Add):
                return left + right
            if isinstance(n.op, ast.Sub):
                return left - right
            if isinstance(n.op, ast.Mult):
                return left * right
            if isinstance(n.op, ast.Div):
                return left / right
            if isinstance(n.op, ast.Pow):
                return left ** right
            if isinstance(n.op, ast.Mod):
                return left % right

        raise ValueError("Unsupported expression")

    return _eval(node)


# -------------------------
# polynomial parser for x up to degree 2
# parses one side like: 2x^2+5x-6 or -x^2+3x+10 or 3x
# returns (a, b, c) for ax^2 + bx + c
# -------------------------
def _coef_from_str(s: str) -> float:
    # coefficient part before x or x^2
    if s in ("", "+"):
        return 1.0
    if s == "-":
        return -1.0
    return float(s)


def parse_poly(side: str) -> tuple[float, float, float]:
    s = side.strip().lower()
    s = s.replace(" ", "")
    s = s.replace("−", "-")
    s = s.replace("×", "*")
    s = s.replace("x²", "x^2")

    # don't allow parentheses in equation parsing to keep it predictable
    if "(" in s or ")" in s:
        raise ValueError("Parentheses not supported in equations")

    # turn "-" into "+-" then split by "+"
    s = s.replace("-", "+-")
    parts = [p for p in s.split("+") if p != ""]

    a = b = c = 0.0

    for term in parts:
        # term like "-2x^2", "5x", "-6", "x^2", "-x"
        if "x^2" in term:
            coef_str = term.replace("x^2", "")
            a += _coef_from_str(coef_str)

        elif "x" in term:
            coef_str = term.replace("x", "")
            b += _coef_from_str(coef_str)

        else:
            c += float(term)

    return a, b, c


def simplify_equation(eq: str) -> tuple[float, float, float]:
    """
    Supports: left = right, moves all to left => left - right = 0
    Returns (a, b, c) for ax^2 + bx + c = 0
    """
    raw = eq.strip().lower().replace(" ", "").replace("−", "-")
    raw = raw.replace("x²", "x^2")

    if "=" not in raw:
        raise ValueError("No equals sign")

    left, right = raw.split("=", 1)
    if left == "":
        left = "0"
    if right == "":
        right = "0"

    a1, b1, c1 = parse_poly(left)
    a2, b2, c2 = parse_poly(right)

    return (a1 - a2, b1 - b2, c1 - c2)


def fmt_num(x: float) -> str:
    if abs(x - round(x)) < 1e-12:
        return str(int(round(x)))
    return f"{x:.6g}"


def fmt_complex(z: complex) -> str:
    re_part = z.real
    im_part = z.imag
    if abs(im_part) < 1e-12:
        return fmt_num(re_part)
    sign = "+" if im_part >= 0 else "-"
    return f"{fmt_num(re_part)} {sign} {fmt_num(abs(im_part))}i"


# -------------------------
# cog
# -------------------------
class Calculator(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(
        name="calc",
        description="Calculator (+ - * / ^) and equation solver (linear/quadratic).",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def calc(self, ctx: discord.ApplicationContext, expression: str):
        expr = (expression or "").strip()
        if not expr:
            await ctx.respond("Please enter an expression or an equation.", ephemeral=True)
            return

        # -------------------------
        # equation mode (has x and =)
        # -------------------------
        if ("x" in expr.lower() or "x²" in expr.lower()) and ("=" in expr):
            try:
                a, b, c = simplify_equation(expr)
            except Exception as e:
                await ctx.respond(
                    "I couldn't parse that equation. Try formats like:\n"
                    "`2x^2+5x-6=0`, `x^2-9=0`, `2x+5=0`, `3x=12`",
                    ephemeral=True
                )
                return

            # decide linear vs quadratic
            lines = []
            lines.append("**Equation solver**")
            lines.append(f"Input: `{expr}`")
            lines.append("")
            lines.append("**Step 1 — Move everything to the left**")
            lines.append(f"Standard form: `{fmt_num(a)}x^2 + {fmt_num(b)}x + {fmt_num(c)} = 0`")
            lines.append("")

            # quadratic
            if abs(a) > 1e-12:
                lines.append("**Step 2 — Identify coefficients**")
                lines.append(f"a = **{fmt_num(a)}**, b = **{fmt_num(b)}**, c = **{fmt_num(c)}**")
                lines.append("")
                lines.append("**Step 3 — Discriminant**")
                D = b * b - 4 * a * c
                lines.append(f"D = b² − 4ac = {fmt_num(b)}² − 4·{fmt_num(a)}·{fmt_num(c)} = **{fmt_num(D)}**")
                lines.append("")

                lines.append("**Step 4 — Solutions**")
                if D >= 0:
                    sqrtD = math.sqrt(D)
                    x1 = (-b + sqrtD) / (2 * a)
                    x2 = (-b - sqrtD) / (2 * a)
                    if abs(D) < 1e-12:
                        lines.append("D = 0 → one real solution:")
                        lines.append(f"x = **{fmt_num(x1)}**")
                    else:
                        lines.append("D > 0 → two real solutions:")
                        lines.append(f"x₁ = **{fmt_num(x1)}**")
                        lines.append(f"x₂ = **{fmt_num(x2)}**")
                else:
                    lines.append("No real solutions (D < 0).")
                    lines.append("But here are the complex solutions:")
                    sqrtD = cmath.sqrt(D)  # complex
                    x1 = (-b + sqrtD) / (2 * a)
                    x2 = (-b - sqrtD) / (2 * a)
                    lines.append(f"x₁ = **{fmt_complex(x1)}**")
                    lines.append(f"x₂ = **{fmt_complex(x2)}**")

                await ctx.respond("\n".join(lines), ephemeral=True)
                return

            # linear (a == 0)
            if abs(b) > 1e-12:
                lines.append("**Step 2 — This is linear (no x² term)**")
                lines.append(f"{fmt_num(b)}x + {fmt_num(c)} = 0")
                lines.append("")
                lines.append("**Step 3 — Solve**")
                # bx + c = 0 => x = -c/b
                x = -c / b
                lines.append(f"x = −c / b = −({fmt_num(c)}) / {fmt_num(b)} = **{fmt_num(x)}**")
                await ctx.respond("\n".join(lines), ephemeral=True)
                return

            # b == 0 too
            lines.append("**Result**")
            if abs(c) < 1e-12:
                lines.append("This equation is true for **all x** (infinitely many solutions).")
            else:
                lines.append("This equation has **no solution**.")
            await ctx.respond("\n".join(lines), ephemeral=True)
            return

        # -------------------------
        # normal calculator mode
        # -------------------------
        try:
            result = safe_eval(expr)
            await ctx.respond(f"Result: **{result}**", ephemeral=True)
        except Exception:
            await ctx.respond(
                "Invalid expression. Examples:\n"
                "`2+2`, `5*(3+1)`, `10/2`, `2^3`",
                ephemeral=True
            )


def setup(bot: discord.Bot):
    bot.add_cog(Calculator(bot))