"""Reading a patient portal download safely, and pulling the coded entries out of its C-CDA documents. No routes.

Everything here works on bytes in memory: nothing is ever written to disk, and a member's path inside a ZIP is only a
name. Hostile or broken input never raises anything but the three exceptions below:

  Refused     the whole upload (a ZIP bomb, too many members, a damaged or password-protected archive, a file that is
              not a download); its message is for a person to read
  Unsafe      one document declares a DOCTYPE or an entity: it is not parsed at all
  Unreadable  one document is not well-formed XML, not in an encoding we accept, or not a C-CDA document

Standard library only (Alpine py3-* packages; no lxml, no defusedxml). `zipfile` reads the ZIP's central directory,
but each member is inflated here with `zlib`, in chunks, so the size limits count the bytes the data really produces
rather than the sizes the archive claims (CPython's own reader stops at the claimed size, which would hide a lying
header behind a checksum error). XML is decoded here and checked for `<!DOCTYPE` / `<!ENTITY` before
`xml.etree.ElementTree` sees it: expat would expand an internal entity, so no declaration may reach it."""
import codecs
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from xml.etree import ElementTree as ET

MAX_MEMBERS = 2000                      # entries in the ZIP's directory, directories included
MAX_MEMBER_BYTES = 50 * 1024 * 1024     # one member, inflated
MAX_TOTAL_BYTES = 500 * 1024 * 1024     # all members together, inflated
CHUNK = 64 * 1024                       # inflate this much at a time, checking the limits after each step
MIN_YEAR = 1900                         # as common.parse_date: an earlier year is a slip, not a visit

V3 = "urn:hl7-org:v3"
NS = {"v3": V3}
SYSTEMS = {  # code system OID -> the name codes.py uses
    "2.16.840.1.113883.6.90": "icd10",   # ICD-10-CM
    "2.16.840.1.113883.6.96": "snomed",  # SNOMED CT
    "2.16.840.1.113883.6.12": "cpt",     # CPT-4
    "2.16.840.1.113883.6.1": "loinc",    # LOINC
}
ACT_CODE = "2.16.840.1.113883.5.4"  # HL7 v3 ActCode: encounter classes (EMER, IMP, AMB, ...)
SECTIONS = {"46240-8": "encounter", "47519-4": "procedure", "11450-4": "diagnosis", "30954-2": "result"}  # LOINC
# Which of a code and its translations an entry reports, by system, best first; document order breaks a tie. Fixed
# here rather than "whichever codes.py knows", so an entry's code (and any key built from it) stays the same when a
# code is added to the tables.
PREFER = {
    "encounter": ("cpt", "snomed", "icd10", "loinc"),
    "procedure": ("cpt", "snomed", "icd10", "loinc"),
    "diagnosis": ("icd10", "snomed", "loinc", "cpt"),
    "result": ("loinc", "snomed", "icd10", "cpt"),
    "value": ("snomed", "loinc", "icd10", "cpt"),  # a result's coded value
}

TOO_MANY = "That download holds too many files to be a portal download."
MEMBER_TOO_LARGE = "A file in that download is too large."
TOTAL_TOO_LARGE = "That download is too large once unpacked."
DAMAGED = "That download is damaged. Download it again."
PASSWORD = "That download is password-protected. Download it again without a password."
NOT_A_DOWNLOAD = "Choose the portal's download: a ZIP, or one XML or PDF document."
EMPTY = "That download has no documents in it."
NESTED = "A ZIP inside the download was not opened."
UNSUPPORTED = "This file is packed in a way the app cannot unpack."
UNSAFE = "This document declares a DOCTYPE or entities, so it was not read."
UNREADABLE = "This document could not be read as XML."
NOT_CCDA = "This document is not a C-CDA document."


class _Problem(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Refused(_Problem):
    """The whole upload is refused; `message` says why, for a person."""


class Unsafe(_Problem):
    """One document declares a DOCTYPE or an entity, and was not parsed."""


class Unreadable(_Problem):
    """One document could not be parsed, or is not a C-CDA document."""


@dataclass
class Document:
    name: str          # the member's path inside the ZIP, cleaned (no "..", no root, no drive), or the upload's name
    data: bytes
    kind: str          # "ccda" | "pdf" | "other"
    error: str | None = None  # set when the member was kept but not read (a nested ZIP, an unknown compression)


@dataclass
class Entry:
    kind: str                       # "encounter" | "procedure" | "diagnosis" | "result"
    entry_id: str | None = None     # "root" or "root^extension"
    system: str | None = None       # "icd10" | "snomed" | "cpt" | "loinc", else None (an unknown code system)
    code: str | None = None
    start: str | None = None        # YYYY-MM-DD
    end: str | None = None
    encounter_class: str | None = None             # ActCode: EMER, IMP, AMB, ... (encounters)
    diagnoses: list[tuple[str, str]] = field(default_factory=list)  # (system, code), encounters
    performers: list[str] = field(default_factory=list)             # display names, "Given Family"
    value_code: tuple[str, str] | None = None      # (system, code), results
    codes: list[tuple[str, str]] = field(default_factory=list)      # every known-system code and translation


@dataclass
class Parsed:
    doc_id: str | None = None
    set_id: str | None = None
    version: int | None = None
    effective: str | None = None
    entries: list[Entry] = field(default_factory=list)


# ---------------------------------------------------------------- the download
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_CENTRAL = b"PK\x01\x02"
_ZIP_ERRORS = (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError, EOFError, struct.error,
               NotImplementedError, OverflowError, IndexError)
_MANIFESTS = {"METADATA.XML", "INDEX.HTM"}  # the XDM layout's own files
_CDA_ROOT = re.compile(r"<(?:[A-Za-z_][\w.-]*:)?ClinicalDocument[\s/>]")
_DRIVE = re.compile(r"[A-Za-z]:")


def read_download(data: bytes, filename: str) -> list[Document]:
    """The documents in a portal download: a ZIP (any member layout; IHE XDM expected), or a single XML or PDF.
    Raises Refused for the whole upload. Members are read in memory and never written anywhere."""
    data = bytes(data)
    if data[:4] in _ZIP_MAGIC:
        return _read_zip(data)
    if len(data) > MAX_MEMBER_BYTES:
        raise Refused(MEMBER_TOO_LARGE)
    name = _clean_name(filename).rsplit("/", 1)[-1]
    kind, _error = _classify(name, data)
    if kind == "other" and not _looks_like_xml(data):
        raise Refused(NOT_A_DOWNLOAD)
    return [Document(name, data, kind)]


def _read_zip(data: bytes) -> list[Document]:
    # Each directory entry begins with this signature, so the count bounds the entries before zipfile builds one
    # object per entry.
    if data.count(_CENTRAL) > MAX_MEMBERS:
        raise Refused(TOO_MANY)
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            infos = zf.infolist()
    except _ZIP_ERRORS:
        raise Refused(DAMAGED) from None
    if len(infos) > MAX_MEMBERS:
        raise Refused(TOO_MANY)
    members = [i for i in infos if not i.is_dir()]
    if not members:
        raise Refused(EMPTY)
    # Cheap early refusals from what the archive claims. The counts while inflating are what decide.
    if any(i.file_size > MAX_MEMBER_BYTES for i in members):
        raise Refused(MEMBER_TOO_LARGE)
    if sum(i.file_size for i in members) > MAX_TOTAL_BYTES:
        raise Refused(TOTAL_TOO_LARGE)
    spans = [_span(data, i) for i in members]
    # Members must not share bytes: overlapping entries are how a small archive inflates to many times its size.
    ordered = sorted(spans)
    if any(later[0] < earlier[2] for earlier, later in zip(ordered, ordered[1:])):
        raise Refused(DAMAGED)

    docs, total = [], 0
    for info, (_header, start, end) in zip(members, spans):
        name = _clean_name(info.filename)
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            docs.append(Document(name, b"", "other", UNSUPPORTED))
            continue
        content = _inflate(memoryview(data)[start:end], info.compress_type, room=MAX_TOTAL_BYTES - total)
        if len(content) != info.file_size or zlib.crc32(content) != info.CRC:
            raise Refused(DAMAGED)  # the header lied about the size, or the data is corrupt
        total += len(content)
        docs.append(Document(name, content, *_classify(name, content)))
    return docs


def _span(data: bytes, info: zipfile.ZipInfo) -> tuple[int, int, int]:
    """(local header offset, data start, data end) of a member, from its local header."""
    if info.flag_bits & 0x1:
        raise Refused(PASSWORD)
    offset = info.header_offset
    head = data[offset:offset + 30] if offset >= 0 else b""
    if len(head) < 30 or head[:4] != b"PK\x03\x04":
        raise Refused(DAMAGED)
    name_len, extra_len = struct.unpack("<HH", head[26:30])
    start = offset + 30 + name_len + extra_len
    end = start + info.compress_size
    if end > len(data):
        raise Refused(DAMAGED)
    return offset, start, end


def _inflate(raw, method: int, *, room: int) -> bytes:
    """A member's bytes, inflated CHUNK at a time. Refused as soon as it passes MAX_MEMBER_BYTES or `room` (what is
    left of MAX_TOTAL_BYTES), whatever the archive claimed; Refused(DAMAGED) when the stream is broken or ends early."""
    out = bytearray()

    def take(chunk):
        out.extend(chunk)
        if len(out) > MAX_MEMBER_BYTES:
            raise Refused(MEMBER_TOO_LARGE)
        if len(out) > room:
            raise Refused(TOTAL_TOO_LARGE)

    if method == zipfile.ZIP_STORED:
        for i in range(0, len(raw), CHUNK):
            take(raw[i:i + CHUNK])
        return bytes(out)

    inflater = zlib.decompressobj(-zlib.MAX_WBITS)  # raw deflate, as in a ZIP
    pos, pending = 0, b""
    try:
        while not inflater.eof:
            if not pending:
                if pos >= len(raw):  # input used up: let zlib hand over what it still holds, if anything
                    chunk = inflater.decompress(b"", CHUNK)
                    if not chunk:
                        break
                    take(chunk)
                    continue
                pending = raw[pos:pos + CHUNK]
                pos += CHUNK
            before = len(pending)
            chunk = inflater.decompress(pending, CHUNK)
            pending = inflater.unconsumed_tail
            if not chunk and len(pending) == before:
                raise Refused(DAMAGED)  # no progress: never loop on a stream zlib will not move through
            take(chunk)
    except zlib.error:
        raise Refused(DAMAGED) from None
    if not inflater.eof:
        raise Refused(DAMAGED)
    return bytes(out)


def _clean_name(raw: str) -> str:
    """A member path as a name only: either slash separates, and empty, ".", ".." and drive ("C:") segments and
    control characters are dropped, so no name climbs out of or starts at a root."""
    parts = []
    for part in re.split(r"[\\/]+", raw or ""):
        part = "".join(ch for ch in part if ch.isprintable()).strip()
        if part in ("", ".", "..") or _DRIVE.fullmatch(part):
            continue
        parts.append(part)
    return "/".join(parts)[-255:] or "document"


def _classify(name: str, data: bytes) -> tuple[str, str | None]:
    """(kind, error) of one document, from its content; the XDM manifests by name."""
    if data[:4] in _ZIP_MAGIC or name.lower().endswith(".zip"):
        return "other", NESTED
    if name.rsplit("/", 1)[-1].upper() in _MANIFESTS:
        return "other", None
    if data.startswith(b"%PDF-"):
        return "pdf", None
    if _CDA_ROOT.search(_head(data)):
        return "ccda", None
    return "other", None


def _head(data: bytes) -> str:
    """The first 64 KiB as text, for telling kinds apart only (never for parsing)."""
    codec = _bom_codec(data) or "utf-8"
    return data[:CHUNK].decode(codec, errors="replace")


def _looks_like_xml(data: bytes) -> bool:
    head = _head(data).lstrip("﻿ \t\r\n")
    return head.startswith("<?xml") or bool(_CDA_ROOT.match(head))


# ---------------------------------------------------------------- one document
_DECLARATION_TEXT = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_DECLARATION_BYTES = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_XML_DECL = re.compile(rb"\s*<\?xml[^>]*?\sencoding\s*=\s*[\"']([A-Za-z0-9._-]{1,40})[\"']")
_CODECS = {"utf-8", "utf-8-sig", "ascii", "latin-1", "iso8859-1", "cp1252", "utf-16", "utf-16-le", "utf-16-be",
           "utf-32", "utf-32-le", "utf-32-be"}  # codecs.lookup() names


def parse(data: bytes) -> Parsed:
    """A C-CDA document's identity and its coded entries. Raises Unsafe for any DOCTYPE or entity declaration
    (checked in the raw bytes and in the decoded text, before any parser runs), Unreadable for anything that does not
    parse as a ClinicalDocument."""
    data = bytes(data)
    if _DECLARATION_BYTES.search(data):
        raise Unsafe(UNSAFE)
    text = _decode(data)
    if _DECLARATION_TEXT.search(text):  # the same text expat is given, so no encoding hides a declaration
        raise Unsafe(UNSAFE)
    try:
        # A str, so expat parses exactly the text checked above and ignores the declared encoding.
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError, RecursionError):
        raise Unreadable(UNREADABLE) from None
    if root.tag != f"{{{V3}}}ClinicalDocument":
        raise Unreadable(NOT_CCDA)
    try:
        version = root.find("v3:versionNumber", NS)
        raw_version = (version.get("value") or "").strip() if version is not None else ""
        return Parsed(
            doc_id=_id(root.find("v3:id", NS)),
            set_id=_id(root.find("v3:setId", NS)),
            version=int(raw_version) if re.fullmatch(r"[0-9]{1,9}", raw_version) else None,
            effective=_date(_attr(root.find("v3:effectiveTime", NS), "value")),
            entries=entries_from(root),
        )
    except RecursionError:
        raise Unreadable(UNREADABLE) from None


def _bom_codec(data: bytes) -> str | None:
    """The encoding a byte order mark (or, without one, the zero bytes around a leading "<") announces."""
    for bom, codec in ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
                       (codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if data.startswith(bom):
            return codec
    head = data[:4]
    if head == b"<\0\0\0":
        return "utf-32-le"
    if head == b"\0\0\0<":
        return "utf-32-be"
    if head[:2] == b"<\0":
        return "utf-16-le"
    if head[:2] == b"\0<":
        return "utf-16-be"
    return None


def _decode(data: bytes) -> str:
    codec = _bom_codec(data)
    if codec is None:
        m = _XML_DECL.match(data)
        codec = m.group(1).decode("ascii") if m else "utf-8"
    try:
        if codecs.lookup(codec).name not in _CODECS:
            raise Unreadable(UNREADABLE)
        return data.decode(codec)
    except (LookupError, UnicodeError):
        raise Unreadable(UNREADABLE) from None


# ---------------------------------------------------------------- entries
def entries_from(root: ET.Element) -> list[Entry]:
    """The coded entries of a ClinicalDocument: the header's componentOf/encompassingEncounter as an encounter, then
    the entries of every section whose LOINC code is in SECTIONS, in document order. Other sections are ignored."""
    out = []
    header = root.find("v3:componentOf/v3:encompassingEncounter", NS)
    if header is not None:
        out.append(_encounter(header, header=True))
    for section in root.iter(f"{{{V3}}}section"):
        kind = SECTIONS.get(_attr(section.find("v3:code", NS), "code"))
        if kind is None:
            continue
        for entry in section.findall("v3:entry", NS):
            if kind == "encounter":
                out += [_encounter(e) for e in entry.findall("v3:encounter", NS)]
            elif kind == "procedure":
                out += [_procedure(s) for tag in ("procedure", "act", "observation")
                        for s in entry.findall(f"v3:{tag}", NS)]
            elif kind == "diagnosis":
                out += _problems(entry)
            else:
                out += _results(entry)
    return out


def _encounter(el, *, header=False) -> Entry:
    code_el = el.find("v3:code", NS)
    pairs = _pairs(code_el)
    system, code = _best(pairs, "encounter")
    start, end = _times(el)
    if header:
        paths = ("v3:responsibleParty/v3:assignedEntity/v3:assignedPerson/v3:name",
                 "v3:encounterParticipant/v3:assignedEntity/v3:assignedPerson/v3:name")
        diagnoses = []
    else:
        paths = ("v3:performer/v3:assignedEntity/v3:assignedPerson/v3:name",)
        diagnoses = []
        # Encounter diagnoses (an act holding Problem Observations) and indications (an observation directly).
        for obs in (el.findall("v3:entryRelationship/v3:observation", NS)
                    + el.findall("v3:entryRelationship/v3:act/v3:entryRelationship/v3:observation", NS)):
            for pair in _pairs(obs.find("v3:value", NS)):
                if pair[0] and pair not in diagnoses:
                    diagnoses.append(pair)
    return Entry(kind="encounter", entry_id=_first_id(el), system=system, code=code, start=start, end=end,
                 encounter_class=_encounter_class(code_el), diagnoses=diagnoses, performers=_people(el, paths),
                 codes=[p for p in pairs if p[0]])


def _procedure(el) -> Entry:
    pairs = _pairs(el.find("v3:code", NS))
    system, code = _best(pairs, "procedure")
    start, end = _times(el)
    return Entry(kind="procedure", entry_id=_first_id(el), system=system, code=code, start=start, end=end,
                 performers=_people(el, ("v3:performer/v3:assignedEntity/v3:assignedPerson/v3:name",)),
                 codes=[p for p in pairs if p[0]])


def _problems(entry) -> list[Entry]:
    """Problem Observations, inside a Problem Concern Act or (from some systems) directly in the entry."""
    out = []
    for act in entry.findall("v3:act", NS):
        for obs in act.findall("v3:entryRelationship/v3:observation", NS):
            out.append(_diagnosis(obs, act))
    out += [_diagnosis(obs, None) for obs in entry.findall("v3:observation", NS)]
    return out


def _diagnosis(obs, act) -> Entry:
    pairs = _pairs(obs.find("v3:value", NS))
    system, code = _best(pairs, "diagnosis")
    start, end = _times(obs)
    if start is None and end is None and act is not None:
        start, end = _times(act)
    return Entry(kind="diagnosis", entry_id=_first_id(obs), system=system, code=code, start=start, end=end,
                 codes=[p for p in pairs if p[0]])


def _results(entry) -> list[Entry]:
    out = []
    for organizer in entry.findall("v3:organizer", NS):
        out += [_result(obs, organizer) for obs in organizer.findall("v3:component/v3:observation", NS)]
    out += [_result(obs, None) for obs in entry.findall("v3:observation", NS)]
    return out


def _result(obs, organizer) -> Entry:
    pairs = _pairs(obs.find("v3:code", NS))
    system, code = _best(pairs, "result")
    value_system, value = _best(_pairs(obs.find("v3:value", NS)), "value")
    value_code = (value_system, value) if value_system else None
    start, end = _times(obs)
    if start is None and end is None and organizer is not None:
        start, end = _times(organizer)
    return Entry(kind="result", entry_id=_first_id(obs), system=system, code=code, start=start, end=end,
                 value_code=value_code, codes=[p for p in pairs if p[0]])


# ---------------------------------------------------------------- small readers
def _attr(el, name) -> str | None:
    if el is None:
        return None
    value = (el.get(name) or "").strip()
    return value or None


def _id(el) -> str | None:
    """"root^extension", or "root" alone; None for a missing or null-flavored id."""
    root = _attr(el, "root")
    if root is None:
        return None
    ext = _attr(el, "extension")
    return f"{root}^{ext}" if ext else root


def _first_id(el) -> str | None:
    return next((i for i in (_id(x) for x in el.findall("v3:id", NS)) if i), None)


_TS = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})")


def _date(value: str | None) -> str | None:
    """YYYY-MM-DD from an HL7 timestamp (20240301, 20240301235900-0800, ...): the date as written, never moved to
    another zone. None for anything shorter than a day, not a calendar date, or before MIN_YEAR."""
    m = _TS.match(value or "")
    if not m:
        return None
    try:
        day = date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None
    return day.isoformat() if day.year >= MIN_YEAR else None


def _times(el) -> tuple[str | None, str | None]:
    """(start, end) from effectiveTime: low and high when either is there, else a single value as the start."""
    et = el.find("v3:effectiveTime", NS)
    if et is None:
        return None, None
    low, high = et.find("v3:low", NS), et.find("v3:high", NS)
    if low is not None or high is not None:
        return _date(_attr(low, "value")), _date(_attr(high, "value"))
    return _date(_attr(et, "value")), None


def _pairs(code_el) -> list[tuple[str | None, str]]:
    """(system, code) of a code element and each of its translations, in document order; system None when the code
    system is not one SYSTEMS names. Null-flavored and empty codes are left out."""
    if code_el is None:
        return []
    out = []
    for el in (code_el, *code_el.findall("v3:translation", NS)):
        code = _attr(el, "code")
        if code:
            out.append((SYSTEMS.get(_attr(el, "codeSystem") or ""), code))
    return out


def _best(pairs, kind) -> tuple[str | None, str | None]:
    """The entry's code: of the code and its translations, the one whose system comes first in PREFER[kind]; the
    primary code with no system when none is in a known system."""
    order = PREFER[kind]
    known = [p for p in pairs if p[0] in order]
    if known:
        return min(known, key=lambda p: order.index(p[0]))  # min keeps the first of equals: document order
    return (None, pairs[0][1]) if pairs else (None, None)


def _encounter_class(code_el) -> str | None:
    if code_el is None:
        return None
    for el in (code_el, *code_el.findall("v3:translation", NS)):
        if _attr(el, "codeSystem") == ACT_CODE and _attr(el, "code"):
            return _attr(el, "code").upper()
    return None


def _people(el, paths) -> list[str]:
    names = []
    for path in paths:
        for name_el in el.findall(path, NS):
            name = _person_name(name_el)
            if name and name not in names:
                names.append(name)
    return names


def _person_name(el) -> str | None:
    """"Given Family" from a name's parts (prefix and suffix left out), or its plain text when it has no parts."""
    parts = [p.text or "" for p in (*el.findall("v3:given", NS), *el.findall("v3:family", NS))]
    text = " ".join(parts) if parts else (el.text or "")
    text = " ".join(text.split())[:200]
    return text or None

