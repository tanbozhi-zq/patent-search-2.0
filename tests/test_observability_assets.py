"""验证观测部署资产、日志轮转和运行手册不携带不应固化的生产事实。"""

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
OBSERVABILITY = ROOT / "deployment" / "observability"


EXPECTED_ALERTS = {
    "PatentSearchHigh5xxRatio",
    "PatentSearchHigh503Ratio",
    "PatentSearchHigh504Ratio",
    "PatentSearchP95LatencyHigh",
    "PatentSearchP99LatencyHigh",
    "PatentSearchBulkheadRejecting",
    "PatentSearchDependencyTimeouts",
    "PatentSearchInstanceNotReady",
    "PatentSearchProcessRestarted",
    "PatentSearchPrometheusStorageBudgetHigh",
}


def test_alert_rules_have_candidate_thresholds_windows_and_response_guidance():
    rules_file = yaml.safe_load(
        (OBSERVABILITY / "alert_rules.yml").read_text()
    )
    rules = [
        rule
        for group in rules_file["groups"]
        for rule in group["rules"]
    ]

    assert {rule["alert"] for rule in rules} == EXPECTED_ALERTS
    business_sli_alerts = {
        "PatentSearchHigh5xxRatio",
        "PatentSearchHigh503Ratio",
        "PatentSearchHigh504Ratio",
        "PatentSearchP95LatencyHigh",
        "PatentSearchP99LatencyHigh",
    }
    rolling_count_alerts = {
        "PatentSearchBulkheadRejecting",
        "PatentSearchDependencyTimeouts",
    }
    for rule in rules:
        if rule["alert"] in rolling_count_alerts:
            assert "for" not in rule
            assert "[5m]" in rule["expr"]
            assert "rolling 5 minute window" in rule["annotations"]["summary"]
        else:
            assert rule["for"]
        assert rule["labels"]["severity"] in {"warning", "critical"}
        expected_threshold_status = (
            "deployment_budget"
            if rule["alert"] == "PatentSearchPrometheusStorageBudgetHigh"
            else "candidate"
        )
        assert rule["labels"]["threshold_status"] == expected_threshold_status
        assert {
            "summary",
            "possible_causes",
            "investigation",
            "mitigation",
            "runbook",
        } <= rule["annotations"].keys()
        expected_runbook_suffix = (
            "internal_prometheus.md#storage-budget-response"
            if rule["alert"] == "PatentSearchPrometheusStorageBudgetHigh"
            else "observability.md#alert-response"
        )
        assert rule["annotations"]["runbook"].endswith(expected_runbook_suffix)
        if rule["alert"] in business_sli_alerts:
            assert 'route!~"/(live|startup|ready|health)"' in rule["expr"]


def test_offline_rule_tests_cover_trigger_and_recovery_for_every_alert():
    tests_file = yaml.safe_load(
        (OBSERVABILITY / "alert_rules_test.yml").read_text()
    )
    cases = [
        case
        for test in tests_file["tests"]
        for case in test["alert_rule_test"]
    ]

    for alert_name in EXPECTED_ALERTS:
        alert_cases = [case for case in cases if case["alertname"] == alert_name]
        assert any(case["exp_alerts"] for case in alert_cases)
        assert any(case["exp_alerts"] == [] for case in alert_cases)

    test_names = {test["name"] for test in tests_file["tests"]}
    assert "rolling count alerts catch one-off bursts and recover" in test_names
    assert "rolling count alerts remain active during sustained events" in test_names


def test_dashboard_distinguishes_failure_boundaries_and_aggregates_gauges_safely():
    dashboard = json.loads(
        (OBSERVABILITY / "grafana_dashboard.json").read_text()
    )
    titles = {panel["title"] for panel in dashboard["panels"]}
    expressions = "\n".join(
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    )
    dependency_panel = next(
        panel
        for panel in dashboard["panels"]
        if panel["title"] == "OpenSearch dependency failures"
    )
    dependency_failure_expression = dependency_panel["targets"][0]["expr"]

    assert {
        "Caller errors (4xx)",
        "Application overload",
        "OpenSearch dependency failures",
        "Unknown program errors",
    } <= titles
    assert 'code="50301"' in expressions
    assert 'code="50002"' in expressions
    assert (
        'outcome=~"connection_error|unavailable|timeout|invalid_response|error"'
        in dependency_failure_expression
    )
    assert "histogram_quantile(0.95" in expressions
    assert "histogram_quantile(0.99" in expressions
    assert "sum by (bulkhead) (patent_search_bulkhead_in_flight)" in expressions
    assert "max by (job, bulkhead)" in expressions
    assert "min by (job, probe)" in expressions
    assert "patent_search_service_start_time_seconds * 1000" in expressions
    assert 'route!~"/(live|startup|ready|health)"' in expressions


def test_operations_document_covers_scrape_retention_receivers_and_import():
    document = (ROOT / "docs" / "ops" / "observability.md").read_text()

    for required in (
        "scrape_interval",
        "30 天",
        "Alertmanager",
        "grafana_dashboard.json",
        "sum(rate(",
        "max by (job, bulkhead)",
        "request ID",
        "canary 待校准候选值",
    ):
        assert required in document


def test_ci_runs_pinned_promtool_rule_checks():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    version_manifest = (
        ROOT / "deployment" / "prometheus" / "version.env"
    ).read_text()

    assert "PROMETHEUS_VERSION=3.14.0" in version_manifest
    assert "PROMETHEUS_LINUX_AMD64_SHA256=" in version_manifest
    assert "source deployment/prometheus/version.env" in workflow
    assert "sha256sum --check --strict" in workflow
    assert "check-observability" in workflow
