from pathlib import Path
from vela.workspace import Workspace


def test_write_and_read(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.write_file("hello.txt", "hello\n")
    assert ws.read_file("hello.txt") == "hello\n"


def test_nested_write(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.write_file("src/main.py", "print('ok')\n")
    assert "src/main.py" in ws.list_files()


def test_stale_hash_on_deleted_file(tmp_path: Path):
    import pytest
    from vela.workspace import ConcurrentEditError
    ws = Workspace(tmp_path)
    ws.write_file("f.txt", "x\n")
    (tmp_path / "f.txt").unlink()
    with pytest.raises(ConcurrentEditError):
        ws.write_file("f.txt", "y\n", expected_hash="deadbeef")
