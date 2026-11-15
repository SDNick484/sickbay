import pytest
from conftest import mk_event, mk_row, mut, post_event

import events
from common import ApiError


def patch(client, event_id, email="alex@example.com", **data):
    return client.patch(f"/api/events/{event_id}", json=data, headers=mut(email))


def test_create_with_defaults_is_open_and_dated_today(client, freeze_today):
    freeze_today("2024-03-01")
    r = post_event(client)
    assert r.status_code == 201
    e = r.get_json()
    assert (e["started_on"], e["resolved_on"], e["open"], e["outcome"], e["notes"]) == \
        ("2024-03-01", None, True, None, [])
    assert e["created_by"] == "alex@example.com"


def test_create_with_note_adds_first_note_with_author(client):
    e = post_event(client, "sam@example.com", started_on="2024-02-01", note="  Pain since morning.  ").get_json()
    [n] = e["notes"]
    assert (n["body"], n["author"], n["author_name"]) == ("Pain since morning.", "sam@example.com", "Sam")


def test_resolve_sets_date_and_outcome(client, con, list_id):
    eid = post_event(client, started_on="2024-02-01").get_json()["id"]
    passed = list_id(con, "outcome", "passed")
    r = patch(client, eid, resolved_on="2024-02-10", outcome_id=passed)
    e = r.get_json()
    assert (e["open"], e["resolved_on"], e["outcome"]) == (False, "2024-02-10", {"id": passed, "label": "Passed on its own"})
    assert e["updated_by"] == "alex@example.com"


def test_outcome_must_be_an_outcome_item(client, con, list_id):
    eid = mk_event(con, "2024-02-01", "2024-02-02")
    for bad in (list_id(con, "location", "kidney"), 99999, "passed", True):
        assert patch(client, eid, outcome_id=bad).status_code == 400


def test_outcome_needs_a_resolved_event(client, con, list_id):
    eid = mk_event(con, "2024-02-01")
    r = patch(client, eid, outcome_id=list_id(con, "outcome", "passed"))
    assert r.status_code == 400 and "resolved" in r.get_json()["error"]


def test_retired_outcome_only_kept_not_newly_chosen(client, con, list_id):
    other = list_id(con, "outcome", "other")
    kept = mk_event(con, "2024-02-01", "2024-02-02", "other")
    fresh = mk_event(con, "2024-03-01", "2024-03-02")
    con.execute("UPDATE lists SET retired = 1 WHERE id = ?", (other,))
    assert patch(client, kept, outcome_id=other).status_code == 200
    assert patch(client, fresh, outcome_id=other).status_code == 400


def test_reopen_clears_outcome(client, con):
    eid = mk_event(con, "2024-02-01", "2024-02-10", "passed")
    e = patch(client, eid, resolved_on=None).get_json()
    assert (e["open"], e["resolved_on"], e["outcome"]) == (True, None, None)


def test_reopen_with_an_outcome_in_the_same_request_is_400(client, con, list_id):
    eid = mk_event(con, "2024-02-01", "2024-02-10", "passed")
    assert patch(client, eid, resolved_on=None, outcome_id=list_id(con, "outcome", "passed")).status_code == 400


def test_end_before_start_is_400(client, con):
    eid = mk_event(con, "2024-02-10")
    r = patch(client, eid, resolved_on="2024-02-09")
    assert (r.status_code, r.get_json()["error"]) == (400, "The end date is before the start date.")
    r = patch(client, mk_event(con, "2024-02-10", "2024-02-12"), started_on="2024-02-13")
    assert r.status_code == 400  # moving the start past the stored end


def test_future_start_is_400_but_tomorrow_is_allowed(client, freeze_today):
    freeze_today("2024-03-01")
    assert post_event(client, started_on="2024-03-02").status_code == 201
    r = post_event(client, started_on="2024-03-03")
    assert (r.status_code, r.get_json()["error"]) == (400, "The start date is in the future.")
    eid = post_event(client, started_on="2024-03-01").get_json()["id"]
    assert patch(client, eid, started_on="2024-03-03").status_code == 400


def test_old_event_stays_editable_after_its_start_is_unchanged(client, con, freeze_today):
    freeze_today("2024-03-01")
    eid = mk_event(con, "2024-03-10")  # already "in the future" relative to the frozen day
    assert patch(client, eid, resolved_on="2024-03-11").status_code == 200


@pytest.mark.parametrize("value", ["2025-02-30", "25/02/2025", "2025-2-3", "20250228", "yesterday", 20250228, True, "0024-01-01"])
def test_dates_typed_on_a_phone_are_400_with_a_readable_message(client, con, value):
    r = post_event(client, started_on=value)
    assert r.status_code == 400 and r.get_json()["error"] == "Start date must be a date like 2024-03-01."
    r = patch(client, mk_event(con, "2024-01-01"), resolved_on=value)
    assert r.status_code == 400 and r.get_json()["error"] == "End date must be a date like 2024-03-01."


def test_start_date_cannot_be_cleared(client, con):
    assert patch(client, mk_event(con, "2024-01-01"), started_on=None).status_code == 400


def test_check_dates_unit(freeze_today):
    freeze_today("2024-03-01")
    events.check_dates("2024-03-02", None)
    events.check_dates("2024-02-01", "2024-02-01")
    events.check_dates(None, None)
    with pytest.raises(ApiError):
        events.check_dates("2024-03-03", None)


def test_list_puts_open_first_then_newest(client, con):
    old_open = mk_event(con, "2023-01-01")
    new_open = mk_event(con, "2024-01-01")
    resolved = mk_event(con, "2025-01-01", "2025-01-05")
    older = mk_event(con, "2022-01-01", "2022-01-02")
    r = client.get("/api/events", headers=mut())
    assert [e["id"] for e in r.get_json()] == [new_open, old_open, resolved, older]
    assert r.get_json()[0] == {"id": new_open, "started_on": "2024-01-01", "resolved_on": None, "open": True}


def test_note_body_required_and_capped_at_10000(client, con):
    eid = mk_event(con, "2024-01-01")
    url = f"/api/events/{eid}/notes"
    for body in ({}, {"body": "  "}, {"body": None}, {"body": 5}, {"body": "x" * 10001}):
        assert client.post(url, json=body, headers=mut()).status_code == 400
    assert client.post(url, json={"body": "x" * 10000}, headers=mut()).status_code == 201


def test_notes_are_oldest_first(client, con, clock):
    eid = mk_event(con, "2024-01-01")
    for body in ("first", "second"):
        client.post(f"/api/events/{eid}/notes", json={"body": body}, headers=mut())
        clock.advance(60)
    e = client.get(f"/api/events/{eid}", headers=mut()).get_json()
    assert [n["body"] for n in e["notes"]] == ["first", "second"]


def test_either_user_may_edit_a_note(client, con, clock):
    eid = mk_event(con, "2024-01-01")
    e = client.post(f"/api/events/{eid}/notes", json={"body": "mine"}, headers=mut("alex@example.com")).get_json()
    nid = e["notes"][0]["id"]
    clock.advance(100)
    r = client.patch(f"/api/notes/{nid}", json={"body": "edited"}, headers=mut("sam@example.com"))
    n = r.get_json()["notes"][0]
    assert (n["body"], n["author"]) == ("edited", "alex@example.com")
    assert n["updated_at"] == n["created_at"] + 100
    assert client.patch(f"/api/notes/{nid}", json={"body": ""}, headers=mut("sam@example.com")).status_code == 400
    assert client.delete(f"/api/notes/{nid}", headers=mut("sam@example.com")).status_code == 204
    assert client.get(f"/api/events/{eid}", headers=mut()).get_json()["notes"] == []


def test_missing_note_is_404(client):
    assert client.patch("/api/notes/999", json={"body": "x"}, headers=mut()).status_code == 404
    assert client.delete("/api/notes/999", headers=mut()).status_code == 404


def test_delete_event_keeps_files_rows(client, con):
    eid = mk_event(con, "2024-01-01")
    client.post(f"/api/events/{eid}/notes", json={"body": "n"}, headers=mut())
    fid = mk_row(con, "files", storage_id="s1", name="report.pdf", mime="application/pdf", size=3, sha256="a" * 64,
                 origin="upload", created_by="alex@example.com", created_at=1)
    mk_row(con, "event_files", event_id=eid, file_id=fid)
    kind = con.execute("SELECT id FROM lists WHERE kind = 'visit_kind' LIMIT 1").fetchone()["id"]
    mk_row(con, "visits", event_id=eid, kind_id=kind, from_date="2024-01-01")
    assert client.delete(f"/api/events/{eid}", headers=mut()).status_code == 204
    for table, n in (("events", 0), ("notes", 0), ("event_files", 0), ("visits", 0), ("files", 1)):
        assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == n


def test_missing_event_is_404(client):
    for call in (client.get("/api/events/999", headers=mut()),
                 client.patch("/api/events/999", json={}, headers=mut()),
                 client.delete("/api/events/999", headers=mut()),
                 client.post("/api/events/999/notes", json={"body": "x"}, headers=mut())):
        assert call.status_code == 404 and call.get_json() == {"error": "No such event."}


def test_note_on_a_missing_event_stores_nothing(client, con):
    client.post("/api/events/999/notes", json={"body": "x"}, headers=mut())
    assert con.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0


def test_a_bad_body_is_400(client):
    assert client.post("/api/events", data="[]", headers=mut()).status_code == 400
