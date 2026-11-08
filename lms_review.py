"""Validate Claude bundle replies and export assessment rows to local CSV and JSON."""
import argparse
import csv
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

ALLOWED_TYPES = {
    "quiz", "test", "midsem", "compre", "assignment", "lab",
    "viva", "project", "presentation", "other",
}
ALLOWED_CHANGES = {"new", "rescheduled", "cancelled", "reminder"}
ALLOWED_SOURCES = {"announcement_text", "attachment", "both"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
DETAIL_FIELDS = ("date", "start_time", "end_time", "venue", "topics", "weightage", "notes")


def load_reply(path):
    raw = path.read_text(encoding="utf-8-sig").strip()
    fence = chr(96) * 3
    fenced = re.search(re.escape(fence) + r"(?:json)?\s*(.*?)" + re.escape(fence), raw, re.S | re.I)
    if fenced:
        raw = fenced.group(1).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < start:
        raise ValueError("no JSON array found")
    data = json.loads(raw[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("reply must contain a JSON array")
    return data


def valid_date(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d")


def valid_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        return None
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError:
        return None
    return value


def normalize(assessment):
    if not isinstance(assessment, dict):
        return None
    item = dict(assessment)
    kind = str(item.get("type") or "").strip().lower()
    item["type"] = kind if kind in ALLOWED_TYPES else "other"
    change = str(item.get("change") or "").strip().lower()
    item["change"] = change if change in ALLOWED_CHANGES else "new"
    source = str(item.get("source") or "").strip().lower()
    item["source"] = source if source in ALLOWED_SOURCES else "announcement_text"
    confidence = str(item.get("confidence") or "").strip().lower()
    item["confidence"] = confidence if confidence in ALLOWED_CONFIDENCE else "low"
    item["title"] = str(item.get("title") or item["type"].capitalize()).strip()
    item["date"] = valid_date(item.get("date"))
    item["start_time"] = valid_time(item.get("start_time"))
    item["end_time"] = valid_time(item.get("end_time"))
    item["date_is_inferred"] = bool(item.get("date_is_inferred", False))
    for field in ("venue", "topics", "weightage", "notes"):
        value = item.get(field)
        item[field] = str(value).strip() if value not in (None, "") else ""
    return item


def normalized_title(value):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def richness(row):
    return sum(bool(row.get(field)) for field in DETAIL_FIELDS)


def deduplicate(rows):
    kept = {}
    ordered = []
    merged = 0
    for row in rows:
        title_key = "" if row["type"] in {"midsem", "compre"} and row["date"] else normalized_title(row["title"])
        key = (row["course"], row["type"], title_key, row["date"])
        if key not in kept:
            kept[key] = row
            ordered.append(key)
            continue
        merged += 1
        best, other = kept[key], row
        if richness(other) > richness(best):
            best, other = other, best
        for field in DETAIL_FIELDS:
            if not best.get(field) and other.get(field):
                best[field] = other[field]
        best["source_item_ids"] = sorted(set(best["source_item_ids"]) | set(other["source_item_ids"]))
        best["source_files"] = sorted(set(best["source_files"]) | set(other["source_files"]))
        best["needs_review"] = best["needs_review"] or other["needs_review"]
        if "cancelled" in (best["change"], other["change"]):
            best["change"] = "cancelled"
        elif "rescheduled" in (best["change"], other["change"]):
            best["change"] = "rescheduled"
        kept[key] = best
    return [kept[key] for key in ordered], merged


def entity_key(row):
    title_key = "" if row["type"] in {"midsem", "compre"} else normalized_title(row["title"])
    return (row["course"], row["type"], title_key)


def mark_superseded(rows):
    """Flag older-date rows of an assessment that a later 'rescheduled' row moves."""
    moved = {}
    for row in rows:
        if row["change"] == "rescheduled" and row["date"]:
            moved.setdefault(entity_key(row), set()).add(row["date"])
    count = 0
    for row in rows:
        row["superseded"] = False
        new_dates = moved.get(entity_key(row))
        if new_dates and row["change"] in {"new", "reminder"} and row["date"] not in new_dates:
            row["superseded"] = True
            count += 1
    return count


def csv_safe(value):
    """Stop spreadsheet apps treating text from the LMS as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="folder containing lms_bundle_*.json and Claude replies")
    parser.add_argument("--include-past", action="store_true", help="include assessments dated before today")
    parser.add_argument("--keep-undated", action="store_true", help="include rows with no valid date")
    parser.add_argument("--keep-superseded", action="store_true",
                        help="include old-date rows replaced by a later reschedule")
    args = parser.parse_args(argv)

    run_dir = args.run_dir
    bundle_paths = sorted(run_dir.glob("lms_bundle_*.json"))
    if not bundle_paths:
        sys.exit(f"No lms_bundle_*.json files found in {run_dir}.")
    bundle_items = {}
    for path in bundle_paths:
        for item in json.loads(path.read_text(encoding="utf-8-sig")):
            if "item_id" in item:
                bundle_items[str(item["item_id"])] = item

    reply_paths = sorted(run_dir.glob("claude_output_*.json"))
    if not reply_paths:
        sys.exit(f"No claude_output_*.json replies found in {run_dir}.")
    rows = []
    answered = set()
    for path in reply_paths:
        try:
            entries = load_reply(path)
        except Exception as error:
            print(f"SKIPPED {path.name}: {error}")
            continue
        accepted = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("item_id", entry.get("announcement_id", "")))
            source = bundle_items.get(item_id)
            if source is not None:
                answered.add(item_id)
            if source is None:
                print(f"WARNING: {path.name} refers to item {item_id!r}, absent from this run's bundles; ignored.")
                continue
            source_problem = bool(source.get("problem")) or any(
                attachment.get("problem") for attachment in source.get("attachments", [])
            )
            for raw_assessment in entry.get("assessments") or []:
                assessment = normalize(raw_assessment)
                if assessment is None:
                    continue
                rows.append({
                    **assessment,
                    "course": source.get("course", ""),
                    "source_item_ids": [item_id],
                    "source_files": [source.get("file", "")] if source.get("file") else [],
                    "needs_review": (
                        not assessment["date"]
                        or assessment["confidence"] == "low"
                        or source_problem
                    ),
                })
                accepted += 1
        print(f"{path.name}: {len(entries)} items, {accepted} assessments accepted.")
    missing = sorted(set(bundle_items) - answered)
    if missing:
        shown = ", ".join(missing[:15]) + (" ..." if len(missing) > 15 else "")
        print(f"WARNING: {len(missing)} bundle item(s) have no entry in any reply "
              f"(not the same as 'no assessments'): {shown}")
    if not rows:
        print("No assessment records found.")
        return

    rows, merged = deduplicate(rows)
    superseded = mark_superseded(rows)
    today = date.today().isoformat()
    past_count = sum(bool(row["date"] and row["date"] < today) for row in rows)
    undated_rows = [row for row in rows if not row["date"]]
    filtered = [
        row for row in rows
        if (args.include_past or not row["date"] or row["date"] >= today)
        and (args.keep_undated or row["date"])
        and (args.keep_superseded or not row["superseded"])
    ]
    print(f"{merged} duplicate assessments merged.")
    if superseded and not args.keep_superseded:
        print(f"{superseded} old-date assessment(s) replaced by a reschedule hidden; "
              "use --keep-superseded to include them.")
    if past_count and not args.include_past:
        print(f"{past_count} past assessments hidden; use --include-past to include them.")
    if undated_rows and not args.keep_undated:
        print(f"{len(undated_rows)} undated assessments skipped; use --keep-undated to include them.")

    filtered.sort(key=lambda row: (row["date"] or "9999-12-31", row["start_time"] or "", row["course"]))
    print(f"\n{len(filtered)} assessments ready for local export:")
    print(f"{'DATE':<11}{'TIME':<6}{'TYPE':<14}{'COURSE':<34}TITLE")
    for row in filtered:
        review_flag = " [review]" if row["needs_review"] else ""
        print(
            f"{row['date'] or '?':<11}{row['start_time'] or '-':<6}{row['type']:<14}"
            f"{row['course'][:32]:<34}{row['title']}{review_flag}"
        )

    output_rows = []
    for row in filtered:
        output_rows.append({
            **row,
            "source_item_ids": ";".join(row["source_item_ids"]),
            "source_files": ";".join(row["source_files"]),
        })
    json_path = run_dir / "assessments.json"
    csv_path = run_dir / "assessments.csv"
    json_path.write_text(json.dumps(output_rows, indent=2, ensure_ascii=False), encoding="utf-8")
    fields = [
        "date", "start_time", "end_time", "course", "type", "title", "change",
        "venue", "topics", "weightage", "notes", "source", "confidence",
        "date_is_inferred", "needs_review", "superseded", "source_item_ids", "source_files",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({k: csv_safe(v) for k, v in row.items()} for row in output_rows)
    print(f"\nSaved local files: {csv_path} and {json_path}. No external service was contacted.")


if __name__ == "__main__":
    main()
