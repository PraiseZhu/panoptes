"""Locked read-modify-write of shared JSON record files (feishu-sent.json, handoff/<owner>/issues.json), same protocol as the
Grok-line report/lib/jsonlog.mjs: O_EXCL lock file <file>.lock (stale after 60 s), re-read under the lock, write tmp + rename.
update(path, fn): fn(doc) mutates/returns doc (None = no change, nothing written). Returns fn's result."""
import os, json, time

def _lock(path, timeout=30):
    lock = path + ".lock"; t0 = time.time()
    while True:
        try: os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)); return lock
        except FileExistsError:
            try:
                if time.time() - os.stat(lock).st_mtime > 60: os.unlink(lock); continue
            except FileNotFoundError: continue
            if time.time() - t0 > timeout: raise RuntimeError(f"lock busy: {lock}")
            time.sleep(0.1)

def update(path, fn, default=None):
    lock = _lock(path)
    try:
        try: doc = json.load(open(path))
        except FileNotFoundError:
            if default is None: raise
            doc = default
        res = fn(doc)
        if res is not None:
            tmp = f"{path}.tmp-{os.getpid()}"; open(tmp, "w").write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n"); os.replace(tmp, path)
        return res
    finally:
        try: os.unlink(lock)
        except FileNotFoundError: pass
