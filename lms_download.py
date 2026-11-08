"""
lms_download.py - downloads course material files and announcement attachments
listed in lms_dump.json (made by lms_fetch.py) into organised folders.

Run:
  python lms_download.py                 -> saves under .\\LMS_Files
  python lms_download.py D:\\LMS_Files          -> saves under D:\\LMS_Files
  python lms_download.py D:\\LMS_Files --yes    -> skip the confirmation prompt

Folder layout:
  <root>\\<Course>\\Materials\\<NN Section>\\<file>              (folder modules keep their subfolders)
  <root>\\<Course>\\Announcements\\<YYYY-MM-DD> <subject>\\<file>

Safe to re-run: files that already exist with the same size are skipped.
Also writes lms_files.json (a manifest of every file and where it was saved).
Needs: pip install requests    (uses lms_token.txt from lms_fetch.py)
"""
import html
import json
import re
import sys
import time
from pathlib import Path

import requests

DUMP = Path("lms_dump.json")
TOKEN_FILE = Path("lms_token.txt")
MANIFEST = Path("lms_files.json")
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

S = requests.Session()
S.headers.update({"User-Agent": "MoodleMobile/4.4.0 (Linux; Android 14)"})


# ---------------------------------------------------------------- naming
def sanitize(name, maxlen=60):
    name = html.unescape(name or "")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name[:maxlen].rstrip(" .")
    if not name:
        return "_"
    if name.split(".")[0].upper() in RESERVED:
        name = "_" + name
    return name


def sanitize_file(filename, maxlen=100):
    stem, dot, ext = html.unescape(filename or "file").rpartition(".")
    if not dot:
        return sanitize(filename, maxlen)
    ext = sanitize(ext, 10)
    return f"{sanitize(stem, maxlen - len(ext) - 1)}.{ext}"


def redact(text):
    """Remove access tokens from text that may be printed or saved."""
    return re.sub(r"(token=)[^&\s'\")]+", r"\1***", str(text))


def unique(path, used):
    if path not in used:
        used.add(path)
        return path
    stem, suf = path.stem, path.suffix
    i = 2
    while True:
        cand = path.with_name(f"{stem} ({i}){suf}")
        if cand not in used:
            used.add(cand)
            return cand
        i += 1


# ------------------------------------------------------------------ plan
def build_plan(dump, root):
    plan, used = [], set()
    for c in dump["courses"]:
        course_dir = root / sanitize(c.get("fullname") or c.get("shortname") or str(c["id"]))

        for si, sec in enumerate(c.get("sections", []), 1):
            sec_dir = course_dir / "Materials" / f"{si:02d} {sanitize(sec.get('name') or 'General', 50)}"
            for m in sec.get("modules", []):
                for f in m.get("files", []):
                    if not f.get("url"):
                        continue
                    base = sec_dir
                    if m.get("type") == "folder":
                        base = sec_dir / sanitize(m.get("name"), 50)
                        for part in (f.get("path") or "/").strip("/").split("/"):
                            if part:
                                base = base / sanitize(part, 40)
                    dest = unique(base / sanitize_file(f.get("name")), used)
                    plan.append({"course": c["fullname"], "kind": "material", "ref": m.get("id"),
                                 "path": f.get("path") or "/",
                                 "title": m.get("name"), "name": f.get("name"), "url": f["url"],
                                 "size": f.get("size") or 0, "dest": dest})

        for a in c.get("announcements", []):
            if not a.get("attachments"):
                continue
            day = (a.get("posted") or "")[:10] or "undated"
            adir = course_dir / "Announcements" / f"{day} {sanitize(a.get('subject'), 50)}"
            for f in a["attachments"]:
                if not f.get("url"):
                    continue
                dest = unique(adir / sanitize_file(f.get("name")), used)
                plan.append({"course": c["fullname"], "kind": "announcement", "ref": a.get("id"),
                             "title": a.get("subject"), "name": f.get("name"), "url": f["url"],
                             "size": f.get("size") or 0, "dest": dest})
    return plan


# -------------------------------------------------------------- download
def dl_url(url, token):
    if "/webservice/pluginfile.php" not in url:
        url = url.replace("/pluginfile.php", "/webservice/pluginfile.php", 1)
    return f"{url}{'&' if '?' in url else '?'}token={token}"


def download(item, token):
    dest = item["dest"]
    if dest.exists() and (not item["size"] or dest.stat().st_size == item["size"]):
        return "skipped"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    last = None
    for _ in range(3):
        try:
            with S.get(dl_url(item["url"], token), stream=True, timeout=120) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                ctype = r.headers.get("Content-Type", "")
                if "application/json" in ctype:
                    body = r.text
                    if '"exception"' in body or '"error"' in body:
                        raise RuntimeError(body[:200])
                    tmp.write_text(body, encoding="utf-8")
                else:
                    with open(tmp, "wb") as fh:
                        for chunk in r.iter_content(1 << 16):
                            fh.write(chunk)
            tmp.replace(dest)
            return "downloaded"
        except Exception as e:
            last = redact(e)
            time.sleep(2)
    tmp.unlink(missing_ok=True)
    return f"failed: {last}"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    yes = "--yes" in sys.argv
    root = Path(args[0]) if args else Path("LMS_Files")

    if not DUMP.exists():
        sys.exit("lms_dump.json not found - run lms_fetch.py first (same folder).")
    if not TOKEN_FILE.exists():
        sys.exit("lms_token.txt not found - run lms_fetch.py first to log in.")
    token = TOKEN_FILE.read_text().strip()
    dump = json.loads(DUMP.read_text(encoding="utf-8"))

    plan = build_plan(dump, root)
    if not plan:
        sys.exit("No downloadable files found in lms_dump.json (re-run lms_fetch.py from the updated version).")

    total_mb = sum(p["size"] for p in plan) / 1e6
    n_mat = sum(p["kind"] == "material" for p in plan)
    print(f"{len(plan)} files ({n_mat} course materials, {len(plan) - n_mat} announcement attachments), "
          f"about {total_mb:.1f} MB")
    print(f"Destination: {root.resolve()}")
    longest = max(len(str(p["dest"].resolve())) for p in plan)
    if longest > 240:
        print(f"WARNING: longest path is {longest} chars; Windows may refuse paths over 260. "
              "Choose a shorter root folder (e.g. D:\\LMS_Files).")
    if not yes and input("Proceed? [y/N]: ").strip().lower() != "y":
        sys.exit("Cancelled.")

    counts = {"downloaded": 0, "skipped": 0, "failed": 0}
    manifest = []
    for i, item in enumerate(plan, 1):
        status = download(item, token)
        key = "failed" if status.startswith("failed") else status
        counts[key] += 1
        print(f"[{i}/{len(plan)}] {status:<11} {item['dest'].relative_to(root) if root in item['dest'].parents else item['dest']}")
        manifest.append({"course": item["course"], "kind": item["kind"], "ref": item["ref"],
                         "path": item.get("path"),
                         "title": item["title"], "file": item["name"],
                         "saved_to": str(item["dest"].resolve()), "status": status})

    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n", counts, f"\nManifest: {MANIFEST.resolve()}")
    if counts["failed"]:
        print("Some files failed - rerun the script to retry (finished files are skipped).")


if __name__ == "__main__":
    main()
