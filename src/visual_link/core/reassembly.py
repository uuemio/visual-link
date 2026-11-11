from .protocol import digest

class Reassembler:
    def __init__(self):
        self.frames = {}
        self.first = None
        self.duplicates = 0

    def add(self, frame):
        if not frame.integrity or frame.failed_blocks:
            raise ValueError('invalid frame')
        m = frame.metadata
        if self.first is None:
            self.first = m
        if any(m.get(k) != self.first.get(k) for k in ('count','total','file_sha256','n','k','session','file_id')):
            raise ValueError('mixed file metadata')
        index = m['index']
        if index in self.frames:
            if self.frames[index].data != frame.data or self.frames[index].metadata != m:
                raise ValueError('conflicting duplicate')
            self.duplicates += 1
        else:
            self.frames[index] = frame

    def stats(self):
        return dict(expected=None if self.first is None else self.first['count'],
                    received=len(self.frames), duplicates=self.duplicates,
                    missing=None if self.first is None else self.first['count']-len(self.frames))

    def finish(self):
        if self.first is None or len(self.frames) != self.first['count']:
            raise ValueError('missing frames: '+str(self.stats()))
        data = b''.join(self.frames[i].data for i in range(self.first['count']))
        if len(data) != self.first['total'] or digest(data).hex() != self.first['file_sha256']:
            raise ValueError('file length or SHA mismatch')
        return data
