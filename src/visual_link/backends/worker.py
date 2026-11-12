"""Isolated driver worker; run only through hardware.capture()."""
import json
import sys
from pathlib import Path

from .camera import capture_one
from .image_sequence import write


def main():
    directory = Path(sys.argv[1])
    config = json.loads((directory/'config.json').read_text())
    metadata = {}
    try:
        image = capture_one(config, metadata, strict='--observe' not in sys.argv[2:])
        write(directory/'capture.png', image)
        metadata['status'] = 'captured'
    except Exception as exc:
        metadata.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    (directory/'device.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return 0 if metadata['status'] == 'captured' else 2


if __name__ == '__main__':
    sys.exit(main())
