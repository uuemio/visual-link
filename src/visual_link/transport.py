"""Bounded selective repeat and file delivery receipts over video control frames."""
import time

from .core.control import Message


ACK_BITS = 64
RETRY_SECONDS = 12.0


class Sender:
    def __init__(self, session, file_hash, total, count, *, adaptive=False, fps_limit=None):
        self.identity = dict(s=session.data_session.hex(), c=session.direction(True)[0],
                             f=file_hash[:24])
        self.receipt = dict(self.identity, sha=file_hash, total=total, count=count)
        self.count = count
        self.window = min(ACK_BITS, session.direction(True)[3])
        self.credit = self.window  # Initial grant is the committed receive window.
        self.base = 0
        self.revision = -1
        self.acknowledged = set()
        self.sent = {}
        self.attempts = {}
        self.confirmed = False
        self.adaptive = adaptive
        self.max_fps = session.direction(True)[2]
        if fps_limit is not None:
            import math
            if type(fps_limit) not in (int, float) or not math.isfinite(fps_limit) or fps_limit <= 0:
                raise ValueError('fps_limit must be finite and positive')
            self.max_fps = min(self.max_fps, fps_limit)
        self.fps = self.max_fps
        self.flight_limit = min(2, self.window) if adaptive else self.window
        self.rto = RETRY_SECONDS
        self.srtt = self.rttvar = None
        self.rtt_samples = 0
        self.progress = 0
        self.last_backoff = float('-inf')

    def pacing(self):
        return dict(fps=self.fps, flight_limit=self.flight_limit, rto=self.rto,
                    srtt=self.srtt, rttvar=self.rttvar, rtt_samples=self.rtt_samples)

    def _backoff(self, now):
        # One reduction per timeout episode, rather than per expired fragment.
        if not self.adaptive or now-self.last_backoff < self.rto:
            return
        self.last_backoff = now
        self.fps = max(self.max_fps/4, self.fps*0.8)
        self.flight_limit = max(1, self.flight_limit//2)
        self.rto = min(30.0, self.rto*2)
        self.progress = 0

    def _ack_timing(self, newly_acked, now):
        if not self.adaptive or not newly_acked:
            return
        # Karn: retransmitted fragments cannot provide an unambiguous RTT.
        samples = [now-self.sent[i] for i in newly_acked
                   if self.attempts[i] == 1 and now > self.sent[i]]
        if samples:
            # A cumulative ACK can cover several presentations. Retain its
            # longest observed delay, including video/control scheduling time.
            sample = max(samples)
            if self.srtt is None:
                self.srtt, self.rttvar = sample, sample/2
            else:
                self.rttvar = 0.75*self.rttvar+0.25*abs(self.srtt-sample)
                self.srtt = 0.875*self.srtt+0.125*sample
            self.rtt_samples += 1
            self.rto = min(30.0, max(3.0, 4/self.max_fps, self.srtt+4*self.rttvar))
        self.progress += len(newly_acked)
        if self.progress >= self.flight_limit:
            self.progress = 0
            self.flight_limit = min(self.window, self.flight_limit+1)
            self.fps = min(self.max_fps, self.fps+self.max_fps*0.05)


    def next_frame(self, now):
        index = self.candidate(now)
        if index is not None:
            if index in self.sent:
                self._backoff(now)
            self.sent[index] = now
            self.attempts[index] = self.attempts.get(index, 0)+1
        return index

    def candidate(self, now):
        """Inspect the next eligible frame without spending credit or retry time."""
        if self.confirmed:
            return None
        pending = [i for i in self.sent if i not in self.acknowledged]
        expired = [i for i in pending if now-self.sent[i] >= self.rto]
        if expired:
            index = min(expired, key=self.sent.get)
        else:
            if len(pending) >= self.flight_limit:
                return None
            index = next((i for i in range(self.base, min(self.count, self.base+self.credit))
                          if i not in self.sent and i not in self.acknowledged), None)
            if index is None:
                return None
        return index

    def receive_ack(self, body, now=None):
        if any(body.get(k) != v for k,v in self.identity.items()):
            return
        if set(body) != set(self.identity) | {'b','m','w','r'}:
            raise ValueError('invalid ACK fields')
        base, mask, credit, revision = (body[k] for k in ('b','m','w','r'))
        if (type(base) is not int or not 0 <= base <= self.count
                or type(credit) is not int or not 0 <= credit <= self.window
                or type(revision) is not int or not 0 <= revision < 2**32
                or not isinstance(mask, str) or len(mask) != 16
                or any(c not in '0123456789abcdef' for c in mask)):
            raise ValueError('invalid ACK bounds')
        bits = int(mask, 16)
        confirmed = set(range(base)) | {base+i for i in range(ACK_BITS) if bits & (1 << i)}
        if any(i >= self.count or i not in self.sent for i in confirmed):
            raise ValueError('ACK confirms unsent data')
        if revision > self.revision:
            if base < self.base:
                raise ValueError('ACK base regressed')
            self.base, self.credit, self.revision = base, credit, revision
        self._ack_timing(confirmed-self.acknowledged, time.monotonic() if now is None else now)
        self.acknowledged.update(confirmed)


class Transport:
    """One file per direction; repeated ACK/FINISH messages are idempotent.

    Receiver storage is owned by Reassembler. A FINISH is offered only after
    that owner has verified and published the complete file.
    """
    def __init__(self, session, sender=None):
        self.session = session
        self.sender = sender
        self.feedback = None
        self.receipt = None
        self.finish_confirmed = False
        self.revision = 0

    def validate_frame(self, frame):
        base = 0 if self.feedback is None else self.feedback['b']
        if frame.metadata['index'] >= base+min(ACK_BITS, self.session.direction(False)[3]):
            raise ValueError('frame outside receive window')

    def update_receiver(self, receiver, delivered=False):
        m = receiver.first
        if m is None:
            return
        base = 0
        while base in receiver.frames:
            base += 1
        mask = sum(1 << (i-base) for i in receiver.frames if base <= i < base+ACK_BITS)
        identity = dict(s=m['session'], c=m['config_id'], f=m['file_id'])
        feedback = dict(identity, b=base, m=f'{mask:016x}',
                        w=min(ACK_BITS, self.session.direction(False)[3]))
        if self.feedback is None or any(self.feedback[k] != v for k,v in feedback.items()):
            self.revision += 1
            self.feedback = dict(feedback, r=self.revision)
        if delivered:
            self.receipt = dict(identity, sha=m['file_sha256'], total=m['total'], count=m['count'])

    def receive(self, message, now=None):
        s = self.session
        if (not getattr(s, 'ready', False) or message.sender != s.peer_id or message.peer != s.local_id
                or message.sender in s.retired):
            return
        if message.kind == 'ACK':
            if message.sequence != 8 or message.ack != 0:
                raise ValueError('invalid ACK envelope')
            if self.sender is not None:
                self.sender.receive_ack(message.body, now)
        elif message.kind == 'FINISH':
            if message.sequence != 9 or message.ack not in (0,9):
                raise ValueError('invalid FINISH envelope')
            if message.ack == 0 and self.sender is not None and message.body == self.sender.receipt:
                if len(self.sender.sent) != self.sender.count:
                    raise ValueError('FINISH confirms unsent file')
                self.sender.confirmed = True
            elif message.ack == 9 and self.receipt is not None and message.body == self.receipt:
                self.finish_confirmed = True

    def outgoing(self):
        s = self.session
        if not getattr(s, 'ready', False):
            return []
        messages = []
        for kind, sequence, ack, body in (
                ('ACK',8,0,self.feedback), ('FINISH',9,0,self.receipt),
                ('FINISH',9,9,self.sender.receipt if self.sender and self.sender.confirmed else None)):
            if body is not None:
                message = Message(s.local_id, s.peer_id, sequence, ack, kind, body)
                message.to_bytes()
                messages.append(message)
        return messages

    def stats(self):
        tx = self.sender
        return dict(sent_confirmed=None if tx is None else tx.confirmed,
                    receive_finish_confirmed=self.finish_confirmed,
                    acknowledged=0 if tx is None else len(tx.acknowledged),
                    retransmissions=0 if tx is None else sum(n-1 for n in tx.attempts.values()),
                    attempts={} if tx is None else tx.attempts,
                    adaptive=False if tx is None else tx.adaptive,
                    pacing=None if tx is None else tx.pacing())
