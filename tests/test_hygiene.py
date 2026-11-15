"""This repo is public: no personal data in any tracked file, and no real portal download.

Fails on any email address outside example.com (GitHub noreply addresses are exempt, for commit authors in docs),
on any string listed in tests/hygiene_denylist.txt, and on a tracked ZIP outside tests/fixtures/. The denylist file is
git-ignored and optional: keep the household's real names, emails, providers and hostnames in it locally, one per
line, so they never enter the repo.

`.github/scripts/privacy_scan.py` and `tests/test_privacy_scan.py` are verbatim copies from SDNick484/jigs and must not
be edited. They name the service addresses the scan allows (GitHub's and Anthropic's no-reply addresses, example.org),
so they are exempt from the email rule here; the privacy scan itself still checks them, and the denylist below still
applies to them."""
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DENYLIST = os.path.join(ROOT, "tests", "hygiene_denylist.txt")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
VERBATIM_COPIES = {".github/scripts/privacy_scan.py", "tests/test_privacy_scan.py"}  # exempt from the email rule only (see the docstring)
# Only used when git is unavailable (a source tarball): mirrors .gitignore closely enough for a test.
SKIP_DIRS = {".git", ".venv", "venv", "data", "dist", "private", "imports", "__pycache__", ".pytest_cache", ".ruff_cache",
             "node_modules", ".superpowers"}
SKIP_FILES = {DENYLIST, os.path.join(ROOT, ".env")}


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
        return [os.path.join(ROOT, p) for p in out.decode().split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            files += [p for p in (os.path.join(dirpath, f) for f in filenames)
                      if p not in SKIP_FILES and not (os.path.basename(dirpath) == "deploy" and p.endswith(".env"))]
        return files


def allowed_domain(domain):
    domain = domain.lower()
    return any(domain == d or domain.endswith("." + d) for d in ("example.com", "noreply.github.com"))


def denied_strings():
    if not os.path.exists(DENYLIST):
        return []
    with open(DENYLIST, encoding="utf-8") as f:
        return [s.strip().lower() for s in f if s.strip() and not s.lstrip().startswith("#")]


def test_no_personal_data_in_tracked_files():
    files = tracked_files()
    assert files, "found no files to check"
    denied = denied_strings()
    problems = []
    for path in files:
        if not os.path.isfile(path):
            continue  # deleted in the working tree but still in the index
        with open(path, "rb") as f:
            body = f.read().decode("utf-8", errors="ignore")
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        if rel not in VERBATIM_COPIES:
            problems += [f"{rel}: email {m.group(0)}" for m in EMAIL.finditer(body) if not allowed_domain(m.group(1))]
        low = body.lower()
        problems += [f"{rel}: a denylisted string" for s in denied if s in low]
    assert not problems, "\n".join(problems)


def test_no_zip_outside_test_fixtures():
    """A real portal download is a ZIP; only hand-made synthetic ones under tests/fixtures/ may be committed."""
    zips = [os.path.relpath(p, ROOT).replace(os.sep, "/") for p in tracked_files() if p.lower().endswith(".zip")]
    assert [z for z in zips if not z.startswith("tests/fixtures/")] == []


def test_allowed_domain():
    assert allowed_domain("example.com") and allowed_domain("Mail.Example.com")
    assert allowed_domain("users.noreply.github.com")
    assert not allowed_domain("example.com.evil.test") and not allowed_domain("notexample.com")
