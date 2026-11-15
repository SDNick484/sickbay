"""Synthetic C-CDA documents and ZIPs for the tests (not a test file). Everything it writes is made up: a patient called
Alex Sample, clinicians with generic names, ids that are random UUIDs or HL7's example OID. Real portal downloads never
enter the repository, so tests build what they need here, in memory (tests/test_hygiene.py forbids committed ZIPs).

The shape follows C-CDA R2.1: a US Realm header, an optional `componentOf/encompassingEncounter`, and a structured
body whose sections carry their LOINC code (encounters 46240-8, procedures 47519-4, problems 11450-4, results
30954-2) and coded entries.

Shapes the builders take (every key optional unless said):

  id          "root" or "root^extension"; None writes `<id nullFlavor="NI"/>`
  code/value  (system, code) with system "icd10", "snomed", "cpt", "loinc", "actcode" or a raw OID; None leaves it out
  time        "20240301" (a single `value`), ("20240301", "20240303") (`low` and `high`; None in either leaves that
              one out), or None for no effectiveTime
  people      [("Robin", "Rivera"), ...] (given, family)

  encounter   {"id", "code", "translations": [(system, code)], "cls": "EMER" (an ActCode translation), "time",
               "diagnoses": [(system, code) | ((system, code), [translations])], "performers": people}
  encompassing {"id", "cls" (the encounter's own code, ActCode), "time", "performers" (encounterParticipant),
               "responsible" (responsibleParty, one person)}
  procedure   {"id", "code", "translations", "time", "performers", "tag": "procedure" | "act" | "observation"}
  problem     {"id", "act_id", "value", "translations", "time", "act_time"}
  result      {"id", "code", "value", "value_translations", "time", "organizer_time"}
"""
import io
import zipfile
from xml.sax.saxutils import escape, quoteattr

HL7 = "2.16.840.1.113883"  # HL7's OID arc; code systems sit under .6, HL7 vocabularies under .5
OIDS = {"icd10": f"{HL7}.6.90", "snomed": f"{HL7}.6.96", "cpt": f"{HL7}.6.12", "loinc": f"{HL7}.6.1",
        "actcode": f"{HL7}.5.4"}
EXAMPLE_ROOT = f"{HL7}.19.5"  # HL7's own example organisation OID, used in its specification's samples

SECTIONS = {  # LOINC section code, title, section templateId (entries required)
    "encounters": ("46240-8", "Encounters", f"{HL7}.10.20.22.2.22.1"),
    "procedures": ("47519-4", "Procedures", f"{HL7}.10.20.22.2.7.1"),
    "problems": ("11450-4", "Problems", f"{HL7}.10.20.22.2.5.1"),
    "results": ("30954-2", "Results", f"{HL7}.10.20.22.2.3.1"),
}


def a(value):
    return quoteattr(str(value))


def ident(value):
    if value is None:
        return '<id nullFlavor="NI"/>'
    root, _, ext = value.partition("^")
    return f"<id root={a(root)}{' extension=' + a(ext) if ext else ''}/>"


def code_el(pair, translations=(), tag="code", xsi=None):
    if pair is None:
        return ""
    system, code = pair
    kids = "".join(f"<translation code={a(c)} codeSystem={a(OIDS.get(s, s))}/>" for s, c in translations)
    typ = f' xsi:type="{xsi}"' if xsi else ""
    attrs = f"{typ} code={a(code)} codeSystem={a(OIDS.get(system, system))}"
    return f"<{tag}{attrs}>{kids}</{tag}>" if kids else f"<{tag}{attrs}/>"


def time_el(spec, tag="effectiveTime"):
    if spec is None:
        return ""
    if isinstance(spec, str):
        return f"<{tag} value={a(spec)}/>"
    low, high = spec
    inner = (f"<low value={a(low)}/>" if low else "") + (f"<high value={a(high)}/>" if high else "")
    return f"<{tag}>{inner}</{tag}>"


def person(given_family, wrapper="assignedPerson"):
    given, family = given_family
    return (f"<{wrapper}><name><given>{escape(given)}</given><family>{escape(family)}</family>"
            f"<suffix>MD</suffix></name></{wrapper}>")


def performers(people, tag="performer"):
    return "".join(f"<{tag}><assignedEntity><id nullFlavor=\"NI\"/>{person(p)}</assignedEntity></{tag}>"
                   for p in people)


def problem_observation(value, translations=(), obs_id="", time=None):
    return (f'<observation classCode="OBS" moodCode="EVN"><templateId root="{HL7}.10.20.22.4.4"/>'
            f"{ident(obs_id) if obs_id != '' else ''}"
            f'<code code="282291009" codeSystem="{OIDS["snomed"]}"/><statusCode code="completed"/>'
            f"{time_el(time)}{code_el(value, translations, tag='value', xsi='CD')}</observation>")


def encounter(e):
    cls = [("actcode", e["cls"])] if e.get("cls") else []
    diagnoses = ""
    for d in e.get("diagnoses", ()):
        value, translations = (d[0], d[1]) if isinstance(d[0], tuple) else (d, ())
        diagnoses += (f'<entryRelationship typeCode="COMP"><act classCode="ACT" moodCode="EVN">'
                      f'<templateId root="{HL7}.10.20.22.4.80"/><code code="29308-4" codeSystem="{OIDS["loinc"]}"/>'
                      f'<entryRelationship typeCode="SUBJ">{problem_observation(value, translations)}'
                      f"</entryRelationship></act></entryRelationship>")
    code = e.get("code")
    translations = [*e.get("translations", ()), *cls]
    if code is None and translations:
        code, translations = translations[0], translations[1:]
    return (f'<encounter classCode="ENC" moodCode="EVN"><templateId root="{HL7}.10.20.22.4.49"/>'
            f"{ident(e.get('id'))}{code_el(code, translations)}{time_el(e.get('time'))}"
            f"{performers(e.get('performers', ()))}{diagnoses}</encounter>")


def procedure(p):
    tag = p.get("tag", "procedure")
    return (f'<{tag} classCode="PROC" moodCode="EVN"><templateId root="{HL7}.10.20.22.4.14"/>'
            f"{ident(p.get('id'))}{code_el(p.get('code'), p.get('translations', ()))}"
            f'<statusCode code="completed"/>{time_el(p.get("time"))}{performers(p.get("performers", ()))}</{tag}>')


def problem(p):
    return (f'<act classCode="ACT" moodCode="EVN"><templateId root="{HL7}.10.20.22.4.3"/>{ident(p.get("act_id"))}'
            f'<code code="CONC" codeSystem="{HL7}.5.6"/>{time_el(p.get("act_time"))}'
            f'<entryRelationship typeCode="SUBJ">'
            f"{problem_observation(p.get('value'), p.get('translations', ()), p.get('id'), p.get('time'))}"
            f"</entryRelationship></act>")


def result(r):
    value = code_el(r.get("value"), r.get("value_translations", ()), tag="value", xsi="CD")
    return (f'<organizer classCode="BATTERY" moodCode="EVN"><templateId root="{HL7}.10.20.22.4.1"/>'
            f'<id root="{EXAMPLE_ROOT}" extension="panel"/><statusCode code="completed"/>'
            f"{time_el(r.get('organizer_time'))}"
            f'<component><observation classCode="OBS" moodCode="EVN"><templateId root="{HL7}.10.20.22.4.2"/>'
            f"{ident(r.get('id'))}{code_el(r.get('code'))}<statusCode code=\"completed\"/>{time_el(r.get('time'))}"
            f"{value}</observation></component></organizer>")


def section(key, entries):
    code, title, template = SECTIONS[key]
    body = "".join(f"<entry>{e}</entry>" for e in entries)
    return (f'<component><section><templateId root="{template}"/>'
            f'<code code="{code}" codeSystem="{OIDS["loinc"]}"/><title>{title}</title>'
            f"<text>Synthetic {title.lower()}.</text>{body}</section></component>")


def make_ccda(*, doc_id, set_id=None, version=1, effective="20240301", encompassing=None, encounters=(),
              procedures=(), problems=(), results=(), extra_sections="") -> bytes:
    """A C-CDA R2.1-shaped Continuity of Care Document with only made-up content. `extra_sections` is raw XML for
    a section the reader should ignore."""
    component_of = ""
    if encompassing is not None:
        e = encompassing
        responsible = ""
        if e.get("responsible"):
            responsible = (f"<responsibleParty><assignedEntity><id nullFlavor=\"NI\"/>{person(e['responsible'])}"
                           f"</assignedEntity></responsibleParty>")
        component_of = (f"<componentOf><encompassingEncounter>{ident(e.get('id'))}"
                        f"{code_el(('actcode', e['cls']) if e.get('cls') else None)}{time_el(e.get('time'))}"
                        f"{responsible}"
                        f"{performers(e.get('performers', ()), tag='encounterParticipant')}"
                        f"</encompassingEncounter></componentOf>")
    sections = "".join(section(k, [build(x) for x in items]) for k, build, items in (
        ("encounters", encounter, encounters), ("procedures", procedure, procedures),
        ("problems", problem, problems), ("results", result, results)) if items)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<?xml-stylesheet type="text/xsl" href="CDA.xsl"?>'
        '<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:sdtc="urn:hl7-org:sdtc" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<realmCode code="US"/><typeId root="{HL7}.1.3" extension="POCD_HD000040"/>'
        f'<templateId root="{HL7}.10.20.22.1.1" extension="2015-08-01"/>'
        f'<templateId root="{HL7}.10.20.22.1.2" extension="2015-08-01"/>'
        f"{ident(doc_id)}"
        f'<code code="34133-9" codeSystem="{OIDS["loinc"]}"/><title>Synthetic summary</title>'
        f"{time_el(effective)}"
        f'<confidentialityCode code="N" codeSystem="{HL7}.5.25"/><languageCode code="en-US"/>'
        f"{ident(set_id).replace('<id', '<setId') if set_id else ''}"
        f"{f'<versionNumber value={a(version)}/>' if version is not None else ''}"
        "<recordTarget><patientRole><id nullFlavor=\"NI\"/><patient><name><given>Alex</given>"
        '<family>Sample</family></name><birthTime value="19800101"/></patient></patientRole></recordTarget>'
        f"<author><time value={a(effective or '20240301')}/><assignedAuthor><id nullFlavor=\"NI\"/>"
        "<assignedAuthoringDevice><softwareName>Synthetic</softwareName></assignedAuthoringDevice>"
        "</assignedAuthor></author>"
        "<custodian><assignedCustodian><representedCustodianOrganization><id nullFlavor=\"NI\"/>"
        "<name>Example Clinic</name></representedCustodianOrganization></assignedCustodian></custodian>"
        f"{component_of}"
        f"<component><structuredBody>{sections}{extra_sections}</structuredBody></component>"
        "</ClinicalDocument>")
    return xml.encode("utf-8")


def make_zip(members: dict[str, bytes], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    """A ZIP built in memory: {member name: content}. A name ending in "/" is a directory entry."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()
