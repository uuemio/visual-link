import unittest
from dataclasses import replace
from types import SimpleNamespace

from visual_link.session import NegotiationSession, Capabilities
from visual_link.transport import Sender, Transport, RETRY_SECONDS
from visual_link.core.control import encode, decode
from visual_link.core.protocol import digest
from visual_link.core.reassembly import Reassembler


def peers(window=3):
    a, b = (NegotiationSession(Capabilities(versions=(1,2), receive_window=window), c*32)
            for c in ('a','b'))
    report = dict(profile='v1-gray16',orientation='normal',dx=2,dy=0,pixels=2065686,symbol_errors=0)
    for tick in range(20):
        for source,target in ((a,b),(b,a)):
            for m in source.outgoing(): target.receive(m)
        if tick == 4:
            a.set_calibration(b.local_id,report)
            b.set_calibration(a.local_id,report)
    assert a.ready and b.ready
    return a,b


class TransportTests(unittest.TestCase):
    def setup_transfer(self, count=5, window=3):
        a,b=peers(window)
        data=bytes(range(count))
        sender=Sender(a,digest(data).hex(),len(data),count)
        tx,rx=Transport(a,sender),Transport(b)
        frames=[]
        for i in range(count):
            m=dict(session=a.data_session.hex(),config_id=a.direction(True)[0],
                   file_id=digest(data)[:12].hex(), file_sha256=digest(data).hex(),
                   total=count,count=count,index=i,n=255,k=247)
            frames.append(SimpleNamespace(metadata=m,data=data[i:i+1],integrity=True,failed_blocks=0))
        return tx,rx,frames,Reassembler()

    def feedback(self, source, target):
        for m in source.outgoing():
            optical=decode(encode(m))
            self.assertEqual(optical,m)
            target.receive(optical);target.receive(optical)

    def deliver(self, rx, r, frame):
        rx.validate_frame(frame)
        r.add(frame)
        complete=r.stats()['missing']==0
        if complete:r.finish()
        rx.update_receiver(r, delivered=complete)

    def test_selective_loss_reordering_and_finish_loss(self):
        tx,rx,frames,r=self.setup_transfer()
        s=tx.sender
        for i in range(3): self.assertEqual(s.next_frame(i),i)
        self.assertIsNone(s.next_frame(4))
        # Whole frame 0 disappears; the other two arrive in reverse order.
        self.deliver(rx,r,frames[2]);self.deliver(rx,r,frames[1])
        old=rx.outgoing()[0]
        self.feedback(rx,tx)
        self.assertEqual(s.acknowledged,{1,2})
        self.assertIsNone(s.next_frame(5))  # Sliding span cannot skip missing base.
        self.assertEqual(s.next_frame(RETRY_SECONDS),0)
        self.deliver(rx,r,frames[0])
        self.feedback(rx,tx)
        tx.receive(old)  # Late feedback never rolls back the window.
        for i in (3,4):
            self.assertEqual(s.next_frame(RETRY_SECONDS+i),i)
            self.deliver(rx,r,frames[i])
        self.assertEqual(r.finish(),bytes(range(5)))
        self.assertFalse(s.confirmed)
        self.assertFalse(rx.finish_confirmed)
        # Lose the first FINISH and its first acknowledgement.
        self.assertTrue(rx.outgoing())
        self.feedback(rx,tx)
        self.assertTrue(s.confirmed)
        self.assertFalse(rx.finish_confirmed)
        self.feedback(tx,rx)
        self.assertTrue(rx.finish_confirmed)
        self.assertIsNone(s.next_frame(999))
        self.assertEqual(tx.stats()['retransmissions'],1)
        self.assertEqual(s.attempts,{0:2,1:1,2:1,3:1,4:1})

    def test_ack_loss_timeout_and_zero_credit(self):
        tx,rx,frames,r=self.setup_transfer(count=2,window=1)
        s=tx.sender
        self.assertEqual(s.next_frame(0),0)
        self.deliver(rx,r,frames[0])
        self.assertIsNone(s.next_frame(RETRY_SECONDS-0.1))
        self.assertEqual(s.next_frame(RETRY_SECONDS),0)
        self.deliver(rx,r,frames[0])
        ack=rx.outgoing()[0]
        tx.receive(replace(ack,body=dict(ack.body,w=0)))
        self.assertIsNone(s.next_frame(100))
        tx.receive(replace(ack,body=dict(ack.body,r=ack.body['r']+1)))
        self.assertEqual(s.next_frame(100),1)
        self.deliver(rx,r,frames[1]);self.feedback(rx,tx);self.feedback(tx,rx)
        self.assertTrue(s.confirmed and rx.finish_confirmed)

    def test_encoding_preparation_does_not_spend_window_or_retry(self):
        tx,rx,frames,r=self.setup_transfer(count=2,window=1)
        sender=tx.sender
        self.assertEqual(sender.candidate(0),0)
        self.assertEqual(sender.candidate(100),0)
        self.assertEqual(sender.sent,{})
        self.assertEqual(sender.attempts,{})
        self.assertEqual(sender.next_frame(100),0)
        self.assertIsNone(sender.candidate(100+RETRY_SECONDS-0.1))
        self.assertEqual(sender.candidate(100+RETRY_SECONDS),0)
        self.assertEqual(sender.attempts,{0:1})
        self.deliver(rx,r,frames[0]);self.feedback(rx,tx)
        self.assertEqual(sender.candidate(200),1)
        self.assertEqual(sender.sent,{0:100})

    def test_invalid_and_wrong_identity_feedback(self):
        tx,rx,frames,r=self.setup_transfer()
        s=tx.sender
        s.next_frame(0);self.deliver(rx,r,frames[0])
        ack=rx.outgoing()[0]
        for change in (dict(b=9),dict(m='g'*16),dict(w=4),dict(r=-1),dict(b=True),dict(m='0000000000000002')):
            with self.assertRaises(ValueError):tx.receive(replace(ack,body=dict(ack.body,**change)))
        self.assertFalse(s.acknowledged)
        for change in (dict(s='0'*32),dict(c=99),dict(f='0'*24)):
            tx.receive(replace(ack,body=dict(ack.body,**change)))
        self.assertFalse(s.acknowledged)
        with self.assertRaises(ValueError):tx.receive(replace(ack,sequence=7))
        tx.receive(replace(ack,sender='c'*32))
        self.assertFalse(s.acknowledged)

    def test_bad_frame_and_unverified_file_never_finish(self):
        tx,rx,frames,r=self.setup_transfer(count=1)
        tx.sender.next_frame(0)
        frames[0].integrity=False
        with self.assertRaises(ValueError):r.add(frames[0])
        rx.update_receiver(r)
        self.assertFalse(rx.outgoing())
        frames[0].integrity=True
        frames[0].data=b'wrong'
        r.add(frames[0])
        with self.assertRaises(ValueError):r.finish()
        rx.update_receiver(r)
        self.assertEqual([m.kind for m in rx.outgoing()],['ACK'])
        self.assertFalse(tx.sender.confirmed)

    def test_restart_rejects_old_feedback_and_receipts(self):
        tx,rx,frames,r=self.setup_transfer(count=1)
        tx.sender.next_frame(0);self.deliver(rx,r,frames[0])
        old=rx.outgoing()
        tx.session.receive(NegotiationSession(local_id='c'*32).outgoing()[0])
        new=Transport(tx.session)
        for m in old:new.receive(m)
        self.assertFalse(new.outgoing())
        self.assertIsNone(new.sender)
        self.assertFalse(new.finish_confirmed)

    def test_window_bounds_and_finish_hash_validation(self):
        tx,rx,frames,r=self.setup_transfer(count=5,window=1)
        with self.assertRaises(ValueError):rx.validate_frame(frames[1])
        s=tx.sender
        s.next_frame(0)
        self.deliver(rx,r,frames[0])
        self.feedback(rx,tx)
        s.next_frame(1)
        # A matching file ID alone cannot confirm a different full hash or size.
        from visual_link.core.control import Message
        for change in (dict(sha='0'*64),dict(total=99),dict(count=1)):
            tx.receive(Message(rx.session.local_id,tx.session.local_id,9,0,'FINISH',dict(s.receipt,**change)))
        self.assertFalse(s.confirmed)

    def test_adaptive_timeout_backoff_karn_and_credit(self):
        session, _ = peers(window=8)
        sender = Sender(session, digest(b'x').hex(), 20, 20, adaptive=True)
        self.assertEqual([sender.next_frame(0), sender.next_frame(1)], [0, 1])
        self.assertIsNone(sender.next_frame(2))
        ack = dict(sender.identity, b=1, m='0000000000000000', w=8, r=1)
        sender.receive_ack(ack, now=2)
        self.assertEqual(sender.rtt_samples, 1)
        self.assertEqual(sender.srtt, 2)
        self.assertEqual(sender.rto, 6)
        self.assertEqual(sender.candidate(6.9), 2)
        self.assertEqual(sender.candidate(7), 1)
        before = sender.pacing()
        self.assertEqual(sender.pacing(), before)  # Inspection never backoffs.
        self.assertEqual(sender.next_frame(7), 1)
        self.assertLess(sender.fps, sender.max_fps)
        self.assertEqual(sender.flight_limit, 1)
        self.assertEqual(sender.rto, 12)
        ack.update(b=2, w=0, r=2)
        sender.receive_ack(ack, now=8)
        self.assertEqual(sender.rtt_samples, 1)  # Ambiguous retransmission RTT.
        self.assertIsNone(sender.next_frame(100))
        snapshot = sender.pacing()
        sender.receive_ack(ack, now=101)
        self.assertEqual(sender.pacing(), snapshot)
        ack.update(w=8, r=3)
        sender.receive_ack(ack, now=102)
        self.assertEqual(sender.next_frame(102), 2)

    def test_adaptive_progress_recovers_within_negotiated_bounds(self):
        session, _ = peers(window=8)
        sender = Sender(session, digest(b'x').hex(), 80, 80, adaptive=True)
        now = 0
        revision = 0
        for index in range(80):
            self.assertEqual(sender.next_frame(now), index)
            # Change the synthetic load; retain ACK-only operation and bounded
            # state even when every tenth fragment's first presentation is lost.
            if index % 10 == 0:
                now += sender.rto+0.001
                self.assertEqual(sender.next_frame(now), index)
            now += 1 if index < 40 else 4
            revision += 1
            sender.receive_ack(dict(sender.identity, b=index+1, m='0'*16,
                                    w=8, r=revision), now=now)
            self.assertLessEqual(sender.fps, sender.max_fps)
            self.assertGreaterEqual(sender.fps, sender.max_fps/4)
            self.assertTrue(1 <= sender.flight_limit <= 8)
            self.assertTrue(3 <= sender.rto <= 30)
            now += 1
        self.assertEqual(len(sender.acknowledged), 80)
        self.assertEqual(sum(n-1 for n in sender.attempts.values()), 8)
        self.assertEqual(sender.rtt_samples, 72)
        self.assertGreater(sender.srtt, 3)

    def test_local_rate_ceiling_never_exceeds_negotiation(self):
        session, _ = peers(window=8)
        for limit in (0.8, 100):
            sender = Sender(session, digest(b'x').hex(), 1, 1, adaptive=True, fps_limit=limit)
            self.assertEqual(sender.fps, min(limit, session.direction(True)[2]))
            sender.next_frame(0)
            sender.receive_ack(dict(sender.identity, b=1, m='0'*16, w=8, r=1), now=1)
            self.assertLessEqual(sender.fps, sender.max_fps)
        for limit in (True, 0, -1, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                Sender(session, digest(b'x').hex(), 1, 1, fps_limit=limit)

    def test_injection_drops_held_frame_even_when_capture_pixels_vary(self):
        import tempfile
        import numpy as np
        from queue import Queue
        from unittest.mock import patch
        from visual_link.bootstrap import _receive
        from visual_link.core.control import Message
        class Messages(Queue):
            def cancel_join_thread(self): pass
            def close(self): pass
        messages=Messages()
        hello=Message('b'*32,None,1,0,'HELLO',{})
        ack=Message('b'*32,'a'*32,8,0,'ACK',{})
        frame=SimpleNamespace(metadata=dict(session='s',config_id=1,file_id='f',index=0))
        images=[np.full((4,4),i,np.uint8) for i in range(10)]
        with tempfile.TemporaryDirectory() as directory, \
             patch('visual_link.bootstrap.capture_frames',return_value=iter(images)), \
             patch('visual_link.bootstrap.decode',side_effect=[hello,ValueError(),ValueError(),ValueError(),ack,ValueError()]), \
             patch('visual_link.discovery.codec_timings',return_value=[1,1]), \
             patch('visual_link.calibration.pattern',return_value=images[0]), \
             patch('visual_link.calibration.measure',return_value={}), \
             patch('visual_link.calibration.receive_wire',return_value=b''), \
             patch('visual_link.core.protocol.decode',side_effect=[ValueError(),frame,frame,frame]):
            _receive(dict(_calibrate=True,timeout=1,test_faults=dict(drop_data_once=[0])),
                     directory,messages,SimpleNamespace(is_set=lambda:False),SimpleNamespace(value=0))
            values=[]
            while not messages.empty():values.append(messages.get_nowait())
            self.assertEqual(sum(isinstance(v,dict) and 'frame' in v for v in values),1)
            self.assertEqual(sum(isinstance(v,dict) and 'fault' in v for v in values),1)


if __name__=='__main__':unittest.main()
