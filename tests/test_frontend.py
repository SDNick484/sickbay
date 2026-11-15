"""The shell around the frontend: what GET / and GET /manifest.json say, and static checks over static/ that keep
the Content-Security-Policy (no inline script, no inline style), the escaping rule and the "no advice" rule true as
screens are added. The JavaScript's own unit tests are tests/frontend.test.mjs
(`node --test tests/frontend.test.mjs`)."""
import glob
import os
import re

import pytest

import settings
from conftest import LAN_IP, auth, mut, read_static

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
JS_FILES = sorted(glob.glob(os.path.join(STATIC, "js", "*.js")))
STATIC_TEXT_FILES = sorted(p for p in glob.glob(os.path.join(STATIC, "**", "*"), recursive=True)
                           if p.endswith((".html", ".css", ".js", ".json", ".svg")))

# The only things a template-literal interpolation that builds markup may be: one call to one of these.
SAFE_CALL = re.compile(r"(esc|mm|daysLabel|dateLabel|render[A-Za-z0-9_]*)\(")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- what the server renders
def test_index_title_and_manifest_use_app_name_only(client):
    html = client.get("/", headers=auth()).get_data(as_text=True)
    assert "<title>Logbook</title>" in html
    assert 'name="apple-mobile-web-app-title" content="Logbook"' in html
    m = client.get("/manifest.json", headers=auth()).get_json()
    assert (m["name"], m["short_name"]) == ("Logbook", "Logbook")
    assert "__APP_NAME__" not in html


def test_app_name_setting_reaches_title_home_screen_name_and_manifest_escaped(client, monkeypatch):
    monkeypatch.setattr(settings, "APP_NAME", 'Tab <b>"&</b>')
    html = client.get("/", headers=auth()).get_data(as_text=True)
    assert "<title>Tab &lt;b&gt;&quot;&amp;&lt;/b&gt;</title>" in html
    assert '<meta name="apple-mobile-web-app-title" content="Tab &lt;b&gt;&quot;&amp;&lt;/b&gt;">' in html
    assert "<b>" not in html
    m = client.get("/manifest.json", headers=auth()).get_json()
    assert (m["name"], m["short_name"]) == ('Tab <b>"&</b>', 'Tab <b>"&</b>')


def test_manifest_is_a_manifest_and_the_page_fetches_it_with_credentials(client):
    r = client.get("/manifest.json", headers=auth())
    assert r.mimetype == "application/manifest+json"
    m = r.get_json()
    assert m["start_url"] == "/" and m["display"] == "standalone" and m["icons"]
    html = client.get("/", headers=auth()).get_data(as_text=True)
    # The request guard covers /manifest.json too, and a manifest fetch carries no credentials by default.
    assert '<link rel="manifest" href="/manifest.json" crossorigin="use-credentials">' in html


def test_index_manifest_and_assets_are_behind_the_identity_guard(client):
    for path in ("/", "/manifest.json", "/static/index.html", "/static/style.css", "/static/js/main.js",
                 "/static/icon.svg"):
        r = client.get(path, environ_base={"REMOTE_ADDR": LAN_IP})
        assert (r.status_code, r.get_json()) == (403, {"error": "Forbidden"}), path


def test_pages_and_assets_carry_the_csp_and_no_store(client):
    for path in ("/", "/manifest.json", "/static/style.css", "/static/js/main.js", "/static/js/core.js",
                 "/static/icon.svg"):
        r = client.get(path, headers=auth())
        assert r.status_code == 200, path
        csp = r.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "style-src 'self'" in csp and "unsafe" not in csp, path
        assert "frame-ancestors 'none'" in csp, path
        assert r.headers["Cache-Control"] == "no-store", path


def test_the_shell_names_nothing_about_the_condition(client):
    """Page title, home-screen name, manifest and icon are neutral."""
    html = client.get("/", headers=auth()).get_data(as_text=True)
    head = html.split("</head>")[0]
    manifest = client.get("/manifest.json", headers=auth()).get_data(as_text=True)
    icon = read(os.path.join(STATIC, "icon.svg"))
    for text in (head, manifest, icon):
        assert not re.search(r"sickbay|kidney|stone|urolog|health|medical", text, re.I), text


def test_the_screens_load_what_the_routes_give_them(client):
    """The paths the frontend calls exist (a 403 for a missing identity would show on the earlier tests; here they
    answer 200 with one)."""
    event = client.post("/api/events", json={}, headers=mut()).get_json()
    for path in ("/api/me", "/api/events", f"/api/events/{event['id']}", "/api/lists", "/api/doctors",
                 f"/api/events/{event['id']}/files"):
        assert client.get(path, headers=auth()).status_code == 200, path
    assert client.get("/static/js/package.json", headers=auth()).get_json() == {"type": "module"}


# ---------------------------------------------------------------- the CSP, held by construction
def test_no_inline_script_style_or_handlers_in_the_page():
    html = read(os.path.join(STATIC, "index.html"))
    assert not re.search(r"<script(?![^>]*\ssrc=)", html), "an inline <script>"
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", html, re.I), "an inline event handler"
    assert not re.search(r"<[^>]+\sstyle\s*=", html, re.I) and "<style" not in html, "inline style"
    assert 'type="module"' in html and "/static/js/main.js" in html


@pytest.mark.parametrize("path", JS_FILES, ids=os.path.basename)
def test_scripts_never_write_inline_style_or_handlers_or_raw_html(path):
    src = read(path)
    assert not re.search(r"""\bstyle\s*=\s*["'\\]""", src), "a style attribute in a string"
    assert not re.search(r"""\.style\b""", src), "element.style (inline style)"
    for banned in ("cssText", 'setAttribute("style"', "setAttribute('style'", "insertAdjacentHTML", "outerHTML",
                   "document.write", "eval(", "new Function"):
        assert banned not in src, banned
    assert not re.search(r"""\bon[a-z]+\s*=\s*["'\\]""", src), "an inline event handler attribute in a string"
    assert not re.search(r"""\.on[a-z]+\s*=[^=]""", src), "assign handlers with addEventListener"


def test_every_mutation_goes_through_the_api_module():
    """Only api.js calls fetch, so X-Sickbay and the JSON body are never forgotten."""
    for path in JS_FILES:
        src = read(path)
        if os.path.basename(path) == "api.js":
            assert src.count("X-Sickbay") >= 2
        else:
            assert "fetch(" not in src and "XMLHttpRequest" not in src, path


def test_files_open_as_a_plain_link_in_a_new_tab_never_a_frame():
    src = read(os.path.join(STATIC, "js", "event.js"))
    assert "/api/files/${f.id}/content" in src and 'target: "_blank"' in src and 'rel: "noopener"' in src
    for path in JS_FILES:
        assert not re.search(r"iframe|<embed|<object|window\.open", read(path)), path


# ---------------------------------------------------------------- escaping
# ---------------------------------------------------------------- escaping
def _skip_string(src, i):
    quote = src[i]
    i += 1
    while src[i] != quote:
        i += 2 if src[i] == "\\" else 1
    return i + 1


def _skip_comment(src, i):
    """i is at a "/": index after the comment starting here, or i when it does not start one."""
    if src.startswith("//", i):
        j = src.find("\n", i)
        return len(src) if j < 0 else j
    if src.startswith("/*", i):
        return src.index("*/", i) + 2
    return i


def _template(src, i, found):
    """src[i] is a backtick. Parse the template literal (and any nested in its ${} expressions); every one is
    appended to `found` as (pieces, exprs): the raw text segments and the ${} expression sources. Returns the
    index after the closing backtick."""
    i += 1
    pieces, exprs, buf = [], [], []
    while src[i] != "`":
        if src[i] == "\\":
            buf.append(src[i:i + 2])
            i += 2
        elif src.startswith("${", i):
            pieces.append("".join(buf))
            buf = []
            start, depth, i = i + 2, 1, i + 2
            while depth:
                c = src[i]
                if c in "'\"":
                    i = _skip_string(src, i)
                elif c == "`":
                    i = _template(src, i, found)
                elif c == "/" and (j := _skip_comment(src, i)) != i:
                    i = j
                else:
                    depth += (c == "{") - (c == "}")
                    i += 1
            exprs.append(src[start:i - 1])
        else:
            buf.append(src[i])
            i += 1
    pieces.append("".join(buf))
    found.append((pieces, exprs))
    return i + 1


def template_literals(src):
    found, i = [], 0
    while i < len(src):
        c = src[i]
        if c in "'\"":
            i = _skip_string(src, i)
        elif c == "`":
            i = _template(src, i, found)
        elif c == "/" and (j := _skip_comment(src, i)) != i:
            i = j
        else:
            i += 1
    return found


def is_single_safe_call(expr):
    """`esc(...)`, `money(...)`, `daysLabel(...)` or `renderXxx(...)` and nothing after the closing parenthesis."""
    expr = expr.strip()
    m = SAFE_CALL.match(expr)
    if not m:
        return False
    depth, i = 1, m.end()
    while i < len(expr):
        c = expr[i]
        if c in "'\"":
            i = _skip_string(expr, i)
            continue
        if c == "`":
            return False  # a template literal as an argument is built elsewhere, by a render function
        depth += (c == "(") - (c == ")")
        i += 1
        if depth == 0:
            return i == len(expr)
    return False


def test_the_scanner_itself():
    src = 'a = `<b>${esc(x)}</b> ${items.map(i => `<i>${i}</i>`).join("")} // not a comment`; // `x`\nb = "`"'
    found = template_literals(src)
    assert len(found) == 2
    outer = next(f for f in found if len(f[1]) == 2)
    assert [e.strip() for e in outer[1]] == ["esc(x)", 'items.map(i => `<i>${i}</i>`).join("")']
    assert is_single_safe_call("esc(a, b(c))") and is_single_safe_call(" renderRow(x) ")
    assert not is_single_safe_call("esc(a) + b") and not is_single_safe_call("x ? esc(a) : ''")
    assert not is_single_safe_call("escape(a)") and not is_single_safe_call("esc(a)(b)")
    assert not is_single_safe_call("esc(a) || `<x>`")


def test_unquoted_attribute_rule_ignores_query_strings():
    unquoted = re.compile(r"\s[A-Za-z_:][\w:.-]*=\s*$")
    assert unquoted.search('<a href=') and unquoted.search('<input value= ')
    assert not unquoted.search('<a href="#/idea?for=') and not unquoted.search('<a href="x" title="')


@pytest.mark.parametrize("path", JS_FILES, ids=os.path.basename)
def test_every_innerhtml_interpolation_is_escaped(path):
    """Every `${...}` in a template literal that holds markup is one call to esc, money, daysLabel or a function
    whose name starts with `render`, and never sits in an unquoted attribute. Every innerHTML assignment is a
    template literal, an empty string or a render* call."""
    src = read(path)
    for pieces, exprs in template_literals(src):
        if not re.search(r"</?[A-Za-z]", "".join(pieces)):
            continue  # not markup: a URL path, a message
        for before, expr in zip(pieces, exprs):
            assert is_single_safe_call(expr), f"{os.path.basename(path)}: unescaped interpolation ${{{expr.strip()}}}"
            # `name=${...}` with no quote: an attribute value that a space or > in the text would break out of
            assert not re.search(r"\s[A-Za-z_:][\w:.-]*=\s*$", before), \
                f"{os.path.basename(path)}: unquoted attribute before ${{{expr.strip()}}}"
    for m in re.finditer(r"innerHTML\s*\+?=\s*(?P<rhs>[^;\n]*)", src):
        rhs = m.group("rhs").strip()
        assert rhs.startswith(("`", "render")) or rhs in ('""', "''"), f"{os.path.basename(path)}: innerHTML = {rhs}"



# ---------------------------------------------------------------- titles and words
def test_document_title_only_set_in_main():
    for f in JS_FILES:
        src = read(f)
        if f.endswith("main.js"):
            found = re.findall(r"document\.title\s*=\s*([^;\n]+)", src)
            assert found and all(v.strip() == "state.me.app_name" for v in found), found  # APP_NAME, from /api/me
        else:
            assert "document.title" not in src, f


@pytest.mark.parametrize("path", STATIC_TEXT_FILES, ids=lambda p: os.path.relpath(p, STATIC))
def test_no_advice_words_in_static(path):
    """The app records history and never advises, scores or projects: none of these words in anything shipped."""
    found = re.findall(r"should|recommend|risk|predict|expected next", read(path), re.I)
    assert not found, found


def test_static_has_the_files_task_10_names():
    for name in ("index.html", "style.css", "manifest.json", "icon.svg", "js/package.json", "js/main.js",
                 "js/core.js", "js/api.js", "js/home.js", "js/event.js", "js/timeline.js"):
        assert os.path.exists(os.path.join(STATIC, name)), name
    assert read_static("js/event.js").count('id: "event-suggestions"') == 1  # the seam Task 12 fills


# ---------------------------------------------------------------- Task 11: timeline, summary, settings
def test_static_has_the_files_task_11_names():
    for name in ("print.css", "js/summary.js", "js/settings.js"):
        assert os.path.exists(os.path.join(STATIC, name)), name


def test_the_page_links_the_print_stylesheet_and_the_screens():
    html = read(os.path.join(STATIC, "index.html"))
    assert '<link rel="stylesheet" href="/static/print.css" media="print">' in html
    nav = re.search(r'<nav id="nav".*?</nav>', html, re.S).group(0)
    main = read(os.path.join(STATIC, "js", "main.js"))
    routes = set(re.findall(r'^\s+"?(\w*)"?: \(\) => import', main, re.M))
    links = re.findall(r'href="#/(\w+)"', nav)
    assert {"summary", "settings"} <= set(links)
    assert set(links) <= routes, "a nav link with no route"


def test_print_css_hides_the_navigation_and_branding_and_fits_the_table():
    css = read(os.path.join(STATIC, "print.css"))
    assert re.search(r"\.top\b[^{]*\{\s*display:\s*none", css)
    assert re.search(r"\.no-print\b", css)
    assert "table-layout: fixed" in css and "@page" in css
    assert "no-print" in read_static("js/summary.js")  # the print button carries the class print.css hides


def test_the_summary_never_sets_a_title_or_a_name_other_than_the_app_name():
    src = read_static("js/summary.js")
    assert "document.title" not in src and 'el("title"' not in src
    assert "A record of history, not an interpretation." in src


def test_timeline_summary_and_settings_load_what_the_routes_give_them(client, history):
    for path in ("/api/timeline", "/api/summary", "/api/lists", "/api/doctors?all=1"):
        assert client.get(path, headers=auth()).status_code == 200, path


def test_the_row_keys_the_screens_read_exist_in_the_timeline_and_summary(client, history):
    """timeline.js and summary.js read `ev.<key>` and `h.<key>` / `data.<key>`; every key is one the API sends."""
    summary = client.get("/api/summary", headers=auth()).get_json()
    row_keys = set(summary["events"][0])
    headline_keys = set(summary["headline"])
    for name in ("timeline.js", "summary.js"):
        src = read_static(f"js/{name}")
        assert set(re.findall(r"\bev\.([a-z_]+)", src)) <= row_keys, name
        assert set(re.findall(r"\bh\.([a-z_]+)", src)) <= headline_keys, name
    assert set(re.findall(r"\bdata\.([a-z_]+)", read_static("js/summary.js"))) <= set(summary) | {"error"}
    assert set(re.findall(r"\bd\.([a-z_]+)", read_static("js/summary.js"))) <= {"name", "specialty"}


def test_the_summary_data_matches_what_the_screen_prints(client, history):
    """Five events, newest first with the gap to the older one; the open one has no resolved date."""
    data = client.get("/api/summary", headers=auth()).get_json()
    assert [e["id"] for e in data["events"]] == history[::-1]
    assert data["events"][0]["open"] and data["events"][-1]["gap_days_before"] is None
    assert data["headline"]["episodes"] == 5 and data["headline"]["hospital_nights"] == 3
    assert data["doctors"] == [{"name": "Dr. Rivera", "specialty": "urology"}]
    assert data["events"][3]["complications"] == ["Urinary tract infection"]


def test_settings_uses_only_the_list_and_doctor_routes_that_exist(client):
    src = read_static("js/settings.js")
    assert 'get("/api/lists")' in src and "/api/doctors?all=1" in src
    assert client.get("/api/doctors?all=1", headers=auth()).status_code == 200
    item = client.post("/api/lists/visit_kind", json={"label": "Telehealth"}, headers=mut()).get_json()["id"]
    assert client.patch(f"/api/lists/items/{item}", json={"counts_nights": True, "retired": True, "sort_order": 9},
                        headers=mut()).status_code == 200
    doc = client.post("/api/doctors", json={"name": "Dr. Chen"}, headers=mut()).get_json()["id"]
    assert client.patch(f"/api/doctors/{doc}", json={"retired": True}, headers=mut()).status_code == 200
    assert client.delete(f"/api/doctors/{doc}", json={}, headers=mut()).status_code == 200
