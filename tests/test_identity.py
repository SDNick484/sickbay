"""The sign-in check and the request guards: default deny, Cloudflare Access JWT plus the allow-list, the development
exception, the mutation checks, the security headers and what a log line may hold."""
import logging
import re

import jwt
import pytest

import app as sickbay
import identity
import settings
from conftest import LAN_IP, ORIGIN, OTHER_KEY, auth, mut, token


def _walk():
    """(path, method) for every rule in url_map with every method, path arguments filled with 1, plus paths that match
    no rule. Later tasks' routes are covered automatically."""
    out = []
    for rule in sickbay.app.url_map.iter_rules():
        path = re.sub(r"<[^>]+>", "1", rule.rule)
        out += [(path, m) for m in sorted(rule.methods - {"HEAD", "OPTIONS"})]
    out += [("/", "GET"), ("/nope", "GET"), ("/nope", "POST"), ("/api/nope", "GET"), ("/api/nope", "DELETE"),
            ("/static/style.css", "GET"), ("/healthz/", "GET")]
    return out


def test_healthz_is_open_and_bare(client):
    r = client.get("/healthz", environ_base={"REMOTE_ADDR": LAN_IP})
    assert r.status_code == 200 and r.get_data(as_text=True) == "ok"
    assert r.mimetype == "text/plain"


def test_every_rule_is_forbidden_without_identity(client):
    """Default deny: from the LAN, with the CSRF header and a same-origin Origin, every rule but /healthz is 403."""
    walked = _walk()
    for path, method in walked:
        r = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": LAN_IP},
                        headers={"X-Sickbay": "1", "Origin": ORIGIN})
        if path == "/healthz":
            assert r.status_code == 200
        else:
            assert (r.status_code, r.get_json()) == (403, {"error": "Forbidden"}), (method, path)
    assert ("/api/me", "GET") in walked and ("/api/_test/echo", "POST") in walked


def test_every_rule_is_forbidden_from_loopback_without_dev_email(client):
    """DEV_EMAIL is empty in tests: loopback alone is no identity either."""
    for path, method in _walk():
        if path != "/healthz":
            r = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
            assert r.status_code == 403, (method, path)


def test_forged_email_header_alone_is_forbidden(client):
    r = client.get("/api/me", headers={"Cf-Access-Authenticated-User-Email": "alex@example.com"})
    assert (r.status_code, r.get_json()) == (403, {"error": "Forbidden"})


def test_valid_jwt_for_allowed_email(client):
    r = client.get("/api/me", headers=auth("alex@example.com"))
    assert r.status_code == 200
    assert r.get_json() == {"email": "alex@example.com", "name": "Alex", "app_name": "Logbook"}


def test_me_names_the_other_user_and_lowercases_the_email(client):
    assert client.get("/api/me", headers=auth("Sam@Example.COM")).get_json() == \
        {"email": "sam@example.com", "name": "Sam", "app_name": "Logbook"}


def test_valid_jwt_for_other_email_is_forbidden(client):
    assert client.get("/api/me", headers=auth("jordan@example.com")).status_code == 403


@pytest.mark.parametrize("kw", [{"aud": "other"}, {"iss": "https://evil.example.com"}, {"exp": -10}, {"key": OTHER_KEY}])
def test_bad_tokens_are_forbidden(client, kw):
    assert client.get("/api/me", headers=auth("alex@example.com", **kw)).status_code == 403


def test_token_without_expiry_is_forbidden(client):
    h = {"Cf-Ray": "test", "Cf-Access-Jwt-Assertion": token("alex@example.com", exp=None)}
    assert client.get("/api/me", headers=h).status_code == 403


def test_unsigned_token_is_forbidden(client):
    t = jwt.encode({"email": "alex@example.com", "aud": "aud-test", "iss": "https://example.cloudflareaccess.com",
                    "exp": 2_000_000_000}, None, algorithm="none")
    assert client.get("/api/me", headers={"Cf-Ray": "test", "Cf-Access-Jwt-Assertion": t}).status_code == 403


def test_garbage_token_is_forbidden(client):
    assert client.get("/api/me", headers={"Cf-Ray": "test", "Cf-Access-Jwt-Assertion": "not.a.jwt"}).status_code == 403


def test_access_cookie_alone_is_not_identity(client):
    client.set_cookie("CF_Authorization", token("alex@example.com"))
    assert client.get("/api/me", headers={"Cf-Ray": "test"}).status_code == 403
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": LAN_IP}).status_code == 403


def test_unconfigured_cloudflare_admits_nobody(client, monkeypatch):
    monkeypatch.setattr(identity, "CF_AUD", "")
    assert client.get("/api/me", headers=auth()).status_code == 403


def test_dev_email_only_from_loopback_without_cf_headers(client, monkeypatch):
    monkeypatch.setattr(identity, "DEV_EMAIL", "alex@example.com")
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": "::1"}).status_code == 200
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": LAN_IP}).status_code == 403
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers={"Cf-Ray": "x"}).status_code == 403


@pytest.mark.parametrize("header", identity.CF_HEADERS)
def test_dev_email_is_off_when_any_cloudflare_header_is_present(client, monkeypatch, header):
    monkeypatch.setattr(identity, "DEV_EMAIL", "alex@example.com")
    assert client.get("/api/me", environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers={header: "x"}).status_code == 403


def test_resolve_user_directly():
    assert identity.resolve_user({}, "127.0.0.1") is None  # DEV_EMAIL is empty in tests
    assert identity.resolve_user({}, "") is None
    assert identity.resolve_user(auth("sam@example.com"), "203.0.113.5") == {"email": "sam@example.com", "name": "Sam"}


# ---------------------------------------------------------------- the mutation guard
def test_mutation_needs_header_origin_and_json(client):
    h = auth()
    echo = "/api/_test/echo"
    assert client.post(echo, json={"a": 1}, headers={**h, "Origin": ORIGIN}).status_code == 400            # no X-Sickbay
    assert client.post(echo, json={"a": 1}, headers={**h, "Origin": ORIGIN, "X-Sickbay": "yes"}).status_code == 400
    assert client.post(echo, data="a=1", headers={**h, "Origin": ORIGIN, "X-Sickbay": "1"}).status_code == 400  # not JSON
    assert client.post(echo, json={"a": 1}, headers={**h, "X-Sickbay": "1"}).status_code == 403               # no Origin
    r = client.post(echo, json={"a": 1}, headers={**h, "X-Sickbay": "1", "Origin": "https://evil.example.com"})
    assert (r.status_code, r.get_json()) == (403, {"error": "Cross-origin request blocked."})
    r = client.post(echo, json={"a": 1}, headers=mut())
    assert (r.status_code, r.get_json()) == (200, {"a": 1})


def test_origin_must_match_host_exactly(client):
    for origin in ("http://localhost.evil.example.com", "http://evil.example.com/localhost", "null", "http://localhost:8080"):
        h = {**mut(), "Origin": origin}
        assert client.post("/api/_test/echo", json={}, headers=h).status_code == 403, origin
    # Behind the tunnel the app sees http while the browser's Origin says https: only the host has to match.
    assert client.post("/api/_test/echo", json={}, headers={**mut(), "Origin": "https://localhost"}).status_code == 200


def test_every_unsafe_method_is_guarded(client):
    for method in ("PUT", "PATCH", "DELETE"):
        r = client.open("/api/nope", method=method, json={}, headers=auth())
        assert r.status_code == 400, method  # the CSRF check runs before routing decides it is a 404
        assert client.open("/api/nope", method=method, json={}, headers=mut()).status_code == 404


def test_json_body_must_be_an_object(client):
    for body in ('["a"]', '"a"', "1", "null", "{not json", ""):
        r = client.post("/api/_test/echo", data=body, headers=mut())
        assert (r.status_code, r.get_json()) == (400, {"error": "Expected a JSON object."}), body


def _form_headers():
    return {k: v for k, v in mut().items() if k != "Content-Type"}


def test_multipart_view_accepts_form_data_but_still_needs_header_and_origin(client):
    def post(headers):
        import io
        return client.post("/api/_test/upload", data={"file": (io.BytesIO(b"%PDF-1.4 x"), "a.pdf")}, headers=headers,
                           content_type="multipart/form-data")

    r = post(_form_headers())
    assert (r.status_code, r.get_json()) == (200, {"size": 10})
    assert post({k: v for k, v in _form_headers().items() if k != "X-Sickbay"}).status_code == 400
    assert post({**_form_headers(), "Origin": "https://evil.example.com"}).status_code == 403
    assert post({k: v for k, v in _form_headers().items() if k != "Origin"}).status_code == 403
    assert post({k: v for k, v in auth().items()}).status_code == 400
    # A multipart view takes multipart only: a JSON body there is a bad request.
    assert client.post("/api/_test/upload", json={"file": "x"}, headers=mut()).status_code == 400


def test_multipart_marker_does_not_exempt_other_views(client):
    import io
    r = client.post("/api/_test/echo", data={"file": (io.BytesIO(b"x"), "a.txt")}, headers=_form_headers(),
                    content_type="multipart/form-data")
    assert r.status_code == 400


def test_body_over_the_upload_limit_is_413(client, monkeypatch):
    import io
    monkeypatch.setitem(sickbay.app.config, "MAX_CONTENT_LENGTH", 1000)
    r = client.post("/api/_test/upload", data={"file": (io.BytesIO(b"x" * 5000), "a.pdf")}, headers=_form_headers(),
                    content_type="multipart/form-data")
    assert (r.status_code, r.get_json()) == (413, {"error": "That file is too large."})


def test_upload_limit_comes_from_settings():
    assert settings.MAX_UPLOAD_MB == 25
    assert sickbay.app.config["MAX_CONTENT_LENGTH"] == 25 * 1024 * 1024


# ---------------------------------------------------------------- headers, errors and logs
def test_security_headers(client):
    r = client.get("/api/me", headers=auth())
    assert r.headers["Cache-Control"] == "no-store" and r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "style-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


@pytest.mark.parametrize("path", ["/api/me", "/healthz", "/nope"])
def test_security_headers_on_every_response_including_refusals(client, path):
    r = client.get(path, environ_base={"REMOTE_ADDR": LAN_IP})
    assert r.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_unknown_api_path_is_json_404(client):
    r = client.get("/api/nope", headers=auth())
    assert (r.status_code, r.get_json()) == (404, {"error": "Not Found"})


def test_wrong_method_is_json_405(client):
    r = client.delete("/api/me", headers=mut())
    assert (r.status_code, r.get_json()) == (405, {"error": "Method Not Allowed"})


def test_server_error_is_json_and_logs_only_route_and_exception_class(client, monkeypatch, caplog):
    monkeypatch.setitem(sickbay.app.config, "TESTING", False)
    with caplog.at_level(logging.INFO):
        r = client.get("/api/_test/boom", headers=auth())
    assert (r.status_code, r.get_json()) == (500, {"error": "Internal Server Error"})
    text = "\n".join(rec.getMessage() for rec in caplog.records) + "".join(str(rec.exc_info) for rec in caplog.records)
    assert "ValueError" in text and "/api/_test/boom" in text
    assert "ZZ-SECRET-VALUE" not in text


def test_request_log_names_the_rule_not_the_path_or_query(client, caplog):
    with caplog.at_level(logging.INFO, logger="sickbay"):
        client.get("/api/nope/ZZ-TYPED-VALUE?q=ZZ-QUERY", headers=auth())
        client.get("/api/me?q=ZZ-QUERY", headers=auth())
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "GET /api/me 200" in text and "404" in text
    assert "ZZ-TYPED-VALUE" not in text and "ZZ-QUERY" not in text
    assert "alex@example.com" not in text
