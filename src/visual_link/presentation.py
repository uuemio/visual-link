"""Prefetch a pre-encoded PNG sequence into a bounded display queue."""
import queue
import threading
import time

from .core.mapping import read_image


class Presentation:
    def __init__(self, paths, fps, log, queue_size=2):
        if not paths:
            raise ValueError('display-stream requires a nonempty PNG directory')
        self.paths, self.period, self.log = paths, 1/fps, log
        self.queue = queue.Queue(maxsize=queue_size)
        self.stop = threading.Event()
        self.shown = 0
        self.late = 0
        self.last = None
        self.thread = threading.Thread(target=self._prefetch, daemon=True)
        self.thread.start()

    def _prefetch(self):
        index = 0
        try:
            while not self.stop.is_set():
                item = (index, read_image(self.paths[index % len(self.paths)]))
                while not self.stop.is_set():
                    try:
                        self.queue.put(item, timeout=0.1)
                        break
                    except queue.Full:
                        pass
                index += 1
        except Exception as exc:
            while not self.stop.is_set():
                try:
                    self.queue.put(exc, timeout=0.1)
                    return
                except queue.Full:
                    pass

    def first(self, timeout):
        try:
            item = self.queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise ValueError('display prefetch timed out') from exc
        if isinstance(item, Exception):
            raise ValueError(f'display prefetch failed: {item}') from item
        return item[1]

    def advance(self):
        now = time.monotonic()
        if self.last is None:
            self._record(0, now)
            return None
        if now-self.last < self.period:
            return None
        try:
            item = self.queue.get_nowait()
        except queue.Empty:
            raise ValueError('display prefetch starved; reduce FPS')
        if isinstance(item, Exception):
            raise ValueError(f'display prefetch failed: {item}') from item
        index, image = item
        if now-self.last > self.period+0.05:
            self.late += 1
        self._record(index, now)
        return image

    def _record(self, index, now):
        import json
        self.log.write(json.dumps(dict(presentation=index, file=str(self.paths[index % len(self.paths)]),
                                      monotonic_ns=time.monotonic_ns(),
                                      interval_seconds=None if self.last is None else now-self.last))+'\n')
        self.log.flush()
        self.last = now
        self.shown += 1

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)
