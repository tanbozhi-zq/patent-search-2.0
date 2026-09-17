from scripts.check_docs import check_document


def test_broken_link_and_anchor_are_rejected(tmp_path):
    target = tmp_path / "target.md"
    target.write_text("# 中文标题\n\n# 中文标题\n")
    page = tmp_path / "README.md"
    page.write_text("[ok](target.md#中文标题-1)\n[bad](target.md#missing)\n[absent](gone.md)\n")
    errors = check_document(page, tmp_path)
    assert len(errors) == 2
    assert any("missing heading" in error for error in errors)
    assert any("missing repository link" in error for error in errors)


def test_invalid_json_is_rejected_and_example_links_are_ignored(tmp_path):
    page = tmp_path / "README.md"
    page.write_text('```text\n[example](missing.md)\n```\n\n```json\n{"broken": }\n```\n')
    assert check_document(page, tmp_path) == ["README.md: invalid JSON code block"]


def test_invalid_search_example_is_rejected(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    page = docs / "api.md"
    page.write_text('```json\n{"mode":"vector","q":"forbidden","semantic_text":"valve","vector_fields":["abstract"]}\n```\n')
    assert check_document(page, tmp_path) == ["docs/api.md: search example violates request contract"]
