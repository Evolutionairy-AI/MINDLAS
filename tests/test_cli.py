import pytest

from mindlas import __version__
from mindlas.cli import main


def test_no_command_returns_nonzero(capsys):
    rc = main([])
    assert rc == 2


def test_version_flag_prints_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out
