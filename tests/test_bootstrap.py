from scripts.bootstrap import main


def test_bootstrap_runs():
    assert main() == 0
