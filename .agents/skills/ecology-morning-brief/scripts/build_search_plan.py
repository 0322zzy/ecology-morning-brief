#!/usr/bin/env python3
"""CLI for the v2 query planner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from brief_engine import load_config
from search_planner import VALID_MODES, build_search_plan


SKILL_DIR = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Build a bounded multi-strategy ecology search plan")
    value.add_argument("--config", type=Path, default=SKILL_DIR / "config" / "interests.yaml")
    value.add_argument("--profile", type=Path)
    value.add_argument("--mode", choices=VALID_MODES)
    value.add_argument("--date", help="Report date YYYY-MM-DD")
    value.add_argument("--lookback", type=int, help="Override lookback hours")
    value.add_argument("--output", type=Path, required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    config = load_config(args.config)
    profile = load_config(args.profile) if args.profile else None
    plan = build_search_plan(
        config,
        profile,
        mode=args.mode,
        report_date=args.date,
        lookback=args.lookback,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote={args.output} mode={plan['mode']} modules={len(plan['modules'])} "
        f"queries={len(plan['queries'])} lookback={plan['window']['lookback_hours']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
