"""Task 7: the code tables. One row per code, so adding a code to codes.py is one line there and one row here; a code
in a table without a row here (or a row without its code) fails."""
import re

import pytest

import ccda
import codes
import db

STONE_ROWS = [  # ICD-10-CM, titles as in the CMS/CDC tabular list
    ("icd10", "N20.0", "Calculus of kidney"),
    ("icd10", "N20.1", "Calculus of ureter"),
    ("icd10", "N20.2", "Calculus of kidney with calculus of ureter"),
    ("icd10", "N20.9", "Urinary calculus, unspecified"),
    ("icd10", "N21.0", "Calculus in bladder"),
    ("icd10", "N21.1", "Calculus in urethra"),
    ("icd10", "N21.8", "Other lower urinary tract calculus"),
    ("icd10", "N21.9", "Calculus of lower urinary tract, unspecified"),
    ("icd10", "N22", "Calculus of urinary tract in diseases classified elsewhere"),
    ("icd10", "N23", "Unspecified renal colic"),
    ("icd10", "N13.2", "Hydronephrosis with renal and ureteral calculous obstruction"),
]
COMPLICATION_ROWS = [
    ("icd10", "N39.0", "uti"),
    ("icd10", "N10", "kidney_infection"),
    ("icd10", "N13.6", "kidney_infection"),
    ("icd10", "A41.9", "sepsis"),
    ("icd10", "R65.20", "sepsis"),
    ("icd10", "R65.21", "sepsis"),
    ("icd10", "N13.1", "obstruction"),
    ("icd10", "N13.2", "obstruction"),
    ("icd10", "N17.9", "kidney_injury"),
]
PROCEDURE_ROWS = [
    ("cpt", "52332", ("stent_placement",)),
    ("cpt", "52310", ("stent_removal",)),
    ("cpt", "52315", ("stent_removal",)),
    ("cpt", "52352", ("ureteroscopy",)),
    ("cpt", "52353", ("ureteroscopy",)),
    ("cpt", "52356", ("ureteroscopy", "stent_placement")),
    ("cpt", "50590", ("swl",)),
    ("cpt", "50080", ("pcnl",)),
    ("cpt", "50081", ("pcnl",)),
    ("cpt", "50432", ("nephrostomy",)),
]
STONE_ANALYSIS_ROWS = []  # Task 13 adds these from a real download
STONE_TYPE_VALUE_ROWS = []
VISIT_ROWS = [("EMER", "er"), ("IMP", "admission"), ("ACUTE", "admission"), ("AMB", "office"),
              ("OBSENC", "admission")]

FORMATS = {
    "icd10": re.compile(r"[A-TV-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?"),
    "cpt": re.compile(r"[0-9]{4}[0-9FTU]"),
    "snomed": re.compile(r"[1-9][0-9]{5,17}"),
    "loinc": re.compile(r"[0-9]{1,7}-[0-9]"),
}
SLUGS = {kind: {slug for slug, _label in items} for kind, items in db.SEED_LISTS.items()}


@pytest.mark.parametrize("system,code,title", STONE_ROWS)
def test_stone_diagnosis(system, code, title):
    assert codes.STONE_DIAGNOSES[(system, code)] == title


@pytest.mark.parametrize("system,code,slug", COMPLICATION_ROWS)
def test_complication(system, code, slug):
    assert codes.COMPLICATIONS[(system, code)] == slug


@pytest.mark.parametrize("system,code,slugs", PROCEDURE_ROWS)
def test_procedure(system, code, slugs):
    assert codes.PROCEDURES[(system, code)] == slugs


@pytest.mark.parametrize("cls,slug", VISIT_ROWS)
def test_visit_class(cls, slug):
    assert codes.VISIT_CLASS[cls] == slug


def test_every_table_entry_has_a_row():
    assert set(codes.STONE_DIAGNOSES) == {(s, c) for s, c, _ in STONE_ROWS}
    assert set(codes.COMPLICATIONS) == {(s, c) for s, c, _ in COMPLICATION_ROWS}
    assert set(codes.PROCEDURES) == {(s, c) for s, c, _ in PROCEDURE_ROWS}
    assert set(codes.STONE_ANALYSIS) == {(s, c) for s, c, _ in STONE_ANALYSIS_ROWS}
    assert set(codes.STONE_TYPE_VALUES) == {(s, c) for s, c, _ in STONE_TYPE_VALUE_ROWS}
    assert codes.VISIT_CLASS == dict(VISIT_ROWS)


def _table_rows():
    for name, kind in (("STONE_DIAGNOSES", None), ("COMPLICATIONS", "complication_kind"),
                       ("PROCEDURES", "procedure_kind"), ("STONE_ANALYSIS", None), ("STONE_TYPE_VALUES", "stone_type")):
        for key, value in getattr(codes, name).items():
            yield pytest.param(key, value, kind, id=f"{name}-{key[0]}-{key[1]}")


@pytest.mark.parametrize("key,value,kind", list(_table_rows()))
def test_every_code_table_entry(key, value, kind):
    """A known system, a code in that system's format, and values that are seeded list slugs (or, for the tables
    keyed to a name, a non-empty name)."""
    system, code = key
    assert system in FORMATS and system in ccda.SYSTEMS.values()
    assert FORMATS[system].fullmatch(code), f"{code!r} is not shaped like a {system} code"
    if kind is None:
        assert isinstance(value, str) and value.strip()
    else:
        slugs = value if isinstance(value, tuple) else (value,)
        assert slugs and all(slug in SLUGS[kind] for slug in slugs), value


@pytest.mark.parametrize("cls,slug", sorted(codes.VISIT_CLASS.items()))
def test_every_visit_class_is_a_visit_kind(cls, slug):
    assert re.fullmatch(r"[A-Z]+", cls) and slug in SLUGS["visit_kind"]


def test_stone_diagnoses_are_icd10_urinary_calculus_codes():
    """N20-N23 is the urolithiasis block; N13.2 is the one obstruction code that names a calculus."""
    assert all(system == "icd10" and re.match(r"N2[0-3]|N13\.2$", code) for system, code in codes.STONE_DIAGNOSES)
