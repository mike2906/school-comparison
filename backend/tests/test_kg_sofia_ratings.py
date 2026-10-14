"""Sofia ИСОДЗ round lists reduced to cut-off rows (fixtures: real 2026 lists, children anonymised)."""

import json
from datetime import date
from pathlib import Path

import pytest

from app.scrapers.sources.bg.kg_sofia_ratings import (
    aggregate_round,
    group_rounds,
    parse_list,
    rating_date,
    rows_for_round,
    start_offset,
)
from app.services.admission_cutoffs import check_row, iter_rows, write_rows

FIXTURES = Path(__file__).parent / "fixtures" / "kg_sofia_ratings"


def _content(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["content"]


def _payload(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_missing_list_is_none():
    assert parse_list(_content("missing_list")) is None


def test_kindergarten_lists_split_by_queue():
    rated = parse_list(_content("rated_443_7471"))
    assert [section.queue for section in rated] == ["sop", "chronic", "general"]
    general = rated[2]
    assert [entry.points for entry in general.entries] == [19.0, 17.0, 17.0, 16.0, 16.0, 16.0]
    assert [entry.has_random for entry in general.entries] == [False, False, False, True, True, True]


def test_entry_list_reads_places_and_changed_cohort():
    entry = parse_list(_content("entry_443_7471"))
    assert [(section.queue, section.places) for section in entry] == [("sop", 1), ("chronic", 1), ("general", 6)]
    general = entry[2]
    assert len(general.entries) == 16
    assert sum(item.changed_cohort for item in general.entries) == 3


def test_kindergarten_round_aggregate():
    rows = aggregate_round(
        parse_list(_content("rated_443_7471")),
        parse_list(_content("not_rated_443_7471")),
        parse_list(_content("entry_443_7471")),
    )
    by_queue = {row["queue"]: row for row in rows}
    # Empty СОП / chronic queues still had a place each, so they are kept.
    assert by_queue["sop"]["admitted_count"] == 0 and by_queue["sop"]["places"] == 1
    general = by_queue["general"]
    assert general == {
        "queue": "general",
        "places": 6,
        "applicants_count": 11,
        "admitted_count": 6,
        "undersubscribed": False,
        "last_admitted_points": 16.0,
        "tie_at_cutoff": True,
        "not_admitted_at_cutoff": 1,
    }


def test_first_grade_round_aggregate():
    rated = parse_list(_content("rated_31_9463"))
    assert len(rated) == 1 and rated[0].queue == "general"
    first, last = rated[0].entries[0], rated[0].entries[-1]
    assert (first.priority_group, first.points, first.has_random) == (1, 9.0, False)
    assert (last.priority_group, last.points, last.has_random) == (4, 0.0, True)

    (row,) = aggregate_round(rated, None, None)
    assert row["last_admitted_group"] == 4
    assert row["last_admitted_points"] == 0.0
    assert row["tie_at_cutoff"] is True
    assert row["undersubscribed"] is True  # no not-rated list was given


@pytest.mark.parametrize(
    "label,offset",
    [
        ("2-ра ясла, родени 2024 г.", 2),
        ("1-ва ясла, родени 2025 г.", 1),
        ("1-ва група, родени 2023 г.", 3),
        ("2-ра група, родени 2022 г.", 4),
        ("3-та група, родени 2021 г.", 5),
        ("Подготвителна група, родени 2020 г.", 6),
        ("1-ви клас, родени 2019 г.", 7),
        ("Смесена група", None),
    ],
)
def test_start_offset(label, offset):
    assert start_offset(label) == offset


def test_rating_date_is_sofia_local():
    assert rating_date("2026-09-03T21:00:00.000+00:00") == date(2026, 9, 4)


def test_kindergarten_groups_get_stage_campaign_and_age_group():
    rounds = group_rounds(_payload("dz_0_171"), "kindergarten")
    assert len(rounds) == 15  # 5 groups x 3 rounds
    by_group = {(r.group_id, r.rating_id): r for r in rounds}
    first = by_group[(7471, 443)]
    assert (first.stage, first.campaign, first.age_group, first.classified_on) == (
        "kindergarten", "2026/2027", "first", date(2026, 9, 4),
    )
    nursery = by_group[(9006, 443)]
    assert (nursery.stage, nursery.age_group) == ("nursery", "nursery")
    preschool = by_group[(6527, 445)]
    assert (preschool.stage, preschool.age_group) == ("kindergarten", "preschool")


def test_first_grade_groups():
    rounds = group_rounds(_payload("dz_1_629"), "grade_1")
    assert [r.rating_id for r in rounds] == [31, 32, 33, 34, 35, 36]
    assert {(r.stage, r.campaign, r.age_group) for r in rounds} == {("grade_1", "2026/2027", None)}
    assert rounds[0].classified_on == date(2026, 6, 2)


def test_group_outside_the_published_school_years_is_skipped():
    payload = _payload("dz_0_171")
    payload["items"]["dz"]["currentGroups"][1]["draft"] = 2015  # would be campaign 2018/2019
    assert all(r.group_id != 7471 for r in group_rounds(payload, "kindergarten"))


def test_rows_carry_labels_and_no_child_data(tmp_path):
    (round_,) = [r for r in group_rounds(_payload("dz_0_171"), "kindergarten") if (r.group_id, r.rating_id) == (7471, 443)]
    aggregates = aggregate_round(
        parse_list(_content("rated_443_7471")),
        parse_list(_content("not_rated_443_7471")),
        parse_list(_content("entry_443_7471")),
    )
    rows = rows_for_round(round_, aggregates, "2026-10-14T09:00:00+00:00")
    for row in rows:
        check_row(row)
    general = next(row for row in rows if row["queue"] == "general")
    assert general["rule_set"] == "bg/sofia/kindergarten/2026-2027"
    assert general["source_id"] == "171" and general["district"] == "Възраждане"
    assert "XX" not in json.dumps(rows, ensure_ascii=False)

    (path,) = write_rows(tmp_path, rows)
    assert path.relative_to(tmp_path).as_posix() == "bg/sofia/kindergarten/2026-2027/round-443.json"
    # Re-writing the same rows replaces them rather than duplicating.
    write_rows(tmp_path, rows)
    assert len(list(iter_rows(tmp_path))) == len(rows)


def test_check_row_rejects_per_child_fields():
    with pytest.raises(ValueError):
        check_row({"country": "bg", "city": "sofia", "stage": "kindergarten", "campaign": "2026/2027",
                   "round_id": 1, "source": "kg.sofia.bg", "source_id": "1", "queue": "general",
                   "initials": "АБ"})
