import pytest
from conftest import ALEX, SAM, add_detail, mk_event, mk_row, mut

import details
import figures
from common import ApiError


@pytest.mark.parametrize("raw,tenths", [
    ("7", 70), (7, 70), ("7.5", 75), (7.5, 75), ("0.1", 1), ("", None), (None, None), (" 7.5 ", 75), (".5", 5),
    ("7.", 70), ("200", 2000), ("200.0", 2000), (0.1, 1), ("7.55", 76), ("   ", None)])
def test_parse_size_accepts(raw, tenths):
    assert details.parse_size(raw) == tenths


@pytest.mark.parametrize("raw", ["7,5", "0", "-1", "NaN", "inf", "1e9", "200.1", "7mm", True, False, "0.04", 0, -1,
                                 float("nan"), float("inf"), 1e9, 10**400, "9" * 400, "1e1", "+7", "7 5", [7], {}])
def test_parse_size_refuses(raw):
    with pytest.raises(ApiError) as e:
        details.parse_size(raw)
    assert (e.value.status, e.value.message) == (400, "Size is in mm, like 7 or 7.5.")


def test_size_text_agrees_with_figures():
    assert all(details._size_mm(t) == figures._size_mm(t) for t in range(1, 2001))
    assert details._size_mm(None) is None


def stone(client, eid, **data):
    return add_detail(client, eid, "stones", **data)


@pytest.mark.parametrize("typed,tenths,mm", [("7", 70, "7"), ("7.5", 75, "7.5"), (7.5, 75, "7.5"), (7, 70, "7")])
def test_stone_size_through_the_api(client, con, typed, tenths, mm):
    eid = mk_event(con, "2024-03-01")
    [s] = stone(client, eid, size_mm=typed).get_json()["stones"]
    assert (s["size_tenths"], s["size_mm"]) == (tenths, mm)
    assert con.execute("SELECT size_tenths FROM stones").fetchone()[0] == tenths


@pytest.mark.parametrize("typed", ["7,5", "0", "-1", "NaN", "1e9", "inf", 0, -1, 1e9, "abc"])
def test_bad_stone_size_is_400_and_stores_nothing(client, con, typed):
    eid = mk_event(con, "2024-03-01")
    r = stone(client, eid, size_mm=typed)
    assert (r.status_code, r.get_json()) == (400, {"error": "Size is in mm, like 7 or 7.5."})
    assert con.execute("SELECT COUNT(*) FROM stones").fetchone()[0] == 0


def test_bad_size_on_patch_is_400_and_keeps_the_old_one(client, con):
    eid = mk_event(con, "2024-03-01")
    sid = stone(client, eid, size_mm="7.5").get_json()["stones"][0]["id"]
    assert client.patch(f"/api/stones/{sid}", json={"size_mm": "7,5"}, headers=mut()).status_code == 400
    r = client.patch(f"/api/stones/{sid}", json={"size_mm": ""}, headers=mut())
    assert r.get_json()["stones"][0]["size_mm"] is None


def test_several_stones_on_one_event(client, con, list_id):
    eid = mk_event(con, "2024-03-01")
    kidney = list_id(con, "location", "kidney")
    stone(client, eid, size_mm="4", side="left", location_id=kidney)
    stone(client, eid, size_mm="7.5", side="right", type_id=list_id(con, "stone_type", "uric_acid"),
          type_from_lab=True)
    e = stone(client, eid, side="both").get_json()
    assert [s["size_mm"] for s in e["stones"]] == ["4", "7.5", None]
    assert e["stones"][0]["location"] == {"id": kidney, "label": "Kidney"}
    assert e["stones"][1]["type"]["label"] == "Uric acid" and e["stones"][1]["type_from_lab"] is True
    assert e["stones"][2]["side"] == "both" and e["stones"][2]["from_import"] is False


def test_stone_defaults_and_bad_side(client, con):
    eid = mk_event(con, "2024-03-01")
    [s] = stone(client, eid).get_json()["stones"]
    assert (s["side"], s["type_from_lab"], s["location"], s["type"], s["size_mm"]) == ("unknown", False, None, None, None)
    for bad in ("up", None, 1):
        assert stone(client, eid, side=bad).status_code == 400
    assert stone(client, eid, type_from_lab="yes").status_code == 400


def visit(client, eid, **data):
    return add_detail(client, eid, "visits", **data)


def test_visit_nights_counted_only_for_admission(client, con, list_id):
    eid = mk_event(con, "2024-03-01")
    visit(client, eid, kind_id=list_id(con, "visit_kind", "er"), from_date="2024-03-01", to_date="2024-03-01")
    visit(client, eid, kind_id=list_id(con, "visit_kind", "admission"), from_date="2024-03-01", to_date="2024-03-03")
    e = visit(client, eid, kind_id=list_id(con, "visit_kind", "admission"), from_date="2024-03-05").get_json()
    assert [(v["kind"]["label"], v["nights"]) for v in e["visits"]] == \
        [("ER", None), ("Hospital admission", 2), ("Hospital admission", None)]


def test_nights_follow_the_flag_not_the_slug(client, con, list_id):
    eid = mk_event(con, "2024-03-01")
    con.execute("UPDATE lists SET counts_nights = 1 WHERE id = ?", (list_id(con, "visit_kind", "office"),))
    e = visit(client, eid, kind_id=list_id(con, "visit_kind", "office"), from_date="2024-03-01",
              to_date="2024-03-02").get_json()
    assert e["visits"][0]["nights"] == 1


def test_visit_end_before_start_is_400(client, con, list_id):
    eid = mk_event(con, "2024-03-01")
    kind = list_id(con, "visit_kind", "er")
    r = visit(client, eid, kind_id=kind, from_date="2024-03-05", to_date="2024-03-04")
    assert (r.status_code, r.get_json()["error"]) == (400, "The end date is before the start date.")
    vid = visit(client, eid, kind_id=kind, from_date="2024-03-05").get_json()["visits"][0]["id"]
    assert client.patch(f"/api/visits/{vid}", json={"to_date": "2024-03-01"}, headers=mut()).status_code == 400
    assert client.patch(f"/api/visits/{vid}", json={"from_date": "2024-03-06", "to_date": "2024-03-06"},
                        headers=mut()).status_code == 200
    # moving only the start past the stored end is refused too
    assert client.patch(f"/api/visits/{vid}", json={"from_date": "2024-03-07"}, headers=mut()).status_code == 400


@pytest.mark.parametrize("bad", ["2025-02-30", "25/02/2025", "2025-2-3", "20250228", 5, "tomorrow"])
def test_bad_dates_are_400_with_a_readable_message(client, con, list_id, bad):
    eid = mk_event(con, "2024-03-01")
    kind = list_id(con, "visit_kind", "er")
    r = visit(client, eid, kind_id=kind, from_date=bad)
    assert (r.status_code, r.get_json()["error"]) == (400, "From date must be a date like 2024-03-01.")
    r = add_detail(client, eid, "followups", what="CT scan", on_date=bad)
    assert (r.status_code, r.get_json()["error"]) == (400, "Date must be a date like 2024-03-01.")
    assert con.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 0


def test_visit_needs_kind_and_from_date(client, con, list_id):
    eid = mk_event(con, "2024-03-01")
    assert visit(client, eid, from_date="2024-03-01").status_code == 400
    assert visit(client, eid, kind_id=list_id(con, "visit_kind", "er")).status_code == 400
    assert visit(client, eid, kind_id=list_id(con, "visit_kind", "er"), from_date=None).status_code == 400


def test_dates_inside_an_event_are_free(client, con, list_id, freeze_today):
    freeze_today("2024-03-01")
    eid = mk_event(con, "2024-03-01")
    e = add_detail(client, eid, "followups", what="CT scan", on_date="2024-09-01").get_json()
    assert e["followups"][0]["on_date"] == "2024-09-01"
    assert visit(client, eid, kind_id=list_id(con, "visit_kind", "er"), from_date="2023-01-01").status_code == 201


def test_wrong_kind_list_item_is_400(client, con, list_id):
    eid = mk_event(con, "2024-03-01")
    cases = (("procedures", "kind_id", "stone_type", "uric_acid"), ("procedures", "kind_id", "visit_kind", "er"),
             ("visits", "kind_id", "procedure_kind", "swl"), ("complications", "kind_id", "outcome", "passed"),
             ("stones", "location_id", "stone_type", "mixed"), ("stones", "type_id", "location", "kidney"))
    for kind, key, list_kind, slug in cases:
        extra = {"from_date": "2024-03-01"} if kind == "visits" else {}
        r = add_detail(client, eid, kind, **{key: list_id(con, list_kind, slug)}, **extra)
        assert r.status_code == 400, (kind, key)
        assert "from the list" in r.get_json()["error"]
    for bad in (99999, "5", True, 1.5):
        assert add_detail(client, eid, "procedures", kind_id=bad).status_code == 400


def test_kind_is_required_and_not_clearable(client, con, list_id):
    eid = mk_event(con, "2024-03-01")
    assert add_detail(client, eid, "procedures").status_code == 400
    pid = add_detail(client, eid, "procedures", kind_id=list_id(con, "procedure_kind", "swl")) \
        .get_json()["procedures"][0]["id"]
    assert client.patch(f"/api/procedures/{pid}", json={"kind_id": None}, headers=mut()).status_code == 400


def test_retired_item_kept_but_not_newly_chosen(client, con, list_id):
    eid = mk_event(con, "2024-03-01")
    swl, stent = list_id(con, "procedure_kind", "swl"), list_id(con, "procedure_kind", "stent_placement")
    pid = add_detail(client, eid, "procedures", kind_id=swl).get_json()["procedures"][0]["id"]
    con.execute("UPDATE lists SET retired = 1 WHERE id IN (?, ?)", (swl, stent))
    # a new row may not choose a retired item
    assert add_detail(client, eid, "procedures", kind_id=swl).status_code == 400
    # the existing row keeps it, and can be edited around it
    r = client.patch(f"/api/procedures/{pid}", json={"kind_id": swl, "note": "done"}, headers=mut())
    assert r.status_code == 200 and r.get_json()["procedures"][0]["kind"]["id"] == swl
    # but it cannot be switched to another retired item
    assert client.patch(f"/api/procedures/{pid}", json={"kind_id": stent}, headers=mut()).status_code == 400
    # and it still reads back with its label
    assert client.get(f"/api/events/{eid}", headers=mut()).get_json()["procedures"][0]["kind"]["label"] \
        == "Shock-wave lithotripsy"


def test_procedure_complication_and_followup_with_doctor(client, con, list_id):
    eid = mk_event(con, "2024-03-01")
    rivera = mk_row(con, "doctors", name="Dr. Rivera", specialty="Urology")
    add_detail(client, eid, "procedures", kind_id=list_id(con, "procedure_kind", "ureteroscopy"),
               on_date="2024-03-02", doctor_id=rivera, note="  with stent  ")
    add_detail(client, eid, "complications", kind_id=list_id(con, "complication_kind", "uti"), on_date="2024-03-03")
    e = add_detail(client, eid, "followups", what="Stent removal", on_date="2024-03-20", doctor_id=rivera) \
        .get_json()
    assert e["procedures"][0]["doctor"] == {"id": rivera, "name": "Dr. Rivera"}
    assert e["procedures"][0]["note"] == "with stent"
    assert e["complications"][0]["kind"]["label"] == "Urinary tract infection"
    assert (e["followups"][0]["what"], e["followups"][0]["done"]) == ("Stent removal", False)
    fid = e["followups"][0]["id"]
    e = client.patch(f"/api/followups/{fid}", json={"done": True, "doctor_id": None}, headers=mut()).get_json()
    assert (e["followups"][0]["done"], e["followups"][0]["doctor"]) == (True, None)
    assert add_detail(client, eid, "followups", what="  ").status_code == 400
    assert add_detail(client, eid, "followups", what="x", doctor_id=9999).status_code == 400


def test_unknown_detail_kind_is_404(client, con):
    eid = mk_event(con, "2024-03-01")
    assert add_detail(client, eid, "doctors").status_code == 404
    assert client.patch("/api/widgets/1", json={}, headers=mut()).status_code == 404
    assert client.delete("/api/widgets/1", headers=mut()).status_code == 404


def test_missing_event_or_row_is_404_and_bad_body_is_400(client, con):
    assert add_detail(client, 999, "stones").status_code == 404
    assert client.patch("/api/stones/999", json={}, headers=mut()).status_code == 404
    assert client.delete("/api/stones/999", headers=mut()).status_code == 404
    eid = mk_event(con, "2024-03-01")
    r = client.post(f"/api/events/{eid}/stones", data="[1]", headers=mut())
    assert r.status_code == 400


def test_event_get_includes_details_and_doctors(client, con, list_id):
    eid = mk_event(con, "2024-03-01")
    e = client.get(f"/api/events/{eid}", headers=mut()).get_json()
    assert all(e[k] == [] for k in ("stones", "visits", "procedures", "complications", "followups", "doctors"))
    rivera = mk_row(con, "doctors", name="Dr. Rivera", specialty="Urology", practice="Example Clinic")
    stone(client, eid, size_mm="5")
    r = client.put(f"/api/events/{eid}/doctors/{rivera}", json={"role": "urologist"}, headers=mut())
    assert r.status_code == 200
    e = r.get_json()
    assert e["doctors"] == [{"id": rivera, "name": "Dr. Rivera", "specialty": "Urology",
                             "practice": "Example Clinic", "retired": False, "role": "urologist"}]
    assert e["stones"][0]["size_mm"] == "5" and e["notes"] == []
    # events.get has the same shape in every event route
    assert client.patch(f"/api/events/{eid}", json={}, headers=mut()).get_json()["stones"][0]["size_mm"] == "5"


def test_link_doctor_changes_role_and_unlinks(client, con):
    eid = mk_event(con, "2024-03-01")
    chen = mk_row(con, "doctors", name="Dr. Chen")
    client.put(f"/api/events/{eid}/doctors/{chen}", json={"role": "ER"}, headers=mut())
    e = client.put(f"/api/events/{eid}/doctors/{chen}", json={}, headers=mut()).get_json()
    assert [(d["id"], d["role"]) for d in e["doctors"]] == [(chen, None)]
    assert client.put(f"/api/events/{eid}/doctors/999", json={}, headers=mut()).status_code == 404
    assert client.put(f"/api/events/999/doctors/{chen}", json={}, headers=mut()).status_code == 404
    assert client.delete(f"/api/events/{eid}/doctors/{chen}", headers=mut()).status_code == 204
    assert client.delete(f"/api/events/{eid}/doctors/{chen}", headers=mut()).status_code == 404
    assert client.get(f"/api/events/{eid}", headers=mut()).get_json()["doctors"] == []


def test_detail_write_bumps_event_updated(client, con, clock, list_id):
    eid = mk_event(con, "2024-03-01")
    chen = mk_row(con, "doctors", name="Dr. Chen")

    def stamp():
        r = con.execute("SELECT updated_by, updated_at FROM events WHERE id = ?", (eid,)).fetchone()
        return r["updated_by"], r["updated_at"]

    seen = [stamp()]
    clock.advance(10)
    sid = stone(client, eid).get_json()["stones"][0]["id"]
    seen.append(stamp())
    clock.advance(10)
    client.patch(f"/api/stones/{sid}", json={"side": "left"}, headers=mut("sam@example.com"))
    seen.append(stamp())
    clock.advance(10)
    client.put(f"/api/events/{eid}/doctors/{chen}", json={}, headers=mut())
    seen.append(stamp())
    clock.advance(10)
    client.delete(f"/api/events/{eid}/doctors/{chen}", headers=mut("sam@example.com"))
    seen.append(stamp())
    clock.advance(10)
    client.delete(f"/api/stones/{sid}", headers=mut())
    seen.append(stamp())
    times = [t for _, t in seen]
    assert times == sorted(set(times)) and len(times) == 6
    assert [u for u, _ in seen[2:]] == ["sam@example.com", "alex@example.com", "sam@example.com", "alex@example.com"]


def test_a_refused_write_does_not_bump_the_event(client, con, clock):
    eid = mk_event(con, "2024-03-01")
    before = tuple(con.execute("SELECT updated_by, updated_at FROM events").fetchone())
    clock.advance(10)
    assert stone(client, eid, size_mm="7,5").status_code == 400
    assert tuple(con.execute("SELECT updated_by, updated_at FROM events").fetchone()) == before


def test_from_import_follows_the_suggestion_id(con, list_id):
    eid = mk_event(con, "2024-03-01")
    imp = mk_row(con, "imports", created_by="alex@example.com", created_at=1, documents=0, new=0, updated=0, same=0,
                 skipped_deleted=0, failed=0, suggestions=1)
    sug = mk_row(con, "suggestions", key="k", import_id=imp, kind="visit", payload="{}")
    kind = list_id(con, "visit_kind", "er")
    details.add(con, ALEX, eid, "visits", {"kind_id": kind, "from_date": "2024-03-01"}, suggestion_id=sug)
    details.add(con, SAM, eid, "visits", {"kind_id": kind, "from_date": "2024-03-02"})
    assert [v["from_import"] for v in details.for_event(con, eid)["visits"]] == [True, False]


def test_add_works_inside_a_caller_transaction(con, list_id):
    import db
    eid = mk_event(con, "2024-03-01")
    with db.transaction(con):
        details.add(con, ALEX, eid, "stones", {"size_mm": "3"})
        details.link_doctor(con, eid, mk_row(con, "doctors", name="Dr. Chen"))
    assert len(details.for_event(con, eid)["stones"]) == 1
    with pytest.raises(ApiError), db.transaction(con):
        details.add(con, ALEX, eid, "stones", {"size_mm": "4"})
        details.add(con, ALEX, eid, "stones", {"size_mm": "bad"})
    assert len(details.for_event(con, eid)["stones"]) == 1


def test_deleting_the_event_removes_its_details(client, con, list_id):
    eid = mk_event(con, "2024-03-01")
    stone(client, eid, size_mm="3")
    visit(client, eid, kind_id=list_id(con, "visit_kind", "er"), from_date="2024-03-01")
    assert client.delete(f"/api/events/{eid}", headers=mut()).status_code == 204
    assert con.execute("SELECT (SELECT COUNT(*) FROM stones) + (SELECT COUNT(*) FROM visits)").fetchone()[0] == 0
