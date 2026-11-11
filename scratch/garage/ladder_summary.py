#!/usr/bin/env python3
"""Aggregate ladder .jsonl files by cell: blocks delivered and verified payload rate.

Usage: ladder_summary.py <ladder-*.jsonl> [...]
"""

import json
import sys
from collections import defaultdict


def main(paths):
    for path in paths:
        groups = defaultdict(list)
        for line in open(path):
            row = json.loads(line)
            if "error" not in row:
                groups[row["name"]].append(row)
        print(f"\n{path.split('/')[-1]}")
        print(f"  {'cell':<24}{'runs':>5}{'live blocks':>14}{'live bps':>10}{'corrected blocks':>19}{'corr bps':>10}"
              f"{'per-run corrected':>22}")
        for name, rows in groups.items():
            sched = sum(r["scheduled"] for r in rows)
            air = sum(r["airtime_s"] for r in rows)
            live = sum(r["verified"] for r in rows)
            line = f"  {name:<24}{len(rows):>5}{live:>9}/{sched:<4}{live * 2048 / air:>10.0f}"
            if all("corrected" in r for r in rows):
                corr = sum(r["corrected"]["verified"] for r in rows)
                per = ",".join(str(r["corrected"]["verified"]) for r in rows)
                line += f"{corr:>14}/{sched:<4}{corr * 2048 / air:>10.0f}{per:>22}"
            print(line)


if __name__ == "__main__":
    main(sys.argv[1:])
