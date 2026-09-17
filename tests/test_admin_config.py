"""验证管理配置注册表、候选值校验、SQLite 草稿存储与审计边界。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3

import pytest

from app.core.admin_config import (
    ADMIN_CONFIG_REGISTRY_VERSION,
    ADMIN_CONFIG_DRAFT_TTL_SECONDS,
    CONFIG_PARAMETER_REGISTRY,
    AdminConfigDraftStore,
    AdminConfigStoreError,
    UnknownConfigKeyError,
    baseline_fingerprint,
    config_definition,
    current_config_values,
    validate_config_candidate,
)
from app.core.config import Settings
from app.core.metrics import HTTP_DURATION_BUCKETS, OPENSEARCH_DURATION_BUCKETS


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "enable_auth": False,
        "patent_search_bulkhead_capacity": 4,
        "patent_search_heavy_bulkhead_capacity": 3,
        "patent_search_bulkhead_acquire_timeout_seconds": 0.01,
        "service_release_commit": "68ae664",
        "service_release_tag": "v0.10.0",
        "service_instance_id": "instance-a",
    }
    values.update(overrides)
    return Settings(**values)


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "admin-state" / "admin-config.sqlite3"


def test_registry_is_unique_bounded_and_contains_no_sensitive_or_target_keys():
    keys = [definition.key for definition in CONFIG_PARAMETER_REGISTRY]

    assert len(keys) == len(set(keys)) == 16
    assert ADMIN_CONFIG_REGISTRY_VERSION
    assert not any(
        forbidden in key
        for key in keys
        for forbidden in (
            "secret",
            "password",
            "api_token",
            "mcp_token",
            "credential",
            "host",
            "index",
            "alias",
            "certificate",
            "port",
            "path",
            "systemd",
        )
    )
    for definition in CONFIG_PARAMETER_REGISTRY:
        assert definition.value_type in {"integer", "number"}
        assert definition.minimum < definition.maximum
        assert definition.apply_mode in {"runtime_reload", "restart_required"}
        assert definition.purpose
        assert definition.risk
        assert definition.observation_metrics


def test_current_values_are_derived_from_the_same_registry_as_validation():
    settings = _settings()
    values = current_config_values(settings)

    assert set(values) == {item.key for item in CONFIG_PARAMETER_REGISTRY}
    assert values["bulkhead.global_capacity"] == 4
    assert values["request.deadline_seconds"] == 240.0


def test_runtime_timeout_ranges_fit_their_largest_observable_buckets():
    assert (
        config_definition("request.deadline_seconds").maximum
        < max(HTTP_DURATION_BUCKETS)
    )
    assert (
        config_definition("opensearch.timeout_seconds").maximum
        < max(OPENSEARCH_DURATION_BUCKETS)
    )


@pytest.mark.parametrize(
    "candidate",
    (
        {"opensearch.password": 1},
        {"opensearch.host": 1},
        {"opensearch.index": 1},
        {"ENV_FREEFORM": 1},
        {"systemd.unit": 1},
    ),
)
def test_unknown_and_sensitive_keys_are_rejected_before_persistence(candidate):
    with pytest.raises(UnknownConfigKeyError):
        validate_config_candidate(_settings(), candidate)


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("opensearch.max_retries", True),
        ("opensearch.max_retries", "0"),
        ("opensearch.retry_backoff_seconds", None),
        ("opensearch.retry_backoff_seconds", float("nan")),
        ("opensearch.retry_backoff_seconds", float("inf")),
    ),
)
def test_types_are_strict_and_non_finite_values_are_invalid(key, value):
    result = validate_config_candidate(_settings(), {key: value})

    assert result.status == "invalid"
    assert result.candidate_values == {}
    assert any(error.code == "invalid_type" and error.key == key for error in result.errors)


def test_noop_range_and_capacity_relationships_are_validated_server_side():
    noop = validate_config_candidate(
        _settings(),
        {"bulkhead.global_capacity": 4},
    )
    out_of_range = validate_config_candidate(
        _settings(),
        {"opensearch.pool_maxsize": 33},
    )
    invalid_capacity = validate_config_candidate(
        _settings(),
        {"bulkhead.heavy_capacity": 4},
    )
    valid_capacity = validate_config_candidate(
        _settings(),
        {
            "bulkhead.heavy_capacity": 5,
            "bulkhead.global_capacity": 6,
            "opensearch.pool_maxsize": 8,
        },
    )

    assert any(error.code == "unchanged_value" for error in noop.errors)
    assert any(error.code == "out_of_range" for error in out_of_range.errors)
    assert any(
        error.code == "heavy_capacity_not_reserved"
        for error in invalid_capacity.errors
    )
    assert valid_capacity.status == "validated"


def test_timeout_retry_and_backoff_follow_the_runtime_deadline_clamp():
    current_equal_timeout_is_safe_without_retry = validate_config_candidate(
        _settings(),
        {"opensearch.max_retries": 0},
    )
    current_equal_timeout_is_safe_with_quick_failure_retry = (
        validate_config_candidate(
            _settings(),
            {"opensearch.retry_backoff_seconds": 0.2},
        )
    )
    invalid_retry_backoff = validate_config_candidate(
        _settings(patent_search_deadline_seconds=1, opensearch_timeout_seconds=1),
        {"opensearch.retry_backoff_seconds": 1},
    )
    invalid_timeout = validate_config_candidate(
        _settings(opensearch_max_retries=0),
        {"request.deadline_seconds": 120},
    )
    oversized_deadline = validate_config_candidate(
        _settings(),
        {"request.deadline_seconds": 241},
    )
    oversized_dependency_timeout = validate_config_candidate(
        _settings(),
        {"opensearch.timeout_seconds": 241},
    )

    assert current_equal_timeout_is_safe_without_retry.status == "validated"
    assert (
        current_equal_timeout_is_safe_with_quick_failure_retry.status == "validated"
    )
    assert any(
        error.code == "retry_backoff_exhausts_deadline"
        for error in invalid_retry_backoff.errors
    )
    assert any(
        error.code == "dependency_timeout_exceeds_deadline"
        for error in invalid_timeout.errors
    )
    assert any(
        error.code == "out_of_range" and error.key == "request.deadline_seconds"
        for error in oversized_deadline.errors
    )
    assert any(
        error.code == "out_of_range" and error.key == "opensearch.timeout_seconds"
        for error in oversized_dependency_timeout.errors
    )


def test_query_budgets_may_only_tighten_and_must_remain_internally_consistent():
    loosened = validate_config_candidate(
        _settings(query_max_chars=500),
        {"query.max_chars": 600},
    )
    inconsistent = validate_config_candidate(
        _settings(),
        {"query.max_result_window": 50},
    )
    tightened = validate_config_candidate(
        _settings(),
        {
            "query.max_page_size": 50,
            "query.max_result_window": 5000,
        },
    )

    assert any(error.code == "must_not_loosen_limit" for error in loosened.errors)
    assert any(
        error.code == "invalid_query_budget_combination"
        for error in inconsistent.errors
    )
    assert tightened.status == "validated"


def test_baseline_fingerprint_is_deterministic_secret_free_and_cas_sensitive():
    baseline = _settings(api_token="SENTINEL_ONE")
    same_runtime = _settings(
        api_token="SENTINEL_TWO",
        opensearch_host="another.internal",
        opensearch_index="another-alias",
    )
    changed_value = _settings(opensearch_max_retries=0)
    changed_release = _settings(service_release_commit="68ae665")

    assert baseline_fingerprint(baseline) == baseline_fingerprint(baseline)
    assert baseline_fingerprint(baseline) == baseline_fingerprint(same_runtime)
    assert baseline_fingerprint(baseline) != baseline_fingerprint(changed_value)
    assert baseline_fingerprint(baseline) != baseline_fingerprint(changed_release)


def test_store_persists_complete_rows_with_reopen_and_immutable_triggers(tmp_path):
    database_path = _database_path(tmp_path)
    store = AdminConfigDraftStore(database_path)
    draft = store.create(
        settings=_settings(),
        operator="admin",
        reason="停止额外重试以验证尾延迟",
        candidate_values={"opensearch.max_retries": 0},
    )

    reopened = AdminConfigDraftStore(database_path).list(settings=_settings())

    assert draft.status == "validated"
    assert (draft.expires_at - draft.created_at).total_seconds() == (
        ADMIN_CONFIG_DRAFT_TTL_SECONDS
    )
    assert [item.draft_id for item in reopened] == [draft.draft_id]
    exported = store.get(settings=_settings(), draft_id=draft.draft_id)
    assert exported is not None
    assert exported.draft_id == reopened[0].draft_id
    assert exported.diff == reopened[0].diff
    assert store.get(settings=_settings(), draft_id="missing") is None
    assert reopened[0].diff["opensearch.max_retries"].old == 1
    assert reopened[0].diff["opensearch.max_retries"].new == 0
    assert os.stat(database_path).st_mode & 0o777 == 0o600
    assert os.stat(database_path.parent).st_mode & 0o777 == 0o700

    with sqlite3.connect(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE config_change_drafts SET reason = 'changed' WHERE draft_id = ?",
                (draft.draft_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM config_change_drafts WHERE draft_id = ?",
                (draft.draft_id,),
            )


def test_invalid_known_values_are_audited_without_persisting_raw_input(tmp_path):
    database_path = _database_path(tmp_path)
    sentinel = "SENTINEL_SHOULD_NOT_BE_STORED"
    store = AdminConfigDraftStore(database_path)

    draft = store.create(
        settings=_settings(),
        operator="admin",
        reason="验证错误类型只保存脱敏结果",
        candidate_values={"request.deadline_seconds": sentinel},
    )

    assert draft.status == "invalid"
    assert draft.candidate_values == {}
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT candidate_values_json, validation_result_json "
            "FROM config_change_drafts WHERE draft_id = ?",
            (draft.draft_id,),
        ).fetchone()
    assert sentinel not in "".join(row)


def test_unknown_values_are_rejected_before_the_database_is_created(tmp_path):
    database_path = _database_path(tmp_path)
    store = AdminConfigDraftStore(database_path)

    with pytest.raises(UnknownConfigKeyError):
        store.create(
            settings=_settings(),
            operator="admin",
            reason="未知字段不能进入审计库",
            candidate_values={"secret.api_token": 1},
        )

    assert not database_path.exists()


def test_list_derives_expired_without_mutating_the_saved_validation(tmp_path):
    store = AdminConfigDraftStore(_database_path(tmp_path))
    draft = store.create(
        settings=_settings(),
        operator="admin",
        reason="保存当前基线",
        candidate_values={"opensearch.max_retries": 0},
    )

    [expired] = store.list(settings=_settings(opensearch_max_retries=0))

    assert expired.draft_id == draft.draft_id
    assert expired.status == "expired"
    assert expired.validation_status == "validated"


def test_list_derives_expired_when_the_server_ttl_elapses(tmp_path):
    now = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)
    current_time = [now]
    store = AdminConfigDraftStore(
        _database_path(tmp_path),
        clock=lambda: current_time[0],
    )
    draft = store.create(
        settings=_settings(),
        operator="admin",
        reason="过期后必须重新预检",
        candidate_values={"opensearch.max_retries": 0},
    )

    current_time[0] = now + timedelta(seconds=ADMIN_CONFIG_DRAFT_TTL_SECONDS)
    [expired] = store.list(settings=_settings())

    assert draft.status == "validated"
    assert expired.status == "expired"
    assert expired.validation_status == "validated"
    assert expired.expires_at == current_time[0]


def test_concurrent_creates_have_unique_ids_and_complete_rows(tmp_path):
    store = AdminConfigDraftStore(_database_path(tmp_path), busy_timeout_seconds=5)

    def create(index: int):
        return store.create(
            settings=_settings(),
            operator="admin",
            reason=f"并发草案 {index}",
            candidate_values={"opensearch.max_retries": 0},
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        drafts = list(executor.map(create, range(20)))

    listed = store.list(settings=_settings(), limit=100)
    assert len({draft.draft_id for draft in drafts}) == 20
    assert len(listed) == 20
    assert all(draft.status == "validated" for draft in listed)


def test_store_refuses_a_broad_state_directory(tmp_path):
    broad_directory = tmp_path / "broad-state"
    broad_directory.mkdir(mode=0o755)
    broad_directory.chmod(0o755)
    store = AdminConfigDraftStore(broad_directory / "admin.sqlite3")

    with pytest.raises(AdminConfigStoreError, match="permissions"):
        store.list(settings=_settings())


def test_store_refuses_symbolic_link_state_and_database_paths(tmp_path):
    outside_database = tmp_path / "outside.sqlite3"
    outside_database.touch(mode=0o600)
    state_directory = tmp_path / "private-state"
    state_directory.mkdir(mode=0o700)
    database_link = state_directory / "admin.sqlite3"
    database_link.symlink_to(outside_database)

    with pytest.raises(AdminConfigStoreError, match="regular private file"):
        AdminConfigDraftStore(database_link).list(settings=_settings())
    assert outside_database.stat().st_size == 0

    real_directory = tmp_path / "real-state"
    real_directory.mkdir(mode=0o700)
    state_link = tmp_path / "linked-state"
    state_link.symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(AdminConfigStoreError, match="symbolic link"):
        AdminConfigDraftStore(state_link / "admin.sqlite3").list(
            settings=_settings()
        )


def test_initialized_store_revalidates_the_database_path_before_each_connection(
    tmp_path,
):
    database_path = _database_path(tmp_path)
    store = AdminConfigDraftStore(database_path)
    original = store.create(
        settings=_settings(),
        operator="admin",
        reason="初始化主审计库",
        candidate_values={"opensearch.max_retries": 0},
    )
    outside_path = tmp_path / "outside-state" / "admin-config.sqlite3"
    outside_store = AdminConfigDraftStore(outside_path)
    outside = outside_store.create(
        settings=_settings(),
        operator="other-admin",
        reason="另一份合法审计库",
        candidate_values={"opensearch.max_retries": 0},
    )

    database_path.chmod(0o644)
    assert store.get(settings=_settings(), draft_id=original.draft_id) is not None
    assert database_path.stat().st_mode & 0o777 == 0o600

    database_path.unlink()
    database_path.symlink_to(outside_path)
    with pytest.raises(AdminConfigStoreError, match="regular private file"):
        store.list(settings=_settings())

    [outside_record] = outside_store.list(settings=_settings())
    assert outside_record.draft_id == outside.draft_id


def test_initialized_store_does_not_recreate_a_disappeared_database(tmp_path):
    database_path = _database_path(tmp_path)
    store = AdminConfigDraftStore(database_path)
    assert store.list(settings=_settings()) == []

    database_path.unlink()
    with pytest.raises(AdminConfigStoreError, match="disappeared"):
        store.list(settings=_settings())

    assert not database_path.exists()
