"""Measure the connect codec path using preserved USB evidence, without display I/O."""
import argparse
import json
import math
import platform
import statistics
import time
from pathlib import Path

import cv2
import numpy as np

from visual_link import calibration
from visual_link.core import control, protocol
from visual_link.core.mapping import read_image


def measure(operation, iterations):
    samples = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns()-start)/1e6)
    ordered = sorted(samples)
    return dict(count=len(samples), min_ms=ordered[0], median_ms=statistics.median(samples),
                p95_ms=ordered[math.ceil(len(samples)*0.95)-1], max_ms=ordered[-1],
                total_ms=sum(samples), samples_ms=samples)


def profile(capture, measurement, control_capture, iterations, preencode_count):
    image = read_image(capture)
    geometry = json.loads(Path(measurement).read_text())
    wire = calibration.receive_wire(image, geometry)
    frame = protocol.decode(wire)
    control_image = read_image(control_capture)
    message = control.decode(control_image)
    previous = image.copy()
    identity = None
    if 'session' in frame.metadata:
        m = frame.metadata
        identity = (bytes.fromhex(m['session']), m['config_id'], bytes.fromhex(m['file_id']))
    m = frame.metadata

    def encode_frame():
        return protocol.encode_frame(frame.data, index=m['index'], count=m['count'],
                    total=m['total'], file_hash=bytes.fromhex(m['file_sha256']),
                    seed=m['index'], identity=identity)[0]

    def reject_control():
        try:
            control.decode(image)
        except ValueError:
            return
        raise AssertionError('business image decoded as control')

    def receive_data():
        reject_control()
        result = protocol.decode(calibration.receive_wire(image, geometry))
        assert result.data == frame.data

    operations = dict(control_on_data=reject_control,
                      control_decode=lambda: control.decode(control_image),
                      control_encode=lambda: control.encode(message),
                      duplicate_compare=lambda: np.array_equal(previous, image),
                      receive_wire=lambda: calibration.receive_wire(image, geometry),
                      rs_and_hash=lambda: protocol.decode(wire),
                      receive_data=receive_data, encode_frame=encode_frame)
    # Warm caches once, separately from measured samples.
    for operation in operations.values():
        operation()
    timings = {name: measure(operation, iterations) for name, operation in operations.items()}
    started = time.perf_counter_ns()
    images = [encode_frame() for _ in range(preencode_count)]
    elapsed = (time.perf_counter_ns()-started)/1e6
    return dict(scope='native codec replay of saved USB frames; no live link throughput',
                platform=platform.platform(), python=platform.python_version(),
                numpy=np.__version__, opencv=cv2.__version__,
                capture=str(capture), calibration=str(measurement), control_capture=str(control_capture),
                frame_index=m['index'], payload_bytes=len(frame.data), timings=timings,
                preencode=dict(count=len(images), elapsed_ms=elapsed,
                               image_bytes=sum(i.nbytes for i in images)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--calibration', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=12)
    parser.add_argument('--preencode-count', type=int, default=24)
    args = parser.parse_args()
    if args.iterations < 1 or args.preencode_count < 1:
        parser.error('iteration and preencode counts must be positive')
    # Reserve the output before doing expensive work, without overwriting evidence.
    with args.output.open('x', encoding='utf-8') as output:
        result = profile(args.capture, args.calibration, args.control,
                         args.iterations, args.preencode_count)
        json.dump(result, output, indent=2)
        output.write('\n')
    print(json.dumps(dict(output=str(args.output), preencode=result['preencode'])))


if __name__ == '__main__':
    main()
