from __future__ import annotations

from app.patrol_points import (
    DWELL_SPEED_NOTE,
    annotate_patrol_points,
    apply_preset_ptz,
    live_set_points_body,
    read_points,
    save_patrol_points,
    write_points,
)


def test_write_read_sidecar(tmp_path, monkeypatch) -> None:
    from app import patrol_points

    monkeypatch.setattr(patrol_points, "POINTS_DIR", tmp_path / "patrol-points")
    written = write_points(
        "cam-1",
        0,
        [
            {"slot": 1, "presetDurationSeconds": 9, "presetMovementSpeed": 220},
            {"slot": 2, "presetDurationSeconds": 0, "presetMovementSpeed": 1000},
        ],
    )
    assert written["patrolSlot"] == 0
    loaded = read_points("cam-1", 0)
    assert loaded["points"][0]["presetDurationSeconds"] == 9
    assert loaded["points"][1]["presetDurationSeconds"] == 0
    assert "pan" not in loaded["points"][0]


def test_sidecar_clamps_and_ignores_ptz(tmp_path, monkeypatch) -> None:
    from app import patrol_points

    monkeypatch.setattr(patrol_points, "POINTS_DIR", tmp_path / "patrol-points")
    written = write_points(
        "cam-1",
        2,
        [{"slot": 3, "presetDurationSeconds": 100000, "presetMovementSpeed": -5, "pan": 12, "tilt": 13}],
    )
    point = written["points"][0]
    assert point["presetDurationSeconds"] == 127
    assert point["presetMovementSpeed"] == 0
    assert set(point) == {"slot", "presetDurationSeconds", "presetMovementSpeed"}
    kept = write_points(
        "cam-1",
        3,
        [{"slot": 1, "presetDurationSeconds": 12, "presetMovementSpeed": 0}],
    )
    assert kept["points"][0]["presetDurationSeconds"] == 12
    assert kept["points"][0]["presetMovementSpeed"] == 0


def test_annotate_merges_sidecar_over_tour(tmp_path, monkeypatch) -> None:
    from app import patrol_points

    monkeypatch.setattr(patrol_points, "POINTS_DIR", tmp_path / "patrol-points")
    write_points("cam-1", 0, [{"slot": 1, "presetDurationSeconds": 4, "presetMovementSpeed": 333}])
    patrols = [
        {
            "id": "p",
            "slot": 0,
            "presetDurationSeconds": 18,
            "presetMovementSpeed": 400,
            "presets": [
                {"slot": 1, "name": "A", "pan": 10},
                {"slot": 2, "name": "B", "pan": 20},
            ],
        }
    ]
    annotated = annotate_patrol_points("cam-1", patrols)
    assert annotated[0]["presets"][0]["presetDurationSeconds"] == 4
    assert annotated[0]["presets"][0]["dwellSource"] == "sidecar"
    assert annotated[0]["presets"][1]["presetDurationSeconds"] == 18
    assert annotated[0]["presets"][1]["dwellSource"] == "tour"
    assert annotated[0]["presets"][0]["pan"] == 10
    assert annotated[0]["pointsSidecar"] is True


def test_save_notes_helper_not_applied(tmp_path, monkeypatch) -> None:
    from app import patrol_points

    monkeypatch.setattr(patrol_points, "POINTS_DIR", tmp_path / "patrol-points")
    result = save_patrol_points(
        "cam-1",
        0,
        [{"slot": 1, "presetDurationSeconds": 6, "presetMovementSpeed": 200, "pan": 1}],
    )
    assert result["dwellSpeed"] == {"saved": "sidecar", "applied": False}
    assert result["ptz"]["saved"] is False
    assert result["note"] == DWELL_SPEED_NOTE
    assert apply_preset_ptz("cam-1", [])["saved"] is False
    assert apply_preset_ptz("cam-1", [])["writable"] is False
    assert "ptzSocket" not in DWELL_SPEED_NOTE
    assert "saved locally" in DWELL_SPEED_NOTE


def test_sanitize_blocks_path_escape(tmp_path, monkeypatch) -> None:
    from app import patrol_points

    monkeypatch.setattr(patrol_points, "POINTS_DIR", tmp_path / "patrol-points")
    write_points("../etc", 0, [{"slot": 0, "presetDurationSeconds": 1, "presetMovementSpeed": 200}])
    kids = list((tmp_path / "patrol-points").iterdir())
    assert kids
    assert ".." not in kids[0].name
    assert "/" not in kids[0].name


def test_live_body_clamps_points() -> None:
    body = live_set_points_body(
        [
            {"slot": 300, "presetDurationSeconds": 500, "presetMovementSpeed": 5000},
            {"preset": 2, "dwellTime": -4, "moveSpeed": 200},
        ]
    )
    assert body == {
        "points": [
            {"preset": 254, "dwellTime": 127, "moveSpeed": 1000},
            {"preset": 2, "dwellTime": 0, "moveSpeed": 200},
        ]
    }
