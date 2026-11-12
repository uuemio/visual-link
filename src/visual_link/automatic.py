"""Startup mode agreement, signal lock and calibration before data commitment."""
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, replace
import json
import multiprocessing as mp
from pathlib import Path
import queue
import time

from . import calibration, discovery
from .backends.camera import capture_frames
from .core.control import Message, encode, decode
from .core.image import ImageParameters, image_parameters
from .hardware import save_json
from .presentation import END_PRESENTATION, display_parameters
from .session import Capabilities, Session, State, NegotiationSession, common_modes


def capabilities(observed, config):
    """Only explicit native BGR sizes supported by the codec become candidates."""
    def sizes(rows, capture=False):
        result = set()
        allowed = config.get('candidate_modes', [[1920, 1080], [1920, 1200]])
        for row in rows:
            mode = (row.get('width'), row.get('height'))
            if list(mode) not in allowed or (capture and row.get('format') != 'BGR3'):
                continue
            if not capture and (row.get('interlaced', False) or row.get('bits_per_pixel', 32) != 32):
                continue
            try:
                ImageParameters(*mode)
            except (TypeError, ValueError):
                continue
            result.add(mode)
        return tuple(sorted(result, reverse=True))
    return Capabilities(versions=(2, 3), modes=sizes(observed['display']['declared']),
                        rx_modes=sizes(observed['capture']['declared'], True),
                        receive_window=config.get('receive_window', 1))


class ModeSession(Session):
    """Coordinator advances only failed directional indices; rounds reject stale results."""
    def __init__(self, caps, local_id=None):
        super().__init__(caps, local_id)
        self.round = 0
        self.indices = (0, 0)
        self.peer_selection = False
        self.selection_confirmed = False
        self.local_status = [0, 0]  # tx/rx: 0 pending, 1 verified, -1 failed
        self.peer_status = None
        self.timings = self.peer_timings = None

    def candidates(self):
        caps = (self.capabilities, self.peer_capabilities) if self.coordinator else (self.peer_capabilities, self.capabilities)
        return [common_modes(*caps), common_modes(*caps[::-1])]

    def selected(self):
        return tuple(rows[index] for rows, index in zip(self.candidates(), self.indices))

    def reset(self, number, indices):
        candidates = self.candidates()
        if any(type(i) is not int or not 0 <= i < len(rows) for i, rows in zip(indices, candidates)):
            raise ValueError('no remaining common directional mode')
        self.round, self.indices = number, tuple(indices)
        self.peer_selection = False
        self.selection_confirmed = False
        self.local_status = [0, 0]
        self.peer_status = self.timings = self.peer_timings = None

    @property
    def verified(self):
        return self.local_status == [1, 1] and self.peer_status == [1, 1]

    def status_message(self):
        return Message(self.local_id, self.peer_id, 9, 0, 'STATUS',
                       dict(r=self.round, s=self.local_status, t=self.timings))

    def outgoing(self):
        messages = super().outgoing()
        if self.state == State.INCOMPATIBLE:
            raise ValueError('no common directional native mode')
        if self.state != State.CAPABILITIES:
            return messages
        if self.coordinator:
            if not self.round:
                self.reset(1, (0, 0))
            elif self.peer_status is not None:
                local_tx = 0 if self.coordinator else 1
                failed = [False, False]
                for status, tx_index in ((self.local_status, local_tx), (self.peer_status, 1-local_tx)):
                    if status[0] == -1:
                        failed[tx_index] = True
                    if status[1] == -1:
                        failed[1-tx_index] = True
                if any(failed):
                    self.reset(self.round+1, [i+int(f) for i, f in zip(self.indices, failed)])
        if self.round:
            messages.extend([Message(self.local_id, self.peer_id, 8, 8 if self.peer_selection else 0, 'SELECT',
                                     dict(r=self.round, i=list(self.indices))), self.status_message()])
        for message in messages:
            message.to_bytes()
        return messages

    def receive(self, message):
        before = self.peer_id
        super().receive(message)
        if before is not None and before != self.peer_id:
            raise ValueError('peer restarted during mode application; start a new connection')
        if message.sender != self.peer_id or message.peer != self.local_id or self.state != State.CAPABILITIES:
            return
        body = message.body
        if message.kind == 'SELECT':
            if (message.sequence != 8 or message.ack not in (0,8) or set(body) != {'r', 'i'}
                    or type(body['r']) is not int or body['r'] < 1
                    or not isinstance(body['i'], list) or len(body['i']) != 2):
                raise ValueError('invalid mode selection')
            if body['r'] > self.round and not self.coordinator:
                # A coordinator may only advance each direction to a lower candidate.
                if any(type(i) is not int or i < old for i, old in zip(body['i'], self.indices)):
                    raise ValueError('mode selection regressed')
                self.reset(body['r'], body['i'])
            if body['r'] == self.round and tuple(body['i']) == self.indices:
                self.peer_selection = True
                self.selection_confirmed |= message.ack == 8
        elif message.kind == 'STATUS':
            if (message.sequence != 9 or message.ack != 0 or set(body) != {'r', 's', 't'}
                    or type(body['r']) is not int or body['r'] < 1 or not isinstance(body['s'], list)
                    or len(body['s']) != 2 or any(type(v) is not int or v not in (-1, 0, 1) for v in body['s'])):
                raise ValueError('invalid mode status')
            if body['s'][1] == 1 and body['t'] is None:
                raise ValueError('verified receive mode requires measurements')
            if body['t'] is not None:
                replace(self.capabilities, measured_ms=tuple(body['t']))
            if body['r'] == self.round:
                self.peer_status, self.peer_timings = body['s'], body['t']


def _receive(config, directory, messages, stop, peer=None):
    """Reopen at signal changes; a matching black frame never verifies a candidate."""
    directory = Path(directory)
    parameters = image_parameters(config, 'rx')
    target = None if peer is None else calibration.pattern(int(peer, 16), parameters)
    measured = None
    attempt = 0
    while not stop.is_set():
        metadata = {}
        probe = None
        frames = None
        try:
            # refresh_capture owns and closes its isolated capture before this reader opens.
            if peer is not None:
                probe_dir = directory/f'lock-{attempt:03d}'
                probe_dir.mkdir()
                try:
                    _, image, probe = discovery.refresh_capture(config, probe_dir)
                except Exception as exc:
                    # Keep decoding geometry-independent controls on an unmatched
                    # signal, so a failed direction can receive its fallback SELECT.
                    metadata['refresh_error'] = str(exc)
                else:
                    messages.put(dict(actual=probe), timeout=1)
            frames = capture_frames(config, metadata, stop.is_set, strict=False)
            began = time.monotonic()
            for image in frames:
                if stop.is_set():
                    break
                try:
                    message = decode(image)
                except ValueError:
                    if target is not None and measured is None and image.shape[:2] == parameters.shape:
                        try:
                            measured = calibration.measure(target, image, parameters)
                        except ValueError:
                            if time.monotonic()-began > 5:
                                break  # Reopen on an unlocked/black signal, preserving evidence.
                            continue
                        if probe is None:
                            # A transient refresh failure can recover on this reopened
                            # stream. Publish its verified native observation, too.
                            actual = dict(width=image.shape[1], height=image.shape[0])
                            probe = dict(capture=dict(metadata), requested=dict(width=parameters.width, height=parameters.height),
                                         actual=actual, matches=True, source='continuous capture after refresh retry')
                            save_json(directory/f'recovered-probe-{attempt:03d}.json', probe)
                            messages.put(dict(actual=probe), timeout=1)
                        from .backends.image_sequence import write
                        write(directory/'calibration.png', image)
                        save_json(directory/'calibration.json', measured)
                        timings = discovery.codec_timings(parameters)
                        tx = image_parameters(config, 'tx')
                        if tx != parameters:
                            timings[0] = discovery.codec_timings(tx)[0]
                        # Measure a warmed software sampling interval on this actual mode.
                        import statistics
                        next(frames)
                        samples = []
                        for _ in range(4):
                            next(frames)
                            samples.append(time.monotonic())
                        timings.append(round(max(statistics.median(b-a for a,b in zip(samples,samples[1:])), 0.001)*1000, 3))
                        messages.put(dict(calibration=measured, timings=timings), timeout=1)
                    continue
                messages.put(message, timeout=1)
                # A stream of valid controls can also precede input lock/calibration.
                if target is not None and measured is None and time.monotonic()-began > 8:
                    break
        except Exception as exc:
            metadata['error'] = str(exc)
        finally:
            if frames is not None:
                frames.close()
            save_json(directory/f'capture-{attempt:03d}.json', metadata)
        attempt += 1
        stop.wait(0.5)
    messages.cancel_join_thread()


def stop_worker(worker, stop, messages):
    stop.set()
    worker.join(timeout=1)
    if worker.is_alive():
        worker.terminate()
        worker.join(timeout=2)
    if worker.is_alive():
        worker.kill()
        worker.join()
    messages.close()


@contextmanager
def startup(config, directory, seconds, display):
    """Yield selected config and a same-identity data session under the display scope."""
    directory = Path(directory)/'startup'
    directory.mkdir()
    observed = discovery.device_capabilities(config)
    save_json(directory/'capabilities.json', observed)
    session = ModeSession(capabilities(observed, config))
    current = observed['display']['current_output']
    bootstrap_canvas = {k: current[k] for k in ('width', 'height')}
    canvas = dict(config, _control_canvas=bootstrap_canvas)
    ctx = mp.get_context('spawn')
    deadline = time.monotonic()+seconds
    applied_round = 0
    local_calibration = actual = None
    switch = False
    log = (directory/'control.jsonl').open('x', encoding='utf-8')
    modes = ExitStack()
    try:
        while time.monotonic() < deadline:
            stage = directory/f'stage-{applied_round:03d}'
            stage.mkdir()
            messages, stop = ctx.Queue(maxsize=32), ctx.Event()
            worker = ctx.Process(target=_receive, args=(canvas, str(stage), messages, stop,
                                                       session.peer_id if applied_round else None))
            worker.start()
            last = 0.0
            index = 0
            began = time.monotonic()
            progress_at = 0.0
            selection_shown = None
            switch = False
            target = None if not applied_round or session.local_status[0] != 1 else calibration.pattern(int(session.local_id, 16), image_parameters(canvas, 'tx'))

            def record(direction, message):
                log.write(json.dumps(dict(direction=direction, monotonic_ns=time.monotonic_ns(), message=asdict(message)))+'\n')
                log.flush()

            def advance():
                nonlocal last, index, switch, local_calibration, actual, progress_at, selection_shown
                if not worker.is_alive():
                    raise ValueError('mode capture worker exited')
                for _ in range(16):
                    try:
                        message = messages.get_nowait()
                    except queue.Empty:
                        break
                    if isinstance(message, dict):
                        if 'actual' in message:
                            actual = message['actual']
                        if 'calibration' in message:
                            local_calibration = message['calibration']
                            session.local_status[1] = 1
                            session.timings = message['timings']
                    else:
                        record('rx', message)
                        session.receive(message)
                outgoing = session.outgoing()
                if session.verified:
                    return END_PRESENTATION
                pending_switch = session.round != applied_round and session.peer_selection
                if (pending_switch and session.selection_confirmed and selection_shown is not None
                        and time.monotonic()-selection_shown >= 3):
                    switch = True
                    return END_PRESENTATION
                if applied_round and time.monotonic()-began > config.get('signal_lock_seconds', 25):
                    if session.local_status[1] == 0:
                        session.local_status[1] = -1
                progress = dict(state='mode_calibration' if applied_round else 'capabilities',
                                round=session.round, indices=session.indices,
                                local_status=session.local_status, peer_status=session.peer_status,
                                selected_modes=None if not session.round else session.selected(), actual_capture=actual)
                if session.round:
                    tx, rx = session.selected() if session.coordinator else session.selected()[::-1]
                    progress.update(tx_mode=dict(zip(('width', 'height'), tx)),
                                    rx_mode=dict(zip(('width', 'height'), rx)))
                if time.monotonic()-progress_at >= 0.5:
                    save_json(directory/'progress.json', progress)
                    progress_at = time.monotonic()
                if time.monotonic()-last < 0.6:
                    return None
                last = time.monotonic()
                index += 1
                if pending_switch:
                    message = next(m for m in outgoing if m.kind == 'SELECT')
                    if selection_shown is None:
                        selection_shown = time.monotonic()
                    record('tx', message)
                    p = display_parameters(canvas)
                    return encode(message, p.width, p.height)
                if target is not None and index % 3 == 0:
                    return target
                message = outgoing[(index//3 if target is not None else index) % len(outgoing)]
                record('tx', message)
                p = display_parameters(canvas)
                return encode(message, p.width, p.height)

            try:
                p = display_parameters(canvas)
                display(canvas, encode(session.outgoing()[0], p.width, p.height), max(0.1, deadline-time.monotonic()), advance=advance)
            finally:
                stop_worker(worker, stop, messages)
            if session.verified:
                break
            if not switch:
                raise ValueError('automatic mode agreement timed out')
            modes.close()
            local_calibration = actual = None
            session.local_status[1] = 0
            session.timings = None
            selected = session.selected()
            tx, rx = selected if session.coordinator else selected[::-1]
            chosen = dict(config, tx_mode=dict(zip(('width', 'height'), tx)), rx_mode=dict(zip(('width', 'height'), rx)))
            try:
                report = modes.enter_context(discovery.apply_display_mode(chosen))
                canvas = chosen
                session.local_status[0] = 1
                save_json(directory/f'mode-{session.round:03d}.json', report)
            except Exception as exc:
                # Keep a valid control canvas while reporting a rejected display candidate.
                canvas = dict(config, _control_canvas=bootstrap_canvas, rx_mode=chosen['rx_mode'])
                session.local_status[0] = -1
                save_json(directory/f'mode-{session.round:03d}.json', dict(error=str(exc)))
            applied_round = session.round
        if not session.verified or local_calibration is None or actual is None:
            raise ValueError('automatic startup did not verify both directional candidates')
        final = NegotiationSession(session.capabilities, session.local_id)
        final.peer_id = session.peer_id
        final.peer_capabilities = session.peer_capabilities
        final.directional_timings = ((session.timings, session.peer_timings) if session.coordinator
                                     else (session.peer_timings, session.timings))
        final.peer_confirmed = True
        final.state = State.CAPABILITIES
        final.selected_modes = session.selected()
        final.local_calibration = local_calibration
        final.startup_message = session.status_message()
        # The final REPORT handshake still independently confirms calibration before COMMIT.
        result = dict(selected_modes=final.selected_modes, tx_mode=canvas['tx_mode'], rx_mode=canvas['rx_mode'],
                      actual_capture=actual, round=session.round, session_id=session.session_id,
                      measured_ms=session.timings, peer_measured_ms=session.peer_timings,
                      local_id=session.local_id, peer_id=session.peer_id)
        save_json(directory/'result.json', result)
        yield dict(canvas, _initial_calibration=local_calibration, _initial_peer=session.peer_id), final, result
    finally:
        modes.close()
        log.close()
