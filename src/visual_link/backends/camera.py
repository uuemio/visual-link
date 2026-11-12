"""Strict single-frame capture; the caller must bound blocking driver calls."""
import sys
import time

import cv2
import numpy as np
from ..core.image import image_parameters


def capture_frames(config, metadata, stopped=lambda: False):
    """Yield continuous native BGR pixels using one negotiated device handle."""
    parameters = image_parameters(config, 'rx')
    width, height = parameters.width, parameters.height
    backend = config['backend']
    if backend == 'windows' and sys.platform == 'win32':
        api = cv2.CAP_DSHOW
    elif backend == 'linux' and sys.platform.startswith('linux'):
        api = cv2.CAP_V4L2
    else:
        raise ValueError(f'{backend} capture is unavailable on {sys.platform}')
    # DirectShow uses the Data1 of MEDIASUBTYPE_RGB24, not V4L2's BGR3 FOURCC.
    requested_format = 0xE436EB7D if backend == 'windows' else cv2.VideoWriter_fourcc(*'BGR3')
    format_name = 'MEDIASUBTYPE_RGB24' if backend == 'windows' else 'BGR3'
    camera = cv2.VideoCapture()
    metadata.update(requested=dict(device=config['device'], width=width, height=height,
                                   format=format_name, format_id=requested_format), opencv=cv2.__version__)
    try:
        if not camera.open(config['device'], api):
            raise ValueError('cannot open capture device; check index, permissions and USB connection')
        metadata['api'] = camera.getBackendName()
        metadata['set_accepted'] = {
            'fourcc': camera.set(cv2.CAP_PROP_FOURCC, requested_format),
            'width': camera.set(cv2.CAP_PROP_FRAME_WIDTH, width),
            'height': camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height),
            'buffers': camera.set(cv2.CAP_PROP_BUFFERSIZE, 1),
        }
        value = int(camera.get(cv2.CAP_PROP_FOURCC)) & 0xffffffff
        fourcc = ''.join(chr((value >> (8*i)) & 255) for i in range(4))
        metadata['reported'] = dict(
            width=camera.get(cv2.CAP_PROP_FRAME_WIDTH),
            height=camera.get(cv2.CAP_PROP_FRAME_HEIGHT), format_id=value,
            format=format_name if value == requested_format else fourcc,
            fps=camera.get(cv2.CAP_PROP_FPS), convert_rgb=camera.get(cv2.CAP_PROP_CONVERT_RGB))
        if value != requested_format:
            raise ValueError(f'driver format {value:#x} ({fourcc!r}) differs from {format_name}; no format fallback')
        if (metadata['reported']['width'], metadata['reported']['height']) != (width, height):
            raise ValueError(f'driver did not negotiate native {width}x{height}')
        deadline = time.monotonic() + config['timeout']
        valid = 0
        while not stopped():
            if valid <= 5 and time.monotonic() >= deadline:
                raise ValueError('no valid frame after warmup; check HDMI signal')
            ok, image = camera.read()
            if not ok or image is None or image.size == 0:
                time.sleep(0.01)
                continue
            if image.dtype != np.uint8 or image.shape != (*parameters.shape, 3):
                raise ValueError(f'capture must return native {width}x{height} uint8 BGR')
            valid += 1
            if valid <= 5:
                continue
            metadata.update(shape=list(image.shape), dtype=str(image.dtype),
                            strides=list(image.strides), warmup_frames=5,
                            orientation_applied=False, read_monotonic_ns=time.monotonic_ns())
            yield image
    finally:
        camera.release()


def capture_one(config, metadata):
    """Read one warmed-up frame and always close the continuous generator."""
    frames = capture_frames(config, metadata)
    try:
        return next(frames)
    finally:
        frames.close()
