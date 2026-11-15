"""Figures across events: gaps, headline figures, timeline rows and doctor-summary rows. Read-only SQL over the
schema; no routes here. Everything is history: a count, a median or a span of what happened. Nothing looks forward
(no next date, no expectation, no score), and the keys say so (tests/test_figures.py checks them)."""
from datetime import date


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def gaps(starts: list[str]) -> list[int]:
    """Days between consecutive start dates, oldest first (the input need not be sorted). Start to start: an open
    event's start is known, so it counts. Two events starting the same day are a gap of 0."""
    ordered = sorted(starts)
    return [_days(a, b) for a, b in zip(ordered, ordered[1:])]


def median_days(values: list[int]) -> int | None:
    """The median in whole days; None when there are no values. With an even count, the mean of the middle two,
    rounded half up."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid] + 1) // 2


def _size_mm(tenths: int) -> str:
    """Integer tenths of a mm as text: 70 is "7", 75 is "7.5"."""
    whole, tenth = divmod(tenths, 10)
    return f"{whole}.{tenth}" if tenth else str(whole)


def _by_event(con, sql: str) -> dict[int, list]:
    """Rows of a query whose first column is the event id, grouped by it, in the query's order."""
    out: dict[int, list] = {}
    for row in con.execute(sql):
        out.setdefault(row[0], []).append(row)
    return out


def _nights_by_event(con) -> dict[int, int]:
    """Nights per event: to minus from, over visits of a kind flagged counts_nights (admissions by default). A visit
    with no end date counts nothing yet."""
    rows = con.execute(
        "SELECT v.event_id, v.from_date, v.to_date FROM visits v JOIN lists l ON l.id = v.kind_id "
        "WHERE l.counts_nights = 1 AND v.to_date IS NOT NULL")
    out: dict[int, int] = {}
    for r in rows:
        out[r["event_id"]] = out.get(r["event_id"], 0) + _days(r["from_date"], r["to_date"])
    return out


def _starts(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT started_on FROM events ORDER BY started_on, id")]


def _headline(starts: list[str], nights: int, today: str) -> dict:
    between = gaps(starts)
    return {
        "episodes": len(starts),
        "typical_gap_days": median_days(between),
        "shortest_gap_days": min(between) if between else None,
        "longest_gap_days": max(between) if between else None,
        # today minus the latest start; a start dated after today (a clock moved back) shows as 0, not a negative
        "since_last_days": max(0, _days(max(starts), today)) if starts else None,
        "hospital_nights": nights,
        "first_started_on": min(starts) if starts else None,
    }


def headline(con, today: str) -> dict:
    """{episodes, typical_gap_days, shortest_gap_days, longest_gap_days, since_last_days, hospital_nights,
    first_started_on}. The gap figures are None with fewer than two events, since_last_days and first_started_on
    with none. Hospital nights are the sum over every event."""
    return _headline(_starts(con), sum(_nights_by_event(con).values()), today)


def _rows(con) -> list[dict]:
    """One row per event, newest first, with the gap to the previous (older) event."""
    events = con.execute("SELECT id, started_on, resolved_on FROM events ORDER BY started_on, id").fetchall()
    stones = _by_event(con, (
        "SELECT s.event_id, s.size_tenths, s.side, t.label AS type FROM stones s "
        "LEFT JOIN lists t ON t.id = s.type_id ORDER BY s.event_id, s.id"))
    visits = _by_event(con, (
        "SELECT v.event_id, k.label FROM visits v JOIN lists k ON k.id = v.kind_id "
        "ORDER BY v.event_id, v.from_date, v.id"))
    procedures = _by_event(con, (
        "SELECT p.event_id, k.label FROM procedures p JOIN lists k ON k.id = p.kind_id "
        "ORDER BY p.event_id, p.on_date IS NULL, p.on_date, p.id"))
    complications = _by_event(con, (
        "SELECT c.event_id, k.label FROM complications c JOIN lists k ON k.id = c.kind_id "
        "ORDER BY c.event_id, c.on_date IS NULL, c.on_date, c.id"))
    nights = _nights_by_event(con)
    rows, previous = [], None
    for e in events:
        st = stones.get(e["id"], [])
        types: list[str] = []
        for s in st:
            if s["type"] and s["type"] not in types:
                types.append(s["type"])
        sides: list[str] = []
        for s in st:
            if s["side"] != "unknown" and s["side"] not in sides:
                sides.append(s["side"])
        rows.append({
            "id": e["id"],
            "started_on": e["started_on"],
            "resolved_on": e["resolved_on"],
            "open": e["resolved_on"] is None,
            "duration_days": _days(e["started_on"], e["resolved_on"]) if e["resolved_on"] else None,
            "gap_days_before": _days(previous, e["started_on"]) if previous else None,
            "sizes_mm": [_size_mm(s["size_tenths"]) for s in st if s["size_tenths"] is not None],
            "sides": sides,
            "types": types,
            "visit_kinds": [r[1] for r in visits.get(e["id"], [])],
            "nights": nights.get(e["id"], 0),
            "procedures": [r[1] for r in procedures.get(e["id"], [])],
            "complications": [r[1] for r in complications.get(e["id"], [])],
        })
        previous = e["started_on"]
    rows.reverse()
    return rows


def timeline(con, today: str) -> dict:
    """{"headline": {...}, "events": [row, ...]} with the rows newest first. A row is {id, started_on, resolved_on,
    open, duration_days (resolved events only), gap_days_before (to the previous, older event; None on the oldest),
    sizes_mm (text, e.g. "7.5"), sides (left/right/both, known ones), types, visit_kinds, nights, procedures,
    complications} (list items are the list labels)."""
    rows = _rows(con)
    return {"headline": headline(con, today), "events": rows}


def summary(con, today: str) -> dict:
    """The timeline's headline and rows, plus "doctors": [{name, specialty}], every doctor linked to any event or
    named on any procedure, once each, by name."""
    doctors = con.execute(
        "SELECT name, specialty FROM doctors WHERE id IN "
        "(SELECT doctor_id FROM event_doctors UNION SELECT doctor_id FROM procedures WHERE doctor_id IS NOT NULL) "
        "ORDER BY name COLLATE NOCASE, id").fetchall()
    return {**timeline(con, today),
            "doctors": [{"name": d["name"], "specialty": d["specialty"]} for d in doctors]}
