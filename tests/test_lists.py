import pytest
from conftest import auth, mut

import lists
from common import ApiError


def get(client, path, **kw):
    return client.get(path, headers=auth(), **kw)


def send(client, method, path, body):
    return client.open(path, method=method, json=body, headers=mut())


def test_lists_returns_seeded_kinds_in_order(client):
    data = get(client, "/api/lists").get_json()
    assert list(data) == list(lists.KINDS) and len(lists.KINDS) == 6
    assert [i["slug"] for i in data["outcome"]] == ["passed", "removed", "other"]
    first = data["location"][0]
    assert set(first) == {"id", "slug", "label", "sort_order", "retired", "counts_nights"}
    assert first["retired"] is False
    nights = {i["slug"]: i["counts_nights"] for i in data["visit_kind"]}
    assert nights["admission"] is True and nights["er"] is False


def test_add_appends_and_rejects_duplicate_label_in_kind_with_409(client):
    r = send(client, "POST", "/api/lists/location", {"label": "  Renal pelvis "})
    assert r.status_code == 201
    items = get(client, "/api/lists").get_json()["location"]
    assert items[-1]["label"] == "Renal pelvis" and items[-1]["id"] == r.get_json()["id"] and items[-1]["slug"] is None
    assert send(client, "POST", "/api/lists/location", {"label": "Kidney"}).status_code == 409
    assert send(client, "POST", "/api/lists/outcome", {"label": "Kidney"}).status_code == 201  # other kind is fine
    assert send(client, "POST", "/api/lists/location", {"label": "  "}).status_code == 400


def test_add_rejects_unknown_kind_with_404(client):
    assert send(client, "POST", "/api/lists/colour", {"label": "Red"}).status_code == 404


def test_retired_item_stays_listed_with_flag(client, con, list_id):
    item = list_id(con, "location", "bladder")
    assert send(client, "PATCH", f"/api/lists/items/{item}", {"retired": True}).status_code == 200
    items = {i["id"]: i for i in get(client, "/api/lists").get_json()["location"]}
    assert items[item]["retired"] is True
    assert send(client, "PATCH", f"/api/lists/items/{item}", {"retired": "yes"}).status_code == 400


def test_rename_reorder_and_rename_clash(client, con, list_id):
    kidney, ureter = list_id(con, "location", "kidney"), list_id(con, "location", "ureter")
    assert send(client, "PATCH", f"/api/lists/items/{kidney}", {"label": "Renal", "sort_order": 99}).status_code == 200
    items = get(client, "/api/lists").get_json()["location"]
    assert items[-1]["id"] == kidney and items[-1]["label"] == "Renal"
    assert send(client, "PATCH", f"/api/lists/items/{ureter}", {"label": "Renal"}).status_code == 409
    assert send(client, "PATCH", f"/api/lists/items/{ureter}", {"sort_order": 1.5}).status_code == 400
    assert send(client, "PATCH", f"/api/lists/items/{ureter}", {"label": ""}).status_code == 400
    assert send(client, "PATCH", "/api/lists/items/99999", {"label": "x"}).status_code == 404


def test_counts_nights_only_settable_on_visit_kind(client, con, list_id):
    er = list_id(con, "visit_kind", "er")
    assert send(client, "PATCH", f"/api/lists/items/{er}", {"counts_nights": True}).status_code == 200
    assert {i["slug"]: i["counts_nights"] for i in get(client, "/api/lists").get_json()["visit_kind"]}["er"] is True
    other = list_id(con, "outcome", "passed")
    r = send(client, "PATCH", f"/api/lists/items/{other}", {"counts_nights": True})
    assert r.status_code == 400 and r.get_json()["error"]


def test_slug_is_never_set_by_api(client, con, list_id):
    item = list_id(con, "outcome", "passed")
    send(client, "PATCH", f"/api/lists/items/{item}", {"slug": "hacked", "kind": "location", "label": "Passed"})
    assert con.execute("SELECT slug, kind FROM lists WHERE id = ?", (item,)).fetchone()[:] == ("passed", "outcome")
    new = send(client, "POST", "/api/lists/outcome", {"label": "Moved", "slug": "x"}).get_json()["id"]
    assert con.execute("SELECT slug FROM lists WHERE id = ?", (new,)).fetchone()[0] is None


def test_doctor_crud(client):
    r = send(client, "POST", "/api/doctors", {"name": "Dr. Rivera", "specialty": "Urology", "practice": "Example Clinic"})
    assert r.status_code == 201
    did = r.get_json()["id"]
    send(client, "POST", "/api/doctors", {"name": "Dr. Chen"})
    names = [d["name"] for d in get(client, "/api/doctors").get_json()]
    assert names == ["Dr. Chen", "Dr. Rivera"]
    assert send(client, "PATCH", f"/api/doctors/{did}", {"specialty": None, "retired": True}).status_code == 200
    assert [d["name"] for d in get(client, "/api/doctors").get_json()] == ["Dr. Chen"]
    everyone = {d["id"]: d for d in get(client, "/api/doctors?all=1").get_json()}
    assert everyone[did] == {"id": did, "name": "Dr. Rivera", "specialty": None, "practice": "Example Clinic", "retired": True}
    assert client.delete(f"/api/doctors/{did}", headers=mut()).status_code == 200
    assert client.delete(f"/api/doctors/{did}", headers=mut()).status_code == 404
    assert send(client, "PATCH", f"/api/doctors/{did}", {"name": "x"}).status_code == 404


def test_doctor_name_required_and_trimmed(client):
    assert send(client, "POST", "/api/doctors", {"name": "   "}).status_code == 400
    assert send(client, "POST", "/api/doctors", {}).status_code == 400
    assert send(client, "POST", "/api/doctors", {"name": 5}).status_code == 400
    did = send(client, "POST", "/api/doctors", {"name": "  Dr. Chen  ", "specialty": " "}).get_json()["id"]
    d = get(client, "/api/doctors").get_json()[0]
    assert d["name"] == "Dr. Chen" and d["specialty"] is None
    assert send(client, "PATCH", f"/api/doctors/{did}", {"name": ""}).status_code == 400


@pytest.mark.parametrize("table", ["event_doctors", "procedures", "followups"])
def test_delete_referenced_doctor_is_409(client, con, mk_doctor, mk_row, mk_event, list_id, table):
    did, ev = mk_doctor(con), mk_event(con, "2024-01-01")
    cols = {"event_doctors": {"event_id": ev, "doctor_id": did},
            "procedures": {"event_id": ev, "kind_id": list_id(con, "procedure_kind", "other"), "doctor_id": did},
            "followups": {"event_id": ev, "what": "CT scan", "doctor_id": did}}[table]
    mk_row(con, table, **cols)
    r = client.delete(f"/api/doctors/{did}", headers=mut())
    assert r.status_code == 409 and r.get_json()["error"] == "This doctor is on an event. Retire them instead."
    assert con.execute("SELECT COUNT(*) FROM doctors WHERE id = ?", (did,)).fetchone()[0] == 1


def test_find_doctor_ignores_case_and_spaces(con, mk_doctor):
    did = mk_doctor(con, "Dr. Rivera")
    mk_doctor(con, "Dr. Chen")
    assert lists.find_doctor(con, "dr.  rivera") == did
    assert lists.find_doctor(con, "  DR. RIVERA ") == did
    assert lists.find_doctor(con, "Dr. Rive") is None
    assert lists.find_doctor(con, "   ") is None and lists.find_doctor(con, None) is None


def test_module_functions_raise_api_errors(con):
    with pytest.raises(ApiError) as e:
        lists.add(con, "nope", "x")
    assert e.value.status == 404
    assert lists.add(con, "location", "Renal pelvis") > 0


def test_lists_routes_need_identity(client):
    assert client.get("/api/lists").status_code == 403
    assert client.get("/api/doctors").status_code == 403
