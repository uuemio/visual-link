"""Bidirectional video HELLO/CAPS runner using existing native display adapters."""
import multiprocessing as mp
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from .backends.camera import capture_frames
from .core.control import decode, encode
from .hardware import save_json
from .session import Session, State, Capabilities, CalibrationSession, NegotiationSession
from . import calibration


def _archive(source, destination):
    """Retain session evidence without replacing an existing archive."""
    destination.hardlink_to(source)
    source.unlink()


def _receive(config, directory, messages, stop, heartbeat):
    stats = dict(captured=0, control_decoded=0, data_decoded=0,
                 data_decode_failed=0, duplicate_images=0,
                 control_cache_hits=0,
                 data_queue_dropped=0, control_queue_dropped=0,
                 control_decode_seconds=0.0, data_decode_seconds=0.0)
    metadata = dict(receive_stats=stats)
    saved = set()
    peer = target = measured = None
    captured_times = []
    previous = None
    control_image = control_message = None
    data_saved = set()
    dropped_data = set()
    dropped_control = set()
    suppressed_data = suppressed_control = None
    faults = config.get('test_faults', {})
    automatic = config.get('_calibrate', False)
    control_cache = config.get('test_control_cache', True)
    try:
        for image in capture_frames(config, metadata, stop.is_set):
            stats['captured'] += 1
            heartbeat.value = time.monotonic()
            if automatic and len(captured_times) < 4:
                captured_times.append(time.monotonic())
                if len(captured_times) == 4:
                    from .discovery import codec_timings
                    import statistics
                    timings = codec_timings()+[round(statistics.median(b-a for a,b in zip(captured_times,captured_times[1:]))*1000,3)]
                    messages.put(dict(probe=dict(capture=dict(metadata), measured_ms=timings)), timeout=config['timeout'])
                    heartbeat.value = time.monotonic()
                continue
            started = time.monotonic()
            try:
                try:
                    import numpy as np
                    if control_cache and control_image is not None and np.array_equal(control_image, image):
                        message = control_message
                        stats['control_cache_hits'] += 1
                    else:
                        # Keep one owned image: capture drivers may reuse buffers.
                        control_image = control_message = None
                        message = decode(image)
                        if control_cache:
                            control_image, control_message = image.copy(), message
                finally:
                    stats['control_decode_seconds'] += time.monotonic()-started
            except ValueError:
                if automatic and target is not None and measured is None:
                    try:
                        result = calibration.measure(target, image)
                    except ValueError:
                        continue
                    from .backends.image_sequence import write
                    write(Path(directory)/f'calibration-{peer}.png', image)
                    save_json(Path(directory)/f'calibration-{peer}.json', dict(sender=peer, **result))
                    measured = dict(sender=peer, calibration=result)
                    messages.put(measured, timeout=config['timeout'])
                if measured is not None:
                    import numpy as np
                    if previous is not None and np.array_equal(previous, image):
                        stats['duplicate_images'] += 1
                        continue
                    previous = image.copy()
                    data_started = time.monotonic()
                    try:
                        from .core.protocol import decode as decode_data
                        frame = decode_data(calibration.receive_wire(image, measured['calibration']))
                    except ValueError:
                        stats['data_decode_failed'] += 1
                        continue
                    finally:
                        stats['data_decode_seconds'] += time.monotonic()-data_started
                    stats['data_decoded'] += 1
                    key = (frame.metadata.get('session'), frame.metadata.get('config_id'), frame.metadata.get('file_id'), frame.metadata['index'])
                    if key == suppressed_data:
                        continue
                    suppressed_data = None
                    if key not in data_saved and len(data_saved) < 8:
                        from .backends.image_sequence import write
                        write(Path(directory)/f'data-capture-{len(data_saved):02d}.png', image)
                        data_saved.add(key)
                    if frame.metadata['index'] in faults.get('drop_data_once', []) and key not in dropped_data:
                        dropped_data.add(key)
                        suppressed_data = key
                        messages.put(dict(fault=dict(kind='data_drop', metadata=frame.metadata)), timeout=config['timeout'])
                        continue  # previous suppresses the entire held video presentation.
                    try:
                        messages.put_nowait(dict(frame=frame))
                    except queue.Full:
                        stats['data_queue_dropped'] += 1
                continue
            stats['control_decoded'] += 1
            previous = None
            suppressed_data = None
            if message.kind in faults.get('drop_control_once', []) and message.kind not in dropped_control:
                dropped_control.add(message.kind)
                # Suppress all captures of this control envelope until a different one appears.
                suppressed_control = message
                messages.put(dict(fault=dict(kind='control_drop', message=asdict(message))), timeout=config['timeout'])
            if suppressed_control is not None:
                if message == suppressed_control:
                    continue
                suppressed_control = None
            if automatic and message.kind == 'HELLO' and message.sender != peer:
                peer = message.sender
                target = calibration.pattern(int(peer,16))
                measured = None
                previous = None
            key = (message.sender, message.kind, message.ack, message.to_bytes())
            if key not in saved and len(saved) < 64:
                from .backends.image_sequence import write
                write(Path(directory)/f'control-capture-{len(saved):02d}.png', image)
                saved.add(key)
            try:
                messages.put_nowait(message)
            except queue.Full:
                stats['control_queue_dropped'] += 1
    except Exception as exc:
        metadata.update(status='failed', error=str(exc))
    finally:
        save_json(Path(directory)/'device.json', metadata)
        messages.cancel_join_thread()
        messages.close()


def exchange(config, directory, seconds, display=None, receive_target=_receive, calibrate=False, negotiate=False, input_file=None):
    """Exchange video control and bounded business presentations until exit."""
    if display is None:
        if config['backend'] == 'windows':
            from .backends.windows import display_one as display
        elif config['backend'] == 'linux':
            from .backends.linux import display_one as display
        else:
            raise ValueError('bootstrap requires a platform backend')
    directory = Path(directory)
    ctx = mp.get_context('spawn')
    messages, stop = ctx.Queue(maxsize=8), ctx.Event()
    heartbeat = ctx.Value('d', time.monotonic())
    if calibrate:
        config = dict(config, _calibrate=True)
    worker = ctx.Process(target=receive_target, args=(config, str(directory), messages, stop, heartbeat))
    if negotiate and not calibrate:
        raise ValueError('negotiation requires calibration')
    session = NegotiationSession() if negotiate else CalibrationSession() if calibrate else Session()
    from .core.storage import DiskReassembler, FileSource
    from .core.protocol import encode_frame
    from .transport import Sender, Transport
    receiver = DiskReassembler(directory/f'received-{session.peer_id}.part', directory/'received.bin')
    transport = Transport(session)
    payload = None if input_file is None else FileSource(input_file)
    delivery = None
    publisher = ThreadPoolExecutor(max_workers=1)
    tx_images = {}
    encoder = ThreadPoolExecutor(max_workers=1)
    tx_peer = None
    tx_index = 0
    last_data = 0.0
    received = None
    last = time.monotonic()
    index = 0
    hold_seconds = 1.0
    ready_announced = False
    last_hello = 0.0
    feedback_index = 0
    log = (directory/'control.jsonl').open('x', encoding='utf-8')
    progress_at = 0.0

    def report_progress(force=False):
        nonlocal progress_at
        now = time.monotonic()
        if not force and now-progress_at < 0.5:
            return
        progress_at = now
        value = dict(state=session.state.value, phase=getattr(session, 'phase', None),
                     connection_ready=getattr(session, 'ready', False),
                     send_count=0 if payload is None else payload.count,
                     received_count=len(receiver.frames),
                     receive_count=None if receiver.first is None else receiver.first['count'],
                     receive_complete=received is not None, transport=transport.stats())
        temporary = directory/'progress.tmp'
        save_json(temporary, value)
        temporary.replace(directory/'progress.json')

    def prepare_frame(index):
        if index in tx_images:
            return
        sender = transport.sender
        # Freeze identity and arguments before another peer can replace the session.
        arguments = dict(index=index, count=sender.count, total=payload.total,
                         file_hash=bytes.fromhex(sender.receipt['sha']), seed=index,
                         identity=(session.data_session, session.direction(True)[0],
                                   bytes.fromhex(sender.identity['f'])))

        def encode_image():
            started = time.monotonic()
            image = encode_frame(payload.read(index), **arguments)[0]
            return image, time.monotonic()-started

        tx_images[index] = encoder.submit(encode_image)

    def record(direction, message):
        import json
        log.write(json.dumps(dict(monotonic_ns=time.monotonic_ns(), direction=direction,
                                  message=asdict(message), state=session.state.value,
                                  calibrated=getattr(session, 'calibrated', False),
                                  phase=getattr(session, 'phase', None)))+'\n')
        log.flush()

    def data_event(direction, metadata):
        import json
        with (directory/'data.jsonl').open('a', encoding='utf-8') as output:
            output.write(json.dumps(dict(monotonic_ns=time.monotonic_ns(), direction=direction,
                                        metadata=metadata))+'\n')

    def complete_delivery(wait=False, feedback=True):
        nonlocal delivery, received
        if delivery is not None and (wait or delivery.done()):
            received = dict(delivery.result(), **receiver.stats())
            save_json(directory/'received.json', received)
            data_event('published', received)
            if feedback:
                transport.update_receiver(receiver, delivered=True)
            delivery = None

    def advance(present=True):
        nonlocal last, index, receiver, transport, tx_images, tx_peer, tx_index, last_data, received
        nonlocal hold_seconds, ready_announced, last_hello, feedback_index, delivery
        complete_delivery()
        if not worker.is_alive():
            raise ValueError('bootstrap capture worker exited; see device.json')
        if time.monotonic()-heartbeat.value > config['timeout']:
            raise ValueError('bootstrap capture stalled beyond timeout')
        # Bound work per GUI callback even if input stays busy.
        for _ in range(8):
            try:
                message = messages.get_nowait()
            except queue.Empty:
                break
            if isinstance(message, dict):
                if 'fault' in message:
                    data_event('injected', message['fault'])
                elif 'frame' in message:
                    frame = message['frame']
                    try:
                        session.validate_data(frame.metadata)
                    except (ValueError, AttributeError) as exc:
                        data_event('rejected_identity', dict(error=str(exc), **frame.metadata))
                        continue
                    try:
                        transport.validate_frame(frame)
                        receiver.add(frame)
                    except ValueError as exc:
                        data_event('rejected', dict(error=str(exc), **frame.metadata))
                        continue
                    data_event('rx', frame.metadata)
                    # ACK accepted fragments before the full-file hash/fsync. The
                    # publisher never grants FINISH until the file is published.
                    transport.update_receiver(receiver, delivered=received is not None)
                    if received is None and delivery is None and receiver.stats()['missing'] == 0:
                        data_event('publish_started', receiver.stats())
                        delivery = publisher.submit(receiver.finish)
                elif 'calibration' in message:
                    session.set_calibration(message['sender'], message['calibration'])
                else:
                    raise ValueError('unexpected probe after startup')
            else:
                before = session.peer_id
                try:
                    session.receive(message)
                    transport.receive(message)
                    if message.kind == 'ACK' and transport.sender is not None:
                        data_event('pacing', transport.sender.pacing())
                except ValueError as exc:
                    data_event('control_rejected', dict(error=str(exc), kind=message.kind))
                    continue
                if before != session.peer_id:
                    complete_delivery(wait=True, feedback=False)
                    receiver.close()
                    for future in tx_images.values():
                        future.cancel()
                    if before is not None:
                        save_json(directory/f'transport-{before}.json', transport.stats())
                        if (directory/'sent.json').exists():
                            _archive(directory/'sent.json', directory/f'sent-{before}.json')
                    receiver = DiskReassembler(directory/f'received-{session.peer_id}.part', directory/'received.bin')
                    tx_images = {}
                    tx_peer = None
                    tx_index = 0
                    ready_announced = False
                    hold_seconds = 1.0
                    last_data = last_hello = 0.0
                    feedback_index = 0
                    transport = Transport(session)
                    if received is not None:
                        # Preserve completed files across peer restarts.
                        _archive(directory/'received.bin', directory/f'received-{before}.bin')
                        _archive(directory/'received.json', directory/f'received-{before}.json')
                        received = None
                record('rx', message)
        if transport.sender is not None:
            for acknowledged in transport.sender.acknowledged.intersection(tx_images):
                tx_images.pop(acknowledged).cancel()
        report_progress()
        if not present or time.monotonic()-last < hold_seconds:
            return None
        ready = negotiate and session.ready
        # First announce receiver readiness, then wait for video proof that the
        # coordinator's final READY reached the peer before spending its window.
        announce = ready and (not ready_announced or not session.can_send)
        if ready and not announce and payload is not None:
            if time.monotonic()-last_data >= 1/(transport.sender.fps if transport.sender else session.direction(True)[2]):
                if tx_peer != session.peer_id:
                    count = payload.count
                    file_hash = payload.file_hash
                    identity = (session.data_session, session.direction(True)[0], file_hash[:12])
                    tx_peer, tx_index = session.peer_id, 0
                    transport.sender = Sender(session, file_hash.hex(), payload.total, count, adaptive=True,
                                              fps_limit=config.get('send_fps_limit'))
                    save_json(directory/'sent.json', dict(bytes=payload.total, sha256=file_hash.hex(),
                              session=identity[0].hex(), config_id=identity[1], count=count))
                selected = transport.sender.candidate(time.monotonic())
                if selected is not None:
                    prepare_frame(selected)
                if selected is not None and tx_images[selected].done():
                    image, elapsed = tx_images[selected].result()
                    chosen = transport.sender.next_frame(time.monotonic())
                    if chosen != selected:
                        raise ValueError('sender selection changed during presentation')
                    if transport.sender.attempts[selected] == 1:
                        data_event('encode', dict(index=selected, seconds=elapsed))
                    data_event('tx', dict(index=selected, attempt=transport.sender.attempts[selected],
                                          session=session.data_session.hex(), config_id=session.direction(True)[0],
                                          pacing=transport.sender.pacing()))
                    tx_index += 1
                    last = last_data = time.monotonic()
                    hold_seconds = config.get('data_hold_seconds', 0.75)/transport.sender.fps
                    if selected+1 < transport.sender.count:
                        prepare_frame(selected+1)
                    return image
        if calibrate and session.state == State.CAPABILITIES and not (negotiate and session.ready):
            # One calibration slot per four seconds; three control opportunities.
            if int(time.monotonic()-began) % 4 == 3:
                last = time.monotonic()
                return target_image
        outgoing = session.outgoing()
        if ready:
            hold_seconds = config.get('control_hold_seconds', 0.25)
            if time.monotonic()-last_hello >= 4:
                message = outgoing[0]  # Keep restart discovery alive during business.
                last_hello = time.monotonic()
            elif announce:
                message = next(m for m in outgoing if m.kind == 'READY')
                ready_announced = True
            else:
                feedback = transport.outgoing()
                if feedback:
                    message = feedback[feedback_index % len(feedback)]
                    feedback_index += 1
                else:
                    message = next(m for m in outgoing if m.kind == 'READY')
        else:
            hold_seconds = 1.0
            message = outgoing[index % len(outgoing)]
        index += 1
        record('tx', message)
        last = time.monotonic()
        return encode(message)

    failure = None
    try:
        report_progress(force=True)
        if calibrate:
            from .discovery import display_info
            observed_display = display_info(config)
        worker.start()
        if calibrate:
            try:
                probe = messages.get(timeout=config['timeout'])['probe']
            except (queue.Empty, KeyError, TypeError) as exc:
                raise ValueError('device probe failed or timed out; see device.json') from exc
            save_json(directory/'probe.json', dict(display=observed_display, **probe,
                      scope='selected native mode; four warmed capture frames; encode and local BGR/control-search/calibrated-mapping/RS receive path'))
            session.capabilities = Capabilities(versions=(1,2) if negotiate else (1,),
                      receive_window=config.get('receive_window', 1), measured_ms=tuple(probe['measured_ms']))
            from .backends.image_sequence import write
            target_image = calibration.pattern(int(session.local_id,16))
            write(directory/'calibration-target.png', target_image)
        began = time.monotonic()
        last = began
        first = session.outgoing()[0]
        record('tx', first)
        display(config, encode(first), seconds,
                ready=lambda value: save_json(directory/'ready.json', value), advance=advance)
        advance(present=False)  # Drain final messages and check worker health.
        if (session.state != State.CAPABILITIES or (calibrate and not session.calibrated)
                or (negotiate and not session.ready) or (receiver.first is not None and received is None)
                or (payload is not None and not (transport.sender and transport.sender.confirmed))
                or (received is not None and not transport.finish_confirmed)):
            raise ValueError(f'connection incomplete: state={session.state.value}; '
                             f'ready={getattr(session, "ready", False)}; transport={transport.stats()}')
    except Exception as exc:
        failure = str(exc)
        raise
    finally:
        try:
            encoder.shutdown(wait=True, cancel_futures=True)
            publisher.shutdown(wait=True, cancel_futures=True)
            receiver.close()
            if payload is not None:
                payload.close()
        except Exception as exc:
            failure = failure or str(exc)
            raise
        finally:
            stop.set()
            if worker.pid is not None:
                worker.join(timeout=1)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=2)
                if worker.is_alive():
                    worker.kill()
                    worker.join()
            messages.close()
            log.close()
            report_progress(force=True)
            save_json(directory/'result.json', dict(status='failed' if failure else 'complete',
                      error=failure, state=session.state.value, local_id=session.local_id,
                      peer_id=session.peer_id, session_id=session.session_id,
                      coordinator=session.coordinator,
                      phase=getattr(session, 'phase', None),
                      configurations=getattr(session, 'proposal', None), received=received,
                      connection_ready=getattr(session, 'ready', False),
                      send_ready=getattr(session, 'can_send', False),
                      receive_complete=received is not None,
                      receive_count=None if receiver.first is None else receiver.first['count'],
                      transport=transport.stats(),
                      tx_presentations=tx_index,
                      calibrated=getattr(session, 'calibrated', False),
                      local_calibration=getattr(session, 'local_calibration', None),
                      peer_calibration=getattr(session, 'peer_report', None),
                      local_capabilities=asdict(session.capabilities),
                      peer_capabilities=None if session.peer_capabilities is None else asdict(session.peer_capabilities)))
    return dict(status='complete', session_id=session.session_id, state=session.state.value,
                calibrated=getattr(session, 'calibrated', False), ready=getattr(session, 'ready', False))
