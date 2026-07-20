import pytest


def test_cli_builds_single_target(monkeypatch):
    from server.scripts import ingest_games

    captured = {}

    def fake_run(session_factory, *, targets=None):
        captured["targets"] = targets
        return {"targets": 1, "upserted": 3, "failed": 0}

    monkeypatch.setattr(ingest_games, "run_ingest_once", fake_run)
    monkeypatch.setattr(ingest_games, "SessionLocal", lambda: None, raising=False)

    ingest_games.main(["--source", "taptap", "--category", "国风", "--pages", "1"])

    assert captured["targets"][0]["source"] == "taptap"
    assert captured["targets"][0]["category"] == "国风"


def test_cli_exits_nonzero_when_ingest_failed(monkeypatch):
    from server.scripts import ingest_games

    def fake_run(session_factory, *, targets=None):
        return {"targets": 1, "upserted": 0, "failed": 1}

    monkeypatch.setattr(ingest_games, "run_ingest_once", fake_run)
    monkeypatch.setattr(ingest_games, "SessionLocal", lambda: None, raising=False)

    with pytest.raises(SystemExit) as exc:
        ingest_games.main(["--source", "taptap", "--category", "国风"])

    assert exc.value.code == 1
