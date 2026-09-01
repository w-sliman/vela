from pathlib import Path
import pytest
from vela.policy import classify_command, ensure_within


def test_escape_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        ensure_within(tmp_path, "../secret.txt")


def test_pytest_allowed():
    assert classify_command("pytest -q").action == "allow"


def test_rm_requires_approval():
    assert classify_command("rm -rf build").action == "approve"


def test_sudo_requires_approval():
    assert classify_command("sudo systemctl restart nginx").action == "approve"


def test_pipe_to_shell_requires_approval():
    assert classify_command("echo pwned | bash").action == "approve"


def test_python_dash_c_requires_approval():
    assert classify_command('python -c "import os; os.system(1)"').action == "approve"


def test_find_delete_requires_approval():
    assert classify_command("find / -name x -delete").action == "approve"


def test_pip_install_requires_approval():
    assert classify_command("pip install requests").action == "approve"


def test_compound_command_requires_approval():
    assert classify_command("git status && git diff").action == "approve"


def test_simple_readonly_commands_still_allowed():
    for cmd in ("pytest -q", "git status", "ls", "grep foo bar.txt"):
        assert classify_command(cmd).action == "allow"


def test_relative_dotdot_requires_approval():
    for cmd in ("cat ../../etc/passwd", "head -n 5 ../../../etc/shadow", "cd ..", "ls ../", "grep -r x ../../etc"):
        assert classify_command(cmd).action == "approve"


def test_tilde_path_requires_approval():
    for cmd in ("cat ~/.ssh/id_rsa", "cd ~", "cat ~/secrets.txt"):
        assert classify_command(cmd).action == "approve"


def test_env_var_path_requires_approval():
    assert classify_command("cat $HOME/.ssh/id_rsa").action == "approve"


def test_dotdot_inside_identifier_still_allowed():
    # '..' as part of a token (not a path component) must not trigger approval.
    for cmd in ("python -m a..b", "echo 1..2"):
        assert classify_command(cmd).action == "allow"


def test_host_sensitive_pseudo_filesystems_require_approval():
    """Reading host state via /proc, /sys, /dev etc. is not a 'read-only dev command'."""
    for cmd in [
        "cat /proc/self/environ",
        "cat /proc/1/cmdline",
        "cat /sys/class/net/eth0/address",
        "cat /dev/mem",
        "cat /boot/grub/grub.cfg",
        "cat /mnt/backup/secrets.txt",
        "ls /srv",
        "ls /media",
        "find /tmp -name '*.key'",
        "cat /run/secrets/token",
        "ls /",
    ]:
        assert classify_command(cmd).action == "approve", cmd


def test_workspace_relative_commands_stay_allowed():
    """The widened host-path rule must not escalate ordinary workspace work."""
    for cmd in [
        "pytest -q",
        "ls",
        "ls ./src",
        "cat src/main.py",
        "cat ./a/b.py",
        "grep -rn TODO .",
        "git status",
        "python manage.py test",
        "echo ok",
    ]:
        assert classify_command(cmd).action == "allow", cmd
