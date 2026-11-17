#!/usr/bin/env python3
"""Launch-day Twitch CCU quantile forecaster.

Reads every ``*.in`` case in the input directory (schema
``makefun/twitch-ccu-case/v1``) and writes one ``<id>.out`` forecast per
case to the output directory: five quantiles (5/25/50/75/95%) of the
category's hourly viewer count for release-day hours 2..24.

Method
------
For every past launch in the case's ``comparables`` we take its release-day
hourly counts and express each hour as a log-ratio to that launch's own
hour-1 print.  A launch-day curve is therefore a shape, independent of the
launch's absolute size.  For the case at hand we take, per hour, the
weighted empirical quantiles of those log-ratios and multiply back by the
case's hour-1 print.

Two weights shape the pool:

* scale -- launches whose hour-1 print is close (in log space) to the
  case's hour-1 print count more.  Big launches behave differently from
  small ones: their day curves dip less overnight and peak harder in the
  UTC evening.  A Gaussian kernel of bandwidth 1.0 in log space does this.
* unlock pattern -- launches that unlocked at rolling midnight local time
  (the way GTA VI unlocks, country by country) count double, because their
  timezone unlock wave -- New Zealand first, then Europe, the UK, the US
  east coast and finally the west coast -- is the same wave the case's day
  will ride.  The median curve this produces dips to ~0.55x the hour-1
  print around hour 10-11 UTC and peaks at ~1.3-1.45x around hour 20-21.
* a small floor weight (0.05) on every comparable keeps the effective
  pool from collapsing onto two or three launches when the case's hour-1
  print sits far from the pack; it blends the global launch-day shape in
  and stabilises the tails for small launches.

When too few comparables cover an hour we fall back to the house baseline
shape (flat median, spread widening as ``0.15 * sqrt(h - 1)``).

Local validation (leave-one-out over the kit's 37 usable comparables):
mean pinball loss 0.466 over all launches and 0.202 over the 20 launches
with hour-1 >= 50k viewers (the size class of the practice cases and of
GTA VI), with 90% coverage 0.89 on that class, versus 0.930 / 0.251 for
the flat house baseline on the same sets.

Standard library only, no network, no clock, no randomness: two runs on
the same case write byte-identical output.
"""

import json
import math
import os
import sys
from datetime import datetime

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
# Standard-normal quantiles at LEVELS.
Z = (-1.6448536269514722, -0.6744897501960817, 0.0,
     0.6744897501960817, 1.6448536269514722)
MAX_VIEWERS = 1_000_000_000.0
SCALE_BANDWIDTH = 1.0
ROLLING_BOOST = 2.0
WEIGHT_FLOOR = 0.05
MIN_POINTS = 5
MIN_TOTAL_WEIGHT = 3.0
BASELINE_SPREAD = 0.15


def parse_time(text):
    """Parse an ISO-8601 UTC timestamp into a datetime."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def is_rolling_midnight(comp):
    """True when a comparable unlocked at rolling midnight local time."""
    unlock = (comp.get("unlock") or "").lower()
    return ("rolling" in unlock or "midnight local" in unlock
            or "12 am local" in unlock)


def release_day_hours(comp):
    """Map a comparable's hourly rows onto release-day hours 1..24.

    Returns ``{hour: viewers}`` with only positive prints kept, or None
    when the launch has no usable hour-1 print (e.g. the GTA VI category's
    own pre-release history, whose release day has not happened yet).
    """
    try:
        day_start = parse_time(comp["day_start"])
    except (KeyError, TypeError, ValueError):
        return None
    hours = {}
    for row in comp.get("hourly") or []:
        try:
            when = parse_time(row["from"])
            viewers = row["viewers"]
        except (KeyError, TypeError, ValueError):
            continue
        if viewers is None or viewers <= 0:
            continue
        hour = int((when - day_start).total_seconds() // 3600) + 1
        if 1 <= hour <= 24:
            hours[hour] = viewers
    if 1 not in hours:
        return None
    return hours


def weighted_quantiles(points, levels):
    """Step-function weighted quantiles of (value, weight) pairs.

    ``points`` is a list of ``(value, weight)``; the quantile at level
    ``tau`` is the smallest value whose cumulative weight reaches
    ``tau`` of the total weight.
    """
    ordered = sorted(points)
    total = sum(w for _, w in ordered)
    out = []
    for tau in levels:
        target = tau * total
        acc = 0.0
        value = ordered[-1][0]
        for v, w in ordered:
            acc += w
            if acc >= target:
                value = v
                break
        out.append(value)
    return out


def baseline_quantiles(hour1, hour):
    """House-baseline shape: flat median, spread widening over the day."""
    spread = BASELINE_SPREAD * math.sqrt(hour - 1)
    return [hour1 * math.exp(z * spread) for z in Z]


def forecast_case(case):
    """Forecast one case; returns the ``{"hours": [...]}`` object."""
    known = case.get("known") or []
    hour1 = None
    for row in known:
        try:
            if row.get("hour") == 1 and row.get("viewers"):
                hour1 = float(row["viewers"])
                break
        except (AttributeError, TypeError, ValueError):
            continue
    if hour1 is None or hour1 <= 0:
        hourly = case.get("history_hourly") or []
        if hourly:
            try:
                hour1 = float(hourly[-1].get("viewers") or 0) or 1.0
            except (AttributeError, TypeError, ValueError):
                hour1 = 1.0
        else:
            hour1 = 1.0
    hour1 = max(hour1, 1.0)
    log_hour1 = math.log(hour1)

    # Release-day curves of the comparables, with their unlock pattern.
    curves = []
    for comp in case.get("comparables") or []:
        hours = release_day_hours(comp)
        if hours is None:
            continue
        curves.append((hours, is_rolling_midnight(comp)))

    rows = []
    for hour in range(2, 25):
        points = []
        for hours, rolling in curves:
            if hour not in hours:
                continue
            log_ratio = math.log(hours[hour]) - math.log(hours[1])
            scale = (math.log(hours[1]) - log_hour1) / SCALE_BANDWIDTH
            weight = math.exp(-0.5 * scale * scale) + WEIGHT_FLOOR
            if rolling:
                weight *= ROLLING_BOOST
            points.append((log_ratio, weight))
        total_weight = sum(w for _, w in points)
        if len(points) < MIN_POINTS or total_weight < MIN_TOTAL_WEIGHT:
            quantiles = baseline_quantiles(hour1, hour)
        else:
            offsets = weighted_quantiles(points, LEVELS)
            quantiles = [hour1 * math.exp(q) for q in offsets]
        # Clamp to the valid range and keep the row non-decreasing.
        clean = []
        prev = 0.0
        for q in quantiles:
            q = min(max(q, 1.0), MAX_VIEWERS)
            q = max(q, prev)
            clean.append(round(q, 2))
            prev = clean[-1]
        rows.append({"hour": hour, "q": clean})
    return {"hours": rows}


def main(argv):
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR") or "/input"
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
    names = sorted(
        name for name in os.listdir(input_dir) if name.endswith(".in")
    )
    if not names:
        print("no .in case files in %s" % input_dir, file=sys.stderr)
        return 1
    os.makedirs(output_dir, exist_ok=True)
    for name in names:
        case_id = name[:-3]
        with open(os.path.join(input_dir, name), "r", encoding="utf-8") as fh:
            case = json.load(fh)
        forecast = forecast_case(case)
        out_path = os.path.join(output_dir, case_id + ".out")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(forecast, fh, separators=(",", ":"), sort_keys=False)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
