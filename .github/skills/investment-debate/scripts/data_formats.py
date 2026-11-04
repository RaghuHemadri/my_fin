#!/usr/bin/env python3
"""Lossless compact views of MCP tool output, for the prefetched data bundles (prefetch_data.py).

Yahoo returns record lists as JSON that repeats every field name on every row. These views keep every row,
every field and every value, but write each field name once:
- financial statements: one row per line item, one column per period;
- other record lists: a Markdown table with one column per field;
- price bars: CSV (date is the session day; the exact Yahoo timestamps stay in the raw file saved next to it);
- objects and plain text: unchanged.

Cell encoding is reversible: None is an empty cell; a string that would read back as something else (empty,
padded, or valid JSON such as 123 or true) is written as a JSON string; numbers keep their exact JSON form;
nested values are compact JSON; '|' and line breaks are escaped. parse_table() inverts it, and
the tests round-trip real-shaped data through it. Standard library only.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indicators import iso_day  # noqa: E402  (same session-day rule as the indicator script)

ABSENT = "∅"  # a field missing from that record (not the same as null)


def parse_json(text: str | None) -> Any:
    try:
        return json.loads(text) if text is not None else None
    except ValueError:
        return None


def is_records(data: Any) -> bool:
    return isinstance(data, list) and bool(data) and all(isinstance(row, dict) for row in data)


def _needs_quotes(text: str) -> bool:
    """A string must be JSON-quoted when it would otherwise read back as something else."""
    if text in ("", ABSENT) or text != text.strip():
        return True
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def cell(value: Any) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = json.dumps(value, ensure_ascii=False) if _needs_quotes(value) else value
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "\\n") if text else text


def uncell(text: str) -> Any:
    text = re.sub(r"\\(\\|\||n)", lambda m: {"\\": "\\", "|": "|", "n": "\n"}[m.group(1)], text)
    if text == "":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _columns(records: list[dict]) -> list[str]:
    return list(dict.fromkeys(key for row in records for key in row))


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def records_table(records: list[dict]) -> str:
    """Markdown table: one column per field, in order of first appearance."""
    columns = _columns(records)
    lines = [_row([cell(c) for c in columns]), _row(["---"] * len(columns))]
    lines += [_row([cell(row.get(c)) if c in row else ABSENT for c in columns]) for row in records]
    return "\n".join(lines)


def statement_table(records: list[dict], period_key: str = "date") -> str:
    """Financial statement transposed: one row per line item, one column per period."""
    periods = [row.get(period_key) for row in records]
    items = [c for c in _columns(records) if c != period_key]
    lines = [_row(["Line item", *(cell(p) for p in periods)]), _row(["---"] * (len(periods) + 1))]
    lines += [_row([cell(item), *(cell(row.get(item)) if item in row else ABSENT for row in records)])
              for item in items]
    return "\n".join(lines)


def parse_table(markdown: str) -> list[list[Any]]:
    """Cells of a Markdown table produced above, decoded (header row included, separator row dropped)."""
    rows = []
    for line in markdown.strip().splitlines():
        parts = re.split(r"(?<!\\)\|", line.strip())[1:-1]
        cells = [p.strip() for p in parts]
        if cells and all(c == "---" for c in cells):
            continue
        rows.append([_ABSENT_MARK if c == ABSENT else uncell(c) for c in cells])
    return rows


def records_from_table(markdown: str) -> list[dict]:
    header, *rows = parse_table(markdown)
    return [{k: v for k, v in zip(header, row) if not _missing(v)} for row in rows]


def statement_from_table(markdown: str, period_key: str = "date") -> list[dict]:
    header, *rows = parse_table(markdown)
    periods = header[1:]
    records = [{period_key: p} for p in periods]
    for row in rows:
        for record, value in zip(records, row[1:]):
            if not _missing(value):
                record[row[0]] = value
    return records


_ABSENT_MARK = object()


def _missing(value: Any) -> bool:
    return value is _ABSENT_MARK


def session_day(stamp: Any) -> str:
    return iso_day(str(stamp))


BAR_FIELDS = ("Open", "High", "Low", "Close", "Volume", "Dividends", "Stock Splits")


def bars_csv(records: list[dict], as_of: str | None = None, start: str | None = None) -> tuple[str, dict]:
    """CSV of Yahoo bars (session date plus every numeric field) and a summary. With as_of, bars after it are
    left out (point-in-time); with start, bars before it are left out."""
    fields = [f for f in BAR_FIELDS if any(f in r for r in records)]
    fields += [f for f in _columns(records) if f not in fields and f != "Date"]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["date", *(f.lower().replace(" ", "_") for f in fields)])
    kept = []
    for record in records:
        day = session_day(record.get("Date"))
        if (as_of and day > as_of) or (start and day < start):
            continue
        kept.append((day, record))
        writer.writerow([day, *("" if record.get(f) is None else json.dumps(record.get(f)) for f in fields)])
    closes = [r.get("Close") for _, r in kept if isinstance(r.get("Close"), (int, float))]
    summary = {"bars": len(kept), "first": kept[0][0] if kept else None, "last": kept[-1][0] if kept else None,
               "last_close": closes[-1] if closes else None, "dropped_after_as_of": len(records) - len(kept) if as_of else 0,
               "high": max(closes) if closes else None, "low": min(closes) if closes else None}
    return out.getvalue(), summary


def render(text: str | None, tool: str = "") -> tuple[str, str]:
    """(kind, body) for a tool's text: statement, table, json or text. Bars are handled with bars_csv."""
    data = parse_json(text)
    if is_records(data):
        if tool == "get_financial_statement" and all("date" in row for row in data):
            return "statement", statement_table(data)
        return "table", records_table(data)
    if data is not None and not isinstance(data, str):
        return "json", json.dumps(data, indent=1, ensure_ascii=False)
    return "text", text or ""
