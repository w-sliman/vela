from pathlib import Path
import pytest
from coding_agent.policy import classify_command, ensure_within


def test_escape_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        ensure_within(tmp_path, "../secret.txt")


def test_pytest_allowed(tmp_path: Path):
    assert classify_command("pytest -q", tmp_path).action == "allow"


def test_rm_requires_approval(tmp_path: Path):
    assert classify_command("rm -rf build", tmp_path).action == "approve"


def test_sudo_requires_approval(tmp_path: Path):
    assert classify_command("sudo systemctl restart nginx", tmp_path).action == "approve"


def test_pipe_to_shell_requires_approval(tmp_path: Path):
    assert classify_command("echo pwned | bash", tmp_path).action == "approve"


def test_python_dash_c_requires_approval(tmp_path: Path):
    assert classify_command('python -c "import os; os.system(1)"', tmp_path).action == "approve"


def test_find_delete_requires_approval(tmp_path: Path):
    assert classify_command("find / -name x -delete", tmp_path).action == "approve"


def test_pip_install_requires_approval(tmp_path: Path):
    assert classify_command("pip install requests", tmp_path).action == "approve"


def test_compound_command_requires_approval(tmp_path: Path):
    assert classify_command("git status && git diff", tmp_path).action == "approve"


def test_simple_readonly_commands_still_allowed(tmp_path: Path):
    for cmd in ("pytest -q", "git status", "ls", "grep foo bar.txt"):
        assert classify_command(cmd, tmp_path).action == "allow"
