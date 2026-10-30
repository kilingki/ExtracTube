"""Exact time arithmetic; serialized times never determine physical identity."""

from decimal import Decimal, ROUND_HALF_EVEN
from fractions import Fraction
from .errors import VisionError

def decimal(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise VisionError('유한한 수치가 필요합니다.', kind='input')
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except Exception as exc:
        raise VisionError('유한한 수치가 필요합니다.', kind='input') from exc

def seconds(value):
    if isinstance(value, Fraction):
        value = Decimal(value.numerator) / Decimal(value.denominator)
    return format(decimal(value).quantize(Decimal('0.000000001'), rounding=ROUND_HALF_EVEN), 'f')

def score(value):
    return format(decimal(value).quantize(Decimal('0.000000000001'), rounding=ROUND_HALF_EVEN), 'f')

def actual(pts, time_base, start):
    if type(pts) is not int or any(type(time_base.get(k)) is not int or time_base[k] <= 0 for k in ('numerator','denominator')):
        raise VisionError('PTS/time_base 형식 오류입니다.', kind='input')
    return Fraction(pts * time_base['numerator'], time_base['denominator']) - Fraction(decimal(start))

def span(start, end, reference):
    return {'start': seconds(start), 'end': seconds(end), 'time_reference': reference}
