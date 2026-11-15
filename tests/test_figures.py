import pytest

import figures
from conftest import auth


def _flat_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _flat_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _flat_keys(v)


def test_gaps_are_start_to_start_and_include_open_event(con, mk_event):
    mk_event(con, "2024-01-01", resolved_on="2024-01-05")
    mk_event(con, "2024-03-01")  # open: its start still counts
    assert figures.gaps(["2024-03-01", "2024-01-01"]) == [60]
    assert figures.headline(con, "2024-04-01")["longest_gap_days"] == 60
    assert figures.gaps(["2024-01-01"]) == [] and figures.gaps([]) == []
    assert figures.gaps(["2024-01-01", "2024-01-01"]) == [0]


def test_median_odd_even_and_empty():
    assert figures.median_days([100]) == 100
    assert figures.median_days([100, 201]) == 151
    assert figures.median_days([]) is None
    assert figures.median_days([5, 1, 3]) == 3
    assert figures.median_days([1, 2, 3, 10]) == 3  # (2 + 3) / 2 = 2.5, rounded half up


def test_headline_with_zero_one_and_three_events(con, mk_event):
    h = figures.headline(con, "2024-06-01")
    assert h == {"episodes": 0, "typical_gap_days": None, "shortest_gap_days": None, "longest_gap_days": None,
                 "since_last_days": None, "hospital_nights": 0, "first_started_on": None}
    mk_event(con, "2024-01-01")
    h = figures.headline(con, "2024-06-01")
    assert h["episodes"] == 1 and h["typical_gap_days"] is None and h["longest_gap_days"] is None
    assert h["since_last_days"] == 152 and h["first_started_on"] == "2024-01-01"
    mk_event(con, "2024-02-01")
    mk_event(con, "2024-05-01")
    h = figures.headline(con, "2024-06-01")
    assert (h["episodes"], h["shortest_gap_days"], h["longest_gap_days"]) == (3, 31, 90)
    assert h["typical_gap_days"] == 61  # (31 + 90 + 1) // 2


def test_since_last_uses_latest_start(client, con, mk_event, freeze_today):
    mk_event(con, "2024-05-01")
    mk_event(con, "2023-01-01")
    freeze_today("2024-05-11")
    body = client.get("/api/timeline", headers=auth()).get_json()
    assert body["headline"]["since_last_days"] == 10


def test_since_last_never_negative(con, mk_event):
    mk_event(con, "2024-05-01")
    assert figures.headline(con, "2024-04-01")["since_last_days"] == 0


def test_hospital_nights_sum_only_counting_kinds(con, mk_event, mk_row, list_id):
    a, b = mk_event(con, "2024-01-01"), mk_event(con, "2024-03-01")
    adm, er = list_id(con, "visit_kind", "admission"), list_id(con, "visit_kind", "er")
    mk_row(con, "visits", event_id=a, kind_id=adm, from_date="2024-01-01", to_date="2024-01-03")  # 2
    mk_row(con, "visits", event_id=a, kind_id=er, from_date="2024-01-01", to_date="2024-01-02")   # not counted
    mk_row(con, "visits", event_id=b, kind_id=adm, from_date="2024-03-01", to_date="2024-03-04")  # 3
    mk_row(con, "visits", event_id=b, kind_id=adm, from_date="2024-03-10", to_date=None)          # open: 0
    assert figures.headline(con, "2024-06-01")["hospital_nights"] == 5
    rows = {r["id"]: r for r in figures.timeline(con, "2024-06-01")["events"]}
    assert (rows[a]["nights"], rows[b]["nights"]) == (2, 3)


def test_nights_follow_the_flag_not_the_slug(con, mk_event, mk_row, list_id):
    e = mk_event(con, "2024-01-01")
    er = list_id(con, "visit_kind", "er")
    con.execute("UPDATE lists SET counts_nights = 1 WHERE id = ?", (er,))
    mk_row(con, "visits", event_id=e, kind_id=er, from_date="2024-01-01", to_date="2024-01-02")
    assert figures.headline(con, "2024-06-01")["hospital_nights"] == 1


def test_duration_only_for_resolved(con, mk_event):
    done, still = mk_event(con, "2024-01-01", resolved_on="2024-01-11"), mk_event(con, "2024-03-01")
    rows = {r["id"]: r for r in figures.timeline(con, "2024-06-01")["events"]}
    assert rows[done]["duration_days"] == 10 and rows[done]["open"] is False
    assert rows[still]["duration_days"] is None and rows[still]["open"] is True


def test_timeline_rows_newest_first_with_gap_before(client, con, mk_event, mk_row, list_id):
    old = mk_event(con, "2023-01-01", resolved_on="2023-01-04", outcome="passed")
    mid = mk_event(con, "2023-03-02")
    new = mk_event(con, "2023-05-01")
    mk_row(con, "stones", event_id=old, size_tenths=75, side="left", location_id=None,
           type_id=list_id(con, "stone_type", "uric_acid"))
    mk_row(con, "stones", event_id=old, size_tenths=40, side="left")
    mk_row(con, "stones", event_id=old, size_tenths=None, side="unknown")
    mk_row(con, "stones", event_id=mid, size_tenths=70, side="right")
    mk_row(con, "procedures", event_id=new, kind_id=list_id(con, "procedure_kind", "ureteroscopy"), on_date="2023-05-02")
    mk_row(con, "procedures", event_id=new, kind_id=list_id(con, "procedure_kind", "stent_placement"), on_date="2023-05-01")
    mk_row(con, "complications", event_id=new, kind_id=list_id(con, "complication_kind", "uti"))
    mk_row(con, "visits", event_id=new, kind_id=list_id(con, "visit_kind", "er"), from_date="2023-05-01")
    body = client.get("/api/timeline", headers=auth()).get_json()
    rows = body["events"]
    assert [r["id"] for r in rows] == [new, mid, old]
    assert [r["gap_days_before"] for r in rows] == [60, 60, None]
    assert rows[2]["sizes_mm"] == ["7.5", "4"] and rows[2]["sides"] == ["left"]
    assert rows[2]["types"] == ["Uric acid"]
    assert rows[1]["sizes_mm"] == ["7"] and rows[1]["sides"] == ["right"]
    assert rows[0]["procedures"] == ["Stent placement", "Ureteroscopy"]
    assert rows[0]["complications"] == ["Urinary tract infection"] and rows[0]["visit_kinds"] == ["ER"]
    assert body["headline"]["episodes"] == 3


def test_summary_lists_each_doctor_once(client, con, mk_event, mk_row, list_id):
    e1, e2 = mk_event(con, "2024-01-01"), mk_event(con, "2024-06-01")
    rivera = mk_row(con, "doctors", name="Dr. Rivera", specialty="Urology")
    chen = mk_row(con, "doctors", name="Dr. Chen", specialty=None)
    mk_row(con, "doctors", name="Dr. Unseen")
    for e in (e1, e2):
        mk_row(con, "event_doctors", event_id=e, doctor_id=rivera, role="urologist")
    mk_row(con, "procedures", event_id=e1, kind_id=list_id(con, "procedure_kind", "swl"), doctor_id=rivera)
    mk_row(con, "procedures", event_id=e2, kind_id=list_id(con, "procedure_kind", "swl"), doctor_id=chen)
    body = client.get("/api/summary", headers=auth()).get_json()
    assert body["doctors"] == [{"name": "Dr. Chen", "specialty": None}, {"name": "Dr. Rivera", "specialty": "Urology"}]
    assert [r["id"] for r in body["events"]] == [e2, e1]


def test_empty_database(client):
    for path in ("/api/timeline", "/api/summary"):
        body = client.get(path, headers=auth()).get_json()
        assert body["events"] == [] and body["headline"]["episodes"] == 0


def test_no_projection_keys(client, con, mk_event):
    mk_event(con, "2024-01-01")
    mk_event(con, "2024-03-01")
    for path in ("/api/timeline", "/api/summary"):
        keys = set(_flat_keys(client.get(path, headers=auth()).get_json()))
        assert keys
        for k in keys:
            assert not any(w in k for w in ("next", "expected", "predict", "risk", "forecast", "due")), k


@pytest.mark.parametrize("path", ["/api/timeline", "/api/summary"])
def test_figure_routes_need_identity(client, path):
    assert client.get(path).status_code == 403
