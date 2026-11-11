"""Bounded file contracts shared by ingestion nodes; no business interpretation."""
import hashlib
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path


def integer(value, name, default, maximum):
    if value is None:
        value = default
    if isinstance(value, bool) or int(value) != float(value) or not 1 <= int(value) <= maximum:
        raise ValueError(f"invalid_{name}")
    return int(value)


def descriptor(path, filename, content_type):
    return {"tmp_path": str(path), "filename": filename, "content_type": content_type}


def temporary(suffix):
    f = tempfile.NamedTemporaryFile(prefix="ingestion-", suffix=suffix, delete=False)
    f.close()
    return Path(f.name)


def digest_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


@contextmanager
def memory_budget(limit):
    """Limit address space in Unix runners; Windows retains the size/count limits."""
    try:
        import resource
    except ImportError:
        yield False
        return
    previous = resource.getrlimit(resource.RLIMIT_AS)
    ceilings = [limit] + [v for v in previous if v != resource.RLIM_INFINITY]
    resource.setrlimit(resource.RLIMIT_AS, (min(ceilings), previous[1]))
    try:
        yield True
    finally:
        resource.setrlimit(resource.RLIMIT_AS, previous)
