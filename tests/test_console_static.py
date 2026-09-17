"""验证检索控制台的静态安全策略、查询语义提示与本地状态清理。"""

from pathlib import Path
import re

from app.core.security import require_console_access
from app.main import app
from app.mappings.query_field_mapping import VECTOR_FIELD_REGISTRY


CONSOLE_HTML = Path("app/static/console/index.html")


def test_console_explains_ipc_exact_semantics(client):
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        response = client().get("/console/")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "ipc:F16K31" in response.text
    assert "IPC 精确语义" in response.text
    assert "仅匹配真实完整分类" in response.text


def test_console_builds_public_vector_field_options_from_the_shared_registry(client):
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        response = client().get("/console/")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "<!-- VECTOR_FIELD_OPTIONS -->" not in response.text
    for public_name in VECTOR_FIELD_REGISTRY:
        assert f'value="{public_name}"' in response.text
    for definition in VECTOR_FIELD_REGISTRY.values():
        assert definition.opensearch_field not in response.text
        assert definition.embedding_model not in response.text
        assert str(definition.dimensions) not in response.text


def test_console_has_no_inline_event_handlers_or_backend_html_sink():
    html = CONSOLE_HTML.read_text(encoding="utf-8")

    assert not re.search(r"\son(?:click|change|input|error|load|keydown)\s*=", html, re.I)
    assert html.count(".innerHTML") == 1
    assert "container.innerHTML = renderBuilderGroup(builderRoot, 0, true);" in html
    assert "escapeHtml(" not in html


def test_console_uses_text_nodes_for_untrusted_response_fields():
    html = CONSOLE_HTML.read_text(encoding="utf-8")

    for required_safe_path in (
        "recordsEl.replaceChildren(...records)",
        "document.getElementById('detailRaw').replaceChildren(domElement('pre', '', jsonText(data)))",
        "detailJsonSection('非专利引用', data.non_patent_references || [])",
        "replaceWithStrongMessage(result, '校验失败'",
    ):
        assert required_safe_path in html


def test_console_never_persists_queries_or_results_and_clears_legacy_state():
    html = CONSOLE_HTML.read_text(encoding="utf-8")

    assert "localStorage.setItem" not in html
    assert "sessionStorage.setItem" not in html
    assert "localStorage.removeItem(LEGACY_SEARCH_STATE_KEY)" in html
    assert "sessionStorage.removeItem(LEGACY_SEARCH_STATE_KEY)" in html
    assert "clearLegacySensitiveState();" in html
    assert "saveState" not in html
    assert "loadState" not in html
