from scripts import check_console_coverage


def test_console_coverage_contract() -> None:
    assert check_console_coverage.main() == 0
