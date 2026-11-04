"""Locked read-modify-write of shared JSON record files (feishu-sent.json, handoff/<owner>/issues.json), same protocol as the
Grok-line report/lib/jsonlog.mjs: O_EXCL lock file <file>.lock (stale after 60 s), re-read under the lock, write tmp + rename.
update(path, fn): fn(doc) mutates/returns doc (None = no change, nothing written). Returns fn's result."""
import fcntl, os, json, time

FLOCK_STALE_S = 2   # 11-05: an EMPTY <file>.lock older than this that nobody flock()s = left behind by a flock writer -> stale at once

def _flock_free(lock):
    try: fd = os.open(lock, os.O_RDONLY)
    except OSError: return False
    try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); fcntl.flock(fd, fcntl.LOCK_UN); return True
    except OSError: return False
    finally: os.close(fd)

def _lock(path, timeout=30):
    """O_EXCL <file>.lock (writes our pid; stale after 60 s), and flock() on it while held. 11-05: pmnudge.record_gap used to
    flock gaps-open.json.lock and leave the file, which this protocol read as held (lost updates / a 30 s hang). Now every Python
    writer goes through update(); an empty lock file nobody flock()s is a leftover and is taken over at once, and a flock-only
    writer that still holds it (an old deploy) is waited for -- the flock below makes the two mechanisms exclude each other."""
    lock = path + ".lock"; t0 = time.time()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.write(fd, f"{os.getpid()}\n".encode())
            while True:
                try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); return lock, fd
                except OSError:
                    if time.time() - t0 > timeout: os.close(fd); os.unlink(lock); raise RuntimeError(f"lock busy (flock): {lock}")
                    time.sleep(0.05)
        except FileExistsError:
            try:
                st = os.stat(lock); age = time.time() - st.st_mtime
                if age > 60 or (st.st_size == 0 and age > FLOCK_STALE_S and _flock_free(lock)): os.unlink(lock); continue
            except FileNotFoundError: continue
            if time.time() - t0 > timeout: raise RuntimeError(f"lock busy: {lock}")
            time.sleep(0.1)

def update(path, fn, default=None):
    lock, fd = _lock(path)
    try:
        try:
            with open(path) as f: doc = json.load(f)
        except FileNotFoundError:
            if default is None: raise
            doc = default
        res = fn(doc)
        if res is not None:
            tmp = f"{path}.tmp-{os.getpid()}"; 
            with open(tmp, "w") as f: f.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
            os.replace(tmp, path)
        return res
    finally:
        try: os.unlink(lock)
        except FileNotFoundError: pass
        os.close(fd)
