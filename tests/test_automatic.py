import unittest
from dataclasses import replace

from visual_link import bootstrap
from visual_link.presentation import display_parameters
from visual_link.automatic import ModeSession, capabilities
from visual_link.session import Capabilities, NegotiationSession, State, common_modes
from visual_link.core.control import Message
from visual_link.core.image import ImageParameters

MODES = ((1920, 1080), (1920, 1200))
A, B = '1'*32, '2'*32


def pump(a, b, turns=15):
    for _ in range(turns):
        for source, dest in ((a,b),(b,a)):
            for message in source.outgoing():
                dest.receive(Message.from_bytes(message.to_bytes()))


class AutomaticTests(unittest.TestCase):
    def test_directional_intersection_and_device_filter(self):
        caps = Capabilities(modes=MODES, rx_modes=((1920,1080),))
        peer = Capabilities(modes=MODES, rx_modes=MODES)
        self.assertEqual(common_modes(caps,peer), list(MODES[::-1]))
        self.assertEqual(common_modes(peer,caps), [MODES[0]])
        observed = dict(display=dict(current_output=dict(width=1920,height=1200),
                                    declared=[dict(width=1920,height=1200),dict(width=1920,height=1080,interlaced=True)]),
                        capture=dict(declared=[dict(width=1920,height=1200,format='YUYV'),dict(width=1920,height=1080,format='BGR3')]))
        result = capabilities(observed,{})
        self.assertEqual(result.modes, (MODES[1],))
        self.assertEqual(result.rx_modes, (MODES[0],))
        with self.assertRaises(ValueError):
            capabilities(observed,dict(candidate_modes=[[1920,1080]]))

    def test_current_output_limits_automatic_upgrade(self):
        observed = dict(display=dict(current_output=dict(width=1920,height=1080),
                                    declared=[dict(width=w,height=h) for w,h in MODES]),
                        capture=dict(declared=[dict(width=w,height=h,format='BGR3') for w,h in MODES]))
        lower = capabilities(observed, {})
        self.assertEqual(lower.modes, (MODES[0],))
        self.assertEqual(set(lower.rx_modes), set(MODES))
        requested = capabilities(observed, dict(candidate_modes=[[1920,1200]]))
        self.assertEqual(requested.modes, (MODES[1],))
        observed['display']['current_output']['height'] = 1200
        higher = capabilities(observed, {})
        self.assertEqual(common_modes(lower, higher), [MODES[0]])
        self.assertEqual(common_modes(higher, lower), list(MODES[::-1]))

    def test_startup_does_not_consume_business_duration(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        @contextmanager
        def startup(*args):
            yield {'selected': True}, 'session', 'modes'
        with patch('visual_link.automatic.startup', startup), \
             patch('visual_link.bootstrap.time.monotonic', side_effect=[0., 55.]), \
             patch('visual_link.bootstrap._exchange') as exchange:
            bootstrap.exchange({}, 'run', 60, display=object(), calibrate=True, negotiate=True)
        self.assertEqual(exchange.call_args.args[2], 60)

    def test_modes_fallback_stale_status_and_exhaustion(self):
        a=ModeSession(Capabilities(modes=MODES,rx_modes=MODES),A)
        b=ModeSession(Capabilities(modes=MODES,rx_modes=MODES),B)
        pump(a,b)
        self.assertTrue(a.peer_selection and b.peer_selection)
        self.assertTrue(a.selection_confirmed and b.selection_confirmed)
        self.assertEqual(a.selected(), (MODES[1],MODES[1]))
        old = b.status_message()
        a.local_status=[1,-1];b.local_status=[1,1];b.timings=[100.,200.,33.]
        pump(a,b)
        self.assertEqual(a.indices,(0,1))
        self.assertEqual(b.selected(), (MODES[1],MODES[0]))
        a.receive(old)
        self.assertEqual(a.round,2)
        self.assertEqual(a.peer_status, [0,0])
        a.local_status=[1,1]; b.local_status=[1,1]
        a.timings=b.timings=[100.,200.,33.]
        pump(a,b)
        self.assertTrue(a.verified and b.verified)
        a.local_status=[1,-1]
        with self.assertRaisesRegex(ValueError,'no remaining'):
            pump(a,b)

    def test_display_rejection_only_lowers_sender_direction(self):
        caps=Capabilities(modes=MODES,rx_modes=MODES)
        a,b=ModeSession(caps,A),ModeSession(caps,B)
        pump(a,b)
        a.local_status=[-1,1];b.local_status=[1,1]
        a.timings=b.timings=[100.,200.,33.]
        pump(a,b)
        self.assertEqual(a.indices,(1,0))
        self.assertEqual(b.selected(),(MODES[0],MODES[1]))
        self.assertFalse(a.verified)

    def test_negotiation_identity_geometry_and_loss(self):
        a=NegotiationSession(Capabilities(versions=(2,3),modes=MODES,rx_modes=MODES,measured_ms=(100,200,33)),A)
        b=NegotiationSession(a.capabilities,B)
        pump(a,b)
        a.selected_modes=b.selected_modes=(MODES[1],MODES[0])
        for session,mode in ((a,MODES[0]),(b,MODES[1])):
            session.set_calibration(session.peer_id,dict(profile='v1-gray16',orientation='normal',dx=2,dy=0,
                                                        pixels=ImageParameters(*mode).pixels,symbol_errors=0))
        # Repeated messages after skipped transitions heal an entire dropped tick.
        for _ in range(2): a.outgoing();b.outgoing()
        pump(a,b,25)
        self.assertTrue(a.can_send and b.can_send)
        self.assertEqual(a.direction(True)[4:], ['gray16-1920x1200-l1',1920,1200,1])
        self.assertEqual(a.direction(False)[0],2)
        self.assertEqual(a.direction(True)[0],3)
        invalid = dict(directions=[row[:] for row in a.proposal['directions']])
        invalid['directions'][0][6]=1080
        with self.assertRaises(ValueError): a._validate(invalid)
        self.assertLessEqual(max(len(m.to_bytes()) for m in a.outgoing()),435)

    def test_unmatched_capture_still_receives_fallback_controls(self):
        import tempfile
        import threading
        from pathlib import Path
        from unittest.mock import Mock, patch
        import numpy as np
        from visual_link.automatic import _receive
        from visual_link.core.control import encode
        message = Message(B,A,8,0,'SELECT',dict(r=2,i=[1,0]))
        stop = threading.Event()
        closed = []
        def frames(*args, **kwargs):
            try:
                yield np.repeat(encode(message)[:,:,None],3,axis=2)
                stop.set()
            finally:
                closed.append(True)
        messages = Mock()
        with tempfile.TemporaryDirectory() as directory:
            with patch('visual_link.automatic.discovery.refresh_capture',side_effect=ValueError('unmatched')), \
                 patch('visual_link.automatic.capture_frames',side_effect=frames):
                _receive(dict(rx_mode=dict(width=1920,height=1200)),directory,messages,stop,B)
            self.assertTrue((Path(directory)/'capture-000.json').exists())
        messages.put.assert_called_once_with(message,timeout=1)
        self.assertEqual(closed,[True])

    def test_refresh_failure_can_recover_with_verified_actual_capture(self):
        import tempfile
        import threading
        from unittest.mock import Mock, patch
        import numpy as np
        from visual_link.automatic import _receive
        stop=threading.Event()
        def frames(*args, **kwargs):
            # A real calibration needs many samples; mock the expensive pixel
            # measurement to exercise only observation and recovery ownership.
            yield np.zeros((1080,1920,3),np.uint8)
            for _ in range(8):
                yield np.zeros((1080,1920,3),np.uint8)
            stop.set()
        messages=Mock()
        with tempfile.TemporaryDirectory() as directory:
            with patch('visual_link.automatic.discovery.refresh_capture',side_effect=ValueError('temporary unlock')), \
                 patch('visual_link.automatic.capture_frames',side_effect=frames), \
                 patch('visual_link.automatic.calibration.measure',return_value=dict(symbol_errors=0)), \
                 patch('visual_link.automatic.discovery.codec_timings',return_value=[100.,200.]), \
                 patch('visual_link.backends.image_sequence.write'):
                _receive({},directory,messages,stop,B)
        items=[call.args[0] for call in messages.put.call_args_list]
        self.assertEqual(items[0]['actual']['actual'],dict(width=1920,height=1080))
        self.assertTrue(items[0]['actual']['matches'])
        self.assertIn('calibration',items[1])

    def test_current_canvas_is_independent_of_codec(self):
        from visual_link.core.control import encode, decode
        canvas = display_parameters(dict(_control_canvas=dict(width=1600,height=900)))
        message = Message(A,None,1,0,'HELLO',{})
        self.assertEqual(decode(encode(message,canvas.width,canvas.height)),message)
        self.assertEqual(display_parameters({}).shape,(1080,1920))

    def test_incompatible_and_unverified_never_commit(self):
        a=ModeSession(Capabilities(modes=(MODES[1],),rx_modes=(MODES[1],)),A)
        b=ModeSession(Capabilities(),B)
        with self.assertRaisesRegex(ValueError,'no common'):
            pump(a,b)
        c=NegotiationSession(local_id=A)
        self.assertFalse(any(m.kind=='COMMIT' for m in c.outgoing()))

if __name__=='__main__': unittest.main()
