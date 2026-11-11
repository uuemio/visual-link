import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from visual_link.core.protocol import capacity, digest
from visual_link.core.storage import DiskReassembler, FileSource
from visual_link.transport import Sender, Transport
from test_transport import peers


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.receiver = DiskReassembler(self.root/'received.part', self.root/'received.bin')
        self.addCleanup(self.receiver.close)

    def frames(self, data):
        a, b = peers(window=8)
        cap = capacity()
        count = max(1, (len(data)+cap-1)//cap)
        frames = []
        for index in range(count):
            chunk = data[index*cap:(index+1)*cap]
            metadata = dict(index=index, count=count, total=len(data), length=len(chunk),
                            n=255, k=247, file_sha256=digest(data).hex(),
                            frame_sha256=digest(chunk).hex(), session=a.data_session.hex(),
                            config_id=a.direction(True)[0], file_id=digest(data)[:12].hex())
            frames.append(SimpleNamespace(metadata=metadata, data=chunk, integrity=True, failed_blocks=[]))
        return a, b, frames

    def test_source_random_access_empty_and_mutation(self):
        path = self.root/'source.bin'
        for data in (b'', b'a'*capacity()+b'final'):
            path.write_bytes(data)
            source = FileSource(path)
            try:
                self.assertEqual(source.file_hash, digest(data))
                self.assertEqual(b''.join(source.read(i) for i in range(source.count)), data)
                self.assertEqual(source.read(source.count-1), data[capacity()*(source.count-1):])
                with self.assertRaises(ValueError): source.read(source.count)
                with path.open('ab') as stream: stream.write(b'x')
                with self.assertRaisesRegex(ValueError, 'source changed'): source.read(0)
            finally:
                source.close()

    def test_reordering_duplicates_tail_and_empty_publication(self):
        data = b'a'*capacity()+b'b'*capacity()+b'tail'
        _, _, frames = self.frames(data)
        r = self.receiver
        for index in (2, 0, 2): r.add(frames[index])
        self.assertEqual(r.stats()['duplicates'], 1)
        self.assertTrue(all(isinstance(m, dict) for m in r.frames.values()))
        with self.assertRaisesRegex(ValueError, 'missing frames'): r.finish()
        r.add(frames[1])
        self.assertFalse(r.destination.exists())
        self.assertEqual(r.finish(), dict(bytes=len(data), sha256=digest(data).hex()))
        self.assertEqual(r.destination.read_bytes(), data)
        self.assertFalse(r.partial.exists())
        r.add(frames[2])
        self.assertEqual(r.finish()['bytes'], len(data))
        empty = DiskReassembler(self.root/'empty.part', self.root/'empty.bin')
        self.addCleanup(empty.close)
        empty.add(self.frames(b'')[2][0])
        empty.finish()
        self.assertEqual(empty.destination.stat().st_size, 0)

    def test_corruption_io_error_and_destination_never_overwritten(self):
        _, _, frames = self.frames(b'payload')
        r = self.receiver
        r.add(frames[0])
        r.stream.seek(0); r.stream.write(b'X')
        with self.assertRaisesRegex(ValueError, 'SHA mismatch'): r.finish()
        self.assertTrue(r.partial.exists())
        self.assertFalse(r.destination.exists())
        r.stream.seek(0); r.stream.write(b'payload')
        with patch('visual_link.core.storage.os.fsync', side_effect=OSError('disk error')):
            with self.assertRaises(OSError): r.finish()
        self.assertFalse(r.destination.exists())
        r.destination.write_bytes(b'original')
        with self.assertRaises(FileExistsError): r.finish()
        self.assertEqual(r.destination.read_bytes(), b'original')
        self.assertTrue(r.partial.exists())
        other = DiskReassembler(r.partial, self.root/'other.bin')
        self.addCleanup(other.close)
        with self.assertRaises(FileExistsError): other.add(frames[0])

    def test_conflicting_duplicate_and_configuration_rejected(self):
        _, _, frames = self.frames(b'payload')
        r = self.receiver
        frame = frames[0]
        r.add(frame)
        changed = dict(frame.metadata, config_id=99)
        with self.assertRaises(ValueError): r.add(SimpleNamespace(**dict(vars(frame), metadata=changed)))
        changed = dict(frame.metadata, frame_sha256=digest(b'changed').hex())
        with self.assertRaises(ValueError):
            r.add(SimpleNamespace(**dict(vars(frame), metadata=changed, data=b'changed')))
        with self.assertRaises(ValueError): r.add(SimpleNamespace(**dict(vars(frame), data=b'bad')))
        self.assertEqual(r.finish()['sha256'], frame.metadata['file_sha256'])

    def test_ack_during_slow_publish_and_finish_loss(self):
        a, b, frames = self.frames(b'payload')
        sender = Sender(a, frames[0].metadata['file_sha256'], 7, 1, adaptive=True)
        tx, rx = Transport(a, sender), Transport(b)
        sender.next_frame(0)
        r = self.receiver
        r.add(frames[0]); rx.update_receiver(r)
        entered, release = Event(), Event()
        real_fsync = __import__('os').fsync
        def slow_fsync(fd):
            entered.set()
            if not release.wait(5): raise TimeoutError('test publisher stalled')
            return real_fsync(fd)
        with ThreadPoolExecutor(max_workers=1) as executor, \
                patch('visual_link.core.storage.os.fsync', side_effect=slow_fsync):
            future = executor.submit(r.finish)
            try:
                self.assertTrue(entered.wait(3))
                self.assertFalse(future.done())
                self.assertEqual([m.kind for m in rx.outgoing()], ['ACK'])
                tx.receive(rx.outgoing()[0], now=1)
                # Long publication must not cause last-fragment retransmission.
                self.assertIsNone(sender.next_frame(1000))
                self.assertFalse(sender.confirmed)
                r.add(frames[0])  # Duplicate while publisher owns descriptor.
            finally:
                release.set()
            future.result()
        rx.update_receiver(r, delivered=True)
        finish = next(m for m in rx.outgoing() if m.kind == 'FINISH')
        self.assertFalse(sender.confirmed)  # First FINISH lost.
        tx.receive(finish); tx.receive(finish)
        self.assertTrue(sender.confirmed)
        self.assertFalse(rx.finish_confirmed)
        confirmation = next(m for m in tx.outgoing() if m.kind == 'FINISH')
        rx.receive(confirmation); rx.receive(confirmation)
        self.assertTrue(rx.finish_confirmed)
        self.assertEqual(sender.attempts, {0: 1})


if __name__ == '__main__': unittest.main()
