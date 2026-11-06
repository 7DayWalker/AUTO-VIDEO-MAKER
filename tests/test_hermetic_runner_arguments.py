"""Flag-only hermetic runs must never collect root-level live smoke scripts."""
import pytest

from tools.run_hermetic_pytest import _pytest_arguments


@pytest.mark.parametrize("arguments,expected", [
    ([], ["tests"]),
    (["-q"], ["tests", "-q"]),
    (["-q", "--tb=short"], ["tests", "-q", "--tb=short"]),
    (["-q", "-k", "recap"], ["tests", "-q", "-k", "recap"]),
    (["--tb", "short", "-m", "not slow"], ["tests", "--tb", "short", "-m", "not slow"]),
    (["tests", "-q"], ["tests", "-q"]),
    (["tests/test_recap_budget.py::test_custom_source_ceiling_and_tiny_fixture_exemption_are_explicit", "-q"],
     ["tests/test_recap_budget.py::test_custom_source_ceiling_and_tiny_fixture_exemption_are_explicit", "-q"]),
])
def test_only_explicit_targets_override_the_tests_directory(arguments, expected):
    before = arguments.copy()
    assert _pytest_arguments(arguments) == expected
    assert arguments == before