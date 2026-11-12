"""Geometry regressions, including pre-change 1080p wire/image fingerprints."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from visual_link import calibration
from visual_link.config import load
from visual_link.core.image import DEFAULT_IMAGE, ImageParameters, image_parameters
from visual_link.core.mapping import image_to_wire, wire_to_image
from visual_link.core.protocol import capacity, decode, decode_wire, digest, encode_frame
from visual_link.core.reassembly import Reassembler
from visual_link.core.storage import DiskReassembler, FileSource
from visual_link.discovery import codec_timings
from visual_link.backends.camera import capture_frames


class ImageParameterTests(unittest.TestCase):
    def test_geometry_and_capacity(self):
        for height, wire, cap, tail in ((1080, 1032843, 1000103, 93),
                                       (1200, 1147923, 1111500, 168)):
            with self.subTest(height=height):
                p = ImageParameters(height=height)
                self.assertEqual(p.interleave_shape, (959, height-3))
                self.assertEqual(p.wire_bytes, wire)
                self.assertEqual(p.capacity, cap)
                self.assertEqual(capacity(parameters=p), cap)
                self.assertEqual(wire-255-cap//247*255, tail)
        for values in ({'height': 720}, {'offset': 3}, {'top': 0}, {'width': True}):
            with self.assertRaises(ValueError):
                ImageParameters(**values)

    def test_1080_golden(self):
        expected = (
            ('f2ed09e17566361f50d62a30130adb701415c87ef12b9693551bc97227dc2c7d',
             '9ab8442cb97105c493e6398ff0ebc82b6dcb2f94b5ef795163dbc23c1de8607c'),
            ('afed592379b09a64e21e6543390ae16463486339f6563124dcfac7c5b8fbb6ee',
             '84f61e51f86e9bbf1be849f4f1f03cbf129aa7f2d4ce9cc4ba53a1e43d61d04a'))
        for i, identity in enumerate((None, (bytes(range(16)), 1, digest(b'agent1 golden')[:12]))):
            image, wire = encode_frame(b'agent1 golden', seed=42, identity=identity)
            self.assertEqual((digest(wire).hex(), digest(image.tobytes()).hex()), expected[i])
        self.assertEqual(digest(calibration.pattern(42).tobytes()).hex(),
                         'dc2f0f3ba353c2d4c5df9c91dfb302c524c1bd8257c50f0f54a016992ed9d38a')

    def test_mapping_source_capture_bgr_and_layout(self):
        for height in (1080, 1200):
            p = ImageParameters(height=height)
            wire = np.random.default_rng(12).bytes(p.wire_bytes)
            for layout in ('sequential', 'column'):
                image = wire_to_image(wire, layout, p)
                self.assertEqual(image.shape, p.shape)
                self.assertEqual(image_to_wire(image, False, layout, p), wire)
                captured = np.zeros_like(image)
                captured[:, :p.active_width] = image[:, p.offset:]
                bgr = np.repeat(captured[:, :, None], 3, axis=2)
                self.assertEqual(image_to_wire(bgr, True, layout, p), wire)
            with self.assertRaises(ValueError):
                image_to_wire(image, parameters=ImageParameters(height=2280-height))
            with self.assertRaises(ValueError):
                decode(wire, ImageParameters(height=2280-height))

    def test_empty_full_and_rs_correction(self):
        for height in (1080, 1200):
            p = ImageParameters(height=height)
            for data in (b'', b'x'*p.capacity):
                _, wire = encode_frame(data, parameters=p)
                damaged = bytearray(wire)
                damaged[8] ^= 7  # Protected header.
                damaged[255+10] ^= 13  # Payload codeword.
                self.assertEqual(decode(bytes(damaged), p).data, data)
                self.assertEqual(decode(bytes(damaged), p).corrected_symbols, 1)
            _, wire = encode_frame(b'slow diagnostic decoder', parameters=p)
            self.assertEqual(decode_wire(wire, p).data, b'slow diagnostic decoder')
            with self.assertRaises(ValueError):
                encode_frame(b'x'*(p.capacity+1), parameters=p)

    def test_two_directions_multiframe_disk_and_memory(self):
        # Interleave two live directions in one process. Their fragment offsets differ.
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            sources, disks, memory, originals = [], [], [], []
            try:
                for i, height in enumerate((1080, 1200)):
                    p = ImageParameters(height=height)
                    data = np.random.default_rng(i).bytes(p.capacity*2+37)
                    path = root/f'input-{i}'
                    path.write_bytes(data)
                    originals.append(data)
                    sources.append(FileSource(path, p))
                    disks.append(DiskReassembler(root/f'{i}.part', root/f'out-{i}', p))
                    memory.append(Reassembler())
                    self.assertEqual(sources[i].count, 3)
                for index in (2, 0, 0, 1):  # Out of order, duplicate, short final fragment.
                    for i, source in enumerate(sources):
                        p = source.parameters
                        identity = (b's'*16, i+1, source.file_hash[:12])
                        image, _ = encode_frame(source.read(index), index=index, count=source.count,
                                                total=source.total, file_hash=source.file_hash,
                                                identity=identity, parameters=p)
                        frame = decode(image_to_wire(image, False, 'column', p), p)
                        disks[i].add(frame)
                        memory[i].add(frame)
                for i in range(2):
                    self.assertEqual(memory[i].finish(), originals[i])
                    receipt = disks[i].finish()
                    self.assertEqual(receipt['sha256'], digest(originals[i]).hex())
                    self.assertEqual((root/f'out-{i}').read_bytes(), originals[i])
                    self.assertEqual(disks[i].stats()['duplicates'], 1)
                    self.assertFalse((root/f'{i}.part').exists())
            finally:
                for item in sources+disks:
                    item.close()

    def test_storage_uses_explicit_receive_capacity(self):
        p = ImageParameters(height=1200)
        data = b'x'*(p.capacity+1)
        _, wire = encode_frame(data[:p.capacity], total=len(data), count=2,
                               file_hash=digest(data), parameters=p)
        frame = decode(wire, p)
        with tempfile.TemporaryDirectory() as root:
            receiver = DiskReassembler(Path(root)/'part', Path(root)/'out')
            try:
                with self.assertRaises(ValueError):
                    receiver.add(frame)
                self.assertFalse((Path(root)/'part').exists())
            finally:
                receiver.close()

    def test_calibration_geometry_orientation_and_thresholds(self):
        for height in (1080, 1200):
            p = ImageParameters(height=height)
            target = calibration.pattern(42, p)
            capture = np.zeros_like(target)
            capture[:, :p.active_width] = target[:, p.offset:]
            capture = capture[::-1].copy()
            result = calibration.measure(target, capture, p)
            self.assertEqual(result['orientation'], 'flip_vertical')
            self.assertEqual((result['dx'], result['dy']), (2, 0))
            self.assertEqual(result['roi'], list(p.rx_roi))
            self.assertEqual(result['pixels'], p.pixels)
            self.assertEqual(result['symbol_errors'], 0)
            report = calibration.compare(target, capture, 'flip_vertical', p)
            self.assertEqual(report['symbol_errors'], 0)
            self.assertTrue(report['configured_alignment_matches'])
            image, wire = encode_frame(b'calibrated data', parameters=p)
            captured = np.zeros_like(image)
            captured[:, :p.active_width] = image[:, p.offset:]
            bgr = np.repeat(captured[::-1, :, None], 3, axis=2)
            self.assertEqual(calibration.receive_wire(bgr, result, p), wire)
            wrong = dict(result, roi=[0, 1, 0, 2])
            with self.assertRaises(ValueError):
                calibration.receive_wire(bgr, wrong, p)
            self.assertEqual(len(codec_timings(p)), 2)

    def test_configuration_direction_overrides(self):
        config = json.loads(Path('configs/pi-recommended.json').read_text())
        config['tx_mode'] = {'width': 1920, 'height': 1200}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'config.json'
            path.write_text(json.dumps(config))
            parsed = load(path)
            self.assertEqual(image_parameters(parsed, 'tx').height, 1200)
            self.assertEqual(image_parameters(parsed, 'rx'), DEFAULT_IMAGE)
            config['rx_mode'] = {'height': 720}
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                load(path)

    def test_capture_requests_and_checks_receive_geometry(self):
        class Camera:
            def __init__(self, p):
                self.p, self.values, self.closed = p, {}, False
            def open(self, *args): return True
            def getBackendName(self): return 'mock'
            def set(self, prop, value): self.values[prop] = value; return True
            def get(self, prop): return self.values.get(prop, 0)
            def read(self): return True, np.zeros((*self.p.shape, 3), np.uint8)
            def release(self): self.closed = True
        for height in (1080, 1200):
            p = ImageParameters(height=height)
            camera = Camera(p)
            config = dict(backend='linux', device=0, timeout=2, width=1920, height=1080,
                          tx_mode={'height': 2280-height}, rx_mode={'height': height})
            with patch('visual_link.backends.camera.cv2.VideoCapture', return_value=camera), \
                    patch('visual_link.backends.camera.sys.platform', 'linux'):
                metadata = {}
                frames = capture_frames(config, metadata)
                try:
                    self.assertEqual(next(frames).shape, (*p.shape, 3))
                    self.assertEqual(metadata['requested']['height'], height)
                    self.assertEqual(camera.values[cv2.CAP_PROP_FRAME_HEIGHT], height)
                finally:
                    frames.close()
                self.assertTrue(camera.closed)


if __name__ == '__main__':
    unittest.main()
