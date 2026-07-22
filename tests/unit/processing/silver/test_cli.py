"""Silver CLI dispatch, inspection, and destructive guard tests."""

import json
from types import SimpleNamespace

import processing.silver.cli as cli
from processing.silver.models import ProcessingResult, ProcessingStatus


def test_process_cli_accepts_incremental_controls() -> None:
    args = cli.build_parser({}).parse_args(
        [
            "process-cdc",
            "--input-prefix",
            "cdc/",
            "--entity",
            "customers",
            "--from-date",
            "2026-07-01",
            "--to-date",
            "2026-07-31",
            "--max-objects",
            "2",
            "--dry-run",
        ]
    )

    assert args.entity == "customers"
    assert args.max_objects == 2 and args.dry_run


def test_reset_requires_confirmation() -> None:
    assert cli.main(["reset-state"]) == 2


def test_process_commands_dispatch_and_emit_structured_results(monkeypatch, capsys) -> None:
    calls: list[tuple[str, bool, bool]] = []

    class Discovery:
        def discover(self, **_kwargs):
            return (SimpleNamespace(uri="local-input"),)

    class Processor:
        def process_cdc(self, _item, *, force_reprocess: bool, dry_run: bool):
            calls.append(("cdc", force_reprocess, dry_run))
            return ProcessingResult(
                run_id="run-cdc",
                input_object_uri="local-input",
                status=ProcessingStatus.COMPLETED,
                entity_name="customers",
            )

        def process_settlement(self, _item, *, force_reprocess: bool, dry_run: bool):
            calls.append(("settlement", force_reprocess, dry_run))
            return ProcessingResult(
                run_id=None,
                input_object_uri="local-input",
                status=ProcessingStatus.COMPLETED,
                entity_name="settlements",
                dry_run=True,
            )

    runtime = (
        SimpleNamespace(max_objects=10),
        Discovery(),
        Processor(),
        object(),
        object(),
    )
    monkeypatch.setattr(cli, "_runtime", lambda *_args: runtime)

    assert cli.main(["process-cdc", "--input-prefix", "cdc/", "--force-reprocess"]) == 0
    cdc_output = json.loads(capsys.readouterr().out)
    assert cdc_output["discovered"] == 1
    assert cli.main(["process-settlements", "--input-prefix", "settlements/", "--dry-run"]) == 0
    settlement_output = json.loads(capsys.readouterr().out)
    assert settlement_output["results"][0]["dry_run"] is True
    assert calls == [("cdc", True, False), ("settlement", False, True)]


def test_inspect_is_payload_safe_and_reset_removes_only_manifest(
    monkeypatch, capsys, tmp_path
) -> None:
    manifest = object()
    storage = object()
    runtime = (SimpleNamespace(max_objects=1), object(), object(), storage, manifest)
    monkeypatch.setattr(cli, "_runtime", lambda *_args: runtime)
    monkeypatch.setattr(cli, "manifest_summary", lambda value: [{"safe": value is manifest}])
    monkeypatch.setattr(
        cli,
        "parquet_summary",
        lambda value, uri: {"safe": value is storage, "uri": uri},
    )

    assert cli.main(["inspect", "--object-uri", "local.parquet"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected == {
        "parquet": {"safe": True, "uri": "local.parquet"},
        "runs": [{"safe": True}],
    }

    monkeypatch.chdir(tmp_path)
    target = tmp_path / "data" / "control" / "silver.sqlite3"
    target.parent.mkdir(parents=True)
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / f"data/control/silver.sqlite3{suffix}").write_text("runtime")
    monkeypatch.setattr(
        cli.SilverSettings,
        "from_env",
        lambda _environment: SimpleNamespace(manifest_path=target),
    )

    assert cli.main(["reset-state", "--confirm"]) == 0
    reset = json.loads(capsys.readouterr().out)
    assert reset["bronze_untouched"] is True
    assert sorted(reset["removed"]) == [
        "silver.sqlite3",
        "silver.sqlite3-shm",
        "silver.sqlite3-wal",
    ]
