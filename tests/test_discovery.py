import unittest
from dataclasses import asdict

import numpy as np

from visual_link import calibration
from visual_link.core.control import Message, encode, decode
from visual_link.core.protocol import encode_frame
from visual_link.discovery import codec_timings
from visual_link.session import Capabilities, CalibrationSession


def captured(source, flip=False, dx=2):
    result=np.zeros(source.shape,np.uint8)
    result[:,:-dx]=source[:,dx:]
    return result[::-1].copy() if flip else result


class DiscoveryTests(unittest.TestCase):
    def test_calibrated_decisions_preserve_every_uint8_boundary(self):
        image = np.resize(np.arange(256, dtype=np.uint8), (1080, 1920))
        for flip in (False, True):
            for offset in (0, 0.5):
                thresholds = [i*16+offset for i in range(1,16)]
                oriented = image[::-1] if flip else image
                values = np.searchsorted(thresholds, oriented[3:, :1918]).ravel().astype(np.uint8)
                packed = (values[::2] << 4) | values[1::2]
                expected = packed.reshape(-1,959).T.copy().tobytes()
                result = dict(orientation='flip_vertical' if flip else 'normal',
                              roi=[3,1080,0,1918], thresholds=thresholds)
                self.assertEqual(calibration.receive_wire(image, result), expected)

    def test_geometry_thresholds_and_wire_preservation(self):
        source=calibration.pattern(123)
        for flip in (False,True):
            image=captured(source,flip)
            # Non-default levels prove the measured thresholds are actually used.
            image=(20+image.astype(float)*0.8).astype(np.uint8)
            result=calibration.measure(source,image)
            self.assertEqual(result['orientation'],'flip_vertical' if flip else 'normal')
            self.assertEqual((result['dx'],result['dy'],result['symbol_errors']),(2,0,0))
            tx,wire=encode_frame(b'calibrated data',seed=1)
            rx=(20+captured(tx,flip).astype(float)*0.8).astype(np.uint8)
            self.assertEqual(calibration.receive_wire(rx,result),wire)

    def test_reject_desktop_wrong_session_and_clipped_roi(self):
        source=calibration.pattern(123)
        for image in (np.zeros(source.shape,np.uint8),captured(calibration.pattern(124)),captured(source,dx=3)):
            with self.assertRaises(ValueError):
                calibration.measure(source,image)

    def test_capability_measurement_validation_and_size(self):
        caps=Capabilities(measured_ms=(100.123,1000.456,33.333))
        for peer in (None,'b'*32):
            message=Message('a'*32,peer,2,2,'CAPS',asdict(caps))
            self.assertEqual(Capabilities.parse(decode(encode(message)).body),caps)
        for times in ((0,1,1),(1,float('nan'),2),(1,2)):
            with self.assertRaises(ValueError):
                Capabilities(measured_ms=times)
        from unittest.mock import patch
        from visual_link.core.control import decode as decode_control
        with patch('visual_link.discovery.decode_control', wraps=decode_control) as control, \
             patch('visual_link.discovery.receive_wire', wraps=calibration.receive_wire) as mapping:
            self.assertTrue(all(x>0 for x in codec_timings()))
            control.assert_called_once()
            mapping.assert_called_once()
            self.assertEqual(control.call_args.args[0].shape, (1080,1920,3))

    def test_reports_loss_restart_and_confirmation(self):
        a,b=CalibrationSession(local_id='a'*32),CalibrationSession(local_id='b'*32)
        result=dict(profile='v1-gray16',orientation='normal',dx=2,dy=0,pixels=2065686,symbol_errors=0)
        for _ in range(6):
            for source,target in ((a,b),(b,a)):
                for message in source.outgoing():target.receive(message)
        a.set_calibration(b.local_id,result)
        b.set_calibration(a.local_id,dict(result,orientation='flip_vertical'))
        self.assertFalse(a.calibrated)
        for tick in range(6):
            for source,target in ((a,b),(b,a)):
                for message in source.outgoing():
                    if message.kind=='REPORT' and tick==0:continue
                    target.receive(message);target.receive(message)
        self.assertTrue(a.calibrated and b.calibrated)
        b=CalibrationSession(local_id='c'*32)
        a.receive(b.outgoing()[0])
        self.assertFalse(a.calibrated)
        self.assertIsNone(a.local_calibration)
        self.assertIsNone(a.peer_report)



def calibration_peer(config, directory, messages, stop, heartbeat):
    import time
    messages.put(dict(probe=dict(capture={},measured_ms=[1,2,33])))
    messages.put(Message('b'*32,None,1,0,'HELLO',{}))
    messages.put(Message('b'*32,'a'*32,2,2,'CAPS',asdict(Capabilities(measured_ms=(1,2,33)))))
    result=dict(profile='v1-gray16',orientation='normal',dx=2,dy=0,pixels=2065686,symbol_errors=0)
    messages.put(dict(sender='b'*32,calibration=result))
    messages.put(Message('b'*32,'a'*32,3,3,'REPORT',result))
    while not stop.wait(0.01):heartbeat.value=time.monotonic()
    messages.cancel_join_thread()


class ConnectTests(unittest.TestCase):
    def test_probe_calibration_report_pipeline(self):
        import json
        import tempfile
        import time
        from pathlib import Path
        from unittest.mock import patch
        from visual_link.bootstrap import exchange
        def display(config,image,seconds,ready,advance):
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                advance();time.sleep(0.01)
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            with patch('visual_link.discovery.display_info',return_value={}), \
                 patch('visual_link.bootstrap.CalibrationSession',return_value=CalibrationSession(local_id='a'*32)):
                result=exchange({'timeout':5},directory,0.2,display=display,
                                receive_target=calibration_peer,calibrate=True)
            self.assertTrue(result['calibrated'])
            report=json.loads((directory/'result.json').read_text())
            self.assertEqual(report['local_capabilities']['measured_ms'],[1,2,33])
            self.assertTrue((directory/'probe.json').exists())


if __name__=='__main__':unittest.main()
