"""验证管理看板静态资源的 CSP、安全 DOM 用法与部署依赖声明。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app/static/admin/index.html").read_text()
STYLE = (ROOT / "app/static/admin/admin.css").read_text()
SCRIPT = (ROOT / "app/static/admin/admin.js").read_text()


def test_admin_page_keeps_scripts_and_styles_external_for_strict_csp():
    assert '<link rel="stylesheet" href="/admin/admin.css">' in HTML
    assert '<link rel="icon" href="/admin/favicon.svg" type="image/svg+xml">' in HTML
    assert '<script src="/admin/admin.js" defer></script>' in HTML
    assert "<style" not in HTML
    assert "onclick=" not in HTML.lower()
    assert "onchange=" not in HTML.lower()


def test_admin_script_uses_safe_dom_sinks_and_does_not_persist_diagnostics():
    assert ".innerHTML" not in SCRIPT
    assert "insertAdjacentHTML" not in SCRIPT
    assert "document.write" not in SCRIPT
    assert "localStorage" not in SCRIPT
    assert "sessionStorage" not in SCRIPT
    assert ".textContent" in SCRIPT
    assert "sampleMap('bulkhead_worst_utilization', 'bulkhead', Math.max)" in SCRIPT
    assert "metric('route_request_rate')" in SCRIPT
    assert "revision !== state.logRevision" in SCRIPT


def test_admin_page_exposes_required_operating_sections():
    for identifier in (
        'id="releaseVersion"',
        'id="metricsGeneratedAt"',
        'id="metricsIdentity"',
        'id="logScopeIdentity"',
        'id="coreConfigRows"',
        'id="instanceRows"',
        'id="bulkheadRows"',
        'id="dependencyRows"',
        'id="routeRows"',
        'id="outcomeRows"',
        'id="configRows"',
        'id="logForm"',
        'id="logRows"',
    ):
        assert identifier in HTML
    assert 'id="adminModeBadge"' in HTML
    assert "管理 · ADMIN" in HTML
    assert "运行参数" in HTML
    assert "状态总结" not in HTML
    assert "正常" not in HTML
    assert "Route 模板" not in HTML
    assert "max_instance_utilization" in HTML
    assert 'list="logRouteOptions"' in HTML
    assert 'id="includeSystem"' in HTML
    assert "使用 Request ID ${requestId} 筛选日志" in SCRIPT
    assert ".config-details[open] summary::before" in STYLE


def test_admin_page_exposes_guarded_runtime_actions_only_for_reloadable_drafts():
    for identifier in (
        'id="draftWorkspace"',
        'id="configDraftForm"',
        'id="draftParameterRows"',
        'id="draftReason"',
        'id="draftResult"',
        'id="draftHistoryRows"',
        'id="runtimeControl"',
        'id="runtimeRollbackButton"',
        'id="runtimeAudit"',
        'id="runtimeOperationRows"',
    ):
        assert identifier in HTML
    assert "这里只预检并保存草案，不会修改当前运行参数" in HTML
    assert "预检并保存草案" in HTML
    assert "export" in HTML
    assert "导出 JSON" in SCRIPT
    assert "/config-drafts/export?id=" in SCRIPT
    assert "应用运行时草案" in SCRIPT
    assert "回滚到上一版本" in HTML
    assert "/runtime-config/apply" in SCRIPT
    assert "/runtime-config/rollback" in SCRIPT
    assert "runtimeDraftCanApply" in SCRIPT
    assert "renderRuntimeOperations" in SCRIPT
    assert "runtimeOperationSummary" in SCRIPT
    assert "runtimeConfig.writes_enabled" in SCRIPT
    for prohibited_action in (
        ">重启<",
        ">部署<",
        "/restart",
        "/deploy",
        "/shell",
    ):
        assert prohibited_action not in HTML
        assert prohibited_action not in SCRIPT
    assert "X-Admin-Intent" in SCRIPT
    assert "baseline_fingerprint" in SCRIPT


def test_config_draft_state_uses_the_private_systemd_state_directory():
    unit = (ROOT / "deployment/patent-search-service.service").read_text()
    environment = (ROOT / ".env.example").read_text()

    assert "StateDirectory=patent-search-service" in unit
    assert "StateDirectoryMode=0700" in unit
    assert "UMask=0077" in unit
    assert "ADMIN_CONFIG_DRAFTS_ENABLED=false" in environment
    assert "ADMIN_RUNTIME_CONFIG_ENABLED=false" in environment
    assert (
        "ADMIN_CONFIG_DATABASE_PATH=/var/lib/patent-search-service/"
        "admin-config.sqlite3"
    ) in environment


def test_admin_journal_dependency_and_least_scope_deployment_assets_are_explicit():
    requirement = (ROOT / "requirements-admin-journal.txt").read_text()
    drop_in = (ROOT / "deployment/patent-search-admin-journal.conf").read_text()
    sysusers = (
        ROOT / "deployment/sysusers.d/patent-search-admin-journal.conf"
    ).read_text()
    tmpfiles = (
        ROOT / "deployment/tmpfiles.d/patent-search-admin-journal.conf"
    ).read_text()
    runbook = (ROOT / "docs/ops/admin_dashboard.md").read_text()

    assert "systemd-python==235" in requirement
    assert "SupplementaryGroups=systemd-journal" not in drop_in
    assert "SupplementaryGroups=patent-search-journal-readers" in drop_in
    assert "Requires=systemd-journald@patent-search.service" in drop_in
    assert "ExecStartPre=+/usr/bin/systemd-tmpfiles --create" in drop_in
    assert "g patent-search-journal-readers" in sysusers
    assert "/var/log/journal/%m.patent-search" in tmpfiles
    assert "/run/log/journal/%m.patent-search" in tmpfiles
    assert "g:patent-search-journal-readers:r--" in tmpfiles
    assert "g:patent-search-journal-readers:r-x" in tmpfiles
    assert "d:g:patent-search-journal-readers:r--" in tmpfiles
    assert "r-X" not in tmpfiles
    for boundary in (
        "patent-search` namespace",
        "5,000",
        "16 KiB",
        "250 ms",
        "unavailable",
        "ADMIN_LOG_SOURCE=process",
        "patent-search-journal-readers",
    ):
        assert boundary in runbook
