"""Fail-closed tests for the FF-06C disposable MinIO launcher."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.validation.run_disposable_minio_tests import (
    CANONICAL_BUCKETS,
    PERSISTENT_MINIO_VOLUME,
    IsolationError,
    RunContext,
    assert_resource_absent,
    render_override,
    validate_minio_container,
    validate_rendered_config,
    validate_resource_ownership,
    validate_temporary_path,
    validate_tracked_inputs,
)

RUN_ID = "ff06c-minio-20260729t120000z-deadbeef"


def _context(tmp_path: Path) -> RunContext:
    return RunContext(
        run_id=RUN_ID,
        project_name=RUN_ID,
        volume_name=f"{RUN_ID}-data",
        network_name=f"{RUN_ID}-network",
        temporary_directory=tmp_path,
        override_path=tmp_path / "compose.override.yml",
        api_port=49100,
        console_port=49101,
    )


def _custom_labels(context: RunContext) -> dict[str, str]:
    return context.labels


def _volume_labels(context: RunContext) -> dict[str, str]:
    return {
        **context.labels,
        "com.docker.compose.project": context.project_name,
        "com.docker.compose.volume": "ff06c_minio_data",
    }


def _rendered(context: RunContext) -> dict:
    return {
        "services": {
            "minio": {
                "labels": _custom_labels(context),
                "volumes": [
                    {
                        "type": "volume",
                        "source": "ff06c_minio_data",
                        "target": "/data",
                    }
                ],
                "networks": {"ff06c_minio_network": None},
            },
            "minio-init": {
                "labels": _custom_labels(context),
                "environment": dict(CANONICAL_BUCKETS),
                "networks": {"ff06c_minio_network": None},
            },
        },
        "volumes": {
            "ff06c_minio_data": {
                "name": context.volume_name,
                "labels": _custom_labels(context),
            }
        },
        "networks": {
            "ff06c_minio_network": {
                "name": context.network_name,
                "labels": _custom_labels(context),
            }
        },
    }


def _container(context: RunContext) -> dict:
    return {
        "Config": {
            "Labels": {
                **context.labels,
                "com.docker.compose.project": context.project_name,
                "com.docker.compose.service": "minio",
            }
        },
        "Mounts": [
            {
                "Type": "volume",
                "Name": context.volume_name,
                "Destination": "/data",
            }
        ],
        "NetworkSettings": {
            "Networks": {context.network_name: {}},
            "Ports": {
                "9000/tcp": [{"HostPort": str(context.api_port)}],
                "9001/tcp": [{"HostPort": str(context.console_port)}],
            },
        },
    }


def _volume(context: RunContext) -> dict:
    return {"Name": context.volume_name, "Labels": _volume_labels(context)}


def test_override_generation_is_external_and_replaces_persistent_mount(tmp_path: Path) -> None:
    context = _context(tmp_path)

    rendered = render_override(context)

    assert "volumes: !override" in rendered
    assert "minio_data: !reset null" in rendered
    assert PERSISTENT_MINIO_VOLUME not in rendered
    assert context.volume_name not in rendered
    assert "${FF06C_MINIO_VOLUME_NAME:?}" in rendered


def test_valid_rendered_config_and_post_start_resources_pass(tmp_path: Path) -> None:
    context = _context(tmp_path)

    validate_rendered_config(_rendered(context), context)
    validate_minio_container(_container(context), _volume(context), context)


def test_persistent_global_volume_in_rendered_config_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    config = _rendered(context)
    config["volumes"]["persistent"] = {"name": PERSISTENT_MINIO_VOLUME}

    with pytest.raises(IsolationError, match="PERSISTENT_VOLUME_IN_EFFECTIVE_CONFIG"):
        validate_rendered_config(config, context)


def test_two_data_mounts_are_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    config = _rendered(context)
    config["services"]["minio"]["volumes"].append(
        {"type": "volume", "source": "another", "target": "/data"}
    )

    with pytest.raises(IsolationError, match="DATA_MOUNT_COUNT_INVALID"):
        validate_rendered_config(config, context)


def test_preexisting_run_volume_is_rejected() -> None:
    with pytest.raises(IsolationError, match="RUN_VOLUME_PREEXISTS"):
        assert_resource_absent([{"Name": "existing"}], error_code="FF06C_RUN_VOLUME_PREEXISTS")


def test_missing_run_owned_volume_labels_are_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    config = _rendered(context)
    config["volumes"]["ff06c_minio_data"]["labels"].pop("com.fintech.validation.run-id")

    with pytest.raises(IsolationError, match="OWNERSHIP_LABEL_MISMATCH"):
        validate_rendered_config(config, context)


def test_foreign_run_id_on_volume_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    volume = _volume(context)
    volume["Labels"]["com.fintech.validation.run-id"] = "ff06c-minio-20260729t120000z-feedface"

    with pytest.raises(IsolationError, match="OWNERSHIP_LABEL_MISMATCH"):
        validate_resource_ownership(volume, context, kind="volume")


def test_compose_project_collision_is_rejected() -> None:
    with pytest.raises(IsolationError, match="COMPOSE_PROJECT_COLLISION"):
        assert_resource_absent(
            ["container-id"],
            error_code="FF06C_COMPOSE_PROJECT_COLLISION",
        )


def test_foreign_container_owner_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    container = _container(context)
    container["Config"]["Labels"]["com.docker.compose.project"] = "foreign-project"

    with pytest.raises(IsolationError, match="FOREIGN_CONTAINER_PROJECT"):
        validate_resource_ownership(
            container,
            context,
            kind="container",
            expected_service="minio",
        )


def test_endpoint_from_another_container_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    container = _container(context)
    container["NetworkSettings"]["Ports"]["9000/tcp"][0]["HostPort"] = "49200"

    with pytest.raises(IsolationError, match="ENDPOINT_OWNERSHIP_INVALID"):
        validate_minio_container(container, _volume(context), context)


def test_run_specific_bucket_renaming_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    config = _rendered(context)
    config["services"]["minio-init"]["environment"]["MINIO_SILVER_BUCKET"] = "ff06-silver"

    with pytest.raises(IsolationError, match="CANONICAL_BUCKET_CONTRACT_CHANGED"):
        validate_rendered_config(config, context)


def test_cleanup_refuses_foreign_volume(tmp_path: Path) -> None:
    context = _context(tmp_path)
    volume = _volume(context)
    volume["Labels"]["com.docker.compose.project"] = "foreign-project"

    with pytest.raises(IsolationError, match="FOREIGN_VOLUME_PROJECT"):
        validate_resource_ownership(volume, context, kind="volume")


def test_cleanup_refuses_foreign_container_service(tmp_path: Path) -> None:
    context = _context(tmp_path)
    container = _container(context)
    container["Config"]["Labels"]["com.docker.compose.service"] = "database"

    with pytest.raises(IsolationError, match="FOREIGN_CONTAINER_SERVICE"):
        validate_resource_ownership(
            container,
            context,
            kind="container",
            expected_service="minio",
        )


def test_protected_untracked_input_is_rejected() -> None:
    protected = "docs/implementation-prompts/production-pilot-review.md"

    with pytest.raises(IsolationError, match="PROTECTED_PATH_ENUMERATED"):
        validate_tracked_inputs(
            [protected],
            tracked=frozenset({protected}),
        )


def test_temporary_override_inside_repository_is_rejected() -> None:
    path = Path("build/ff06c/compose.override.yml")

    with pytest.raises(IsolationError, match="TEMP_PATH_INSIDE_REPOSITORY"):
        validate_temporary_path(path)


def test_effective_config_rejects_persistent_minio_mount(tmp_path: Path) -> None:
    context = _context(tmp_path)
    config = _rendered(context)
    config["services"]["minio"]["volumes"][0]["source"] = PERSISTENT_MINIO_VOLUME

    with pytest.raises(IsolationError, match="PERSISTENT_VOLUME_IN_EFFECTIVE_CONFIG"):
        validate_rendered_config(config, context)


def test_post_start_wrong_volume_source_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    container = deepcopy(_container(context))
    container["Mounts"][0]["Name"] = "foreign-volume"

    with pytest.raises(IsolationError, match="POST_START_DATA_MOUNT_INVALID"):
        validate_minio_container(container, _volume(context), context)
