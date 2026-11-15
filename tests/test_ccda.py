"""Task 7: reading a portal download safely (Review Focus 1), and pulling coded entries out of C-CDA documents.

Every document and archive is synthetic and built in memory (tests/ccda_build.py)."""
import builtins
import io
import os
import random
import struct
import zipfile
import zlib

import pytest

import ccda
from ccda_build import EXAMPLE_ROOT, make_ccda, make_zip

PDF = b"%PDF-1.4\n% synthetic\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF\n"
INDEX = b"<html><body><p>Synthetic index</p></body></html>"
METADATA = (b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<SubmitObjectsRequest xmlns="urn:oasis:names:tc:ebxml-regrep:xsd:lcm:3.0"/>')


def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def doc(n=1, **kw):
    return make_ccda(doc_id=uid(n), **kw)


def xdm():
    return make_zip({
        "INDEX.HTM": INDEX,
        "IHE_XDM/": b"",
        "IHE_XDM/SUBSET01/": b"",
        "IHE_XDM/SUBSET01/METADATA.XML": METADATA,
        "IHE_XDM/SUBSET01/DOC0001.XML": doc(1),
        "IHE_XDM/SUBSET01/DOC0002.XML": doc(2),
        "IHE_XDM/SUBSET01/SUMMARY.PDF": PDF,
    })


# ---------------------------------------------------------------- raw ZIP surgery, to forge hostile archives
def central_entries(data):
    """[(offset of the central directory entry, name)] in order."""
    eocd = data.rfind(b"PK\x05\x06")
    count, _size, start = struct.unpack("<HII", data[eocd + 10:eocd + 20])
    out, pos = [], start
    for _ in range(count):
        n, m, k = struct.unpack("<HHH", data[pos + 28:pos + 34])
        out.append((pos, data[pos + 46:pos + 46 + n].decode()))
        pos += 46 + n + m + k
    return out


def patch(data, name, *, file_size=None, flags=None, same_data_as=None):
    """Rewrite fields of member `name` in both its central directory entry and its local header: the uncompressed
    size or the flag bits. Or (central only) point it at member `same_data_as`'s data, with that member's checksum
    and sizes, so the two entries share bytes and each one checks out."""
    buf = bytearray(data)
    entries = dict((n, pos) for pos, n in central_entries(data))
    pos = entries[name]
    local = struct.unpack("<I", buf[pos + 42:pos + 46])[0]
    if file_size is not None:
        struct.pack_into("<I", buf, pos + 24, file_size)
        struct.pack_into("<I", buf, local + 22, file_size)
    if flags is not None:
        struct.pack_into("<H", buf, pos + 8, flags)
        struct.pack_into("<H", buf, local + 6, flags)
    if same_data_as is not None:
        other = entries[same_data_as]
        for lo, hi in ((10, 12), (16, 28), (42, 46)):  # method; CRC and both sizes; local header offset
            buf[pos + lo:pos + hi] = buf[other + lo:other + hi]
    return bytes(buf)


def by_name(docs):
    return {d.name: d for d in docs}


# ---------------------------------------------------------------- reading the download
def test_reads_xdm_zip_into_documents():
    docs = by_name(ccda.read_download(xdm(), "download.zip"))
    assert set(docs) == {"INDEX.HTM", "IHE_XDM/SUBSET01/METADATA.XML", "IHE_XDM/SUBSET01/DOC0001.XML",
                         "IHE_XDM/SUBSET01/DOC0002.XML", "IHE_XDM/SUBSET01/SUMMARY.PDF"}  # directories skipped
    assert {n: d.kind for n, d in docs.items()} == {
        "INDEX.HTM": "other", "IHE_XDM/SUBSET01/METADATA.XML": "other", "IHE_XDM/SUBSET01/DOC0001.XML": "ccda",
        "IHE_XDM/SUBSET01/DOC0002.XML": "ccda", "IHE_XDM/SUBSET01/SUMMARY.PDF": "pdf"}
    assert docs["IHE_XDM/SUBSET01/DOC0001.XML"].data == doc(1)
    assert docs["IHE_XDM/SUBSET01/SUMMARY.PDF"].data == PDF
    assert all(d.error is None for d in docs.values())
    assert ccda.parse(docs["IHE_XDM/SUBSET01/DOC0002.XML"].data).doc_id == uid(2)


def test_member_kind_comes_from_content_not_name():
    docs = by_name(ccda.read_download(make_zip({"a.bin": doc(1), "b.xml": PDF, "c.xml": b"<other/>",
                                                "DOC0009.XML": METADATA, "x/metadata.xml": doc(3)}), "d.zip"))
    assert {n: d.kind for n, d in docs.items()} == {
        "a.bin": "ccda", "b.xml": "pdf", "c.xml": "other", "DOC0009.XML": "other",
        "x/metadata.xml": "other"}  # METADATA.XML is the XDM manifest wherever it sits, whatever it holds


def test_stored_members_are_read_too():
    docs = ccda.read_download(make_zip({"DOC0001.XML": doc(1)}, compression=zipfile.ZIP_STORED), "d.zip")
    assert [(d.kind, d.data) for d in docs] == [("ccda", doc(1))]


def test_single_xml_and_single_pdf():
    assert ccda.read_download(doc(1), "DOC0001.XML") == [ccda.Document("DOC0001.XML", doc(1), "ccda", None)]
    assert ccda.read_download(PDF, "C:\\Users\\someone\\Summary.pdf") == [
        ccda.Document("Summary.pdf", PDF, "pdf", None)]
    utf16 = doc(1).decode().replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16")
    assert ccda.read_download(utf16, "../../doc.xml")[0].kind == "ccda"
    assert ccda.read_download(utf16, "../../doc.xml")[0].name == "doc.xml"
    assert ccda.read_download(METADATA, "METADATA.XML")[0].kind == "other"


@pytest.mark.parametrize("data", [b"", b"\x89PNG\r\n\x1a\n" + b"\0" * 20, b"GIF89a", INDEX, b"just text",
                                  b"PK\x03\x04 not really a zip"])
def test_single_file_that_is_not_a_download_is_refused(data):
    with pytest.raises(ccda.Refused) as e:
        ccda.read_download(data, "upload.zip")
    assert e.value.message and str(e.value) == e.value.message


def test_member_over_limit_refused_by_streamed_size(monkeypatch):
    """The header says 100 bytes; the deflate stream really holds 20 000. The reader counts what it inflates."""
    monkeypatch.setattr(ccda, "MAX_MEMBER_BYTES", 4096)
    big = b"<a>" + b"x" * 20_000 + b"</a>"
    data = patch(make_zip({"DOC0001.XML": doc(1), "BIG.XML": big}), "BIG.XML", file_size=100)
    assert zipfile.ZipFile(io.BytesIO(data)).getinfo("BIG.XML").file_size == 100  # the lie stands in the header
    with pytest.raises(ccda.Refused, match="too large"):
        ccda.read_download(data, "d.zip")


def test_member_claiming_over_limit_refused_before_reading(monkeypatch):
    monkeypatch.setattr(ccda, "MAX_MEMBER_BYTES", 4096)
    with pytest.raises(ccda.Refused, match="too large"):
        ccda.read_download(make_zip({"BIG.XML": b"x" * 5000}), "d.zip")
    monkeypatch.setattr(ccda, "MAX_MEMBER_BYTES", 5000)
    assert len(ccda.read_download(make_zip({"BIG.XML": b"x" * 5000}), "d.zip")[0].data) == 5000  # at the limit


def test_header_size_that_lies_is_refused_as_damaged():
    data = patch(make_zip({"DOC0001.XML": doc(1)}), "DOC0001.XML", file_size=100)
    with pytest.raises(ccda.Refused, match="damaged"):
        ccda.read_download(data, "d.zip")


def test_too_many_members_refused(monkeypatch):
    monkeypatch.setattr(ccda, "MAX_MEMBERS", 3)
    assert len(ccda.read_download(make_zip({f"D{i}.XML": doc(i) for i in range(3)}), "d.zip")) == 3
    with pytest.raises(ccda.Refused, match="too many"):
        ccda.read_download(make_zip({f"D{i}.XML": doc(i) for i in range(4)}), "d.zip")
    with pytest.raises(ccda.Refused, match="too many"):  # directory entries count: they cost the parser the same
        ccda.read_download(make_zip({"a/": b"", "b/": b"", "c/": b"", "D.XML": doc(1)}), "d.zip")


def test_total_over_limit_refused(monkeypatch):
    monkeypatch.setattr(ccda, "MAX_TOTAL_BYTES", 10_000)
    with pytest.raises(ccda.Refused, match="too large once unpacked"):  # the headers' own claims add up too far
        ccda.read_download(make_zip({f"D{i}.XML": b"y" * 4000 for i in range(3)}), "d.zip")
    # Honest headers under the total, but the second member's stream holds more than it claims.
    data = patch(make_zip({"D1.XML": b"y" * 4000, "D2.XML": b"z" * 8000}), "D2.XML", file_size=10)
    with pytest.raises(ccda.Refused, match="too large once unpacked"):
        ccda.read_download(data, "d.zip")


def test_nested_zip_not_opened():
    inner = make_zip({"DOC0001.XML": doc(1)})
    docs = by_name(ccda.read_download(make_zip({"inner.zip": inner, "DOC0002.XML": inner, "DOC0003.XML": doc(3)}),
                                      "d.zip"))
    assert set(docs) == {"inner.zip", "DOC0002.XML", "DOC0003.XML"}  # nothing from inside the inner ZIP
    for name in ("inner.zip", "DOC0002.XML"):  # by name or by content
        assert docs[name].kind == "other" and docs[name].error and docs[name].data == inner
    assert docs["DOC0003.XML"].kind == "ccda" and docs["DOC0003.XML"].error is None


def test_member_paths_never_touch_disk(tmp_path, monkeypatch):
    names = ["../../x.xml", "/abs/y.xml", "C:\\win\\z.xml", "a/./b/../c.xml", "..\\..\\w.xml", "../", "q\x07r.xml"]
    data = make_zip({n: doc(i) for i, n in enumerate(names)})
    monkeypatch.chdir(tmp_path)

    def no_disk(*_a, **_k):
        raise AssertionError("the reader touched the filesystem")

    with monkeypatch.context() as m:
        for mod, attr in ((builtins, "open"), (io, "open"), (os, "open"), (os, "mkdir"), (os, "makedirs"),
                          (os, "rename"), (os, "replace")):
            m.setattr(mod, attr, no_disk)
        docs = ccda.read_download(data, "../../download.zip")
    assert os.listdir(tmp_path) == []
    assert sorted(d.name for d in docs) == sorted(["x.xml", "abs/y.xml", "win/z.xml", "a/b/c.xml", "w.xml", "qr.xml"])
    assert all(".." not in d.name.split("/") and not d.name.startswith("/") and ":" not in d.name for d in docs)


def test_encrypted_member_refused():
    data = patch(make_zip({"DOC0001.XML": doc(1)}), "DOC0001.XML", flags=0x1)
    with pytest.raises(ccda.Refused, match="password"):
        ccda.read_download(data, "d.zip")


def test_overlapping_members_refused():
    """Two directory entries pointing at the same data are how a small archive inflates to many times its size."""
    data = patch(make_zip({"D1.XML": doc(1), "D2.XML": doc(2)}), "D2.XML", same_data_as="D1.XML")
    with pytest.raises(ccda.Refused, match="damaged"):
        ccda.read_download(data, "d.zip")


def test_damaged_zip_refused():
    good = make_zip({"DOC0001.XML": doc(1), "DOC0002.XML": doc(2)})
    with pytest.raises(ccda.Refused, match="damaged"):
        ccda.read_download(good[: len(good) // 2], "d.zip")
    flipped = bytearray(good)
    flipped[60] ^= 0xFF  # inside the first member's compressed data
    with pytest.raises(ccda.Refused, match="damaged"):
        ccda.read_download(bytes(flipped), "d.zip")


def test_unsupported_compression_is_reported_per_document():
    pytest.importorskip("bz2")
    plain = make_zip({"DOC0001.XML": doc(1)})
    odd = make_zip({"DOC0002.XML": doc(2)}, compression=zipfile.ZIP_BZIP2)
    merged = io.BytesIO()
    with zipfile.ZipFile(merged, "w") as out:
        for src in (plain, odd):
            with zipfile.ZipFile(io.BytesIO(src)) as zf:
                for info in zf.infolist():
                    out.writestr(info, zf.read(info), compress_type=info.compress_type)
    docs = by_name(ccda.read_download(merged.getvalue(), "d.zip"))
    assert docs["DOC0001.XML"].kind == "ccda" and docs["DOC0001.XML"].error is None
    assert docs["DOC0002.XML"].kind == "other" and docs["DOC0002.XML"].error and docs["DOC0002.XML"].data == b""


def test_empty_zip_refused():
    with pytest.raises(ccda.Refused):
        ccda.read_download(make_zip({}), "d.zip")
    with pytest.raises(ccda.Refused):
        ccda.read_download(make_zip({"only/": b""}), "d.zip")


def test_mutated_downloads_never_crash():
    """Random damage anywhere in a download is refused or read, and each document then parses or is reported."""
    rng = random.Random(7)
    good = xdm()
    for _ in range(400):
        bad = bytearray(good)
        for _ in range(rng.randint(1, 8)):
            bad[rng.randrange(len(bad))] = rng.randrange(256)
        try:
            docs = ccda.read_download(bytes(bad), "d.zip")
        except ccda.Refused:
            continue
        for d in docs:
            if d.kind == "ccda":
                try:
                    ccda.parse(d.data)
                except (ccda.Unsafe, ccda.Unreadable):
                    pass


# ---------------------------------------------------------------- parsing one document safely
LAUGHS = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
          b'<ClinicalDocument xmlns="urn:hl7-org:v3"><title>&lol2;</title></ClinicalDocument>')
EXTERNAL = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY ext SYSTEM "file:///etc/hostname">]>'
            b'<ClinicalDocument xmlns="urn:hl7-org:v3"><title>&ext;</title></ClinicalDocument>')


@pytest.mark.parametrize("data", [
    LAUGHS,
    EXTERNAL,
    LAUGHS.replace(b"DOCTYPE", b"doctype"),
    b'<!DOCTYPE ClinicalDocument SYSTEM "http://example.com/cda.dtd">' + doc(1)[38:],
    doc(1).replace(b"<title>", b"<!-- <!ENTITY x 'y'> --><title>"),  # anywhere, even in a comment
    doc(1).replace(b"<title>", b"<title><![CDATA[<!doctype html>]]>"),
    LAUGHS.decode().replace('version="1.0"', 'version="1.0" encoding="UTF-16"').encode("utf-16"),  # no ASCII bytes
    LAUGHS.decode().encode("utf-16-le"),  # no BOM either
], ids=["laughs", "external", "lowercase", "external-dtd", "in-comment", "in-cdata", "utf16", "utf16-no-bom"])
def test_doctype_and_entity_refused_as_unsafe(data):
    with pytest.raises(ccda.Unsafe) as e:
        ccda.parse(data)
    assert e.value.message


@pytest.mark.parametrize("data", [
    doc(1)[: len(doc(1)) // 2],
    doc(1)[:-1],
    b"",
    b"\xff\xfe\x00garbage",
    b"<ClinicalDocument xmlns='urn:hl7-org:v3'><title>&undefined;</title></ClinicalDocument>",
    b'<?xml version="1.0"?><other xmlns="urn:hl7-org:v3"/>',  # well-formed, but not a C-CDA document
    b"<ClinicalDocument><title/></ClinicalDocument>",  # no HL7 namespace
    b'<?xml version="1.0" encoding="UTF-8"?><ClinicalDocument xmlns="urn:hl7-org:v3"><title>\xff</title>',
    b'<?xml version="1.0" encoding="no-such-codec"?><ClinicalDocument xmlns="urn:hl7-org:v3"/>',
    b'<?xml version="1.0" encoding="utf-7"?><ClinicalDocument xmlns="urn:hl7-org:v3"/>',  # not a codec we accept
    b'<?xml version="1.0" encoding="UTF-16"?><ClinicalDocument xmlns="urn:hl7-org:v3"/>',  # says UTF-16, is not
    b"<a>" * 100_000,  # deep and unclosed
])
def test_truncated_xml_is_unreadable_not_crash(data):
    with pytest.raises(ccda.Unreadable) as e:
        ccda.parse(data)
    assert e.value.message


def test_deeply_nested_document_parses_or_is_unreadable():
    deep = doc(1).replace(b"<title>", b"<title>" + b"<b>" * 50_000 + b"</b>" * 50_000)
    try:
        parsed = ccda.parse(deep)
    except ccda.Unreadable:
        return
    assert parsed.doc_id == uid(1)


def test_other_encodings_parse():
    text = doc(1).decode()
    for codec, decl in (("utf-16", "UTF-16"), ("utf-16-be", "UTF-16"), ("latin-1", "ISO-8859-1"),
                        ("utf-8-sig", "UTF-8")):
        data = text.replace('encoding="UTF-8"', f'encoding="{decl}"').encode(codec)
        assert ccda.parse(data).doc_id == uid(1), codec


def test_mutated_xml_never_crashes():
    rng = random.Random(11)
    good = doc(1, encounters=[{"id": uid(9), "cls": "EMER", "time": ("20240301", "20240302"),
                               "diagnoses": [("icd10", "N20.1")], "performers": [("Robin", "Rivera")]}])
    for _ in range(400):
        bad = bytearray(good)
        for _ in range(rng.randint(1, 6)):
            bad[rng.randrange(len(bad))] = rng.randrange(256)
        try:
            ccda.parse(bytes(bad))
        except (ccda.Unsafe, ccda.Unreadable):
            pass


# ---------------------------------------------------------------- header and entries
def test_header_ids_and_version():
    p = ccda.parse(make_ccda(doc_id=f"{EXAMPLE_ROOT}^DOC-1", set_id=f"{EXAMPLE_ROOT}^SET-1", version=3,
                             effective="20240301101500-0500"))
    assert (p.doc_id, p.set_id, p.version, p.effective) == (f"{EXAMPLE_ROOT}^DOC-1", f"{EXAMPLE_ROOT}^SET-1", 3,
                                                            "2024-03-01")
    p = ccda.parse(make_ccda(doc_id=uid(1), version=None, effective=None))
    assert (p.doc_id, p.set_id, p.version, p.effective, p.entries) == (uid(1), None, None, None, [])
    p = ccda.parse(make_ccda(doc_id=None, set_id=uid(2)))
    assert (p.doc_id, p.set_id, p.version) == (None, uid(2), 1)
    bad_version = make_ccda(doc_id=uid(1)).replace(b'<versionNumber value="1"/>', b'<versionNumber value="two"/>')
    assert ccda.parse(bad_version).version is None


def test_encounter_with_class_dates_and_diagnoses():
    p = ccda.parse(doc(1, encounters=[{
        "id": f"{EXAMPLE_ROOT}^ENC-7", "code": ("cpt", "99284"), "cls": "EMER", "time": ("20240301", "20240302"),
        "diagnoses": [("icd10", "N20.1"), (("snomed", "68566005"), [("icd10", "N39.0")])],
        "performers": [("Robin", "Rivera"), ("Robin", "Rivera"), ("Casey", "Chen")],
    }]))
    assert p.entries == [ccda.Entry(
        kind="encounter", entry_id=f"{EXAMPLE_ROOT}^ENC-7", system="cpt", code="99284", start="2024-03-01",
        end="2024-03-02", encounter_class="EMER",
        diagnoses=[("icd10", "N20.1"), ("snomed", "68566005"), ("icd10", "N39.0")],
        performers=["Robin Rivera", "Casey Chen"], codes=[("cpt", "99284")])]


def test_encounter_class_only_from_actcode():
    p = ccda.parse(doc(1, encounters=[{"id": uid(5), "code": ("cpt", "EMER")},
                                      {"id": uid(6), "cls": "amb", "time": "20240301"}]))
    assert [(e.encounter_class, e.system, e.code) for e in p.entries] == [(None, "cpt", "EMER"), ("AMB", None, "amb")]


def test_encompassing_encounter_from_header():
    p = ccda.parse(doc(1, encompassing={"id": f"{EXAMPLE_ROOT}^VISIT-3", "cls": "IMP",
                                        "time": ("20240301083000-0800", "20240304"),
                                        "performers": [("Casey", "Chen")], "responsible": ("Robin", "Rivera")},
                       procedures=[{"id": uid(8), "code": ("cpt", "52332"), "time": "20240302"}]))
    first = p.entries[0]
    assert first == ccda.Entry(kind="encounter", entry_id=f"{EXAMPLE_ROOT}^VISIT-3", encounter_class="IMP",
                               code="IMP", start="2024-03-01", end="2024-03-04",
                               performers=["Robin Rivera", "Casey Chen"])
    assert [e.kind for e in p.entries] == ["encounter", "procedure"]


def test_procedure_cpt_and_date():
    p = ccda.parse(doc(1, procedures=[
        {"id": uid(11), "code": ("cpt", "52356"), "time": "20240302", "performers": [("Robin", "Rivera")]},
        # The primary code is SNOMED; a procedure reports CPT first (ccda.PREFER), so the translation is chosen.
        {"id": uid(12), "code": ("snomed", "0000001"), "translations": [("cpt", "52332")], "time": "20240303",
         "tag": "act"},
        {"id": uid(13), "code": ("snomed", "0000002"), "time": "20240304", "tag": "observation"},
        {"id": None, "code": ("2.999.1", "LOCAL-1")},  # an unknown code system
    ]))
    assert [(e.kind, e.entry_id, e.system, e.code, e.start, e.end, e.performers) for e in p.entries] == [
        ("procedure", uid(11), "cpt", "52356", "2024-03-02", None, ["Robin Rivera"]),
        ("procedure", uid(12), "cpt", "52332", "2024-03-03", None, []),
        ("procedure", uid(13), "snomed", "0000002", "2024-03-04", None, []),
        ("procedure", None, None, "LOCAL-1", None, None, []),
    ]
    assert p.entries[1].codes == [("snomed", "0000001"), ("cpt", "52332")]
    assert p.entries[3].codes == []


def test_problem_icd10():
    p = ccda.parse(doc(1, problems=[
        {"id": uid(21), "act_id": uid(20), "value": ("icd10", "N20.0"), "time": ("20240301", None)},
        {"id": uid(22), "value": ("snomed", "0000003"), "translations": [("icd10", "N20.1")],
         "act_time": ("20230101", "20230201")},
        {"id": uid(23), "value": ("snomed", "0000004"), "translations": [("2.999.3", "LOCAL")]},
        {"id": uid(24), "value": ("snomed", "0000005"), "translations": [("icd10", "Z00.00")]},
    ]))
    assert [(e.kind, e.entry_id, e.system, e.code, e.start, e.end) for e in p.entries] == [
        ("diagnosis", uid(21), "icd10", "N20.0", "2024-03-01", None),
        ("diagnosis", uid(22), "icd10", "N20.1", "2023-01-01", "2023-02-01"),  # the concern's dates as a fallback
        ("diagnosis", uid(23), "snomed", "0000004", None, None),  # the translation's system is unknown
        ("diagnosis", uid(24), "icd10", "Z00.00", None, None),  # ICD-10 first, whether or not codes.py knows it
    ]


def test_result_value_code():
    p = ccda.parse(doc(1, results=[
        {"id": uid(31), "code": ("loinc", "00000-0"), "value": ("snomed", "000000"), "time": "20240305"},
        {"id": uid(32), "code": ("loinc", "00000-0"), "value": ("2.999.2", "LOCAL"),
         "value_translations": [("snomed", "000001")], "organizer_time": "20240306"},
        {"id": uid(33), "code": ("loinc", "00000-0"), "value": ("2.999.2", "LOCAL")},
    ]))
    assert [(e.kind, e.entry_id, e.system, e.code, e.value_code, e.start) for e in p.entries] == [
        ("result", uid(31), "loinc", "00000-0", ("snomed", "000000"), "2024-03-05"),
        ("result", uid(32), "loinc", "00000-0", ("snomed", "000001"), "2024-03-06"),
        ("result", uid(33), "loinc", "00000-0", None, None),
    ]


@pytest.mark.parametrize("time,start,end", [
    ("20240301", "2024-03-01", None),
    (("20240301", "20240305"), "2024-03-01", "2024-03-05"),
    (("20240301", None), "2024-03-01", None),
    ((None, "20240305"), None, "2024-03-05"),
    ("20240301235900-0800", "2024-03-01", None),  # the date as written, not moved to UTC
    ("202403011200+0100", "2024-03-01", None),
    (("20240301000000.000+0000", "20240302235959-1200"), "2024-03-01", "2024-03-02"),
    ("2024", None, None),  # a year alone is no date
    ("202403", None, None),
    ("20240230", None, None),  # not a calendar date
    ("18991231", None, None),  # before 1900, as common.parse_date
    ("garbage", None, None),
    (None, None, None),
])
def test_low_high_and_single_value_times(time, start, end):
    p = ccda.parse(doc(1, procedures=[{"id": uid(41), "code": ("cpt", "50590"), "time": time}]))
    assert (p.entries[0].start, p.entries[0].end) == (start, end)


def test_null_flavored_time_and_code():
    data = doc(1, procedures=[{"id": uid(42), "code": ("cpt", "50590"), "time": "20240301"}])
    data = data.replace(b'<effectiveTime value="20240301"/>', b'<effectiveTime nullFlavor="UNK"/>')
    data = data.replace(b'<code code="50590"', b'<code nullFlavor="UNK" code=""')
    e = ccda.parse(data).entries[0]
    assert (e.system, e.code, e.start, e.end) == (None, None, None, None)


def test_only_the_four_sections_are_read():
    meds = ('<component><section><code code="10160-0" codeSystem="2.16.840.1.113883.6.1"/><entry>'
            '<encounter classCode="ENC" moodCode="EVN"><id root="x"/></encounter></entry></section></component>')
    p = ccda.parse(doc(1, extra_sections=meds, problems=[{"id": uid(51), "value": ("icd10", "N20.9")}]))
    assert [e.kind for e in p.entries] == ["diagnosis"]


def test_entries_in_document_order_across_sections():
    p = ccda.parse(doc(1, encounters=[{"id": uid(61)}], procedures=[{"id": uid(62)}],
                       problems=[{"id": uid(63)}], results=[{"id": uid(64)}]))
    assert [(e.kind, e.entry_id) for e in p.entries] == [("encounter", uid(61)), ("procedure", uid(62)),
                                                          ("diagnosis", uid(63)), ("result", uid(64))]


def test_reported_code_does_not_depend_on_the_code_tables(monkeypatch):
    """Adding a code to codes.py must not change which code an entry reports (keys may be built from it)."""
    import codes
    data = doc(1, procedures=[{"code": ("snomed", "0000006"), "translations": [("cpt", "99999")]}])
    before = ccda.parse(data).entries
    monkeypatch.setitem(codes.PROCEDURES, ("snomed", "0000006"), ("other",))
    assert ccda.parse(data).entries == before and (before[0].system, before[0].code) == ("cpt", "99999")


def test_systems_from_oids():
    assert ccda.SYSTEMS == {"2.16.840.1.113883.6.90": "icd10", "2.16.840.1.113883.6.96": "snomed",
                            "2.16.840.1.113883.6.12": "cpt", "2.16.840.1.113883.6.1": "loinc"}


def test_inflate_counts_real_bytes():
    """A deflate stream that inflates to far more than its compressed size is cut off at the limit, not held."""
    raw = zlib.compressobj(9, zlib.DEFLATED, -15)
    stream = raw.compress(b"\0" * 5_000_000) + raw.flush()
    with pytest.raises(ccda.Refused, match="too large"):
        ccda._inflate(stream, zipfile.ZIP_DEFLATED, room=1_000_000)
