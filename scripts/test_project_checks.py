from scripts import check_console_coverage, check_entity_compat_dsl


def test_console_coverage_contract() -> None:
    assert check_console_coverage.main() == 0


def test_entity_compat_dsl_contract() -> None:
    assert check_entity_compat_dsl.main() == 0
