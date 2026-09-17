"""验证内部 Prometheus 的配置、systemd 单元、目录和版本清单部署资产。"""

import re
from pathlib import Path

import yaml

from app.core.admin_metrics import DEFAULT_ADMIN_PROMETHEUS_JOB


ROOT = Path(__file__).resolve().parents[1]
PROMETHEUS = ROOT / "deployment" / "prometheus"


def _jobs(config: dict) -> dict[str, dict]:
    return {job["job_name"]: job for job in config["scrape_configs"]}


def test_internal_prometheus_config_is_bounded_and_uses_deployment_file_sd():
    config = yaml.safe_load((PROMETHEUS / "prometheus.yml").read_text())
    jobs = _jobs(config)

    assert config["global"] == {
        "scrape_interval": "15s",
        "scrape_timeout": "2s",
        "evaluation_interval": "30s",
    }
    assert config["storage"]["tsdb"]["retention"] == {
        "time": "30d",
        "size": "2GB",
    }
    assert config["rule_files"] == [
        "/etc/patent-search-prometheus/rules/*.yml"
    ]
    assert set(jobs) == {DEFAULT_ADMIN_PROMETHEUS_JOB, "prometheus"}

    service_job = jobs[DEFAULT_ADMIN_PROMETHEUS_JOB]
    assert service_job["metrics_path"] == "/metrics"
    assert {
        "body_size_limit": service_job["body_size_limit"],
        "sample_limit": service_job["sample_limit"],
        "label_limit": service_job["label_limit"],
        "label_name_length_limit": service_job["label_name_length_limit"],
        "label_value_length_limit": service_job["label_value_length_limit"],
        "target_limit": service_job["target_limit"],
        "extra_scrape_metrics": service_job["extra_scrape_metrics"],
    } == {
        "body_size_limit": "256KB",
        "sample_limit": 1000,
        "label_limit": 16,
        "label_name_length_limit": 64,
        "label_value_length_limit": 128,
        "target_limit": 10,
        "extra_scrape_metrics": True,
    }
    assert 256 * 1024 >= 13_951 * 10
    assert service_job["sample_limit"] >= 101 * 5
    assert service_job["label_limit"] >= 5 * 3
    assert service_job["label_name_length_limit"] >= 9 * 3
    assert service_job["label_value_length_limit"] >= 40 * 3
    assert service_job["target_limit"] >= 2 * 5
    assert "static_configs" not in service_job
    assert service_job["file_sd_configs"] == [
        {
            "files": [
                "/etc/patent-search-prometheus/targets/patent-search.yml"
            ],
            "refresh_interval": "30s",
        }
    ]

    self_job = jobs["prometheus"]
    assert "static_configs" not in self_job
    assert self_job["file_sd_configs"] == [
        {
            "files": [
                "/etc/patent-search-prometheus/targets/prometheus.yml"
            ],
            "refresh_interval": "30s",
        }
    ]

    serialized = (PROMETHEUS / "prometheus.yml").read_text().lower()
    for forbidden in ("password", "authorization", "bearer_token", "basic_auth"):
        assert forbidden not in serialized


def test_prometheus_unit_is_loopback_only_bounded_and_failure_isolated():
    unit = (PROMETHEUS / "patent-search-prometheus.service").read_text()

    for required in (
        "User=patent-search-prometheus",
        "Group=patent-search-prometheus",
        "EnvironmentFile=/etc/patent-search-prometheus/runtime.env",
        "--web.listen-address=127.0.0.1:${PROMETHEUS_WEB_PORT}",
        "--query.timeout=5s",
        "--query.max-concurrency=8",
        "--web.max-connections=32",
        "MemoryHigh=384M",
        "MemoryMax=512M",
        "CPUQuota=50%",
        "TasksMax=128",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ReadWritePaths=/var/lib/patent-search-prometheus",
    ):
        assert required in unit

    for forbidden in (
        "--web.enable-admin-api",
        "--web.enable-lifecycle",
        "--web.enable-remote-write-receiver",
        "Requires=patent-search-service",
        "BindsTo=patent-search-service",
    ):
        assert forbidden not in unit


def test_prometheus_runtime_identity_and_directories_are_dedicated():
    sysusers = (
        PROMETHEUS / "sysusers.d" / "patent-search-prometheus.conf"
    ).read_text()
    tmpfiles = (
        PROMETHEUS / "tmpfiles.d" / "patent-search-prometheus.conf"
    ).read_text()

    assert sysusers.startswith("u patent-search-prometheus ")
    assert "/usr/sbin/nologin" in sysusers
    assert (
        "d /var/lib/patent-search-prometheus 0750 "
        "patent-search-prometheus patent-search-prometheus"
    ) in tmpfiles
    assert "d /etc/patent-search-prometheus 0750 root patent-search-prometheus" in tmpfiles
    assert " 0777 " not in tmpfiles


def test_prometheus_version_manifest_is_the_ci_source_of_truth():
    manifest = (PROMETHEUS / "version.env").read_text().splitlines()
    values = dict(line.split("=", 1) for line in manifest if line)
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert values["PROMETHEUS_VERSION"] == "3.14.0"
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        values["PROMETHEUS_LINUX_AMD64_SHA256"],
    )
    assert "source deployment/prometheus/version.env" in workflow
    assert 'PROMETHEUS_VERSION: "' not in workflow
    assert "check config --syntax-only deployment/prometheus/prometheus.yml" in (
        ROOT / "Makefile"
    ).read_text()


def test_internal_prometheus_runbook_keeps_live_facts_out_of_the_repository():
    document = (ROOT / "docs" / "ops" / "internal_prometheus.md").read_text()

    for required in (
        "file_sd_configs",
        "ADMIN_PROMETHEUS_JOB=patent-search",
        "30 天",
        "2 GB",
        "Storage budget response",
        "冷备",
        "回滚",
        "两个抓取周期",
        "应用 `/metrics` 本身不要求业务认证，其保护依赖网络入口",
        "scripts.smoke_admin_metrics",
        "MemoryMax=512M",
        "300/900/3600",
    ):
        assert required in document

    assert "<managed-loopback-port>" in document
    assert "<loopback-service-metrics-target>" in document
    assert "124.174." not in document
