"""Bounded capture/decode pipeline. RX inputs are device pixels and configuration only."""
import json
import multiprocessing as mp
import queue
import time
from pathlib import Path

import numpy as np
from reedsolo import ReedSolomonError

from .backends.camera import capture_frames
from .backends.image_sequence import write
from .core.mapping import image_to_wire
from .core.protocol import decode, digest
from .core.reassembly import Reassembler
from .hardware import save_json


def _capture(config, directory, images, stop, done, counts, heartbeat):
    metadata = {}
    previous = None
    try:
        for image in capture_frames(config, metadata, stop.is_set):
            heartbeat.value = time.monotonic()
            sequence = counts[0]
            counts[0] += 1
            if previous is not None and np.array_equal(previous, image):
                counts[3] += 1
                continue
            try:
                snapshot = image.copy()
                images.put_nowait((sequence, time.monotonic_ns(), snapshot))
                counts[1] += 1
                previous = snapshot
            except queue.Full:
                counts[2] += 1
        metadata['status'] = 'stopped'
    except Exception as exc:
        metadata.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    finally:
        save_json(Path(directory)/'device.json', metadata)
        # Let the feeder deliver accepted items before announcing end of input.
        images.close()
        images.join_thread()
        done.set()


def _decode(config, directory, images, done, heartbeat):
    directory = Path(directory)
    receiver = Reassembler()
    rejected = accepted = corrected = 0
    complete = False
    start = time.monotonic()
    try:
        with (directory/'frames.jsonl').open('x', encoding='utf-8') as log:
            while True:
                heartbeat.value = time.monotonic()
                try:
                    sequence, captured_ns, image = images.get(timeout=0.1)
                except queue.Empty:
                    if done.is_set():
                        break
                    continue
                began = time.monotonic()
                event = dict(sequence=sequence, captured_monotonic_ns=captured_ns,
                             queue_age_seconds=(time.monotonic_ns()-captured_ns)/1e9)
                try:
                    oriented = image[::-1] if config['orientation'] == 'flip_vertical' else image
                    frame = decode(image_to_wire(oriented, captured=True, layout='column'))
                    receiver.add(frame)
                    accepted += 1
                    corrected += frame.corrected_symbols
                    event.update(status='accepted', index=frame.metadata['index'],
                                 corrected_symbols=frame.corrected_symbols)
                    if not complete and receiver.stats()['missing'] == 0:
                        data = receiver.finish()
                        # Partial work never creates the final filename.
                        temporary = directory/'recovered.partial'
                        with temporary.open('xb') as output:
                            output.write(data)
                            output.flush()
                            import os
                            os.fsync(output.fileno())
                        destination = directory/'recovered.bin'
                        with destination.open('xb'):
                            pass
                        temporary.replace(destination)
                        complete = True
                        save_json(directory/'file.json', dict(bytes=len(data), sha256=digest(data).hex(),
                                  completed_after_seconds=time.monotonic()-start))
                except (ValueError, ReedSolomonError) as exc:
                    rejected += 1
                    event.update(status='rejected', error=str(exc))
                    # Retain the first rejection, bound image storage independently of duration.
                    if rejected == 1:
                        write(directory/'first-rejected.png', image)
                event['decode_seconds'] = time.monotonic()-began
                log.write(json.dumps(event)+'\n')
                log.flush()
        save_json(directory/'rx.json', dict(status='complete' if complete else 'incomplete',
                  **receiver.stats(), accepted=accepted, rejected=rejected,
                  corrected_symbols=corrected, elapsed_seconds=time.monotonic()-start))
    except Exception as exc:
        save_json(directory/'rx.json', dict(status='failed', **receiver.stats(), error=str(exc)))
        raise


def receive_stream(config, directory, seconds, queue_size=2, capture_target=_capture,
                   decode_target=_decode):
    """Supervise both stages; terminate stalled open/read/decode, drain on normal stop."""
    ctx = mp.get_context('spawn')
    images = ctx.Queue(maxsize=queue_size)
    stop, done = ctx.Event(), ctx.Event()
    counts = ctx.Array('q', 4)  # captured, enqueued, queue drops, identical pixels (one writer)
    capture_beat, decode_beat = ctx.Value('d', time.monotonic()), ctx.Value('d', time.monotonic())
    directory = Path(directory)
    producer = ctx.Process(target=capture_target, args=(config, str(directory), images, stop, done, counts, capture_beat))
    consumer = ctx.Process(target=decode_target, args=(config, str(directory), images, done, decode_beat))
    started = time.monotonic()
    failure = None
    stopped_at = None
    try:
        consumer.start()
        producer.start()
        while producer.is_alive() or consumer.is_alive():
            now = time.monotonic()
            if now-started >= seconds and stopped_at is None:
                stop.set()
                stopped_at = now
            if producer.is_alive() and now-capture_beat.value > config['timeout']:
                failure = 'capture stalled beyond timeout'
            if consumer.is_alive() and now-decode_beat.value > config['timeout']:
                failure = 'decode stalled beyond timeout'
            if stopped_at is not None and now-stopped_at > config['timeout']:
                failure = 'pipeline did not drain before timeout'
            if consumer.exitcode is not None and (consumer.exitcode != 0 or not done.is_set()):
                failure = 'decoder exited before capture completion'
            if producer.exitcode is not None and not done.is_set():
                failure = 'capture exited without completion'
            if failure:
                break
            time.sleep(0.05)
    finally:
        stop.set()
        for process in (producer, consumer):
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join()
        images.close()
    rx_path = directory/'rx.json'
    rx = json.loads(rx_path.read_text()) if rx_path.exists() else dict(expected=None, received=0, missing=None)
    device_path = directory/'device.json'
    device = json.loads(device_path.read_text()) if device_path.exists() else {}
    if device.get('status') != 'stopped':
        failure = failure or device.get('error', 'capture worker did not finish normally')
    report = dict(rx, captured=counts[0], enqueued=counts[1], queue_full_drops=counts[2],
                  identical_pixels_filtered=counts[3],
                  queue_capacity=queue_size, capture_window_seconds=seconds,
                  elapsed_seconds=time.monotonic()-started,
                  capture_exitcode=producer.exitcode, decode_exitcode=consumer.exitcode)
    if failure:
        report.update(status='failed', error=failure)
    elif rx.get('status') != 'complete':
        report.update(status='incomplete', error='missing frames at end of capture window')
    file_path = directory/'file.json'
    if file_path.exists():
        report['file'] = json.loads(file_path.read_text())
        report['unique_file_bytes_per_window_second'] = report['file']['bytes']/seconds
    report['stability_pass'] = (report.get('status') == 'complete' and counts[2] == 0
                                and report.get('rejected') == 0
                                and report.get('accepted') == counts[1])
    save_json(directory/'result.json', report)
    if report['status'] != 'complete':
        raise ValueError(report.get('error', 'stream reception failed'))
    return report
