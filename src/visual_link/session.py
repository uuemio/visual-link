"""Bootstrap discovery and capability exchange; no device I/O or wall clock."""
from dataclasses import asdict, dataclass
import math
from enum import Enum
from uuid import uuid4

from .core.control import Message, identifier


@dataclass(frozen=True)
class Capabilities:
    # connect populates directional modes from selected device declarations.
    versions: tuple = (1,)
    profiles: tuple = ('v1-gray16',)
    modes: tuple = ((1920, 1080),)
    rx_modes: tuple = ((1920, 1080),)
    layout: int = 1
    receive_window: int = 1
    measured_ms: tuple = ()

    def __post_init__(self):
        from .core.image import ImageParameters
        if type(self.layout) is not int or self.layout != 1 or not self.rx_modes:
            raise ValueError('unsupported layout or empty capture modes')
        for mode in self.modes + self.rx_modes:
            ImageParameters(*mode)
        if len(self.measured_ms) not in (0,3) or any(type(x) not in (int,float) or not math.isfinite(x) or x <= 0 for x in self.measured_ms):
            raise ValueError('invalid performance measurements')
        if (not self.versions or any(type(v) is not int or v < 1 for v in self.versions)
                or not self.profiles or any(not isinstance(p, str) or not p or len(p) > 32 for p in self.profiles)
                or not self.modes or any(len(m) != 2 or any(type(n) is not int or n < 1 or n > 16384 for n in m) for m in self.modes)
                or type(self.receive_window) is not int or not 1 <= self.receive_window <= 65535):
            raise ValueError('invalid capabilities')

    @classmethod
    def parse(cls, body):
        try:
            if set(body) - {'versions', 'profiles', 'modes', 'rx_modes', 'layout', 'receive_window', 'measured_ms'}:
                raise ValueError('unknown capability fields')
            return cls(versions=tuple(body['versions']), profiles=tuple(body['profiles']),
                       modes=tuple(tuple(m) for m in body['modes']),
                       rx_modes=tuple(tuple(m) for m in body['rx_modes']), layout=body['layout'],
                       receive_window=body['receive_window'], measured_ms=tuple(body.get('measured_ms', ())))
        except (TypeError, KeyError) as exc:
            raise ValueError('invalid capabilities') from exc


class State(str, Enum):
    DISCOVERING = 'discovering'
    EXCHANGING = 'exchanging'
    CAPABILITIES = 'capabilities'
    INCOMPATIBLE = 'incompatible'


class Session:
    """Repeat HELLO/CAPS each tick, including after exchange, to heal loss."""
    def __init__(self, capabilities=None, local_id=None):
        self.local_id = identifier(local_id or uuid4().hex)
        self.capabilities = capabilities or Capabilities()
        self.peer_id = None
        self.peer_capabilities = None
        self.peer_confirmed = False
        self.retired = set()
        self.state = State.DISCOVERING

    @property
    def session_id(self):
        return None if self.peer_id is None else ':'.join(sorted((self.local_id, self.peer_id)))

    @property
    def coordinator(self):
        return self.peer_id is not None and self.local_id < self.peer_id

    def outgoing(self):
        ack = 2 if self.peer_capabilities is not None else 0
        messages = [Message(self.local_id, None, 1, 0, 'HELLO', {})]
        if self.peer_id is not None:
            messages.append(Message(self.local_id, self.peer_id, 2, ack, 'CAPS', {k:v for k,v in asdict(self.capabilities).items() if k != 'measured_ms' or v}))
        # Validate the size at the protocol boundary before any display work.
        for message in messages:
            message.to_bytes()
        return messages

    def receive(self, message):
        message.to_bytes()
        if message.sender == self.local_id or message.sender in self.retired:
            return
        if message.peer not in (None, self.local_id):
            return
        if message.kind == 'HELLO':
            if message.sequence != 1 or message.ack != 0 or message.peer is not None or message.body:
                raise ValueError('invalid HELLO')
            if self.peer_id != message.sender:
                if self.peer_id is not None:
                    self.retired.add(self.peer_id)
                self.peer_id = message.sender
                self.peer_capabilities = None
                self.peer_confirmed = False
                self.state = State.EXCHANGING
        elif message.kind == 'CAPS' and message.sender == self.peer_id and message.peer == self.local_id:
            if message.sequence != 2 or message.ack not in (0, 2):
                raise ValueError('invalid CAPS sequence')
            caps = Capabilities.parse(message.body)
            if self.peer_capabilities is not None and caps != self.peer_capabilities:
                raise ValueError('capabilities changed within a session')
            self.peer_capabilities = caps
            self.peer_confirmed |= message.ack == 2
            if (not set(caps.versions).intersection(self.capabilities.versions)
                    or not set(caps.profiles).intersection(self.capabilities.profiles)
                    or not set(caps.modes).intersection(self.capabilities.rx_modes)
                    or not set(caps.rx_modes).intersection(self.capabilities.modes)):
                self.state = State.INCOMPATIBLE
            elif self.peer_confirmed:
                self.state = State.CAPABILITIES


class CalibrationSession(Session):
    """REPORT acknowledgement closes both directions before stage A completes."""
    def __init__(self, capabilities=None, local_id=None):
        super().__init__(capabilities, local_id)
        self.local_calibration = None
        self.peer_report = None
        self.report_confirmed = False

    def report_pixels(self):
        from .core.image import ImageParameters
        modes = getattr(self, 'selected_modes', None)
        mode = modes[0 if self.coordinator else 1] if modes else self.capabilities.modes[0]
        return ImageParameters(*mode).pixels

    @property
    def calibrated(self):
        return (self.state == State.CAPABILITIES and self.local_calibration is not None
                and self.peer_report is not None and self.report_confirmed)

    def set_calibration(self, sender, result):
        if sender == self.peer_id and self.local_calibration is None:
            self.local_calibration = result

    def outgoing(self):
        messages = super().outgoing()
        if self.local_calibration is not None:
            r = self.local_calibration
            body = dict(profile=r['profile'], orientation=r['orientation'], dx=r['dx'], dy=r['dy'],
                        pixels=r['pixels'], symbol_errors=r['symbol_errors'])
            message = Message(self.local_id, self.peer_id, 3, 3 if self.peer_report else 0, 'REPORT', body)
            message.to_bytes()
            messages.append(message)
        return messages

    def receive(self, message):
        before = self.peer_id
        super().receive(message)
        if before != self.peer_id:
            self.local_calibration = self.peer_report = None
            self.report_confirmed = False
        if (message.kind == 'REPORT' and message.sender == self.peer_id
                and message.sender not in self.retired and message.peer == self.local_id):
            body=message.body
            if (message.sequence != 3 or message.ack not in (0,3)
                    or set(body) != {'profile','orientation','dx','dy','pixels','symbol_errors'}
                    or body['profile'] != 'v1-gray16' or body['orientation'] not in ('normal','flip_vertical')
                    or any(type(body[k]) is not int for k in ('dx','dy','pixels','symbol_errors'))
                    or not 0 <= body['dx'] <= 2 or not 0 <= body['dy'] <= 3
                    or body['pixels'] != self.report_pixels() or body['symbol_errors'] != 0):
                raise ValueError('invalid calibration REPORT')
            if self.peer_report is not None and self.peer_report != body:
                raise ValueError('calibration REPORT changed within a session')
            self.peer_report = body
            self.report_confirmed |= message.ack == 3


class NegotiationSession(CalibrationSession):
    """One committed pair of directional configurations per discovered session.

    Repeating the last transition heals loss; earlier messages never regress it.
    IDs 1/2 identify 1080p directions; 3/4 identify 1200p directions.
    """
    def __init__(self, capabilities=None, local_id=None):
        super().__init__(capabilities or Capabilities(versions=(1, 2)), local_id)
        self.selected_modes = None
        self.proposal = None
        self.phase = 'calibrating'
        self.peer_ready = False

    @property
    def ready(self):
        return self.calibrated and self.phase == 'ready'

    @property
    def can_send(self):
        return self.ready and self.peer_ready

    @property
    def data_session(self):
        import hashlib
        return hashlib.sha256(self.session_id.encode('ascii')).digest()[:16]

    def direction(self, sending):
        if self.proposal is None:
            return None
        index = 0 if self.coordinator == sending else 1
        return self.proposal['directions'][index]

    def _candidate(self):
        caps = (self.capabilities, self.peer_capabilities) if self.coordinator else (self.peer_capabilities, self.capabilities)
        directions = []
        for index, (tx, rx) in enumerate((caps, caps[::-1])):
            # Conservative independent limits; this is not a running controller.
            costs = [0.5]  # The current pixel profile is limited to 2 FPS.
            timings = getattr(self, 'directional_timings', (caps[0].measured_ms, caps[1].measured_ms))
            tx_ms, rx_ms = timings if index == 0 else timings[::-1]
            if tx_ms:
                costs.append(tx_ms[0]*2/1000)
            if rx_ms:
                costs.extend((rx_ms[1]*2/1000, rx_ms[2]*2/1000))
            candidates = common_modes(tx, rx)
            mode = candidates[0] if self.selected_modes is None else self.selected_modes[index]
            if mode not in candidates:
                raise ValueError('selected mode not shared by directional devices')
            mode_id = 'gray16-%dx%d-l1' % mode
            config_id = 1 + index + 2 * (mode[1] == 1200)
            directions.append([config_id, 'v1-gray16', round(1/max(costs), 3), rx.receive_window,
                               mode_id, *mode, 1])
        return dict(directions=directions)

    def _validate(self, body):
        if set(body) != {'directions'} or not isinstance(body['directions'], list) or len(body['directions']) != 2:
            raise ValueError('invalid directional configuration')
        if 2 not in self.capabilities.versions or 2 not in self.peer_capabilities.versions:
            raise ValueError('v2 data identity required')
        limits = self._candidate()['directions']
        for row, limit in zip(body['directions'], limits):
            if (not isinstance(row, list) or len(row) != 8 or type(row[0]) is not int or row[:2] != limit[:2] or row[4:] != limit[4:]
                    or any(type(value) is not int for value in row[5:])
                    or type(row[2]) not in (int, float) or not math.isfinite(row[2]) or not 0 < row[2] <= limit[2]
                    or type(row[3]) is not int or not 1 <= row[3] <= limit[3]
                    or row[1] not in self.capabilities.profiles or row[1] not in self.peer_capabilities.profiles):
                raise ValueError('unsupported directional configuration')

    def outgoing(self):
        messages = super().outgoing()
        if self.calibrated:
            if self.coordinator and self.proposal is None:
                candidate = self._candidate()
                self._validate(candidate)
                self.proposal = candidate
                self.phase = 'proposing'
            kind = {'proposing':'PROPOSE', 'accepted':'ACCEPT', 'committing':'COMMIT',
                    'committed':'READY', 'ready':'READY'}.get(self.phase)
            if kind:
                sequence, ack = {'PROPOSE':(4,3), 'ACCEPT':(5,4), 'COMMIT':(6,5),
                                 'READY':(7,7 if self.coordinator or self.ready else 6)}[kind]
                message = Message(self.local_id, self.peer_id, sequence, ack, kind, self.proposal)
                message.to_bytes()
                messages.append(message)
        return messages

    def receive(self, message):
        before = self.peer_id
        super().receive(message)
        if before != self.peer_id:
            self.proposal = None
            self.phase = 'calibrating'
            self.peer_ready = False
        if (message.kind not in ('PROPOSE','ACCEPT','COMMIT','READY') or not self.calibrated
                or message.sender != self.peer_id or message.sender in self.retired or message.peer != self.local_id):
            return
        expected = {'PROPOSE':(4,3,False), 'ACCEPT':(5,4,True), 'COMMIT':(6,5,False),
                    'READY':(7,6 if self.coordinator else 7,self.coordinator)}[message.kind]
        confirmed_ready = (message.kind == 'READY' and self.coordinator
                           and message.sequence == 7 and message.ack == 7)
        if (message.sequence, message.ack, self.coordinator) != expected and not confirmed_ready:
            raise ValueError('invalid configuration transition envelope')
        self._validate(message.body)
        if self.proposal is not None and self.proposal != message.body:
            raise ValueError('configuration changed within a session')
        if message.kind == 'PROPOSE' and self.proposal is None:
            self.proposal = message.body
            self.phase = 'accepted'
        elif message.kind == 'ACCEPT' and self.phase == 'proposing':
            self.phase = 'committing'
        elif message.kind == 'COMMIT' and self.phase == 'accepted':
            self.phase = 'committed'
        elif message.kind == 'READY' and self.phase in ('committing','committed'):
            self.phase = 'ready'
        if message.kind == 'READY' and self.ready and message.ack == 7:
            self.peer_ready = True

    def validate_data(self, metadata):
        config = self.direction(sending=False)
        if (not self.ready or metadata.get('session') != self.data_session.hex()
                or metadata.get('config_id') != config[0]
                or metadata.get('file_id') != metadata['file_sha256'][:24]):
            raise ValueError('data does not belong to ready receive configuration')


def common_modes(tx, rx):
    """Largest first, for one sender display and the opposite capture device."""
    return sorted(set(tx.modes).intersection(rx.rx_modes), key=lambda m: (m[0]*m[1], m), reverse=True)
