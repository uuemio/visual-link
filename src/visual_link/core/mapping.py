import cv2
import numpy as np
from .image import DEFAULT_IMAGE

# Legacy default aliases; active paths use an explicit ImageParameters instance.
WIDTH, HEIGHT = DEFAULT_IMAGE.width, DEFAULT_IMAGE.height
OFFSET, TOP = DEFAULT_IMAGE.offset, DEFAULT_IMAGE.top
SYMBOL_BITS=4
WIRE_BYTES=DEFAULT_IMAGE.wire_bytes
CODEBOOK=np.arange(16,dtype=np.uint8)*17
DECISION=((np.arange(256,dtype=np.uint16)+8)//17).astype(np.uint8)

def symbols(data):
    values = np.frombuffer(data, dtype=np.uint8)
    if SYMBOL_BITS != 4:
        bits = np.unpackbits(values).reshape(-1,SYMBOL_BITS)
        weights = (1 << np.arange(SYMBOL_BITS-1,-1,-1)).astype(np.uint16)
        return (bits*weights).sum(axis=1).astype(np.uint8)
    out = np.empty(values.size * 2, dtype=np.uint8)
    out[::2], out[1::2] = values >> 4, values & 15
    return out

def wire_to_image(wire, layout='sequential', parameters=DEFAULT_IMAGE):
    if len(wire) != parameters.wire_bytes:
        raise ValueError('wrong wire length')
    if layout == 'column':
        wire = np.frombuffer(wire, np.uint8).reshape(parameters.interleave_shape).T.copy().tobytes()
    elif layout != 'sequential':
        raise ValueError('unknown wire layout')
    image = np.zeros(parameters.shape, dtype=np.uint8)
    image[parameters.top:, parameters.offset:] = CODEBOOK[symbols(wire)].reshape(parameters.active_height, parameters.active_width)
    return image

def image_to_wire(image, captured=True, layout='sequential', parameters=DEFAULT_IMAGE):
    if image.dtype != np.uint8 or image.shape[:2] != parameters.shape:
        raise ValueError(f'expected native {parameters.width}x{parameters.height} uint8 image')
    if image.ndim == 3:
        image = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    pixels = image[parameters.top:, :parameters.active_width] if captured else image[parameters.top:, parameters.offset:]
    values = DECISION[pixels].ravel()
    if SYMBOL_BITS == 4:
        packed = ((values[::2] << 4) | values[1::2])
    else:
        shifts = np.arange(SYMBOL_BITS-1,-1,-1,dtype=np.uint8)
        bits = ((values[:,None] >> shifts)&1).astype(np.uint8)
        packed = np.packbits(bits.ravel())
    if layout == 'column':
        packed = packed.reshape(-1, parameters.active_width//2).T.copy().ravel()
    elif layout != 'sequential':
        raise ValueError('unknown wire layout')
    return packed.tobytes()

def read_image(path):
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f'cannot read {path}')
    return image
