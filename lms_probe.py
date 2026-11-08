"""
lms_probe.py - checks how the BITS Hyderabad LMS can be accessed.
Sends NO real credentials. Run:  python lms_probe.py
Needs:  pip install requests
"""
import os
import re
import requests

BASE = os.environ.get("LMS_BASE_URL", "https://lms.bits-hyderabad.ac.in").rstrip("/")
HEADERS = {"User-Agent": "Mozilla/5.0"}


def main():
    s = requests.Session()
    s.headers.update(HEADERS)

    # 1. Login page: is it Moodle, and which login methods exist?
    r = s.get(f"{BASE}/login/index.php", timeout=20)
    html = r.text
    print(f"[login page] HTTP {r.status_code}, final URL: {r.url}")
    print("Looks like Moodle:", "moodle" in html.lower())

    m = re.search(r'M\.cfg\s*=\s*\{[^}]*"release":"([^"]+)"', html)
    if m:
        print("Moodle release:", m.group(1))

    print("Has username/password form:", 'name="username"' in html and 'name="password"' in html)

    idps = set(re.findall(r'href="([^"]*(?:auth/oauth2/login\.php|auth/saml2|auth/shibboleth|auth/cas)[^"]*)"', html))
    print("SSO / OAuth2 login links found:", len(idps))
    for link in list(idps)[:5]:
        print("  ", link[:140])

    # 2. Is the mobile web service enabled? (dummy creds, expect an error JSON)
    t = s.post(
        f"{BASE}/login/token.php",
        data={"username": "probe_user", "password": "probe_pass", "service": "moodle_mobile_app"},
        timeout=20,
    )
    print(f"\n[token.php] HTTP {t.status_code}")
    print(t.text[:300])
    print(
        "\nHow to read it:\n"
        " - errorcode 'invalidlogin'  -> mobile web service is ENABLED (API route works with username/password)\n"
        " - errorcode 'enablewsdescription' or 'servicenotavailable' -> web services disabled (use Playwright)\n"
        " - HTML / 404                -> endpoint blocked (use Playwright)"
    )

    # 3. SSO mobile launch endpoint (used by the mobile app for Google/SSO logins)
    l = s.get(f"{BASE}/admin/tool/mobile/launch.php", params={"service": "moodle_mobile_app", "passport": "1"},
              allow_redirects=False, timeout=20)
    print(f"\n[mobile launch] HTTP {l.status_code} (302 to a login page = SSO launch exists)")


if __name__ == "__main__":
    main()
