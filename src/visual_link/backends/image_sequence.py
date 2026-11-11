from pathlib import Path
import cv2
from ..core.mapping import read_image

def write(path, image):
    if not cv2.imwrite(str(path),image):
        raise OSError(path)

def frames(directory):
    for p in sorted(Path(directory).glob('*.png')):
        yield read_image(p)
