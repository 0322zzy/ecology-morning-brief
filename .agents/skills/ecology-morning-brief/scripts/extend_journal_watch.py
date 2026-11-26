#!/usr/bin/env python3
"""Append verified journals to the independent journal watch."""

from __future__ import annotations

import argparse
import copy
import json
import re
import tempfile
from pathlib import Path

from brief_engine import load_config, normalize_title


def canonical_issn(value: str) -> str:
    digits = value.strip().upper().replace("-", "")
    if re.fullmatch(r"[0-9]{7}[0-9X]", digits) is None:
        raise ValueError(f"Invalid ISSN: {value}")
    check_digit = 10 if digits[-1] == "X" else int(digits[-1])
    checksum = sum(int(digit) * weight for digit, weight in zip(digits[:7], range(8, 1, -1)))
    if (checksum + check_digit) % 11:
        raise ValueError(f"Invalid ISSN checksum: {value}")
    return f"{digits[:4]}-{digits[4:]}"


def journal_key(name: str) -> str:
    return normalize_title(name.replace("&", " and "))


def extend_journal_watch(config: dict, requested: list[list[str]]) -> tuple[dict, list[str], list[str]]:
    effective = copy.deepcopy(config)
    journals = effective.setdefault("journal_watch", {}).setdefault("journals", [])
    known_names = {
        journal_key(name)
        for journal in journals
        for name in [journal["name"], *journal.get("aliases", [])]
    }
    known_issns = {canonical_issn(journal["issn"]) for journal in journals}
    added: list[str] = []
    present: list[str] = []
    for request in requested:
        if len(request) < 2:
            raise ValueError("Each --journal requires a title and ISSN")
        name, raw_issn, *aliases = (value.strip() for value in request)
        issn = canonical_issn(raw_issn)
        names = {journal_key(value) for value in [name, *aliases]}
        if "" in names:
            raise ValueError("Journal titles and aliases must contain text")
        if issn in known_issns or names & known_names:
            present.append(name)
            continue
        journal = {"name": name, "issn": issn, "group": "requested"}
        if aliases:
            journal["aliases"] = list(dict.fromkeys(aliases))
        journals.append(journal)
        known_names.update(names)
        known_issns.add(issn)
        added.append(name)
    return effective, added, present


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--journal", action="append", nargs="+", required=True, metavar="VALUE",
                        help="Verified title and ISSN, followed by any verified index aliases. Repeat to add more journals.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        effective, added, present = extend_journal_watch(load_config(args.base), args.journal)
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent, delete=False) as output:
        staged = Path(output.name)
        try:
            json.dump(effective, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.close()
            staged.replace(args.output)
        finally:
            staged.unlink(missing_ok=True)
    print(json.dumps({"output": str(args.output), "added": added, "already_present": present}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
