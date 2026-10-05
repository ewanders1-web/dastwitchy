"""JSON state helpers for DasTwitchy. All state lives under <project>/state/."""
import json
from pathlib import Path


def load(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(path)


class State:
    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.queue_file = self.dir / "processing_queue.json"
        self.done_dir = self.dir / "done"
        self.done_dir.mkdir(exist_ok=True)
        self.reported_file = self.dir / "reported.json"

    def get_queue(self):
        return load(self.queue_file, [])

    def enqueue(self, path):
        q = self.get_queue()
        p = str(path)
        if p not in q:
            q.append(p)
            save(self.queue_file, q)

    def dequeue(self):
        q = self.get_queue()
        if not q:
            return None
        p = q.pop(0)
        save(self.queue_file, q)
        return p

    def write_done(self, base, payload):
        save(self.done_dir / f"{base}.json", payload)

    def done_bases(self):
        return {p.stem for p in self.done_dir.glob("*.json")}

    def get_reported(self):
        return load(self.reported_file, [])

    def mark_reported(self, base):
        r = self.get_reported()
        if base not in r:
            r.append(base)
            save(self.reported_file, r)

    def unmark_reported(self, base):
        r = [b for b in self.get_reported() if b != base]
        save(self.reported_file, r)
