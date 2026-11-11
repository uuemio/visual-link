import unittest
from dataclasses import replace

from visual_link.session import NegotiationSession, Capabilities
from visual_link.core.control import encode, decode
from visual_link.core.protocol import encode_frame, decode as decode_data, digest
from visual_link.core.reassembly import Reassembler
from visual_link.calibration import pattern, measure, receive_wire
from test_discovery import captured


REPORT = dict(profile='v1-gray16', orientation='normal', dx=2, dy=0,
              pixels=2065686, symbol_errors=0)


class NegotiationTests(unittest.TestCase):
    def peers(self):
        a = NegotiationSession(Capabilities(versions=(1,2), receive_window=2, measured_ms=(100,2000,33)), 'a'*32)
        b = NegotiationSession(Capabilities(versions=(1,2), receive_window=3, measured_ms=(500,100,33)), 'b'*32)
        for _ in range(5):
            for source, target in ((a,b),(b,a)):
                for message in source.outgoing():
                    target.receive(message)
        a.set_calibration(b.local_id, REPORT)
        b.set_calibration(a.local_id, REPORT)
        return a,b

    def converge(self, a, b, lost=None):
        dropped = False
        for _ in range(20):
            for source, target in ((a,b),(b,a)):
                for message in source.outgoing():
                    if message.kind == lost and not dropped:
                        dropped = True
                        continue
                    # Exercise the real protected control format as well.
                    message = decode(encode(message)) if message.kind in ('PROPOSE','ACCEPT','COMMIT','READY') else message
                    target.receive(message)
                    target.receive(message)
        self.assertTrue(a.ready and b.ready)
        self.assertEqual(a.proposal,b.proposal)

    def test_every_transition_loss_duplicates_and_independent_directions(self):
        for lost in ('PROPOSE','ACCEPT','COMMIT','READY'):
            with self.subTest(lost=lost):
                a,b = self.peers()
                self.converge(a,b,lost)
                self.assertEqual(a.direction(True),[1,'v1-gray16',2.0,3])
                self.assertEqual(b.direction(True),[2,'v1-gray16',0.25,2])
                self.assertEqual(a.direction(True), b.direction(False))
                for message in a.outgoing():
                    b.receive(message)
                self.assertTrue(b.ready)

    def test_restart_retires_old_configuration(self):
        a,b = self.peers()
        self.converge(a,b)
        old = b.outgoing()
        old_session = a.data_session
        c = NegotiationSession(local_id='c'*32)
        a.receive(c.outgoing()[0])
        self.assertFalse(a.ready)
        self.assertIsNone(a.proposal)
        for message in old:
            a.receive(message)
        self.assertEqual(a.peer_id,c.local_id)
        self.assertNotEqual(old_session,a.data_session)
        for _ in range(5):
            for source,target in ((a,c),(c,a)):
                for message in source.outgoing():target.receive(message)
        a.set_calibration(c.local_id,REPORT)
        c.set_calibration(a.local_id,REPORT)
        self.converge(a,c)

    def test_final_ready_loss_blocks_coordinator_data_until_peer_confirmation(self):
        a,b = self.peers()
        for _ in range(20):
            for source,target in ((a,b),(b,a)):
                for message in source.outgoing():
                    if source is a and message.kind == 'READY':
                        continue
                    target.receive(message)
        self.assertTrue(a.ready)
        self.assertFalse(a.can_send)
        self.assertFalse(b.ready)
        first = next(m for m in a.outgoing() if m.kind == 'READY')
        b.receive(first)
        self.assertTrue(b.can_send)
        confirmation = next(m for m in b.outgoing() if m.kind == 'READY')
        self.assertEqual(confirmation.ack, 7)
        # Lose confirmation once; repeating it eventually unlocks the sender.
        self.assertFalse(a.can_send)
        a.receive(confirmation)
        a.receive(confirmation)
        self.assertTrue(a.can_send)
        a.receive(replace(confirmation, ack=6))
        self.assertTrue(a.can_send)
        a.receive(NegotiationSession(local_id='c'*32).outgoing()[0])
        self.assertFalse(a.can_send)

    def test_reject_changed_and_unsafe_proposals(self):
        a,b = self.peers()
        self.converge(a,b)
        proposal = next(m for m in a.outgoing() if m.kind == 'READY')
        for body in ({'directions':[[1,'v1-gray16',3,3],[2,'v1-gray16',.25,2]]},
                     {'directions':[[1,'v1-gray16',2,4],[2,'v1-gray16',.25,2]]}):
            with self.assertRaises(ValueError):
                b.receive(replace(proposal,body=body))
        with self.assertRaises(ValueError):
            b.receive(replace(proposal,sequence=6))

    def test_v2_calibrated_file_and_identity_isolation(self):
        a,b = self.peers()
        self.converge(a,b)
        data=b'new business data'*100
        identity=(a.data_session,a.direction(True)[0],digest(data)[:12])
        image,wire=encode_frame(data, identity=identity)
        # Apply the actual receiver path with shifted, nonlinear gray levels.
        import numpy as np
        lut=np.array([10+round(230*(i/15)**1.2) for i in range(16)],np.uint8)
        transform=lambda image: lut[(captured(image)//17)]
        result=measure(pattern(42),transform(pattern(42)))
        frame=decode_data(receive_wire(transform(image),result))
        b.validate_data(frame.metadata)
        r=Reassembler();r.add(frame)
        self.assertEqual(r.finish(),data)
        self.assertEqual(decode_data(wire).metadata,frame.metadata)
        for key,value in (('config_id',2),('session','0'*32),('file_id','0'*24)):
            with self.assertRaises(ValueError):b.validate_data(dict(frame.metadata,**{key:value}))
        legacy=decode_data(encode_frame(data)[1])
        with self.assertRaises(ValueError):b.validate_data(legacy.metadata)
        with self.assertRaises(ValueError):r.add(legacy)

    def test_data_stays_blocked_before_ready(self):
        a,b=self.peers()
        with self.assertRaises(ValueError):
            b.validate_data(dict(file_sha256='0'*64))


if __name__ == '__main__':
    unittest.main()
