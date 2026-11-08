"""Build private JSON bundles for manual assessment extraction with Claude.ai."""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import lms_common as common

DUMP, MANIFEST = common.DUMP, common.MANIFEST
ATT_CHARS = 15000
DOC_EXT = {".pdf", ".docx", ".pptx", ".txt", ".csv", ".md", *common.IMAGE_TYPES}
DOC_INCLUDE = re.compile(
    r"quiz|mid[\s_-]?sem|mid[\s_-]?term|compre|\bexams?\b|examination|schedule|syllabus|notice|assign|evaluat|\btest\b|handout|"
    r"room allocation|roll number|course time|resched|postpone|viva|project|presentation|deadline", re.I)
DOC_EXCLUDE = re.compile(
    r"solution|answer key|marks?[\s_-]?(list|sheet|display)|marksheet|distribution|recheck|discrepanc|"
    r"group[\s_-]?\d+|session[\s_-]?\d|\bslides?\b|chapter|lect(ure)?[\s_-]?\d|tut(orial)?[\s_-]?\d|expt|experiment|"
    r"(class|tut\w*|lect\w*)\b.*cancel", re.I)


def extract_text(path):
    """Return extracted text and an optional explanation for unreadable files."""
    blocks, note = common.read_attachment(path)
    text = "\n".join(block["text"] for block in blocks if block.get("type") == "text")
    if text.strip():
        return text[:ATT_CHARS], note
    return None, note or "no readable text"


PROMPT = """I'm attaching a JSON file of items from my university LMS courses. There are two kinds:
  - kind "announcement": a course announcement (posted_date, posted_weekday, subject, text, and the extracted text
    of its attachments);
  - kind "course_document": a file from the course page such as a quiz schedule, exam details, syllabus or handout
    (reference_date = the date it was uploaded/modified, and its extracted text). For these, use reference_date
    wherever the rules below say "posted date", and set source to "attachment".
Anything with a "problem" could not be read as text; I may attach those files separately, labelled with the item_id.

""" + common.SYSTEM + """

Output format: reply with ONLY one JSON array in a single code block, nothing else. Include one object for EVERY
item_id in the file, with "assessments": [] when there is nothing to record:

[
  {"item_id": 123,
   "assessments": [
     {"type": "quiz|test|midsem|compre|assignment|lab|viva|project|presentation|other",
      "title": "Quiz 2",
      "change": "new|rescheduled|cancelled|reminder",
      "date": "YYYY-MM-DD or null",
      "date_is_inferred": true,
      "start_time": "HH:MM or null",
      "end_time": "HH:MM or null",
      "venue": "string or null",
      "topics": "string or null",
      "weightage": "string or null",
      "notes": "string or null",
      "source": "announcement_text|attachment|both",
      "confidence": "high|medium|low"}
   ]},
  {"item_id": "m45f0", "assessments": []}
]
"""


def load_manifest():
    announcements, materials = {}, {}
    if MANIFEST.exists():
        for item in json.loads(MANIFEST.read_text(encoding="utf-8")):
            if str(item.get("status", "")).startswith("failed"):
                continue
            if item.get("kind") == "announcement":
                announcements.setdefault(str(item["ref"]), []).append(item)
            elif item.get("kind") == "material":
                materials[(str(item["ref"]), item.get("path") or "/", item["file"])] = item["saved_to"]
    else:
        print("Note: lms_files.json not found - files will not be read. Run lms_download.py first.")
    return announcements, materials


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=60, help="announcement lookback in days; 0 includes all")
    parser.add_argument("--doc-days", type=int, default=120, help="document lookback in days; 0 includes all")
    parser.add_argument("--max-docs", type=int, default=40, help="maximum matching documents, newest first")
    parser.add_argument("--doc-all", action="store_true", help="include all supported course documents")
    parser.add_argument("--no-docs", action="store_true", help="include announcements only")
    parser.add_argument("--also", default="", help="extra document-name regex, e.g. sunny|handout")
    parser.add_argument("--all", action="store_true", help="include announcements without keywords or attachments")
    parser.add_argument("--chunk-chars", type=int, default=60000, help="approximate maximum characters per bundle")
    parser.add_argument("--output-dir", type=Path, default=Path("bundle_output"),
                        help="new or empty directory for this run's private files")
    args = parser.parse_args(argv)

    if not DUMP.exists():
        sys.exit("lms_dump.json not found - run lms_fetch.py first.")
    dump = json.loads(DUMP.read_text(encoding="utf-8"))
    files_by_announcement, material_files = load_manifest()
    now = datetime.now(common.IST)
    also = re.compile(args.also, re.I) if args.also else None
    records, manual, problems_by_item = [], [], {}

    cutoff = now - timedelta(days=args.days) if args.days else None
    for course in dump.get("courses", []):
        for announcement in course.get("announcements", []):
            announcement_id = str(announcement["id"])
            posted = common.parse_dt(announcement.get("posted") or "")
            if cutoff and posted and posted < cutoff:
                continue
            files = files_by_announcement.get(announcement_id, [])
            haystack = f"{announcement.get('subject')} {announcement.get('text')}"
            if not (args.all or files or common.KEYWORDS.search(haystack)):
                continue
            attachments = []
            for file_record in files:
                text, problem = extract_text(file_record["saved_to"])
                attachment = {"file": file_record["file"]}
                if text:
                    attachment["text"] = text
                if problem:
                    attachment["problem"] = problem
                    problems_by_item.setdefault(announcement_id, []).append(f"{file_record['file']}: {problem}")
                    manual.append(
                        f"item_id {announcement_id} | {course['fullname']} | {announcement.get('subject')} | "
                        f"{file_record['file']} | {file_record['saved_to']} | {problem}"
                    )
                attachments.append(attachment)
            records.append({
                "item_id": int(announcement["id"]) if announcement_id.isdigit() else announcement["id"],
                "kind": "announcement",
                "course": course["fullname"],
                "posted_date": posted.strftime("%Y-%m-%d") if posted else None,
                "posted_weekday": posted.strftime("%A") if posted else None,
                "subject": announcement.get("subject"),
                "text": announcement.get("text") or "",
                "attachments": attachments,
            })
    announcement_count = len(records)

    documents = []
    if not args.no_docs:
        doc_cutoff = now - timedelta(days=args.doc_days) if args.doc_days else None
        for course in dump.get("courses", []):
            for section in course.get("sections", []):
                for module in section.get("modules", []):
                    for index, file_record in enumerate(module.get("files", [])):
                        document_id = f"m{module['id']}f{index}"
                        name = file_record.get("name") or ""
                        haystack = name if module.get("type") == "folder" else f"{name} {module.get('name') or ''}"
                        if Path(name).suffix.lower() not in DOC_EXT:
                            continue
                        wanted = DOC_INCLUDE.search(haystack) or (also and also.search(haystack))
                        if not args.doc_all and (not wanted or DOC_EXCLUDE.search(haystack)):
                            continue
                        modified = common.parse_dt(file_record.get("modified") or "")
                        if doc_cutoff and modified and modified < doc_cutoff:
                            continue
                        saved_path = material_files.get((str(module["id"]), file_record.get("path") or "/", name))
                        if not saved_path:
                            continue
                        documents.append((
                            modified or datetime.min.replace(tzinfo=common.IST),
                            course, section, module, file_record, document_id, saved_path, modified,
                        ))
        documents.sort(key=lambda item: item[0], reverse=True)
        omitted = max(0, len(documents) - args.max_docs)
        documents = documents[:args.max_docs]
        for _, course, section, module, file_record, document_id, saved_path, modified in documents:
            text, problem = extract_text(saved_path)
            record = {
                "item_id": document_id,
                "kind": "course_document",
                "course": course["fullname"],
                "section": section.get("name"),
                "module": module.get("name"),
                "file": file_record.get("name"),
                "reference_date": modified.strftime("%Y-%m-%d") if modified else None,
                "reference_weekday": modified.strftime("%A") if modified else None,
                "text": text or "",
            }
            if problem:
                record["problem"] = problem
                problems_by_item.setdefault(document_id, []).append(f"{file_record.get('name')}: {problem}")
                manual.append(
                    f"item_id {document_id} | {course['fullname']} | {module.get('name')} | "
                    f"{file_record.get('name')} | {saved_path} | {problem}"
                )
            records.append(record)
        if omitted:
            print(f"Note: {omitted} older matching documents left out; raise --max-docs to include them.")

    if not records:
        print("Nothing matched the filters.")
        return

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_names = ("lms_prompt.txt", "lms_problems.json", "lms_manual_attach.txt")
    if any((output_dir / name).exists() for name in generated_names) or any(output_dir.glob("lms_bundle_*.json")):
        sys.exit(f"{output_dir} already contains generated files. Choose a fresh run folder.")

    chunks, current_chunk, current_size = [], [], 0
    for record in records:
        record_size = len(json.dumps(record, ensure_ascii=False))
        if current_chunk and current_size + record_size > args.chunk_chars:
            chunks.append(current_chunk)
            current_chunk, current_size = [], 0
        current_chunk.append(record)
        current_size += record_size
    chunks.append(current_chunk)

    for index, chunk in enumerate(chunks, 1):
        target = output_dir / f"lms_bundle_{index:02d}.json"
        target.write_text(json.dumps(chunk, indent=1, ensure_ascii=False), encoding="utf-8")
    (output_dir / "lms_prompt.txt").write_text(PROMPT, encoding="utf-8")
    (output_dir / "lms_problems.json").write_text(
        json.dumps(problems_by_item, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "lms_manual_attach.txt").write_text("\n".join(manual) or "(none)", encoding="utf-8")

    bundle_paths = [str(output_dir / f"lms_bundle_{index:02d}.json") for index in range(1, len(chunks) + 1)]
    print(
        f"{announcement_count} announcements + {len(records) - announcement_count} course documents "
        f"-> {len(chunks)} private bundle file(s): " + ", ".join(bundle_paths)
    )
    if len(records) > announcement_count:
        print("Documents included:")
        for record in records[announcement_count:]:
            print(f"  - {record['course'][:30]} | {record['file']}")
    print(f"{len(manual)} file(s) need manual review or attachment; see {output_dir / 'lms_manual_attach.txt'}.")
    print(
        "\nNext: in a new Claude.ai chat, attach each bundle, paste lms_prompt.txt, "
        "then save each reply as claude_output_NN.json in that run folder."
    )


if __name__ == "__main__":
    main()
