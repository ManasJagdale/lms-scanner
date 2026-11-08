"""Shared, local-only LMS helpers for bundling course content."""
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

DUMP = Path("lms_dump.json")
MANIFEST = Path("lms_files.json")
IST = timezone(timedelta(hours=5, minutes=30))

KEYWORDS = re.compile(
    r"quiz|test|exam|mid[\s-]?sem|compre|assignment|viva|deadline|submission|submit|"
    r"evaluat|presentation|project|marks|syllabus|schedule|lab\b|tutorial test|reschedul|postpone|cancel",
    re.I,
)

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

SYSTEM = """You extract academic assessment information for a university student from LMS course announcements and their attachments.

Record every graded assessment, test or deadline: quizzes, tests, mid-semester and comprehensive exams, assignments, lab evaluations, vivas, projects, presentations, submission deadlines. Include cancellations, postponements and reschedules, marking the change accordingly. Ignore everything else (general news, lecture notes, reading material). If there is no assessment information, return an empty list.

Rules:
- Use only what the text and attachments say. Never invent a date, time, venue or topics; leave them out when not stated.
- Resolve relative dates ("tomorrow", "next Tuesday", "this Friday") using the announcement's posted date, and mark inferred dates. Dates are YYYY-MM-DD. Times are 24-hour HH:MM, Indian Standard Time.
- One entry per distinct assessment; a single announcement may contain several.
- If the announcement text and an attachment disagree, report the attachment's details, say so in notes, and use confidence "low".
- topics = syllabus / chapters covered. weightage = marks or percentage. notes = any other instruction (open book, calculator allowed, seating, bring ID, submission portal).
- confidence: high = what it is and the date are explicit; medium = something was resolved or partial; low = guessing or conflicting information.
- title should be short, e.g. "Quiz 2" or "Mid-semester exam"."""


def parse_dt(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def read_attachment(path):
    """Return readable attachment blocks and an optional explanation."""
    p = Path(path)
    if not p.exists():
        return [], "file missing on disk"
    suffix = p.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader
            text = "\n".join((page.extract_text() or "") for page in PdfReader(str(p)).pages[:40])
            if len(text.strip()) < 30:
                return [], "no text layer (scanned PDF?)"
            return [{"type": "text", "text": text[:60000]}], None
        if suffix in IMAGE_TYPES:
            return [], "image - attach it to the chat manually"
        if suffix == ".docx":
            from docx import Document
            document = Document(str(p))
            parts = [paragraph.text for paragraph in document.paragraphs]
            for table in document.tables:
                for row in table.rows:
                    parts.append(" | ".join(cell.text.strip() for cell in row.cells))
            text = "\n".join(part for part in parts if part.strip())
        elif suffix == ".pptx":
            from pptx import Presentation
            parts = []
            for slide_number, slide in enumerate(Presentation(str(p)).slides, 1):
                parts.append(f"--- slide {slide_number} ---")
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        parts.append(shape.text_frame.text)
                    if getattr(shape, "has_table", False):
                        for row in shape.table.rows:
                            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
            text = "\n".join(part for part in parts if part.strip())
        elif suffix in {".txt", ".csv", ".md"}:
            text = p.read_text(encoding="utf-8", errors="replace")
        else:
            return [], f"unsupported file type {suffix or '(none)'}"
        if not text.strip():
            return [], "no readable text found"
        return [{"type": "text", "text": text[:60000]}], None
    except ImportError as error:
        return [], f"missing library '{error.name}' (install requirements.txt)"
    except Exception as error:
        return [], f"could not read: {error}"
