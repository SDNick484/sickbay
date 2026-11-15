"""Task 8: importing a portal download into the inbox, and the suggestions it makes (Review Focus 1, 2 and 5).

An import keeps documents and makes suggestions; nothing reaches an event until a person accepts a suggestion. Every
document and archive is synthetic and built in memory (tests/ccda_build.py)."""
import io
import json
import os
import re

import pytest

import app as sickbay
import ccda
import codes
import files
import imports
import settings
from ccda_build import EXAMPLE_ROOT, make_ccda, make_zip
from conftest import ALEX, PDF_BYTES as PDF, auth, mk_doctor, mk_event, mut, post_import, upload_headers

STONE = ("icd10", "N20.1")        # calculus of ureter
OBSTRUCTION = ("icd10", "N13.2")  # a stone diagnosis that is also an obstruction
UTI = ("icd10", "N39.0")
FLU = ("icd10", "J11.1")          # not stone-related; not in codes.py
URS = ("cpt", "52353")            # ureteroscopy with lithotripsy
URS_STENT = ("cpt", "52356")      # ureteroscopy and stent: two procedures
CT = ("cpt", "74176")             # an imaging code the tables do not know
RIVERA = ("Robin", "Rivera")
CHEN = ("Casey", "Chen")
INDEX = b"<html><body><p>Synthetic index</p></body></html>"
METADATA = b'<?xml version="1.0"?><SubmitObjectsRequest xmlns="urn:oasis:names:tc:ebxml-regrep:xsd:lcm:3.0"/>'
SET = f"{EXAMPLE_ROOT}^set"


def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def hl7(day):
    """2024-03-01 -> 20240301; a pair -> a (low, high) pair."""
    if isinstance(day, tuple):
        return tuple(hl7(d) for d in day)
    return day.replace("-", "") if day else day


def enc(day, *, n=None, cls="EMER", dx=(STONE,), performers=(RIVERA,)):
    return {"id": f"{EXAMPLE_ROOT}^enc{n}" if n else None, "cls": cls, "time": hl7(day), "diagnoses": list(dx),
            "performers": list(performers)}


def proc(day, code=URS, *, n=None, performers=()):
    return {"id": f"{EXAMPLE_ROOT}^proc{n}" if n else None, "code": code, "time": hl7(day), "performers": list(performers)}


def prob(day, value, *, n=None):
    return {"id": f"{EXAMPLE_ROOT}^prob{n}" if n else None, "value": value, "time": hl7(day)}


def doc(n, **kw):
    return make_ccda(doc_id=uid(n), **kw)


def xdm(*docs, pdf=PDF):
    members = {"INDEX.HTM": INDEX, "IHE_XDM/SUBSET01/METADATA.XML": METADATA}
    members.update({f"IHE_XDM/SUBSET01/DOC{i:04d}.XML": d for i, d in enumerate(docs, 1)})
    if pdf:
        members["IHE_XDM/SUBSET01/SUMMARY.PDF"] = pdf
    return make_zip(members)


def run(con, data, name="download.zip", user=ALEX):
    return imports.run(con, user, name, data)


def all_suggestions(con, state=None):
    sql = "SELECT * FROM suggestions" + (" WHERE state = ?" if state else "") + " ORDER BY id"
    return con.execute(sql, (state,) if state else ()).fetchall()


def kinds(con, state="pending"):
    out = []
    for r in all_suggestions(con, state):
        p = json.loads(r["payload"])
        out.append((r["kind"], r["on_date"], p.get("slug") or p.get("name")))
    return sorted(out, key=lambda k: (k[0], k[1] or "", k[2]))


def blobs():
    return sorted(os.listdir(settings.FILES_DIR))


def count(con, table):
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def accept(client, sid, **body):
    return client.post(f"/api/suggestions/{sid}/accept", json=body, headers=mut())


def by_kind(con, kind, state="pending"):
    return [r for r in all_suggestions(con, state) if r["kind"] == kind]


# ---------------------------------------------------------------- importing documents
def test_import_zip_counts_and_inbox(client, con):
    data = xdm(doc(1, encounters=[enc("2024-03-01", n=1)]), doc(2, procedures=[proc("2024-03-02", n=2)]))
    r = post_import(client, data)
    assert r.status_code == 201
    assert r.get_json() == {"documents": 3, "new": 3, "updated": 0, "same": 0, "skipped_deleted": 0, "failed": 0,
                            "suggestions": 3}
    # The two C-CDA documents and the PDF are in the inbox; the XDM manifests are packaging, not documents.
    inbox = client.get("/api/files/inbox", headers=auth()).get_json()
    assert sorted(f["name"] for f in inbox) == ["DOC0001.XML", "DOC0002.XML", "SUMMARY.PDF"]
    assert {f["origin"] for f in inbox} == {"import"}
    row = con.execute("SELECT * FROM imports").fetchone()
    assert (row["created_by"], row["documents"], row["new"], row["suggestions"]) == ("alex@example.com", 3, 3, 3)
    assert kinds(con) == [("doctor", "2024-03-01", "Robin Rivera"), ("procedure", "2024-03-02", "ureteroscopy"),
                          ("visit", "2024-03-01", "er")]


def test_import_changes_no_event_until_accepted(client, con):
    eid = mk_event(con, "2024-03-01")
    before = client.get(f"/api/events/{eid}", headers=auth()).get_json()
    post_import(client, xdm(doc(1, encounters=[enc("2024-03-01", n=1)], procedures=[proc("2024-03-02", n=2)],
                                problems=[prob("2024-03-01", UTI, n=3)])))
    assert count(con, "suggestions") == 4
    assert client.get(f"/api/events/{eid}", headers=auth()).get_json() == before
    assert count(con, "event_files") == 0 and count(con, "doctors") == 0


def test_single_xml_and_single_pdf(client, con):
    r = post_import(client, doc(1, encounters=[enc("2024-03-01", n=1)]), name="DOC0001.XML")
    assert r.get_json()["new"] == 1 and r.get_json()["suggestions"] == 2
    r = post_import(client, PDF, name="summary.pdf")
    assert r.get_json() == {"documents": 1, "new": 1, "updated": 0, "same": 0, "skipped_deleted": 0, "failed": 0,
                            "suggestions": 0}


def test_reimport_same_zip_adds_nothing(client, con):
    data = xdm(doc(1, encounters=[enc("2024-03-01")]), doc(2, procedures=[proc("2024-03-02")]))
    post_import(client, data)
    held, made = blobs(), count(con, "suggestions")
    r = post_import(client, data)
    assert r.get_json() == {"documents": 3, "new": 0, "updated": 0, "same": 3, "skipped_deleted": 0, "failed": 0,
                            "suggestions": 0}
    assert blobs() == held and count(con, "files") == 3 and count(con, "suggestions") == made
    assert count(con, "imports") == 2


def record(first, effective, *, version=1, end=None):
    """The same synthetic record as a portal would rebuild it: one document per visit, each with a setId, its entries
    with and without ids. `first` numbers the document ids, `effective` the documents' timestamps."""
    visit1 = make_ccda(doc_id=uid(first), set_id=f"{SET}1", version=version, effective=effective,
                       encounters=[enc(("2024-03-01", end) if end else "2024-03-01", n=1, cls="IMP")],
                       procedures=[proc("2024-03-02", URS_STENT, performers=[CHEN])],
                       problems=[prob("2024-03-01", UTI)])
    visit2 = make_ccda(doc_id=uid(first + 1), set_id=f"{SET}2", version=1, effective=effective,
                       encounters=[enc("2024-04-10", cls="AMB", performers=[CHEN])],
                       procedures=[proc("2024-04-10", ("cpt", "52310"), n=7)])
    return xdm(visit1, visit2)


def test_redownload_with_new_doc_ids_same_set_adds_nothing(client, con):
    """Review Focus 2: the portal rebuilds the record a month later with new document ids and timestamps, but the
    same setIds: no duplicate files and no duplicate suggestions."""
    first = post_import(client, record(1, "20240501120000-0500")).get_json()
    assert first["new"] == 3 and first["suggestions"] > 0
    held, made = blobs(), kinds(con)
    again = post_import(client, record(101, "20240601093000-0500")).get_json()
    assert again == {"documents": 3, "new": 0, "updated": 0, "same": 3, "skipped_deleted": 0, "failed": 0,
                     "suggestions": 0}
    assert blobs() == held and count(con, "files") == 3
    assert kinds(con) == made


def test_newer_version_replaces_the_file_and_refreshes_pending_suggestions(client, con):
    post_import(client, record(1, "20240501", version=1))
    visit = by_kind(con, "visit")[0]
    assert visit["end_date"] is None
    r = post_import(client, record(201, "20240601", version=2, end="2024-03-04")).get_json()
    assert (r["updated"], r["same"], r["new"], r["suggestions"]) == (1, 2, 0, 0)
    assert count(con, "files") == 3
    assert con.execute("SELECT end_date FROM suggestions WHERE id = ?", (visit["id"],)).fetchone()[0] == "2024-03-04"


def test_dismissed_stays_dismissed_after_reimport(client, con):
    post_import(client, record(1, "20240501"))
    target = by_kind(con, "visit")[0]
    assert client.post(f"/api/suggestions/{target['id']}/dismiss", headers=mut()).status_code == 200
    pending = len(all_suggestions(con, "pending"))
    for first in (1, 301):  # the same download, then a rebuilt one
        assert post_import(client, record(first, "20240701")).get_json()["suggestions"] == 0
    assert con.execute("SELECT state FROM suggestions WHERE id = ?", (target["id"],)).fetchone()[0] == "dismissed"
    assert len(all_suggestions(con, "pending")) == pending
    assert len(by_kind(con, "visit", "pending")) == 1 and len(by_kind(con, "visit", None)) == 2


def test_same_fact_from_two_documents_is_suggested_once(client, con):
    """A summary and a visit document both describe the ER visit, with no shared ids: one suggestion each."""
    summary = doc(1, encounters=[enc("2024-03-01")], procedures=[proc("2024-03-02")])
    visit = doc(2, encounters=[enc("2024-03-01")], procedures=[proc("2024-03-02")])
    assert post_import(client, xdm(summary, visit, pdf=None)).get_json()["suggestions"] == 3
    assert len(kinds(con)) == 3


def test_deleted_document_not_reimported(client, con):
    data = xdm(doc(1, set_id=f"{SET}9", encounters=[enc("2024-03-01")]))
    post_import(client, data)
    xml = con.execute("SELECT id FROM files WHERE name = 'DOC0001.XML'").fetchone()["id"]
    assert client.delete(f"/api/files/{xml}", headers=mut()).status_code == 200
    r = post_import(client, data).get_json()
    assert (r["skipped_deleted"], r["same"], r["new"], r["suggestions"]) == (1, 1, 0, 0)
    # A rebuilt copy (new id, same set) stays deleted too.
    r = post_import(client, xdm(doc(77, set_id=f"{SET}9", effective="20240901", encounters=[enc("2024-03-01")]))).get_json()
    assert r["skipped_deleted"] == 1
    assert con.execute("SELECT COUNT(*) FROM files WHERE name = 'DOC0001.XML'").fetchone()[0] == 0


# ---------------------------------------------------------------- which entries make suggestions
def test_unrelated_history_makes_no_suggestions(client, con):
    summary = doc(1, encounters=[enc("2022-01-10", n=1, dx=(FLU,), performers=[CHEN]),
                                 enc("2024-03-01", n=2, dx=(STONE,))],
                  procedures=[proc("2022-01-10", CT, n=3)],
                  problems=[prob("2022-01-10", UTI, n=4), prob("2021-06-01", FLU, n=5)])
    r = post_import(client, summary, name="summary.xml").get_json()
    assert kinds(con) == [("doctor", "2024-03-01", "Robin Rivera"), ("visit", "2024-03-01", "er")]
    assert r["suggestions"] == 2


def test_complication_inside_a_stone_encounter_is_suggested(client, con):
    """A UTI diagnosed within the dates of a stone encounter is suggested, from the problems section and from the
    encounter's own diagnoses; one outside any stone encounter or event is not."""
    summary = doc(1, encounters=[enc(("2024-03-01", "2024-03-03"), n=1, cls="IMP", dx=(STONE, ("icd10", "A41.9")))],
                  problems=[prob("2024-03-02", UTI, n=2), prob("2023-01-01", UTI, n=3)])
    post_import(client, summary, name="summary.xml")
    assert kinds(con) == [("complication", "2024-03-01", "sepsis"), ("complication", "2024-03-02", "uti"),
                          ("doctor", "2024-03-01", "Robin Rivera"), ("visit", "2024-03-01", "admission")]
    assert by_kind(con, "visit")[0]["end_date"] == "2024-03-03"


def test_stone_diagnosis_dated_in_an_encounter_makes_it_stone_related(client, con):
    """A visit document whose encounter lists no diagnoses, with the stone diagnosis in its problems section."""
    visit = doc(1, encounters=[enc("2024-03-01", n=1, dx=())], problems=[prob("2024-03-01", STONE, n=2)])
    post_import(client, visit, name="visit.xml")
    assert ("visit", "2024-03-01", "er") in kinds(con)


def test_obstruction_is_both_stone_and_complication(client, con):
    post_import(client, doc(1, problems=[prob("2024-03-01", OBSTRUCTION, n=1)]), name="x.xml")
    assert kinds(con) == [("complication", "2024-03-01", "obstruction")]


def test_procedure_mapping_to_two_kinds_makes_two_suggestions(client, con):
    post_import(client, doc(1, procedures=[proc("2024-03-02", URS_STENT, n=1, performers=[CHEN])]), name="x.xml")
    assert kinds(con) == [("doctor", "2024-03-02", "Casey Chen"), ("procedure", "2024-03-02", "stent_placement"),
                          ("procedure", "2024-03-02", "ureteroscopy")]


def test_entry_in_an_event_window_is_suggested_even_without_a_stone_code(client, con):
    eid = mk_event(con, "2024-03-01", "2024-03-05")
    post_import(client, doc(1, encounters=[enc("2024-03-20", n=1, cls="AMB", dx=(FLU,), performers=[])],
                            problems=[prob("2024-03-21", UTI, n=2)]), name="x.xml")
    assert kinds(con) == [("complication", "2024-03-21", "uti"), ("visit", "2024-03-20", "office")]
    assert {s["id"] for s in imports.pending(con, "2024-06-01")["by_event"][eid]} == {r["id"] for r in all_suggestions(con)}


def test_unknown_encounter_class_makes_no_visit(client, con):
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1, cls="HH")]), name="x.xml")
    assert kinds(con) == [("doctor", "2024-03-01", "Robin Rivera")]


def test_no_size_suggestions(client, con):
    post_import(client, record(1, "20240501"))
    assert {r["kind"] for r in all_suggestions(con)} <= {"visit", "procedure", "complication", "stone_type", "doctor"}
    for r in all_suggestions(con):
        assert not re.search(r"size|mm", r["payload"])
    assert count(con, "stones") == 0


STONE_PANEL = ("loinc", "99999-1")       # made-up codes for the test only: the real tables are empty until Task 13
OXALATE = ("snomed", "000000001")


@pytest.fixture
def stone_analysis(monkeypatch):
    monkeypatch.setitem(codes.STONE_ANALYSIS, STONE_PANEL, "Synthetic stone analysis")
    monkeypatch.setitem(codes.STONE_TYPE_VALUES, OXALATE, "calcium_oxalate")


def analysis(day, n):
    return doc(n, results=[{"id": f"{EXAMPLE_ROOT}^res{n}", "code": STONE_PANEL, "value": OXALATE, "time": hl7(day)}])


def test_stone_analysis_sets_the_only_untyped_stone(client, con, stone_analysis, add_detail):
    eid = mk_event(con, "2024-03-01")
    add_detail(client, eid, "stones", size_mm="7", side="left")
    post_import(client, analysis("2024-03-20", 1), name="lab.xml")
    (s,) = by_kind(con, "stone_type")
    event = accept(client, s["id"]).get_json()["event"]
    (stone,) = event["stones"]
    assert (stone["size_mm"], stone["type"]["label"], stone["type_from_lab"]) == ("7", "Calcium oxalate", True)


def test_stone_analysis_adds_a_stone_when_not_exactly_one_is_untyped(client, con, stone_analysis, add_detail):
    eid = mk_event(con, "2024-03-01")
    add_detail(client, eid, "stones", size_mm="7")
    add_detail(client, eid, "stones", size_mm="3")
    post_import(client, analysis("2024-03-20", 1), name="lab.xml")
    stones = accept(client, by_kind(con, "stone_type")[0]["id"]).get_json()["event"]["stones"]
    assert [(s["size_mm"], s["type"] and s["type"]["label"], s["from_import"]) for s in stones] == [
        ("7", None, False), ("3", None, False), (None, "Calcium oxalate", True)]


# ---------------------------------------------------------------- hostile and broken downloads (Review Focus 1)
def test_unsafe_document_counted_failed_others_imported(client, con):
    unsafe = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]><ClinicalDocument xmlns="urn:hl7-org:v3">&e;</ClinicalDocument>'
    good = doc(2, encounters=[enc("2024-03-01")])
    truncated = good[:len(good) // 2]
    nested = make_zip({"inner.xml": good})
    data = make_zip({"A/DOC0001.XML": unsafe, "A/DOC0002.XML": good, "A/DOC0003.XML": truncated, "A/INNER.ZIP": nested})
    r = post_import(client, data)
    assert r.status_code == 201
    assert r.get_json() == {"documents": 4, "new": 1, "updated": 0, "same": 0, "skipped_deleted": 0, "failed": 3,
                            "suggestions": 2}
    # The truncated document is kept (it may still be worth reading); the unsafe one and the nested ZIP are not.
    assert sorted(f["name"] for f in files.inbox(con)) == ["DOC0002.XML", "DOC0003.XML"]


@pytest.mark.parametrize("make", [
    lambda: b"not a download at all",
    lambda: b"PK\x03\x04" + b"\x00" * 40,
    lambda: make_zip({"IHE_XDM/": b""}),
    lambda: make_zip({"METADATA.XML": METADATA, "INDEX.HTM": INDEX}),
    lambda: make_zip({f"m{i}.xml": b"<x/>" for i in range(ccda.MAX_MEMBERS + 1)}, compression=0),
])
def test_refused_zip_is_400_and_writes_nothing(client, con, make):
    r = post_import(client, make())
    assert r.status_code == 400 and r.get_json()["error"]
    assert blobs() == [] and count(con, "files") == 0 and count(con, "imports") == 0 and count(con, "suggestions") == 0


def test_zip_bomb_is_400_and_writes_nothing(client, con, monkeypatch):
    monkeypatch.setattr(ccda, "MAX_MEMBER_BYTES", 10_000)
    data = make_zip({"A/DOC0001.XML": doc(1, encounters=[enc("2024-03-01")]), "A/BOMB.XML": b"\0" * 200_000})
    r = post_import(client, data)
    assert (r.status_code, r.get_json()) == (400, {"error": ccda.MEMBER_TOO_LARGE})
    assert blobs() == [] and count(con, "imports") == 0


def test_member_paths_never_leave_files_dir(client, con, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = make_zip({"../../x.xml": doc(1, encounters=[enc("2024-03-01")]), "/abs/y.pdf": PDF,
                     "C:\\evil\\z.xml": doc(2)})
    r = post_import(client, data)
    assert r.get_json()["new"] == 3
    assert os.listdir(tmp_path) == []
    assert all(re.fullmatch(r"[0-9a-f]{32}", b) for b in blobs()) and len(blobs()) == 3
    assert sorted(f["name"] for f in files.inbox(con)) == ["x.xml", "y.pdf", "z.xml"]
    parent = os.path.dirname(settings.FILES_DIR)
    assert not {"x.xml", "y.pdf", "z.xml"} & set(os.listdir(parent))


def test_import_is_never_a_500(client, monkeypatch):
    monkeypatch.setitem(sickbay.app.config, "TESTING", False)
    good = doc(1, encounters=[enc("2024-03-01")])
    for data in (good[:-40], b"\xef\xbb\xbf" + good, good.replace(b"20240301", b"20241399"), b"<?xml version='1.0'?><x/>",
                 make_zip({"a.xml": b"<?xml version='1.0'?><ClinicalDocument xmlns='urn:hl7-org:v3'>"})):
        assert post_import(client, data, name="x.xml").status_code in (201, 400)


def test_a_failure_mid_run_rolls_back_rows_and_blobs(con, monkeypatch):
    real, calls = files.store, []

    def store(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        return real(*a, **kw)

    monkeypatch.setattr(files, "store", store)
    with pytest.raises(RuntimeError):
        run(con, xdm(doc(1, encounters=[enc("2024-03-01")]), doc(2)))
    assert blobs() == [] and count(con, "files") == 0 and count(con, "imports") == 0 and count(con, "suggestions") == 0


def test_import_needs_a_file_and_the_csrf_checks(client):
    r = client.post("/api/imports", data={}, headers=upload_headers(), content_type="multipart/form-data")
    assert (r.status_code, r.get_json()) == (400, {"error": "Choose a file to upload."})
    h = {k: v for k, v in upload_headers().items() if k != "X-Sickbay"}
    r = client.post("/api/imports", data={"file": (io.BytesIO(PDF), "x.pdf")}, headers=h, content_type="multipart/form-data")
    assert r.status_code == 400


# ---------------------------------------------------------------- keys
def test_suggestion_key_uses_the_entry_id_else_a_hash_of_the_document_identity():
    parsed = ccda.Parsed(doc_id="d", set_id="s")
    with_id = ccda.Entry(kind="procedure", entry_id="1.2^7", system="cpt", code="52353", start="2024-03-02")
    assert imports.suggestion_key(parsed, with_id, "procedure:ureteroscopy") == "e:1.2^7|procedure:ureteroscopy"
    bare = ccda.Entry(kind="procedure", system="cpt", code="52353", start="2024-03-02")
    key = imports.suggestion_key(parsed, bare, "procedure:ureteroscopy")
    assert key.startswith("h:") and key == imports.suggestion_key(ccda.Parsed(doc_id="other", set_id="s"), bare,
                                                                  "procedure:ureteroscopy")
    assert key != imports.suggestion_key(ccda.Parsed(doc_id="d"), bare, "procedure:ureteroscopy")
    assert imports.suggestion_key(ccda.Parsed(), bare, "x", sha="aa") != imports.suggestion_key(ccda.Parsed(), bare, "x", sha="bb")


def test_an_entry_id_shared_by_different_entries_is_not_trusted(client, con):
    """Some systems repeat one id on many entries; two different procedures with the same id are both suggested."""
    same = f"{EXAMPLE_ROOT}^dup"
    d = doc(1, procedures=[{"id": same, "code": URS, "time": "20240302"}, {"id": same, "code": ("cpt", "50590"), "time": "20240310"}])
    post_import(client, d, name="x.xml")
    assert [k[2] for k in kinds(con)] == ["ureteroscopy", "swl"]


# ---------------------------------------------------------------- targets and proposed episodes
def windows_of(con):
    return imports.windows(con, "2025-01-01")


def test_target_window_bounds(con):
    eid = mk_event(con, "2024-03-10", "2024-03-20")
    ws = windows_of(con)
    assert ws == [imports.Window(eid, "2024-03-03", "2024-04-19", "2024-03-10")]
    assert imports.target(ws, "2024-03-03") == eid and imports.target(ws, "2024-03-02") is None
    assert imports.target(ws, "2024-04-19") == eid and imports.target(ws, "2024-04-20") is None


def test_open_event_window_ends_after_90_days(con):
    eid = mk_event(con, "2024-01-01")
    ws = windows_of(con)
    assert imports.target(ws, "2024-03-31") == eid and imports.target(ws, "2024-04-01") is None


def test_event_left_open_a_year_ago_does_not_collect_a_new_download(client, con, freeze_today):
    """Review Focus 5: an event left open by mistake a year ago, then a new download: the later visits are proposed
    as new episodes, not piled onto the old open event."""
    freeze_today("2025-03-20")
    old = mk_event(con, "2024-03-01")
    post_import(client, doc(1, encounters=[enc("2024-03-02", n=1), enc("2025-03-01", n=2), enc("2025-03-10", n=3, cls="IMP")],
                            procedures=[proc("2025-03-11", n=4)]), name="x.xml")
    got = client.get("/api/suggestions", headers=auth()).get_json()
    assert [(s["kind"], s["on_date"]) for s in got["by_event"][str(old)]] == [("visit", "2024-03-02"), ("doctor", "2024-03-02")]
    (episode,) = got["proposed"]
    assert (episode["start"], episode["end"]) == ("2025-03-01", "2025-03-11")
    assert len(episode["suggestion_ids"]) == 5  # two visits, their doctor twice, the procedure
    assert all(s["target_event_id"] is None for s in episode["suggestions"])
    assert client.get(f"/api/events/{old}", headers=auth()).get_json()["visits"] == []


def test_overlap_goes_to_nearest_start(con):
    a = mk_event(con, "2024-03-01", "2024-03-20")   # window to 2024-04-19
    b = mk_event(con, "2024-04-10")                  # window from 2024-04-03
    ws = windows_of(con)
    assert imports.target(ws, "2024-04-04") == b     # 34 days from a, 6 from b
    assert imports.target(ws, "2024-03-25") == a
    c = mk_event(con, "2024-04-16")                  # 2024-04-13 is 3 days from both b and c: the later event
    assert imports.target(windows_of(con), "2024-04-13") == c
    assert a < b < c


def test_same_start_tie_goes_to_the_later_event(con):
    first, second = mk_event(con, "2024-03-01"), mk_event(con, "2024-03-01")
    assert imports.target(windows_of(con), "2024-03-05") == second > first


def test_grouping_chains_within_14_days():
    def s(i, day):
        return {"id": i, "on_date": day}
    assert [[x["id"] for x in g] for g in imports.group([s(3, "2024-03-21"), s(1, "2024-03-01"), s(2, "2024-03-11")])] == [[1, 2, 3]]
    assert [[x["id"] for x in g] for g in imports.group([s(1, "2024-03-01"), s(2, "2024-03-21")])] == [[1], [2]]
    assert [[x["id"] for x in g] for g in imports.group([s(1, "2024-03-01"), s(2, "2024-03-15")])] == [[1, 2]]
    assert [[x["id"] for x in g] for g in imports.group([s(1, "2024-03-01"), s(2, "2024-03-16")])] == [[1], [2]]
    assert imports.group([s(1, None)]) == []


def test_undated_suggestions_are_listed_apart(client, con):
    post_import(client, doc(1, procedures=[{"id": f"{EXAMPLE_ROOT}^p", "code": URS}]), name="x.xml")
    got = imports.pending(con, "2025-01-01")
    assert got["proposed"] == [] and got["by_event"] == {}
    assert [(s["kind"], s["on_date"], s["label"]) for s in got["undated"]] == [("procedure", None, "Ureteroscopy")]


def test_create_event_from_group_then_suggestions_target_it(client, con, freeze_today):
    freeze_today("2024-06-01")
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1)], procedures=[proc("2024-03-02", n=2)]), name="x.xml")
    (episode,) = client.get("/api/suggestions", headers=auth()).get_json()["proposed"]
    r = client.post("/api/suggestions/new-event", json={"suggestion_ids": episode["suggestion_ids"]}, headers=mut())
    assert r.status_code == 201
    event = r.get_json()
    assert (event["started_on"], event["open"], event["visits"], event["procedures"]) == ("2024-03-01", True, [], [])
    assert all(s["state"] == "pending" for s in all_suggestions(con)) and count(con, "event_files") == 0
    got = client.get(f"/api/events/{event['id']}/suggestions", headers=auth()).get_json()
    assert sorted(s["id"] for s in got) == sorted(episode["suggestion_ids"])
    assert client.get("/api/suggestions", headers=auth()).get_json()["proposed"] == []


def test_create_event_keeps_a_long_chain_together(client, con, freeze_today):
    """A chain of visits 13 days apart runs past the new open event's 90 days: every suggestion in the episode still
    targets the event it was created from."""
    freeze_today("2024-12-01")
    days = ["2024-01-01", "2024-01-14", "2024-01-27", "2024-02-09", "2024-02-22", "2024-03-06", "2024-03-19",
            "2024-04-01", "2024-04-14"]
    post_import(client, doc(1, encounters=[enc(d, n=i, performers=()) for i, d in enumerate(days, 1)]), name="x.xml")
    (episode,) = imports.pending(con, "2024-12-01")["proposed"]
    eid = imports.create_event(con, ALEX, episode["suggestion_ids"])
    assert len(imports.pending(con, "2024-12-01")["by_event"][eid]) == len(days)


def test_create_event_checks_its_input(client, con):
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1)],
                            procedures=[{"id": f"{EXAMPLE_ROOT}^p", "code": URS}]), name="x.xml")
    undated = by_kind(con, "procedure")[0]["id"]
    for body, status in (({}, 400), ({"suggestion_ids": []}, 400), ({"suggestion_ids": ["1"]}, 400),
                         ({"suggestion_ids": [True]}, 400), ({"suggestion_ids": [9999]}, 404),
                         ({"suggestion_ids": [undated]}, 400)):
        assert client.post("/api/suggestions/new-event", json=body, headers=mut()).status_code == status, body
    visit = by_kind(con, "visit")[0]["id"]
    client.post(f"/api/suggestions/{visit}/dismiss", headers=mut())
    assert client.post("/api/suggestions/new-event", json={"suggestion_ids": [visit]}, headers=mut()).status_code == 409
    assert count(con, "events") == 0


# ---------------------------------------------------------------- accept, dismiss, move
def test_accept_writes_detail_with_suggestion_id_and_links_file(client, con):
    eid = mk_event(con, "2024-03-01")
    post_import(client, record(1, "20240501"))
    visit = by_kind(con, "visit")[0]
    r = accept(client, visit["id"])
    assert r.status_code == 200
    body = r.get_json()
    assert body["suggestion"]["state"] == "accepted" and body["suggestion"]["event_id"] == eid
    (v,) = body["event"]["visits"]
    assert (v["kind"]["label"], v["from_date"], v["from_import"]) == ("Hospital admission", "2024-03-01", True)
    assert con.execute("SELECT suggestion_id FROM visits").fetchone()[0] == visit["id"]
    assert [f["id"] for f in files.for_event(con, eid)] == [visit["file_id"]]
    row = con.execute("SELECT * FROM suggestions WHERE id = ?", (visit["id"],)).fetchone()
    assert (row["state"], row["event_id"], row["decided_by"]) == ("accepted", eid, "alex@example.com")
    # The other suggestions are still only suggestions.
    assert body["event"]["procedures"] == [] and body["event"]["complications"] == []
    ureteroscopy = by_kind(con, "procedure")[0]  # 52356 is ureteroscopy, then stent placement
    p = accept(client, ureteroscopy["id"]).get_json()["event"]["procedures"]
    assert [(x["kind"]["label"], x["on_date"], x["from_import"]) for x in p] == [("Ureteroscopy", "2024-03-02", True)]
    comp = by_kind(con, "complication")[0]
    c = accept(client, comp["id"]).get_json()["event"]["complications"]
    assert [(x["kind"]["label"], x["on_date"]) for x in c] == [("Urinary tract infection", "2024-03-01")]


def test_accept_doctor_reuses_existing_by_name(client, con):
    eid = mk_event(con, "2024-03-01")
    existing = mk_doctor(con, "robin  RIVERA", retired=1)
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1)]), name="x.xml")
    s = by_kind(con, "doctor")[0]
    listed = client.get(f"/api/events/{eid}/suggestions", headers=auth()).get_json()
    assert [x["doctor_id"] for x in listed if x["kind"] == "doctor"] == [existing]
    doctors = accept(client, s["id"]).get_json()["event"]["doctors"]
    assert [d["id"] for d in doctors] == [existing] and count(con, "doctors") == 1


def test_accept_doctor_adds_a_new_one_and_keeps_an_existing_role(client, con):
    eid = mk_event(con, "2024-03-01")
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1, performers=[CHEN])],
                            procedures=[proc("2024-03-02", n=2, performers=[CHEN])]), name="x.xml")
    first, second = by_kind(con, "doctor")
    (d,) = accept(client, first["id"]).get_json()["event"]["doctors"]
    assert d["name"] == "Casey Chen"
    client.put(f"/api/events/{eid}/doctors/{d['id']}", json={"role": "urologist"}, headers=mut())
    (d,) = accept(client, second["id"]).get_json()["event"]["doctors"]
    assert d["role"] == "urologist" and count(con, "doctors") == 1


def test_accept_twice_is_409(client, con):
    mk_event(con, "2024-03-01")
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1)]), name="x.xml")
    sid = by_kind(con, "visit")[0]["id"]
    assert accept(client, sid).status_code == 200
    r = accept(client, sid)
    assert (r.status_code, r.get_json()) == (409, {"error": "Already decided."})
    assert client.post(f"/api/suggestions/{sid}/dismiss", headers=mut()).status_code == 409
    assert client.patch(f"/api/suggestions/{sid}", json={"event_id": None}, headers=mut()).status_code == 409
    assert count(con, "visits") == 1


def test_accept_without_a_target_is_400_and_with_an_event_id_writes_there(client, con):
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1)]), name="x.xml")
    sid = by_kind(con, "visit")[0]["id"]
    r = accept(client, sid)
    assert r.status_code == 400 and r.get_json()["error"]
    assert accept(client, sid, event_id=9999).status_code == 404
    assert accept(client, sid, event_id="1").status_code == 400
    far = mk_event(con, "2020-01-01", "2020-01-02")
    assert accept(client, sid, event_id=far).get_json()["event"]["visits"][0]["from_date"] == "2024-03-01"


def test_accept_rolls_back_when_the_write_is_refused(client, con):
    eid = mk_event(con, "2024-03-01")
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1)]), name="x.xml")
    con.execute("UPDATE lists SET retired = 1 WHERE kind = 'visit_kind' AND slug = 'er'")
    sid = by_kind(con, "visit")[0]["id"]
    assert accept(client, sid).status_code == 400
    assert con.execute("SELECT state FROM suggestions WHERE id = ?", (sid,)).fetchone()[0] == "pending"
    assert files.for_event(con, eid) == []


def test_dismiss_changes_nothing_else(client, con):
    eid = mk_event(con, "2024-03-01")
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1)]), name="x.xml")
    sid = by_kind(con, "visit")[0]["id"]
    r = client.post(f"/api/suggestions/{sid}/dismiss", headers=mut("sam@example.com"))
    assert r.status_code == 200 and r.get_json()["suggestion"]["state"] == "dismissed"
    row = con.execute("SELECT * FROM suggestions WHERE id = ?", (sid,)).fetchone()
    assert (row["state"], row["decided_by"], row["event_id"]) == ("dismissed", "sam@example.com", None)
    assert count(con, "visits") == 0 and files.for_event(con, eid) == []
    assert client.post("/api/suggestions/9999/dismiss", headers=mut()).status_code == 404


def test_move_overrides_computed_target(client, con):
    near = mk_event(con, "2024-03-01")
    other = mk_event(con, "2023-01-01", "2023-01-05")
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1)]), name="x.xml")
    sid = by_kind(con, "visit")[0]["id"]
    r = client.patch(f"/api/suggestions/{sid}", json={"event_id": other}, headers=mut())
    assert r.status_code == 200 and r.get_json()["suggestion"]["target_event_id"] == other
    assert r.get_json()["suggestion"]["moved"] is True
    got = imports.pending(con, "2024-06-01")
    assert sid in [s["id"] for s in got["by_event"][other]] and sid not in [s["id"] for s in got["by_event"].get(near, [])]
    assert accept(client, sid).get_json()["event"]["id"] == other
    # Moving back to the computed target: event_id null.
    sid2 = by_kind(con, "doctor")[0]["id"]
    client.patch(f"/api/suggestions/{sid2}", json={"event_id": other}, headers=mut())
    r = client.patch(f"/api/suggestions/{sid2}", json={"event_id": None}, headers=mut())
    assert (r.get_json()["suggestion"]["target_event_id"], r.get_json()["suggestion"]["moved"]) == (near, False)


def test_move_checks_its_input(client, con):
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1)]), name="x.xml")
    sid = by_kind(con, "visit")[0]["id"]
    for body, status in (({}, 400), ({"event_id": "2"}, 400), ({"event_id": True}, 400), ({"event_id": 9999}, 404)):
        assert client.patch(f"/api/suggestions/{sid}", json=body, headers=mut()).status_code == status, body
    assert client.patch("/api/suggestions/9999", json={"event_id": None}, headers=mut()).status_code == 404


def test_split_off_one_suggestion_into_its_own_event(client, con, freeze_today):
    freeze_today("2024-06-01")
    eid = mk_event(con, "2024-03-01")
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1)], procedures=[proc("2024-03-05", n=2)]), name="x.xml")
    sid = by_kind(con, "procedure")[0]["id"]
    new = client.post("/api/suggestions/new-event", json={"suggestion_ids": [sid]}, headers=mut()).get_json()
    assert new["started_on"] == "2024-03-05"
    got = imports.pending(con, "2024-06-01")
    assert [s["id"] for s in got["by_event"][new["id"]]] == [sid]
    assert sid not in [s["id"] for s in got["by_event"][eid]]


def test_suggestion_shape(client, con):
    post_import(client, doc(1, encounters=[enc(("2024-03-01", "2024-03-03"), n=1, cls="IMP")]), name="DOC0001.XML")
    got = client.get("/api/suggestions", headers=auth()).get_json()
    (episode,) = got["proposed"]
    visit = next(s for s in episode["suggestions"] if s["kind"] == "visit")
    file_id = con.execute("SELECT id FROM files").fetchone()[0]
    assert visit == {"id": visit["id"], "kind": "visit", "state": "pending", "on_date": "2024-03-01",
                     "end_date": "2024-03-03", "label": "Hospital admission", "slug": "admission", "doctor_id": None,
                     "file": {"id": file_id, "name": "DOC0001.XML"}, "target_event_id": None, "moved": False,
                     "event_id": None}
    assert (episode["start"], episode["end"]) == ("2024-03-01", "2024-03-03")
    assert client.get("/api/events/9999/suggestions", headers=auth()).status_code == 404


def test_a_renamed_list_item_shows_its_new_label(client, con):
    post_import(client, doc(1, encounters=[enc("2024-03-01", n=1)]), name="x.xml")
    con.execute("UPDATE lists SET label = 'Emergency room' WHERE kind = 'visit_kind' AND slug = 'er'")
    (s,) = [s for s in imports.pending(con, "2025-01-01")["proposed"][0]["suggestions"] if s["kind"] == "visit"]
    assert s["label"] == "Emergency room"


def test_no_projection_keys_in_suggestions(client, con):
    post_import(client, record(1, "20240501"))
    text = json.dumps(client.get("/api/suggestions", headers=auth()).get_json()).lower()
    for word in ("risk", "predict", "next_", "expected", "recommend", "score"):
        assert word not in text

