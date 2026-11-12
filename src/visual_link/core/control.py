"""Versioned bootstrap messages, independent of the v1 data geometry."""
import json
import struct
import zlib
from dataclasses import dataclass

import cv2
import numpy as np
from reedsolo import RSCodec, ReedSolomonError

ROWS, COLUMNS = 48, 92
HEADER = struct.Struct('!4sBH')
DATA_BYTES = 446
MAX_PAYLOAD = DATA_BYTES - HEADER.size - 4
KINDS = {'SELECT', 'STATUS', 'HELLO', 'CAPS', 'PROPOSE', 'ACCEPT', 'COMMIT', 'READY', 'REPORT', 'ACK', 'FINISH'}


def identifier(value):
    if not isinstance(value, str) or len(value) != 32:
        raise ValueError('session identifier must be 32 lowercase hex characters')
    if any(c not in '0123456789abcdef' for c in value):
        raise ValueError('invalid session identifier')
    return value


@dataclass(frozen=True)
class Message:
    sender: str
    peer: str | None
    sequence: int
    ack: int
    kind: str
    body: dict

    def to_bytes(self):
        identifier(self.sender)
        if self.peer is not None:
            identifier(self.peer)
        if (type(self.sequence) is not int or not 1 <= self.sequence < 2**32
                or type(self.ack) is not int or not 0 <= self.ack < 2**32):
            raise ValueError('invalid control sequence or acknowledgement')
        if not isinstance(self.kind, str) or self.kind not in KINDS or not isinstance(self.body, dict):
            raise ValueError('invalid control kind or body')
        try:
            payload = json.dumps([self.sender, self.peer, self.sequence, self.ack,
                                  self.kind, self.body], separators=(',', ':'),
                                 allow_nan=False).encode('utf-8')
        except (TypeError, ValueError) as exc:
            raise ValueError('invalid control JSON') from exc
        if len(payload) > MAX_PAYLOAD:
            raise ValueError('control payload exceeds bootstrap capacity')
        return payload

    @classmethod
    def from_bytes(cls, payload):
        try:
            values = json.loads(payload)
            if not isinstance(values, list) or len(values) != 6:
                raise ValueError('expected six message fields')
            message = cls(*values)
            message.to_bytes()
            return message
        except (UnicodeError, TypeError, ValueError) as exc:
            raise ValueError('invalid control message') from exc


def encode(message, width=1920, height=1080):
    """Center a black border and binary cells in a white quiet zone."""
    cell = min(width // (COLUMNS + 4), height // (ROWS + 4))
    if cell < 4:
        raise ValueError('bootstrap display is too small')
    payload = message.to_bytes()
    data = HEADER.pack(b'VLBC', 1, len(payload)) + payload
    data += struct.pack('!I', zlib.crc32(data))
    data = data.ljust(DATA_BYTES, b'\0')
    rs = RSCodec(32)
    wire = bytes(rs.encode(data[:223])) + bytes(rs.encode(data[223:]))
    grid = np.zeros((ROWS, COLUMNS), np.uint8)
    interior = np.zeros((ROWS-2)*(COLUMNS-2), np.uint8)
    bits = np.unpackbits(np.frombuffer(wire, np.uint8))
    interior[:len(bits)] = bits * 255
    grid[1:-1, 1:-1] = interior.reshape(ROWS-2, COLUMNS-2)
    image = np.full((height, width), 255, np.uint8)
    y, x = (height-ROWS*cell)//2, (width-COLUMNS*cell)//2
    image[y:y+ROWS*cell, x:x+COLUMNS*cell] = np.repeat(np.repeat(grid, cell, 0), cell, 1)
    return image


def _decode_grid(grid):
    wire = np.packbits((grid[1:-1, 1:-1].ravel()[:4080] > 127).astype(np.uint8)).tobytes()
    rs = RSCodec(32)
    try:
        data = bytes(rs.decode(wire[:255])[0]) + bytes(rs.decode(wire[255:])[0])
    except ReedSolomonError as exc:
        raise ValueError('uncorrectable bootstrap codeword') from exc
    magic, version, length = HEADER.unpack(data[:HEADER.size])
    if magic != b'VLBC' or version != 1 or length > MAX_PAYLOAD:
        raise ValueError('unsupported bootstrap header')
    end = HEADER.size + length
    if zlib.crc32(data[:end]) != struct.unpack('!I', data[end:end+4])[0] or any(data[end+4:]):
        raise ValueError('bootstrap checksum or padding failed')
    return Message.from_bytes(data[HEADER.size:end])


def decode(image):
    """Find axis-aligned borders; test rotations/reflections without data ROI."""
    if image.dtype != np.uint8 or image.ndim not in (2, 3):
        raise ValueError('bootstrap requires uint8 gray or BGR image')
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(255-binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = sorted((cv2.boundingRect(c) for c in contours), key=lambda b: b[2]*b[3], reverse=True)
    for x, y, w, h in candidates[:16]:
        if min(w, h) < 100:
            continue
        patch = binary[y:y+h, x:x+w]
        for rotation in range(4):
            rotated = np.rot90(patch, rotation)
            if abs(rotated.shape[1]/rotated.shape[0] - COLUMNS/ROWS) > 0.15:
                continue
            for view in (rotated, rotated[:, ::-1]):
                grid = cv2.resize(view, (COLUMNS, ROWS), interpolation=cv2.INTER_AREA)
                try:
                    return _decode_grid(grid)
                except ValueError:
                    pass
    raise ValueError('no valid bootstrap control frame')
