"""Files: kind sniffing, storage by random id, de-duplication, deleted-document memory, links to events, the inbox,
and serving untrusted content from the app's own origin without letting it run there (Review Focus 3)."""
import io
import logging
import os
import re

import pytest

import app as sickbay
import db
import files
import settings
from conftest import ALEX, PDF_BYTES as PDF, auth, mk_file, mut, upload_headers

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
XML = b'<?xml version="1.0"?><ClinicalDocument xmlns="urn:hl7-org:v3"/>'
HTML = b"<!DOCTYPE html><html><body><script>fetch('/api/me')</script></body></html>"


def upload(client, data, name="test.pdf", event_id=None, content_type=None, headers=None):
    path = f"/api/events/{event_id}/files" if event_id else "/api/files"
    f = (io.BytesIO(data), name, content_type) if content_type else (io.BytesIO(data), name)
    return client.post(path, data={"file": f}, headers=headers or upload_headers(), content_type="multipart/form-data")


def blobs():
    return sorted(os.listdir(settings.FILES_DIR))


def keys(con):
    return {r["key"] for r in con.execute("SELECT key FROM deleted_documents")}


# ---------------------------------------------------------------- sniffing
@pytest.mark.parametrize("data, mime", [
    (PDF, "application/pdf"),
    (PNG, "image/png"),
    (JPEG, "image/jpeg"),
    (XML, "application/xml"),
    (b"\xef\xbb\xbf \r\n\t<?xml version='1.0'?><x/>", "application/xml"),
    (b"<ClinicalDocument xmlns='urn:hl7-org:v3'/>", "application/xml"),
    (b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg'/>", "application/xml"),
    (HTML, "text/html"),
    (b"<!doctype HTML><p>x", "text/html"),
    (b"\n  <HTML><body>x</body></HTML>", "text/html"),
    (b"\xef\xbb\xbf<html>", "text/html"),
])
def test_sniff_kinds(data, mime):
    assert files.sniff(data) == mime


@pytest.mark.parametrize("data", [b"GIF89a\x01\x00", b"", b"   ", b"plain text", b"<svg xmlns='http://www.w3.org/2000/svg'/>",
                                  b"PK\x03\x04zip", b"<script>alert(1)</script>", b" %PDF-1.4", b"<!-- c --><html>"])
def test_sniff_refuses_everything_else(data):
    assert files.sniff(data) is None


def test_unaccepted_kind_is_415(client):
    r = upload(client, b"GIF89a\x01\x00", "picture.gif")
    assert (r.status_code, r.get_json()) == (415, {"error": "That kind of file is not accepted."})
    assert blobs() == []


def test_svg_upload_is_refused(client):
    assert upload(client, b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>", "x.svg").status_code == 415


def test_empty_upload_and_missing_field_are_400(client):
    assert upload(client, b"", "empty.pdf").get_json() == {"error": "That file is empty."}
    r = client.post("/api/files", data={"other": "x"}, headers=upload_headers(), content_type="multipart/form-data")
    assert (r.status_code, r.get_json()) == (400, {"error": "Choose a file to upload."})


# ---------------------------------------------------------------- names
@pytest.mark.parametrize("raw, clean", [
    ("report.pdf", "report.pdf"),
    ("../../etc/passwd", "passwd"),
    ("C:\\fakepath\\scan.png", "scan.png"),
    ("IHE_XDM/SUBSET01/DOC0001.XML", "DOC0001.XML"),
    ("a\r\nb\x00c\x1b.pdf", "abc.pdf"),
    ("invoice\u202efdp.exe", "invoicefdp.exe"),  # a right-to-left override that would disguise the extension
    ("  spaced.pdf  ", "spaced.pdf"),
    ("", "file"), (None, "file"), ("..", "file"), ("dir/", "file"), ("\x00\x01", "file"),
    ("x" * 300 + ".pdf", "x" * 200),
])
def test_safe_name(raw, clean):
    assert files.safe_name(raw) == clean


# ---------------------------------------------------------------- serving (Review Focus 3)
def _content(client, file_id):
    return client.get(f"/api/files/{file_id}/content", headers=auth())


def _assert_locked_down(r):
    assert r.headers["Content-Security-Policy"] == "sandbox; default-src 'none'"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_content_headers_inline_pdf_sandboxed_no_store(client):
    fid = upload(client, PDF, "after-visit.pdf").get_json()["id"]
    r = _content(client, fid)
    assert r.status_code == 200 and r.data == PDF
    assert r.headers["Content-Type"] == "application/pdf"
    assert r.headers["Content-Disposition"] == "inline; filename=\"after-visit.pdf\"; filename*=UTF-8''after-visit.pdf"
    _assert_locked_down(r)


def test_name_saying_pdf_with_html_content_is_stored_as_html_and_served_as_attachment(client, con):
    r = upload(client, HTML, "results.pdf", content_type="application/pdf")  # the browser's claimed type is ignored
    assert r.status_code == 201 and r.get_json()["mime"] == "text/html"
    fid = r.get_json()["id"]
    assert con.execute("SELECT mime FROM files WHERE id = ?", (fid,)).fetchone()["mime"] == "text/html"
    r = _content(client, fid)
    assert r.headers["Content-Type"] == "text/html"
    assert r.headers["Content-Disposition"].startswith("attachment;")
    _assert_locked_down(r)


def test_xml_is_attachment(client):
    fid = upload(client, XML, "DOC0001.XML").get_json()["id"]
    r = _content(client, fid)
    assert r.headers["Content-Type"] == "application/xml"
    assert r.headers["Content-Disposition"].startswith("attachment;")
    _assert_locked_down(r)


@pytest.mark.parametrize("data, disposition", [(PDF, "inline"), (PNG, "inline"), (JPEG, "inline"), (XML, "attachment"),
                                               (HTML, "attachment"),
                                               (b"<?xml version='1.0'?><html xmlns='http://www.w3.org/1999/xhtml'>"
                                                b"<script>alert(1)</script></html>", "attachment"),
                                               (b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg'>"
                                                b"<script>alert(1)</script></svg>", "attachment")])
def test_every_kind_is_sandboxed_and_only_pdf_and_images_are_inline(client, data, disposition):
    """Whatever the kind, the content route never serves a page that can run on this origin: markup is a download,
    and every response carries the sandbox CSP and nosniff, so even a browser that showed it would show it inert."""
    fid = upload(client, data, "doc").get_json()["id"]
    r = _content(client, fid)
    assert r.headers["Content-Disposition"].split(";")[0] == disposition
    assert r.headers["Content-Type"] in {"application/pdf", "image/png", "image/jpeg"} or disposition == "attachment"
    _assert_locked_down(r)


def test_content_disposition_cannot_be_broken_by_the_name(client, con):
    # stored directly: the test client cannot put a quote in a multipart filename (browsers send it as %22)
    fid = files.store(con, ALEX, 'r\u00e9sum\u00e9 "q"; x=1 \u2713.pdf\r\nX-Injected: 1', PDF).id
    cd = _content(client, fid).headers["Content-Disposition"]
    assert cd == ("inline; filename=\"r_sum_ _q_; x=1 _.pdfX-Injected: 1\"; "
                  "filename*=UTF-8''r%C3%A9sum%C3%A9%20%22q%22%3B%20x%3D1%20%E2%9C%93.pdfX-Injected%3A%201")
    assert "\r" not in cd and "\n" not in cd and "X-Injected" not in _content(client, fid).headers


def test_file_routes_take_numeric_ids_only():
    """A file's name never appears in a URL: every file route's path arguments are integers."""
    rules = [r for r in sickbay.app.url_map.iter_rules() if "/files" in r.rule]
    assert len(rules) >= 6
    for rule in rules:
        assert all(conv == "int" for conv in re.findall(r"<(\w+):\w+>", rule.rule)), rule.rule
        assert re.findall(r"<\w+>", rule.rule) == [], rule.rule


def test_log_lines_never_name_a_file(client, con, mk_event, caplog):
    event_id = mk_event(con, "2024-03-01")
    with caplog.at_level(logging.DEBUG):
        fid = upload(client, PDF, "ZZ-SECRET-NAME.pdf", event_id=event_id).get_json()["id"]
        client.get(f"/api/files/{fid}", headers=auth())
        client.get(f"/api/events/{event_id}/files", headers=auth())
        _content(client, fid)
        client.delete(f"/api/events/{event_id}/files/{fid}", headers=mut())
        client.get("/api/files/inbox", headers=auth())
        client.delete(f"/api/files/{fid}", headers=mut())
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert f"file {fid}" in text
    assert "ZZ-SECRET-NAME" not in text and "synthetic test document" not in text


# ---------------------------------------------------------------- storage
def test_blob_path_is_random_and_inside_files_dir(client, con):
    r = upload(client, PDF, "../../etc/passwd")
    assert r.status_code == 201 and r.get_json()["name"] == "passwd"
    row = con.execute("SELECT storage_id, name, size, sha256 FROM files").fetchone()
    assert re.fullmatch(r"[0-9a-f]{32}", row["storage_id"])
    assert blobs() == [row["storage_id"]]
    with open(os.path.join(settings.FILES_DIR, row["storage_id"]), "rb") as f:
        assert f.read() == PDF
    assert row["size"] == len(PDF) and len(row["sha256"]) == 64
    assert all(n == "files" or n.startswith("sickbay.db") for n in os.listdir(settings.DATA_DIR))


def test_store_dedupes_by_sha(client, con):
    first = upload(client, PDF, "one.pdf")
    second = upload(client, PDF, "two.pdf", headers=upload_headers("sam@example.com"))
    assert first.status_code == 201 and first.get_json()["status"] == "new"
    assert second.status_code == 200 and second.get_json()["status"] == "same"
    assert second.get_json()["id"] == first.get_json()["id"] and second.get_json()["name"] == "one.pdf"
    assert len(blobs()) == 1


def test_store_dedupes_by_doc_id(con):
    a = files.store(con, ALEX, "DOC0001.XML", XML, origin="import", doc_id="1.2.3^a", set_id="1.2.3^s", version=1)
    b = files.store(con, ALEX, "DOC0001.XML", XML + b" ", origin="import", doc_id="1.2.3^a", set_id="1.2.3^s", version=1)
    assert (a.status, b.status, b.id) == ("new", "same", a.id)
    assert len(blobs()) == 1


def test_same_set_with_equal_or_lower_version_is_same(con):
    a = files.store(con, ALEX, "d.xml", XML, origin="import", doc_id="d2", set_id="s", version=2)
    for doc_id, version in (("d2b", 2), ("d1", 1), ("d0", None)):
        got = files.store(con, ALEX, "d.xml", XML + doc_id.encode(), origin="import", doc_id=doc_id, set_id="s", version=version)
        assert (got.status, got.id) == ("same", a.id)
    assert len(blobs()) == 1


def test_newer_version_of_set_updates_in_place_and_keeps_links(con, mk_event):
    event_id = mk_event(con, "2024-03-01")
    old = files.store(con, ALEX, "DOC0001.XML", XML, origin="import", doc_id="doc-v1", set_id="set-1", version=1)
    files.link(con, event_id, old.id)
    old_blob = blobs()
    newer = XML.replace(b"/>", b"><title>v2</title></ClinicalDocument>")
    got = files.store(con, ALEX, "DOC0009.XML", newer, origin="import", doc_id="doc-v2", set_id="set-1", version=2)
    assert (got.status, got.id) == ("updated", old.id)
    row = con.execute("SELECT * FROM files WHERE id = ?", (old.id,)).fetchone()
    assert (row["doc_id"], row["version"], row["size"], row["name"]) == ("doc-v2", 2, len(newer), "DOC0001.XML")
    assert blobs() == [row["storage_id"]] and blobs() != old_blob  # the old blob is gone, the new one in place
    with open(os.path.join(settings.FILES_DIR, row["storage_id"]), "rb") as f:
        assert f.read() == newer
    assert files.meta(con, old.id)["event_ids"] == [event_id]
    assert con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
    # the older content and id are now "same", not a new file
    assert files.store(con, ALEX, "x", XML, origin="import", doc_id="doc-v1", set_id="set-1", version=1).status == "same"


def test_deleted_file_is_not_stored_again_by_sha_doc_or_set(con):
    a = files.store(con, ALEX, "DOC0001.XML", XML, origin="import", doc_id="doc-1", set_id="set-1", version=1)
    files.delete(con, a.id)
    assert blobs() == [] and con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    sha = files.sha256(XML)
    assert keys(con) == {f"sha:{sha}", "doc:doc-1", "set:set-1"}
    same_bytes = files.store(con, ALEX, "a", XML, origin="import")
    same_doc = files.store(con, ALEX, "b", XML + b"1", origin="import", doc_id="doc-1")
    newer_in_set = files.store(con, ALEX, "c", XML + b"2", origin="import", doc_id="doc-2", set_id="set-1", version=5)
    assert [s.status for s in (same_bytes, same_doc, newer_in_set)] == ["deleted"] * 3
    assert all(s.id is None for s in (same_bytes, same_doc, newer_in_set))
    assert blobs() == [] and con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0


def test_upload_by_hand_brings_back_a_deleted_file(client, con):
    """The deleted-document memory stops an import from bringing a document back; a person uploading the same file
    by hand is choosing it, so the upload stores it and forgets its sha: key (its doc: and set: keys stay)."""
    a = files.store(con, ALEX, "DOC0001.XML", XML, origin="import", doc_id="doc-1", set_id="set-1", version=1)
    files.delete(con, a.id)
    r = upload(client, XML, "DOC0001.XML")
    assert r.status_code == 201 and r.get_json()["status"] == "new"
    assert keys(con) == {"doc:doc-1", "set:set-1"}
    assert files.store(con, ALEX, "x", XML, origin="import", doc_id="doc-1").status == "deleted"


def test_delete_of_a_file_on_an_event_is_409(client, con, mk_event):
    event_id = mk_event(con, "2024-03-01")
    fid = upload(client, PDF, event_id=event_id).get_json()["id"]
    r = client.delete(f"/api/files/{fid}", headers=mut())
    assert (r.status_code, r.get_json()) == (409, {"error": "This file is on an event. Take it off the event first."})
    assert len(blobs()) == 1


def test_delete_from_inbox_removes_row_and_blob(client, con):
    fid = upload(client, PDF).get_json()["id"]
    r = client.delete(f"/api/files/{fid}", headers=mut())
    assert (r.status_code, r.get_json()) == (200, {"ok": True})
    assert blobs() == [] and keys(con) == {f"sha:{files.sha256(PDF)}"}
    assert client.get(f"/api/files/{fid}", headers=auth()).status_code == 404


def test_store_records_origin_and_creator(con):
    got = files.store(con, {"email": "sam@example.com", "name": "Sam"}, "scan.png", PNG)
    row = con.execute("SELECT origin, created_by, mime FROM files WHERE id = ?", (got.id,)).fetchone()
    assert tuple(row) == ("upload", "sam@example.com", "image/png")


def test_store_refuses_unaccepted_kind_without_writing(con):
    with pytest.raises(files.NotAccepted) as e:
        files.store(con, ALEX, "INDEX.ZIP", b"PK\x03\x04", origin="import")
    assert (e.value.status, e.value.message) == (415, "That kind of file is not accepted.")
    assert blobs() == []


# ---------------------------------------------------------------- atomic: blobs follow the transaction
def test_atomic_rollback_removes_new_blobs(con):
    with pytest.raises(RuntimeError, match="boom"):
        with files.atomic(con):
            files.store(con, ALEX, "a.pdf", PDF)
            files.store(con, ALEX, "b.png", PNG)
            assert len(blobs()) == 2
            raise RuntimeError("boom")
    assert blobs() == [] and con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0


def test_atomic_rollback_of_an_update_keeps_the_old_blob(con):
    old = files.store(con, ALEX, "d.xml", XML, origin="import", doc_id="v1", set_id="s", version=1)
    before = blobs()
    with pytest.raises(RuntimeError):
        with files.atomic(con):
            assert files.store(con, ALEX, "d.xml", XML + b"v2", origin="import", doc_id="v2", set_id="s", version=2).status == "updated"
            raise RuntimeError("boom")
    assert blobs() == before
    assert con.execute("SELECT doc_id FROM files WHERE id = ?", (old.id,)).fetchone()["doc_id"] == "v1"


def test_atomic_rollback_of_a_delete_keeps_the_blob(con):
    a_id = mk_file(con)
    with pytest.raises(RuntimeError):
        with files.atomic(con):
            files.delete(con, a_id)
            raise RuntimeError("boom")
    assert len(blobs()) == 1 and files.meta(con, a_id)["id"] == a_id and keys(con) == set()


def test_a_failed_store_caught_inside_atomic_leaves_no_orphan_blob(con, monkeypatch):
    """The importer stores one document at a time inside one files.atomic and counts a failure instead of stopping:
    a store that fails after writing its blob removes that blob itself, so the commit does not keep an orphan."""
    def broken_now():
        raise OSError("clock")
    with files.atomic(con):
        kept = files.store(con, ALEX, "a.pdf", PDF)
        monkeypatch.setattr(files.common, "now", broken_now)  # fails after the blob is written, before the INSERT
        with pytest.raises(OSError):
            files.store(con, ALEX, "b.png", PNG)
        monkeypatch.undo()
    assert [r["id"] for r in con.execute("SELECT id FROM files")] == [kept.id]
    assert len(blobs()) == 1


def test_atomic_is_reentrant_and_store_refuses_a_bare_transaction(con):
    with files.atomic(con):
        with files.atomic(con):
            files.store(con, ALEX, "a.pdf", PDF)
    assert len(blobs()) == 1
    with pytest.raises(RuntimeError, match="files.atomic"):
        with db.transaction(con):
            files.store(con, ALEX, "b.png", PNG)
    assert len(blobs()) == 1


# ---------------------------------------------------------------- links and the inbox
def test_upload_to_event_links_it(client, con, mk_event):
    event_id = mk_event(con, "2024-03-01")
    r = upload(client, PDF, "summary.pdf", event_id=event_id)
    assert r.status_code == 201
    body = r.get_json()
    assert {k: body[k] for k in ("name", "mime", "size", "origin", "event_ids", "status")} == \
        {"name": "summary.pdf", "mime": "application/pdf", "size": len(PDF), "origin": "upload", "event_ids": [event_id],
         "status": "new"}
    listed = client.get(f"/api/events/{event_id}/files", headers=auth()).get_json()
    assert [f["id"] for f in listed] == [body["id"]]
    assert client.get("/api/files/inbox", headers=auth()).get_json() == []


def test_one_file_links_to_two_events(client, con, mk_event):
    e1, e2 = mk_event(con, "2023-01-05"), mk_event(con, "2024-03-01")
    fid = upload(client, PDF, event_id=e1).get_json()["id"]
    r = client.put(f"/api/events/{e2}/files/{fid}", headers=mut())
    assert r.status_code == 200 and r.get_json()["event_ids"] == [e1, e2]
    assert client.put(f"/api/events/{e2}/files/{fid}", headers=mut()).status_code == 200  # linking twice is fine
    for e in (e1, e2):
        assert [f["id"] for f in client.get(f"/api/events/{e}/files", headers=auth()).get_json()] == [fid]
    # uploading the same bytes to the first event again links nothing new and stores nothing new
    again = upload(client, PDF, "copy.pdf", event_id=e1)
    assert again.get_json()["status"] == "same" and again.get_json()["event_ids"] == [e1, e2] and len(blobs()) == 1


def test_unlinked_file_returns_to_inbox(client, con, mk_event):
    e1, e2 = mk_event(con, "2023-01-05"), mk_event(con, "2024-03-01")
    fid = upload(client, PDF, event_id=e1).get_json()["id"]
    client.put(f"/api/events/{e2}/files/{fid}", headers=mut())
    assert client.delete(f"/api/events/{e1}/files/{fid}", headers=mut()).get_json()["event_ids"] == [e2]
    assert client.get("/api/files/inbox", headers=auth()).get_json() == []
    assert client.delete(f"/api/events/{e2}/files/{fid}", headers=mut()).get_json()["event_ids"] == []
    inbox = client.get("/api/files/inbox", headers=auth()).get_json()
    assert [f["id"] for f in inbox] == [fid] and len(blobs()) == 1


def test_deleting_an_event_sends_its_files_to_the_inbox(client, con, mk_event):
    event_id = mk_event(con, "2024-03-01")
    fid = upload(client, PDF, event_id=event_id).get_json()["id"]
    con.execute("DELETE FROM events WHERE id = ?", (event_id,))  # what events.delete does (Task 3)
    assert [f["id"] for f in client.get("/api/files/inbox", headers=auth()).get_json()] == [fid]


def test_inbox_is_newest_first(client, con, clock):
    a = upload(client, PDF, "a.pdf").get_json()["id"]
    clock.advance(60)
    b = upload(client, PNG, "b.png").get_json()["id"]
    c = upload(client, JPEG, "c.jpg").get_json()["id"]  # same second as b: the later id first
    assert [f["id"] for f in client.get("/api/files/inbox", headers=auth()).get_json()] == [c, b, a]


def test_meta_shape(client, con, clock):
    fid = upload(client, PNG, "scan.png").get_json()["id"]
    assert client.get(f"/api/files/{fid}", headers=auth()).get_json() == {
        "id": fid, "name": "scan.png", "mime": "image/png", "size": len(PNG), "origin": "upload",
        "created_at": clock.t, "event_ids": []}


# ---------------------------------------------------------------- refusals
def test_upload_over_limit_is_413(client, monkeypatch):
    monkeypatch.setitem(sickbay.app.config, "MAX_CONTENT_LENGTH", 1024)
    r = upload(client, PDF + b"\x00" * 4096)
    assert (r.status_code, r.get_json()) == (413, {"error": "That file is too large."})
    assert blobs() == []


def test_upload_without_x_sickbay_is_400(client):
    h = {k: v for k, v in upload_headers().items() if k != "X-Sickbay"}
    assert upload(client, PDF, headers=h).status_code == 400
    assert blobs() == []


def test_upload_from_another_origin_is_403(client):
    assert upload(client, PDF, headers={**upload_headers(), "Origin": "https://evil.example.com"}).status_code == 403


def test_upload_as_json_is_400(client):
    """The upload routes take multipart/form-data, and the link routes JSON; neither takes the other."""
    assert client.post("/api/files", json={"file": "x"}, headers=mut()).status_code == 400


def test_missing_file_is_404(client, con, mk_event):
    event_id = mk_event(con, "2024-03-01")
    for method, path in (("GET", "/api/files/99"), ("GET", "/api/files/99/content"), ("DELETE", "/api/files/99"),
                         ("PUT", f"/api/events/{event_id}/files/99"), ("DELETE", f"/api/events/{event_id}/files/99")):
        r = client.open(path, method=method, headers=mut() if method != "GET" else auth())
        assert (r.status_code, r.get_json()) == (404, {"error": "No such file."}), (method, path)


def test_missing_event_is_404(client, con):
    fid = upload(client, PDF).get_json()["id"]
    for method, path in (("GET", "/api/events/99/files"), ("PUT", f"/api/events/99/files/{fid}"),
                         ("DELETE", f"/api/events/99/files/{fid}")):
        r = client.open(path, method=method, headers=mut() if method != "GET" else auth())
        assert (r.status_code, r.get_json()) == (404, {"error": "No such event."}), (method, path)
    r = upload(client, PNG, event_id=99)
    assert (r.status_code, r.get_json()) == (404, {"error": "No such event."})
    assert len(blobs()) == 1  # the refused upload wrote nothing


def test_missing_blob_is_404_not_500(client, con):
    fid = upload(client, PDF).get_json()["id"]
    os.remove(os.path.join(settings.FILES_DIR, blobs()[0]))
    assert _content(client, fid).status_code == 404


def test_file_routes_need_identity(client):
    fid = upload(client, PDF).get_json()["id"]
    for path in (f"/api/files/{fid}", f"/api/files/{fid}/content", "/api/files/inbox"):
        assert client.get(path).status_code == 403
