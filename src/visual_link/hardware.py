"""Single-frame hardware operations, with durable diagnostic artifacts."""
import json
import os
import tempfile
import subprocess
import sys
from pathlib import Path

from .core.mapping import read_image, image_to_wire
from .core.protocol import decode, digest
from .core.reassembly import Reassembler
from .core.image import image_parameters


def save_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def capture(config, directory, strict=True):
    """Bound all OpenCV open/read calls by killing and reaping their worker."""
    save_json(directory/'config.json', config)
    try:
        result = subprocess.run([sys.executable, '-m', 'visual_link.backends.worker', str(directory.resolve()), *([] if strict else ['--observe'])],
                                capture_output=True, timeout=config['timeout'])
    except subprocess.TimeoutExpired as exc:
        save_json(directory/'device.json', dict(status='timeout', expected=None, received=0, missing=None,
                                                error='capture worker exceeded total timeout and was terminated'))
        raise ValueError('capture timed out; see device.json') from exc
    (directory/'driver.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        if not (directory/'device.json').exists():
            save_json(directory/'device.json', dict(status='worker_failed', exitcode=result.returncode))
        raise ValueError('capture failed; see device.json and driver.log')
    return read_image(directory/'capture.png')


def receive_one(config, image, directory):
    """Decode only captured pixels. Never accept a source file or seed."""
    parameters = image_parameters(config, 'rx')
    result = Reassembler()
    try:
        if config['orientation'] == 'flip_vertical':
            image = image[::-1].copy()
        result.add(decode(image_to_wire(image, captured=True, layout='column', parameters=parameters), parameters=parameters))
        data = result.finish()
        destination = directory/'recovered.bin'
        fd, temporary = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            with destination.open('xb'):
                pass
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        report = dict(status='complete', **result.stats(), bytes=len(data), sha256=digest(data).hex())
    except ValueError as exc:
        report = dict(status='rejected', **result.stats(), error=str(exc))
        save_json(directory/'rx.json', report)
        raise
    save_json(directory/'rx.json', report)
    return report
