from coding_agent.cli import make_approval_callback


def test_auto_mode_allows():
    assert make_approval_callback('auto')('rm -rf build', 'destructive') is True


def test_deny_mode_rejects():
    assert make_approval_callback('deny')('rm -rf build', 'destructive') is False
