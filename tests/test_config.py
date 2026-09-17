"""验证运行配置的默认值、环境覆盖、硬上限与鉴权/观测安全约束。"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.mappings.query_field_mapping import DEFAULT_VECTOR_EMBEDDING_MODEL
from app.query.budget import DEFAULT_QUERY_BUDGET, HARD_QUERY_BUDGET


def test_settings_defaults_and_explicit_test_bulkhead_contract():
    settings = Settings(_env_file=None)

    assert settings.service_name == "patent-search-service"
    assert settings.service_host == "0.0.0.0"
    assert settings.service_port == 8000
    assert settings.enable_auth is True
    assert settings.console_username == "console-test-user"
    assert settings.console_password == "console-test-password"
    assert settings.opensearch_port == 9200
    assert settings.opensearch_use_https is True
    assert settings.opensearch_index == "patent_search_read"
    assert settings.opensearch_verify_certs is False
    assert settings.opensearch_timeout_seconds == 240
    assert settings.opensearch_pool_maxsize == 10
    assert settings.opensearch_max_retries == 1
    assert settings.opensearch_retry_backoff_seconds == 0.1
    assert settings.patent_search_bulkhead_capacity == 4
    assert settings.patent_search_heavy_bulkhead_capacity == 3
    assert settings.patent_search_bulkhead_acquire_timeout_seconds == 0.01
    assert settings.patent_search_deadline_seconds == 240
    assert settings.readiness_timeout_seconds == 1
    assert settings.readiness_success_cache_seconds == 2
    assert settings.readiness_failure_cache_seconds == 1
    assert settings.query_vector_api_url == (
        "https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal"
    )
    assert settings.query_vector_api_key == ""
    assert settings.query_vector_model_endpoint == ""
    assert settings.query_vector_model_endpoints == {}
    assert settings.query_vector_endpoint_routes == {}
    assert settings.query_budget == DEFAULT_QUERY_BUDGET


def test_query_vector_settings_are_configurable_without_exposing_api_key(monkeypatch):
    secret = "query-vector-secret-4e21"
    monkeypatch.setenv("QUERY_VECTOR_API_KEY", secret)
    monkeypatch.setenv("QUERY_VECTOR_MODEL_ENDPOINT", "ep-query-vector")

    settings = Settings(_env_file=None)

    assert settings.query_vector_api_key == secret
    assert settings.query_vector_model_endpoint == "ep-query-vector"
    assert settings.query_vector_endpoint_routes == {
        DEFAULT_VECTOR_EMBEDDING_MODEL: "ep-query-vector"
    }
    assert secret not in repr(settings)


def test_query_vector_settings_accept_controlled_model_endpoint_routes(monkeypatch):
    monkeypatch.setenv("QUERY_VECTOR_API_KEY", "secret")
    monkeypatch.setenv(
        "QUERY_VECTOR_MODEL_ENDPOINTS",
        '{"model-a":"endpoint-a","model-b":"endpoint-b"}',
    )

    settings = Settings(_env_file=None)

    assert settings.query_vector_model_endpoint == ""
    assert settings.query_vector_endpoint_routes == {
        "model-a": "endpoint-a",
        "model-b": "endpoint-b",
    }


def test_query_vector_settings_strip_blank_credentials():
    settings = Settings(
        _env_file=None,
        query_vector_api_key="   ",
        query_vector_model_endpoint="  ",
    )

    assert settings.query_vector_api_key == ""
    assert settings.query_vector_model_endpoint == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"query_vector_api_key": "secret"},
        {"query_vector_model_endpoint": "ep-query-vector"},
        {"query_vector_model_endpoints": {"model-v1": "ep-query-vector"}},
        {
            "query_vector_api_key": "secret",
            "query_vector_model_endpoint": "ep-query-vector",
            "query_vector_model_endpoints": {"model-v1": "ep-query-vector"},
        },
        {
            "query_vector_api_key": "secret",
            "query_vector_model_endpoints": {" ": "ep-query-vector"},
        },
        {
            "query_vector_api_key": "secret",
            "query_vector_model_endpoints": {"model-v1": "  "},
        },
        {"query_vector_api_url": "http://ark.example/api/v3/embeddings"},
        {"query_vector_api_url": "https://user:password@ark.example/embeddings"},
        {"query_vector_api_url": "https://ark.example/embeddings?token=secret"},
    ],
)
def test_query_vector_settings_reject_partial_credentials_and_unsafe_url(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


@pytest.mark.parametrize(
    "overrides",
    (
        {"opensearch_timeout_seconds": 241},
        {"patent_search_deadline_seconds": 241},
        {"patent_search_deadline_seconds": 0.5},
        {
            "opensearch_timeout_seconds": 300,
            "opensearch_max_retries": 0,
        },
    ),
)
def test_runtime_timing_baseline_cannot_escape_hard_limits(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("OPENSEARCH_TIMEOUT_SECONDS", "241"),
        ("PATENT_SEARCH_DEADLINE_SECONDS", "300"),
    ),
)
def test_runtime_timing_environment_cannot_exceed_hard_limits(
    monkeypatch,
    name,
    value,
):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_readiness_settings_can_be_configured_with_environment_variables(monkeypatch):
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("READINESS_SUCCESS_CACHE_SECONDS", "4")
    monkeypatch.setenv("READINESS_FAILURE_CACHE_SECONDS", "0.5")

    settings = Settings(_env_file=None)

    assert settings.readiness_timeout_seconds == 2.5
    assert settings.readiness_success_cache_seconds == 4
    assert settings.readiness_failure_cache_seconds == 0.5


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("readiness_timeout_seconds", 5.1),
        ("readiness_success_cache_seconds", 30.1),
        ("readiness_failure_cache_seconds", 30.1),
    ],
)
def test_readiness_settings_have_bounded_values(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_query_budget_can_be_tightened_with_environment_variables(monkeypatch):
    monkeypatch.setenv("QUERY_MAX_REQUEST_BODY_BYTES", "8192")
    monkeypatch.setenv("QUERY_MAX_CHARS", "500")
    monkeypatch.setenv("QUERY_MAX_NESTING_DEPTH", "16")
    monkeypatch.setenv("QUERY_MAX_TOKENS", "128")
    monkeypatch.setenv("QUERY_MAX_AST_NODES", "128")
    monkeypatch.setenv("QUERY_MAX_BOOLEAN_CLAUSES", "64")
    monkeypatch.setenv("QUERY_MAX_PAGE_SIZE", "50")
    monkeypatch.setenv("QUERY_MAX_RESULT_WINDOW", "5000")

    budget = Settings(_env_file=None).query_budget

    assert budget.max_request_body_bytes == 8192
    assert budget.max_query_chars == 500
    assert budget.max_nesting_depth == 16
    assert budget.max_tokens == 128
    assert budget.max_ast_nodes == 128
    assert budget.max_boolean_clauses == 64
    assert budget.max_page_size == 50
    assert budget.max_result_window == 5000


@pytest.mark.parametrize(
    ("field", "hard_limit"),
    [
        ("query_max_request_body_bytes", HARD_QUERY_BUDGET.max_request_body_bytes),
        ("query_max_chars", HARD_QUERY_BUDGET.max_query_chars),
        ("query_max_nesting_depth", HARD_QUERY_BUDGET.max_nesting_depth),
        ("query_max_tokens", HARD_QUERY_BUDGET.max_tokens),
        ("query_max_ast_nodes", HARD_QUERY_BUDGET.max_ast_nodes),
        ("query_max_boolean_clauses", HARD_QUERY_BUDGET.max_boolean_clauses),
        ("query_max_page_size", HARD_QUERY_BUDGET.max_page_size),
        ("query_max_result_window", HARD_QUERY_BUDGET.max_result_window),
    ],
)
def test_query_budget_environment_values_cannot_exceed_code_hard_limits(
    field,
    hard_limit,
):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: hard_limit + 1})


def test_query_result_window_cannot_be_smaller_than_page_size():
    with pytest.raises(
        ValidationError,
        match="max_result_window must be greater than or equal to max_page_size",
    ):
        Settings(
            _env_file=None,
            query_max_page_size=100,
            query_max_result_window=99,
        )


@pytest.mark.parametrize(
    ("missing_name", "provided"),
    [
        (
            "patent_search_bulkhead_capacity",
            {
                "patent_search_heavy_bulkhead_capacity": 3,
                "patent_search_bulkhead_acquire_timeout_seconds": 0.01,
            },
        ),
        (
            "patent_search_heavy_bulkhead_capacity",
            {
                "patent_search_bulkhead_capacity": 4,
                "patent_search_bulkhead_acquire_timeout_seconds": 0.01,
            },
        ),
        (
            "patent_search_bulkhead_acquire_timeout_seconds",
            {
                "patent_search_bulkhead_capacity": 4,
                "patent_search_heavy_bulkhead_capacity": 3,
            },
        ),
    ],
)
def test_bulkhead_runtime_settings_are_required(
    monkeypatch,
    missing_name,
    provided,
):
    for name in (
        "PATENT_SEARCH_BULKHEAD_CAPACITY",
        "PATENT_SEARCH_HEAVY_BULKHEAD_CAPACITY",
        "PATENT_SEARCH_BULKHEAD_ACQUIRE_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError, match=missing_name):
        Settings(_env_file=None, **provided)


def test_opensearch_url_uses_https_when_enabled():
    settings = Settings(
        opensearch_host="example.com",
        opensearch_port=9200,
        opensearch_use_https=True,
    )

    assert settings.opensearch_url == "https://example.com:9200"


def test_opensearch_retry_count_cannot_exceed_one():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, opensearch_max_retries=2)


def test_bulkhead_capacity_cannot_exceed_opensearch_client_slots():
    with pytest.raises(ValidationError, match="PATENT_SEARCH_BULKHEAD_CAPACITY"):
        Settings(
            _env_file=None,
            opensearch_pool_maxsize=3,
            patent_search_bulkhead_capacity=4,
        )


def test_heavy_bulkhead_must_leave_capacity_for_lightweight_requests():
    with pytest.raises(
        ValidationError, match="PATENT_SEARCH_HEAVY_BULKHEAD_CAPACITY"
    ):
        Settings(
            _env_file=None,
            patent_search_bulkhead_capacity=4,
            patent_search_heavy_bulkhead_capacity=4,
        )


@pytest.mark.parametrize("missing_name", ["CONSOLE_USERNAME", "CONSOLE_PASSWORD"])
def test_console_browser_credentials_are_required_when_auth_is_enabled(
    monkeypatch,
    missing_name,
):
    monkeypatch.delenv(missing_name, raising=False)

    with pytest.raises(ValidationError, match=missing_name):
        Settings(
            _env_file=None,
            enable_auth=True,
            api_token="backend-token",
        )


def test_console_password_must_not_reuse_backend_api_token():
    with pytest.raises(ValidationError, match="must not reuse API_TOKEN"):
        Settings(
            _env_file=None,
            enable_auth=True,
            api_token="shared-secret",
            console_username="console-user",
            console_password="shared-secret",
        )


@pytest.mark.parametrize(
    ("username", "password", "message"),
    [
        ("用户", "console-password", "CONSOLE_USERNAME.*printable ASCII"),
        ("console:user", "console-password", "CONSOLE_USERNAME.*must not contain"),
        ("console-user", "密码", "CONSOLE_PASSWORD.*printable ASCII"),
        ("console-user\n", "console-password", "CONSOLE_USERNAME.*printable ASCII"),
        ("console-user", "console\tpassword", "CONSOLE_PASSWORD.*printable ASCII"),
    ],
)
def test_console_credentials_must_match_fastapi_basic_parser(
    username,
    password,
    message,
):
    with pytest.raises(ValidationError, match=message):
        Settings(
            _env_file=None,
            enable_auth=True,
            api_token="backend-token",
            console_username=username,
            console_password=password,
        )


def test_console_credentials_are_not_required_when_auth_is_disabled(monkeypatch):
    monkeypatch.delenv("CONSOLE_USERNAME", raising=False)
    monkeypatch.delenv("CONSOLE_PASSWORD", raising=False)

    settings = Settings(_env_file=None, enable_auth=False)

    assert settings.console_username == ""
    assert settings.console_password == ""


def test_admin_is_disabled_by_default_and_does_not_change_existing_startup():
    settings = Settings(_env_file=None)

    assert settings.admin_enabled is False
    assert settings.admin_viewer_username == ""
    assert settings.admin_viewer_password == ""
    assert settings.admin_config_drafts_enabled is False
    assert settings.admin_runtime_config_enabled is False
    assert settings.admin_config_database_path == (
        "/var/lib/patent-search-service/admin-config.sqlite3"
    )
    assert settings.admin_prometheus_url == ""
    assert settings.admin_log_source == "journal"


@pytest.mark.parametrize(
    "overrides",
    (
        {"admin_prometheus_url": "file:///unused-admin-source"},
        {"admin_metrics_timeout_seconds": 0},
        {"admin_metrics_timeout_seconds": "not-a-number"},
        {"admin_prometheus_job": 'bad"job'},
        {"admin_log_source": "unused-invalid-source"},
    ),
)
def test_disabled_admin_ignores_dormant_invalid_source_settings(overrides):
    settings = Settings(_env_file=None, admin_enabled=False, **overrides)

    assert settings.admin_enabled is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"admin_viewer_username": ""}, "ADMIN_VIEWER_USERNAME"),
        ({"admin_viewer_password": ""}, "ADMIN_VIEWER_PASSWORD"),
        ({"admin_viewer_username": "viewer:user"}, "must not contain"),
        ({"admin_viewer_password": "密码"}, "printable ASCII"),
        ({"admin_viewer_password": "backend-token", "api_token": "backend-token"}, "must not reuse"),
    ],
)
def test_enabled_admin_requires_valid_browser_credentials(overrides, message):
    values = {
        "_env_file": None,
        "admin_enabled": True,
        "admin_viewer_username": "admin-viewer",
        "admin_viewer_password": "admin-password",
        "console_username": "console-user",
        "console_password": "console-password",
    }
    values.update(overrides)

    with pytest.raises(ValidationError, match=message):
        Settings(**values)


def test_admin_and_console_passwords_may_match_when_explicitly_configured():
    settings = Settings(
        _env_file=None,
        enable_auth=True,
        api_token="backend-token",
        console_username="admin",
        console_password="shared-browser-password",
        admin_enabled=True,
        admin_viewer_username="admin",
        admin_viewer_password="shared-browser-password",
    )

    assert settings.console_password == settings.admin_viewer_password


def test_config_drafts_require_admin_and_a_dedicated_absolute_database_path():
    with pytest.raises(ValidationError, match="requires ADMIN_ENABLED"):
        Settings(_env_file=None, admin_config_drafts_enabled=True)

    with pytest.raises(ValidationError, match="ADMIN_CONFIG_DATABASE_PATH"):
        Settings(
            _env_file=None,
            admin_enabled=True,
            admin_viewer_username="admin",
            admin_viewer_password="admin-password",
            admin_config_drafts_enabled=True,
            admin_config_database_path="data/admin.sqlite3",
        )


def test_runtime_config_writes_require_the_existing_admin_draft_gate():
    with pytest.raises(ValidationError, match="ADMIN_CONFIG_DRAFTS_ENABLED"):
        Settings(
            _env_file=None,
            admin_runtime_config_enabled=True,
        )


@pytest.mark.parametrize(
    "url",
    (
        "file:///var/run/prometheus",
        "https://user:password@prometheus.internal",
        "https://prometheus.internal?query=up",
        "https://prometheus.internal#fragment",
    ),
)
def test_prometheus_base_url_rejects_unsafe_shapes(url):
    with pytest.raises(ValidationError, match="ADMIN_PROMETHEUS_URL"):
        Settings(
            _env_file=None,
            admin_enabled=True,
            admin_viewer_username="admin-viewer",
            admin_viewer_password="admin-password",
            admin_prometheus_url=url,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("admin_metrics_timeout_seconds", 0, "ADMIN_METRICS_TIMEOUT_SECONDS"),
        ("admin_metrics_timeout_seconds", 5.1, "ADMIN_METRICS_TIMEOUT_SECONDS"),
        ("admin_metrics_timeout_seconds", "invalid", "ADMIN_METRICS_TIMEOUT_SECONDS"),
        ("admin_prometheus_job", 'bad"job', "ADMIN_PROMETHEUS_JOB"),
        ("admin_log_source", "other", "ADMIN_LOG_SOURCE"),
    ],
)
def test_enabled_admin_validates_observability_source_settings(
    field,
    value,
    message,
):
    with pytest.raises(ValidationError, match=message):
        Settings(
            _env_file=None,
            admin_enabled=True,
            admin_viewer_username="admin-viewer",
            admin_viewer_password="admin-password",
            **{field: value},
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("service_release_commit", "contains space", "7-64 character hex commit"),
        ("service_release_commit", "abc123", "7-64 character hex commit"),
        ("service_release_tag", "v1/unsafe", "bounded deployment identifier"),
        ("service_instance_id", "实例一", "bounded deployment identifier"),
    ],
)
def test_release_metadata_is_bounded_for_metrics_and_admin_output(
    field,
    value,
    message,
):
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("admin_viewer_username", "u" * 129, "must not exceed 128"),
        ("admin_viewer_password", "p" * 1025, "must not exceed 1024"),
    ],
)
def test_admin_basic_credentials_have_hard_length_limits(field, value, message):
    settings = {
        "_env_file": None,
        "admin_enabled": True,
        "admin_viewer_username": "admin-viewer",
        "admin_viewer_password": "admin-password",
        field: value,
    }

    with pytest.raises(ValidationError, match=message):
        Settings(**settings)


@pytest.mark.parametrize("timeout_seconds", [0, 0.11])
def test_bulkhead_acquire_timeout_has_a_short_bounded_range(timeout_seconds):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            patent_search_bulkhead_acquire_timeout_seconds=timeout_seconds,
        )
