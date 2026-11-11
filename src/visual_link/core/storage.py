"""Bounded payload storage for live sessions; partial files remain as evidence."""
import hashlib
import os
from pathlib import Path

from .protocol import capacity, digest
from .reassembly import Reassembler


def file_digest(stream):
    stream.seek(0)
    sha = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        sha.update(chunk)
    return sha.digest()


class FileSource:
    """Keep one source descriptor; hash and read at most one fragment at a time."""
    def __init__(self, path):
        self.stream = Path(path).open('rb')
        try:
            self.signature = self._signature()
            self.total = self.signature[0]
            self.file_hash = file_digest(self.stream)
            self._check()
            self.count = max(1, (self.total + capacity()-1)//capacity())
        except BaseException:
            self.close()
            raise

    def _signature(self):
        stat = os.fstat(self.stream.fileno())
        return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def _check(self):
        if self._signature() != self.signature:
            raise ValueError('source changed during transfer')

    def read(self, index):
        if not 0 <= index < self.count:
            raise ValueError('source index out of bounds')
        self._check()
        offset = index * capacity()
        length = min(capacity(), max(0, self.total-offset))
        self.stream.seek(offset)
        data = self.stream.read(length)
        self._check()
        if len(data) != length:
            raise ValueError('source truncated during transfer')
        return data

    def close(self):
        self.stream.close()


class DiskReassembler(Reassembler):
    """Write fragments at fixed offsets, retaining only metadata in memory.

    The caller serializes add/close. Once complete, finish may run in another
    thread: duplicate checks use metadata only and never touch its descriptor.
    """
    def __init__(self, partial, destination):
        super().__init__()
        self.partial = Path(partial)
        self.destination = Path(destination)
        self.stream = None
        self.published = False

    def add(self, frame):
        if not frame.integrity or frame.failed_blocks:
            raise ValueError('invalid frame')
        m = frame.metadata
        cap = capacity()
        if (m['count'] != max(1, (m['total']+cap-1)//cap)
                or not 0 <= m['index'] < m['count']
                or len(frame.data) != min(cap, max(0, m['total']-m['index']*cap))
                or digest(frame.data).hex() != m['frame_sha256']):
            raise ValueError('invalid fragment size or SHA')
        if self.first is not None and any(m.get(k) != self.first.get(k) for k in
                ('count', 'total', 'file_sha256', 'n', 'k', 'session', 'config_id', 'file_id')):
            raise ValueError('mixed file metadata')
        index = m['index']
        if index in self.frames:
            if self.frames[index] != m:
                raise ValueError('conflicting duplicate')
            self.duplicates += 1
            return
        if self.stream is None:
            self.stream = self.partial.open('x+b')
        self.stream.seek(index*cap)
        if self.stream.write(frame.data) != len(frame.data):
            raise OSError('short fragment write')
        self.first = dict(m) if self.first is None else self.first
        self.frames[index] = dict(m)

    def finish(self):
        if self.first is None or self.stats()['missing'] != 0:
            raise ValueError('missing frames: '+str(self.stats()))
        if not self.published:
            self.stream.flush()
            if (os.fstat(self.stream.fileno()).st_size != self.first['total']
                    or file_digest(self.stream).hex() != self.first['file_sha256']):
                raise ValueError('file length or SHA mismatch')
            os.fsync(self.stream.fileno())
            self.stream.close()
            # An atomic link fails if destination exists. Never replace evidence.
            os.link(self.partial, self.destination)
            self.published = True
            self.partial.unlink()
        return dict(bytes=self.first['total'], sha256=self.first['file_sha256'])

    def close(self):
        if self.stream is not None:
            self.stream.close()
