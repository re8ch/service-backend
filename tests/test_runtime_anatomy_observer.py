import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
OBSERVER = ROOT / "clusters/re8ch/charts/runtime-anatomy/files/observer.py"


def load_observer():
    spec = importlib.util.spec_from_file_location("runtime_anatomy_observer", OBSERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_observer_classifies_storage_syscalls_without_leaving_mount_scope(tmp_path):
    observer = load_observer()
    trace = tmp_path / "trace.42"
    trace.write_text("\n".join([
        '12:00:00 openat(AT_FDCWD, "/workspace/a.txt", O_RDONLY) = 3</workspace/a.txt>',
        '12:00:01 openat(AT_FDCWD, "/workspace/new.txt", O_WRONLY|O_CREAT, 0666) = 4</workspace/new.txt>',
        '12:00:02 unlink("/workspace/old.txt") = 0',
        '12:00:03 openat(AT_FDCWD, "/etc/passwd", O_RDONLY) = 5</etc/passwd>',
    ]))
    observer.attach_content_evidence = lambda row, pid: None
    events = observer.parse_trace(tmp_path / "trace", ["/workspace"], 42)
    assert {(event["operation"], event["path"]) for event in events} == {
        ("Read", "/workspace/a.txt"),
        ("Create", "/workspace/new.txt"),
        ("Delete", "/workspace/old.txt"),
    }


def test_observer_marks_sensitive_content_without_reading_it():
    observer = load_observer()
    row = {"path": "/workspace/.aws/credentials"}
    observer.attach_content_evidence(row, 1)
    assert row["contentEvidence"] == {"state": "Redacted", "reason": "SensitivePath"}
