#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from brief_engine import HistoryDB, default_state_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Mark delivered ecology brief articles as reported")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    report_date = str(payload.get("report_date", "")).strip()
    articles = payload.get("articles", [])
    if not report_date or not isinstance(articles, list):
        raise ValueError("Input must contain report_date and an articles list")
    database = HistoryDB(args.state_dir)
    try:
        for article in articles:
            title = str(article.get("title", "")).strip()
            doi = str(article.get("doi", "")).strip()
            if not title:
                raise ValueError("Every selected article needs a title")
            database.mark_reported(
                doi,
                title,
                report_date,
                related_dois=[str(value) for value in article.get("related_dois", [])],
            )
    finally:
        database.close()
    print(f"marked_reported={len(articles)} report_date={report_date} state={args.state_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
