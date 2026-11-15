"""The code tables the importer recognises. Data, not logic: adding a code is one line here and one row in
tests/test_codes.py.

Keys are (system, code), with system one of "icd10" (ICD-10-CM), "snomed" (SNOMED CT), "cpt" (CPT) or "loinc"
(LOINC); ccda.SYSTEMS maps each code system's OID to these names. Values are slugs of the seeded list items in
db.SEED_LISTS, except in STONE_DIAGNOSES and STONE_ANALYSIS, whose values are the codes' own titles.

Add a code only after checking it against the code system's own reference, never from memory alone: ICD-10-CM
against the CMS/CDC tabular list (NLM's Clinical Tables serve it), CPT against the AMA's descriptors as published in
a current coding guide, ActCode against terminology.hl7.org. Codes are public terminology, not personal data.
"""
ICD10, SNOMED, CPT, LOINC = "icd10", "snomed", "cpt", "loinc"

# An entry is stone-related when its diagnosis is one of these (ICD-10-CM; value: the code's title).
STONE_DIAGNOSES: dict[tuple[str, str], str] = {
    (ICD10, "N20.0"): "Calculus of kidney",
    (ICD10, "N20.1"): "Calculus of ureter",
    (ICD10, "N20.2"): "Calculus of kidney with calculus of ureter",
    (ICD10, "N20.9"): "Urinary calculus, unspecified",
    (ICD10, "N21.0"): "Calculus in bladder",
    (ICD10, "N21.1"): "Calculus in urethra",
    (ICD10, "N21.8"): "Other lower urinary tract calculus",
    (ICD10, "N21.9"): "Calculus of lower urinary tract, unspecified",
    (ICD10, "N22"): "Calculus of urinary tract in diseases classified elsewhere",
    (ICD10, "N23"): "Unspecified renal colic",
    (ICD10, "N13.2"): "Hydronephrosis with renal and ureteral calculous obstruction",
}

# Diagnosis -> complication_kind slug.
COMPLICATIONS: dict[tuple[str, str], str] = {
    (ICD10, "N39.0"): "uti",               # Urinary tract infection, site not specified
    (ICD10, "N10"): "kidney_infection",    # Acute pyelonephritis
    (ICD10, "N13.6"): "kidney_infection",  # Pyonephrosis
    (ICD10, "A41.9"): "sepsis",            # Sepsis, unspecified organism
    (ICD10, "R65.20"): "sepsis",           # Severe sepsis without septic shock
    (ICD10, "R65.21"): "sepsis",           # Severe sepsis with septic shock
    (ICD10, "N13.1"): "obstruction",       # Hydronephrosis with ureteral stricture, not elsewhere classified
    (ICD10, "N13.2"): "obstruction",       # Hydronephrosis with renal and ureteral calculous obstruction
    (ICD10, "N17.9"): "kidney_injury",     # Acute kidney failure, unspecified
}

# Procedure -> procedure_kind slugs (one code can be two procedures).
PROCEDURES: dict[tuple[str, str], tuple[str, ...]] = {
    (CPT, "52332"): ("stent_placement",),                 # Cystourethroscopy, with insertion of indwelling ureteral stent
    (CPT, "52310"): ("stent_removal",),                   # Cystourethroscopy, removal of foreign body, calculus or stent; simple
    (CPT, "52315"): ("stent_removal",),                   # Cystourethroscopy, removal of foreign body, calculus or stent; complicated
    (CPT, "52352"): ("ureteroscopy",),                    # Cystourethroscopy with ureteroscopy; removal or manipulation of calculus
    (CPT, "52353"): ("ureteroscopy",),                    # Cystourethroscopy with ureteroscopy; with lithotripsy
    (CPT, "52356"): ("ureteroscopy", "stent_placement"),  # Cystourethroscopy with ureteroscopy; lithotripsy and ureteral stent
    (CPT, "50590"): ("swl",),                             # Lithotripsy, extracorporeal shock wave
    (CPT, "50080"): ("pcnl",),                            # Percutaneous nephrolithotomy or pyelolithotomy; up to 2 cm
    (CPT, "50081"): ("pcnl",),                            # Percutaneous nephrolithotomy or pyelolithotomy; over 2 cm
    (CPT, "50432"): ("nephrostomy",),                     # Placement of nephrostomy catheter, percutaneous
}

# A result whose code is one of these is a stone analysis (value: the code's title). Empty until Task 13 checks a real
# download and verifies the codes it uses against LOINC.
STONE_ANALYSIS: dict[tuple[str, str], str] = {}

# A stone analysis result's coded value -> stone_type slug. Empty until Task 13, as above.
STONE_TYPE_VALUES: dict[tuple[str, str], str] = {}

# HL7 v3 ActCode encounter class -> visit_kind slug.
VISIT_CLASS: dict[str, str] = {
    "EMER": "er",           # emergency
    "IMP": "admission",     # inpatient encounter
    "ACUTE": "admission",   # inpatient acute
    "AMB": "office",        # ambulatory
    "OBSENC": "admission",  # observation encounter
}
