"""Request-body helpers: json_body, text, parse_date, today, now and the multipart marker. Review Focus 4 (dates
typed on a phone) for the date half: each bad shape is a 400 with a message that names the field."""
import re
import time
from datetime import date

import pytest

import common
from common import ApiError


@pytest.mark.parametrize("value", ["2025-02-30", "25/02/2025", "2025-2-3", "", "2025-02-28x", 20250228])
def test_parse_date_refuses(value):
    with pytest.raises(ApiError) as e:
        common.parse_date(value, field="Start date", required=True)
    assert e.value.status == 400
    assert e.value.message in ("Start date must be a date like 2024-03-01.", "Start date is required.")


@pytest.mark.parametrize("value", ["2025-02-30", "2023-02-29", "2024-13-01", "2024-04-31", "2024-00-10", "2024-01-00",
                                   "25/02/2025", "02/25/2025", "2025-2-3", "2025-02-28x", "2025-02-28T10:00", "20250228",
                                   "2025-W09-5", "2025-059", "+2025-02-28", "２０２５-０２-２８", "0024-03-01", "1899-12-31",
                                   20250228, 2025.0, True, ["2025-02-28"], {"d": "2025-02-28"}])
def test_parse_date_refuses_with_a_readable_message(value):
    with pytest.raises(ApiError) as e:
        common.parse_date(value, field="Start date")
    assert (e.value.status, e.value.message) == (400, "Start date must be a date like 2024-03-01.")


def test_parse_date_accepts_real_dates():
    assert common.parse_date("2024-02-29", field="d") == "2024-02-29"
    assert common.parse_date("1900-01-01", field="d") == "1900-01-01"
    assert common.parse_date(" 2024-03-01 ", field="d") == "2024-03-01"  # a pasted space is forgiven


def test_parse_date_empty():
    assert common.parse_date(None, field="End date") is None
    assert common.parse_date("", field="End date") is None
    assert common.parse_date("  ", field="End date") is None
    for value in (None, "", "  "):
        with pytest.raises(ApiError) as e:
            common.parse_date(value, field="Start date", required=True)
        assert (e.value.status, e.value.message) == (400, "Start date is required.")


def test_text():
    assert common.text({"a": "  hi  "}, "a") == "hi"
    assert common.text({}, "a") is None
    assert common.text({"a": None}, "a") is None
    assert common.text({"a": "   "}, "a") is None
    assert common.text({"a": "x" * 2000}, "a") == "x" * 2000
    assert common.text({"a": "x" * 50}, "a", max_len=50) == "x" * 50


@pytest.mark.parametrize("value", [1, 1.5, True, ["a"], {"a": 1}])
def test_text_refuses_non_strings(value):
    with pytest.raises(ApiError) as e:
        common.text({"name": value}, "name")
    assert (e.value.status, e.value.message) == (400, "name must be text.")


def test_text_required_and_max_len():
    for data in ({}, {"name": None}, {"name": "  "}):
        with pytest.raises(ApiError) as e:
            common.text(data, "name", required=True)
        assert (e.value.status, e.value.message) == (400, "name is required.")
    with pytest.raises(ApiError) as e:
        common.text({"name": "x" * 2001}, "name")
    assert (e.value.status, e.value.message) == (400, "name is too long (at most 2000 characters).")
    with pytest.raises(ApiError):
        common.text({"body": "x" * 11}, "body", max_len=10)


def test_api_error_carries_extra_keys():
    e = ApiError(409, "Already decided.", state="accepted")
    assert (e.status, e.message, e.extra) == (409, "Already decided.", {"state": "accepted"})


def test_today_and_now():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", common.today()) and common.today() == date.today().isoformat()
    assert isinstance(common.now(), int) and abs(common.now() - time.time()) < 5


def test_clock_and_freeze_today_move_common(clock, freeze_today):
    t = common.now()
    clock.advance(60)
    assert common.now() == t + 60
    freeze_today("2024-03-01")
    assert common.today() == "2024-03-01"


def test_multipart_marks_the_view():
    def view():
        return "x"
    assert common.multipart(view) is view and view.multipart is True
