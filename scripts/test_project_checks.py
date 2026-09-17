"""将独立控制台契约检查纳入 pytest 的项目级回归入口。"""

from scripts import check_console_coverage, check_docs


def test_console_coverage_contract() -> None:
    assert check_console_coverage.main() == 0


def test_documentation_contract() -> None:
    assert check_docs.main() == 0
