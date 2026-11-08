"""
lms_fetch.py - pulls your courses, course contents, announcements AND the file
links for their attachments from the BITS Hyderabad LMS (Moodle mobile web
service) into lms_dump.json.

Run:   python lms_fetch.py
Needs: pip install requests

The token is cached in lms_token.txt (keep this file private, never commit it).
Delete lms_token.txt to log in again. Your password is never saved.
The dump contains file URLs WITHOUT your token; lms_download.py adds it on the fly.
"""
import base64
import getpass
import html
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE = os.environ.get("LMS_BASE_URL", "https://lms.bits-hyderabad.ac.in").rstrip("/")
SERVICE = "moodle_mobile_app"
TOKEN_FILE = Path("lms_token.txt")
OUT_FILE = Path("lms_dump.json")
IST = timezone(timedelta(hours=5, minutes=30))

S = requests.Session()
S.headers.update({"User-Agent": "MoodleMobile/4.4.0 (Linux; Android 14)"})


# ----------------------------------------------------------------- auth
def token_from_password():
    user = input("LMS username: ").strip()
    pw = getpass.getpass("LMS password (hidden): ")
    r = S.post(f"{BASE}/login/token.php",
               data={"username": user, "password": pw, "service": SERVICE}, timeout=30)
    data = r.json()
    if "token" not in data:
        print("Login failed:", data.get("error") or data)
        return None
    return data["token"]


def token_from_sso():
    print(
        "\nSSO / Google login - one-time steps:\n"
        "1. Open Chrome, press F12, go to the 'Network' tab, tick 'Preserve log'.\n"
        "2. Log in to the LMS normally in that tab (Google login is fine).\n"
        "3. In the SAME tab, open this URL:\n"
        f"   {BASE}/admin/tool/mobile/launch.php?service={SERVICE}&passport=12345&urlscheme=moodlemobile\n"
        "4. The page will fail to open (that is expected). In the Network tab, find the\n"
        "   request whose URL starts with  moodlemobile://token=  and copy that full URL.\n"
        "   (If you can't find it: the Console tab may show it as a blocked/unknown protocol.)\n"
    )
    raw = input("Paste the moodlemobile://token=... URL (or just the base64 part): ").strip()
    raw = raw.replace("moodlemobile://token=", "")
    raw = raw.split("&")[0]
    raw += "=" * (-len(raw) % 4)
    try:
        decoded = base64.b64decode(raw).decode()
    except Exception as e:
        print("Could not decode that string:", e)
        return None
    parts = decoded.split(":::")
    if len(parts) < 2:
        print("Unexpected format:", decoded[:80])
        return None
    return parts[1]


def get_token():
    if TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text().strip()
        if t:
            return t
    print("How do you log in to the LMS?\n  1) username + password\n  2) Google / SSO only")
    choice = input("Choice [1/2]: ").strip()
    token = token_from_password() if choice == "1" else token_from_sso()
    if not token:
        sys.exit(1)
    TOKEN_FILE.write_text(token)
    return token


# ------------------------------------------------------------- ws calls
def ws(token, function, **params):
    flat = {"wstoken": token, "wsfunction": function, "moodlewsrestformat": "json"}
    for k, v in params.items():
        if isinstance(v, list):
            for i, item in enumerate(v):
                flat[f"{k}[{i}]"] = item
        else:
            flat[k] = v
    r = S.post(f"{BASE}/webservice/rest/server.php", data=flat, timeout=60)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("exception"):
        raise RuntimeError(f"{function}: {data.get('errorcode')} - {data.get('message')}")
    return data


def ts(epoch):
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, IST).isoformat(timespec="minutes")


def to_text(raw_html):
    t = re.sub(r"<(br|/p|/div|/li)\s*/?>", "\n", raw_html or "", flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def discussion_attachments(token, discussion_id):
    """File attachments on an announcement. Returns None if the lookup itself failed."""
    for fn in ("mod_forum_get_discussion_posts", "mod_forum_get_forum_discussion_posts"):
        try:
            res = ws(token, fn, discussionid=discussion_id)
        except RuntimeError:
            continue
        out, seen = [], set()
        for p in res.get("posts", []):
            for a in p.get("attachments") or []:
                url = a.get("fileurl")
                if url and url not in seen:
                    seen.add(url)
                    out.append({"name": a.get("filename"), "size": a.get("filesize"),
                                "url": url, "mimetype": a.get("mimetype"),
                                "modified": ts(a.get("timemodified"))})
        return out
    return None


# ----------------------------------------------------------------- main
def main():
    token = get_token()

    try:
        info = ws(token, "core_webservice_get_site_info")
    except Exception as e:
        print("Token rejected:", e, "\nDeleting cached token; run again.")
        TOKEN_FILE.unlink(missing_ok=True)
        sys.exit(1)
    print(f"Logged in as {info.get('fullname')} (userid {info.get('userid')})")

    courses = ws(token, "core_enrol_get_users_courses", userid=info["userid"])
    print(f"{len(courses)} courses found")
    course_ids = [c["id"] for c in courses]

    # forums per course -> find the 'news' (Announcements) forum
    forums = ws(token, "mod_forum_get_forums_by_courses", courseids=course_ids)
    news_by_course = {}
    for f in forums:
        if f.get("type") == "news":
            news_by_course.setdefault(f["course"], []).append(f)

    dump = {"fetched_at": datetime.now(IST).isoformat(timespec="minutes"),
            "user": info.get("fullname"), "courses": []}
    att_lookup_failed = 0

    for c in courses:
        print(f"- {c['fullname']}")
        entry = {
            "id": c["id"],
            "shortname": c.get("shortname"),
            "fullname": c.get("fullname"),
            "url": f"{BASE}/course/view.php?id={c['id']}",
            "start": ts(c.get("startdate")),
            "end": ts(c.get("enddate")),
            "progress": c.get("progress"),
            "sections": [],
            "announcements": [],
        }

        # course contents (sections + modules + downloadable files)
        try:
            for sec in ws(token, "core_course_get_contents", courseid=c["id"]):
                entry["sections"].append({
                    "name": sec.get("name"),
                    "summary": to_text(sec.get("summary")),
                    "modules": [{
                        "id": m.get("id"),
                        "name": m.get("name"),
                        "type": m.get("modname"),
                        "url": m.get("url"),
                        "description": to_text(m.get("description")),
                        "files": [{"name": fl.get("filename"),
                                   "path": fl.get("filepath"),
                                   "size": fl.get("filesize"),
                                   "url": fl.get("fileurl"),
                                   "modified": ts(fl.get("timemodified"))}
                                  for fl in m.get("contents", []) if fl.get("type") == "file"],
                    } for m in sec.get("modules", [])],
                })
        except Exception as e:
            print("    contents failed:", e)

        # announcements (+ attachments)
        for forum in news_by_course.get(c["id"], []):
            try:
                try:
                    res = ws(token, "mod_forum_get_forum_discussions",
                             forumid=forum["id"], perpage=50)
                    discs = res.get("discussions", [])
                except RuntimeError:
                    res = ws(token, "mod_forum_get_forum_discussions_paginated",
                             forumid=forum["id"], perpage=50)
                    discs = res.get("discussions", [])
                for d in discs:
                    atts = discussion_attachments(token, d.get("discussion"))
                    if atts is None:
                        att_lookup_failed += 1
                        atts = []
                    entry["announcements"].append({
                        "id": d.get("discussion"),
                        "subject": d.get("name") or d.get("subject"),
                        "author": d.get("userfullname"),
                        "posted": ts(d.get("created")),
                        "modified": ts(d.get("timemodified")),
                        "text": to_text(d.get("message")),
                        "url": f"{BASE}/mod/forum/discuss.php?d={d.get('discussion')}",
                        "has_attachments": bool(atts),
                        "attachments": atts,
                    })
            except Exception as e:
                print("    announcements failed:", e)

        n_mat = sum(len(m["files"]) for s in entry["sections"] for m in s["modules"])
        n_att = sum(len(a["attachments"]) for a in entry["announcements"])
        print(f"    {len(entry['sections'])} sections, {len(entry['announcements'])} announcements, "
              f"{n_mat} material files, {n_att} announcement attachments")
        dump["courses"].append(entry)

    OUT_FILE.write_text(json.dumps(dump, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {OUT_FILE.resolve()}")
    if att_lookup_failed:
        print(f"WARNING: attachment lookup failed for {att_lookup_failed} announcements "
              "(the LMS may not expose the posts API) - their attachments are missing.")


if __name__ == "__main__":
    main()
