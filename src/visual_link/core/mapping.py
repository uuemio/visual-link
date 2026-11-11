import cv2
import numpy as np
WIDTH, HEIGHT, OFFSET, TOP = 1920,1080,2,3
SYMBOL_BITS=4
WIRE_BYTES=1918*1077//2
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

def wire_to_image(wire, layout='sequential'):
    if len(wire) != WIRE_BYTES:
        raise ValueError('wrong wire length')
    if layout == 'column':
        wire = np.frombuffer(wire, np.uint8).reshape((WIDTH-OFFSET)//2, -1).T.copy().tobytes()
    elif layout != 'sequential':
        raise ValueError('unknown wire layout')
    image = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    image[TOP:, OFFSET:] = CODEBOOK[symbols(wire)].reshape(HEIGHT-TOP, WIDTH - OFFSET)
    return image

def image_to_wire(image, captured=True, layout='sequential'):
    if image.dtype != np.uint8 or image.shape[:2] != (HEIGHT, WIDTH):
        raise ValueError('expected native 1920x1080 uint8 image')
    if image.ndim == 3:
        image = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    pixels = image[TOP:, :WIDTH-OFFSET] if captured else image[TOP:, OFFSET:]
    values = DECISION[pixels].ravel()
    if SYMBOL_BITS == 4:
        packed = ((values[::2] << 4) | values[1::2])
    else:
        shifts = np.arange(SYMBOL_BITS-1,-1,-1,dtype=np.uint8)
        bits = ((values[:,None] >> shifts)&1).astype(np.uint8)
        packed = np.packbits(bits.ravel())
    if layout == 'column':
        packed = packed.reshape(-1, (WIDTH-OFFSET)//2).T.copy().ravel()
    elif layout != 'sequential':
        raise ValueError('unknown wire layout')
    return packed.tobytes()

def read_image(path):
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f'cannot read {path}')
    return image
