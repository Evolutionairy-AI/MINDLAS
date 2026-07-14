import mindlas


def test_version_present():
    assert isinstance(mindlas.__version__, str)
    assert mindlas.__version__  # non-empty
