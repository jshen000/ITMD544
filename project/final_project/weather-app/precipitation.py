"""Classify forecast precipitation intensity from quantitative rates.

Input values must represent nonnegative forecast rates, not
precipitation probabilities.
"""

import math


def classify_rate(rate, heavy_threshold):
    if rate is None or isinstance(rate, bool):
        return "Not available"

    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return "Not available"

    if not math.isfinite(rate) or rate < 0:
        return "Not available"

    if rate == 0:
        return "No"

    if rate >= heavy_threshold:
        return "Heavy"

    return "Mild"


def rain_category(rate_mm_per_hour):
    return classify_rate(rate_mm_per_hour, 7.6)


def snow_category(rate_cm_per_hour):
    return classify_rate(rate_cm_per_hour, 2.5)
