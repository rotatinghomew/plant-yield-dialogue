"""精确数值的展示工具：比率与份额用 Fraction 保留精确值，输出时再转可读形式。"""

from __future__ import annotations

from fractions import Fraction


def exact_num(value: Fraction):
    """JSON 友好的精确值：整数给 int，其余给最简小数字符串。"""
    if value.denominator == 1:
        return value.numerator
    return str(float(value))
