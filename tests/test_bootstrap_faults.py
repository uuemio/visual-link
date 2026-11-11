"""Inject storage faults through exchange with a real publisher and capture process."""
from contextlib import ExitStack
import json
import multiprocessing as mp
from pathlib import Path
import tempfile
from threading import Event, enumerate as threads
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from visual_link.bootstrap import exchange
from visual_link.core.protocol import digest
from visual_link.core.storage import DiskReassembler
from visual_link.session import NegotiationSession
from visual_link.transport import Transport
from test_transport import peers


def idle_capture(config, directory, messages, stop, heartbeat):
    messages.put(dict(probe=dict(capture={}, measured_ms=[1, 2, 33])))
    while not stop.wait(0.01):
        heartbeat.value = time.monotonic()
    messages.cancel_join_thread()


class FaultyWriteStream:
    """Forward descriptor state and cleanup while replacing only write."""
    def __init__(self, stream, fault):
        self.stream = stream
        self.fault = fault

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, data):
        if self.fault == 'write':
            raise OSError('injected disk full')
        return 0


class BootstrapFaultTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.local, self.peer = peers(window=8)
        self.receivers, self.transports = [], []
        self.entered, self.restarted = Event(), Event()
        self.data = b'fault injection payload'
        sha = digest(self.data).hex()
        self.frame = SimpleNamespace(data=self.data, integrity=True, failed_blocks=[],
            metadata=dict(session=self.peer.data_session.hex(), config_id=self.peer.direction(True)[0],
                          file_id=sha[:24], file_sha256=sha, frame_sha256=sha,
                          index=0, count=1, total=len(self.data), length=len(self.data), n=255, k=247))

    def run_fault(self, fault, restart=False):
        ctx = mp.get_context('spawn')
        messages, stop = ctx.Queue(maxsize=8), ctx.Event()
        heartbeat = ctx.Value('d', time.monotonic())
        process_context = SimpleNamespace(Queue=lambda **kw: messages, Event=lambda: stop,
                                          Value=lambda *args: heartbeat, Process=ctx.Process)
        before_children = {child.pid for child in mp.active_children()}
        before_threads = set(threads())
        original_open, original_receive = Path.open, self.local.receive
        original_fsync = __import__('os').fsync

        def receiver(*args):
            value = DiskReassembler(*args)
            self.receivers.append(value)
            return value

        def transport(*args):
            value = Transport(*args)
            self.transports.append(value)
            return value

        def receive(message):
            original_receive(message)
            if message.sender == 'c'*32:
                self.restarted.set()

        def fsync(fd):
            self.entered.set()
            if restart and not self.restarted.wait(5):
                raise TimeoutError('test restart never reached session')
            if fault == 'fsync':
                raise OSError('injected fsync failure')
            return original_fsync(fd)

        def open_file(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if args == ('x+b',) and fault in ('write', 'short_write'):
                # Keep real seek/close/descriptor behavior; inject only write.
                stream = FaultyWriteStream(stream, fault)
            return stream

        def display(config, image, seconds, ready, advance):
            messages.put(dict(frame=self.frame))
            deadline = time.monotonic()+5
            if restart:
                while not self.entered.is_set():
                    if time.monotonic() >= deadline:
                        self.fail('publisher never entered fsync')
                    advance()
                    time.sleep(0.005)
                self.assertTrue(self.receivers[0].stream is not None)
                self.assertFalse(self.receivers[0].stream.closed)
                self.assertIsNone(self.transports[0].receipt)
                self.assertEqual([m.kind for m in self.transports[0].outgoing()], ['ACK'])
                messages.put(NegotiationSession(local_id='c'*32).outgoing()[0])
                while not self.restarted.is_set():
                    if time.monotonic() >= deadline:
                        self.fail('restart not processed')
                    advance()
                    time.sleep(0.005)
                # Old-session data cannot become new-session data after reset.
                messages.put(dict(frame=self.frame))
                self.assertIsNot(self.transports[-1], self.transports[0])
                while not (self.root/'data.jsonl').exists() or 'rejected_identity' not in (self.root/'data.jsonl').read_text():
                    if time.monotonic() >= deadline:
                        self.fail('old-session frame not processed')
                    advance()
                    time.sleep(0.005)
                return
            while time.monotonic() < deadline:
                advance()
                time.sleep(0.005)
            self.fail('injected failure was not propagated')

        with ExitStack() as stack:
            stack.enter_context(patch('visual_link.bootstrap.mp.get_context', return_value=process_context))
            stack.enter_context(patch('visual_link.bootstrap.NegotiationSession', return_value=self.local))
            stack.enter_context(patch('visual_link.discovery.display_info', return_value={}))
            stack.enter_context(patch('visual_link.bootstrap.encode', return_value=np.zeros((2, 2), np.uint8)))
            stack.enter_context(patch('visual_link.backends.image_sequence.write'))
            stack.enter_context(patch('visual_link.core.storage.DiskReassembler', side_effect=receiver))
            stack.enter_context(patch('visual_link.transport.Transport', side_effect=transport))
            stack.enter_context(patch.object(self.local, 'receive', side_effect=receive))
            stack.enter_context(patch('visual_link.core.storage.os.fsync', side_effect=fsync))
            stack.enter_context(patch.object(Path, 'open', autospec=True, side_effect=open_file))
            if fault == 'link':
                stack.enter_context(patch('visual_link.core.storage.os.link', side_effect=OSError('injected link failure')))
            try:
                with self.assertRaises((OSError, ValueError)):
                    exchange(dict(timeout=10), self.root, 1, display=display,
                             receive_target=idle_capture, calibrate=True, negotiate=True)
            finally:
                # Failures must exit without live capture or publisher resources.
                leaked = [c for c in mp.active_children() if c.pid not in before_children]
                try:
                    self.assertFalse(leaked)
                    self.assertFalse([t for t in threads() if t not in before_threads
                                      and t.name.startswith('ThreadPoolExecutor')])
                    self.assertTrue(stop.is_set())
                    for value in self.receivers:
                        if value.stream is not None:
                            self.assertTrue(value.stream.closed)
                finally:
                    for child in leaked:
                        child.terminate()
                        child.join(3)
                        if child.is_alive():
                            child.kill()
                            child.join()
        result = json.loads((self.root/'result.json').read_text())
        self.assertEqual(result['status'], 'failed')
        # Archive collision occurs after successful local publication, but the
        # interrupted session still must fail and must never issue FINISH.
        self.assertEqual(result['receive_complete'], fault == 'archive_exists')
        self.assertFalse(result['transport']['receive_finish_confirmed'])
        self.assertTrue(all(t.receipt is None for t in self.transports))
        return result

    def test_write_failure_is_not_acknowledged(self):
        result = self.run_fault('write')
        self.assertIn('injected disk full', result['error'])
        self.assertIsNone(self.transports[0].feedback)
        self.assertFalse(self.receivers[0].frames)
        self.assertTrue(self.receivers[0].partial.exists())
        self.assertFalse((self.root/'received.bin').exists())

    def test_short_write_is_not_acknowledged(self):
        result = self.run_fault('short_write')
        self.assertIn('short fragment write', result['error'])
        self.assertIsNone(self.transports[0].feedback)
        self.assertFalse(self.receivers[0].frames)
        self.assertFalse((self.root/'received.bin').exists())

    def test_fsync_failure_never_finishes(self):
        result = self.run_fault('fsync')
        self.assertIn('injected fsync failure', result['error'])
        self.assertIsNotNone(self.transports[0].feedback)
        self.assertTrue(self.receivers[0].partial.exists())
        self.assertFalse((self.root/'received.bin').exists())

    def test_link_failure_never_finishes(self):
        result = self.run_fault('link')
        self.assertIn('injected link failure', result['error'])
        self.assertTrue(self.receivers[0].partial.exists())
        self.assertFalse((self.root/'received.bin').exists())

    def test_existing_output_is_preserved(self):
        destination = self.root/'received.bin'
        destination.write_bytes(b'original output')
        self.run_fault('existing')
        self.assertEqual(destination.read_bytes(), b'original output')
        self.assertEqual(self.receivers[0].partial.read_bytes(), self.data)

    def test_restart_during_publish_archives_without_old_finish(self):
        self.run_fault('none', restart=True)
        self.assertEqual((self.root/('received-'+'b'*32+'.bin')).read_bytes(), self.data)
        self.assertTrue((self.root/('received-'+'b'*32+'.json')).exists())
        self.assertFalse((self.root/'received.bin').exists())
        self.assertEqual(self.local.peer_id, 'c'*32)
        self.assertFalse(self.receivers[-1].frames)
        events = [json.loads(line) for line in (self.root/'data.jsonl').read_text().splitlines()]
        self.assertIn('rejected_identity', [event['direction'] for event in events])

    def test_restart_during_failed_publish_preserves_partial(self):
        result = self.run_fault('fsync', restart=True)
        self.assertIn('injected fsync failure', result['error'])
        self.assertTrue(self.restarted.is_set())
        self.assertEqual(self.receivers[0].partial.read_bytes(), self.data)
        self.assertFalse((self.root/'received.bin').exists())

    def test_restart_does_not_overwrite_archived_output(self):
        archive = self.root/('received-'+'b'*32+'.bin')
        archive.write_bytes(b'previous evidence')
        self.run_fault('archive_exists', restart=True)
        self.assertEqual(archive.read_bytes(), b'previous evidence')
        self.assertEqual((self.root/'received.bin').read_bytes(), self.data)


if __name__ == '__main__':
    unittest.main()
