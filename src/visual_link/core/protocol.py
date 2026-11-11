import hashlib, struct
from dataclasses import dataclass
import numpy as np
from .mapping import WIRE_BYTES, wire_to_image
from .rs import codec, RSCodec, ReedSolomonError, parity
HEADER_BYTES=255
HEADER=struct.Struct(">8sBBBIIIQ32s32s")
MAGIC=b"GRAYRS01"
IDENTITY=struct.Struct(">16sI12s")

def digest(data):
    return hashlib.sha256(data).digest()

def capacity(n=255, k=247):
    codec(n, k)
    return ((WIRE_BYTES - HEADER_BYTES) // n) * k

def encode_frame(data, n=255, k=247, index=0, count=1, total=None, file_hash=None, seed=0, layout='column', identity=None):
    if (n,k) != (255,247) or layout != 'column':
        raise ValueError('stage one requires RS(255,247) and column layout')
    cap = capacity(n, k)
    if len(data) > cap or not 0 <= index < count:
        raise ValueError('payload too long or invalid frame index')
    if total is None:
        total = len(data)
    if file_hash is None:
        file_hash = digest(data)
    header = HEADER.pack(MAGIC if identity is None else b"GRAYRS02", 1 if identity is None else 2, n, k, index, count, len(data), total, digest(data), file_hash)
    if identity is not None:
        session, config_id, file_id = identity
        if len(session) != 16 or len(file_id) != 12 or type(config_id) is not int or not 1 <= config_id < 2**32:
            raise ValueError('invalid data identity')
        header += IDENTITY.pack(session, config_id, file_id)
    head = bytes(RSCodec(128).encode(header.ljust(127, b'\0')))
    rng = np.random.default_rng(seed)
    padded = data + rng.bytes(cap - len(data))
    rs = codec(n, k)
    messages = np.frombuffer(padded, np.uint8).reshape(-1,k)
    body = np.concatenate((messages, parity(messages,n,k)), axis=1).tobytes()
    wire = head + body
    wire += rng.bytes(WIRE_BYTES - len(wire))
    return wire_to_image(wire, layout), wire

@dataclass
class Decoded:
    metadata: dict
    data: bytes
    blocks: list[bytes]
    failed_blocks: list[int]
    corrected_symbols: int
    integrity: bool

def decode_header(wire):
    if len(wire) != WIRE_BYTES:
        raise ValueError('wrong wire length')
    try:
        raw = bytes(RSCodec(128).decode(wire[:HEADER_BYTES])[0])
    except ReedSolomonError as exc:
        raise ValueError('uncorrectable protected header') from exc
    magic, version, n, k, index, count, length, total, frame_hash, file_hash = HEADER.unpack(raw[:HEADER.size])
    if (magic,version) not in ((MAGIC,1),(b"GRAYRS02",2)) or not 0 <= index < count or length > capacity(n, k) or total < length:
        raise ValueError('invalid protected header')
    if (n,k) != (255,247) or count != max(1,(total+capacity(n,k)-1)//capacity(n,k)) or length != min(capacity(n,k),max(0,total-index*capacity(n,k))):
        raise ValueError('unsupported parameters or inconsistent frame lengths')
    metadata = dict(n=n, k=k, index=index, count=count, length=length, total=total,
                    frame_sha256=frame_hash.hex(), file_sha256=file_hash.hex())
    if version == 2:
        session, config_id, file_id = IDENTITY.unpack(raw[HEADER.size:127])
        if config_id == 0:
            raise ValueError('invalid data configuration ID')
        metadata.update(session=session.hex(), config_id=config_id, file_id=file_id.hex())
    return metadata

def decode_wire(wire):
    metadata = decode_header(wire)
    n, k, length = metadata['n'], metadata['k'], metadata['length']
    rs = codec(n, k)
    # All full blocks are decoded, including padding, for a consistent block metric.
    blocks, failures, corrected = [], [], 0
    for b, p in enumerate(range(HEADER_BYTES, HEADER_BYTES + capacity(n, k)//k*n, n)):
        chunk = wire[p:p+n]
        try:
            message, _, errata = rs.decode(chunk)
            blocks.append(bytes(message))
            corrected += len(errata)
        except ReedSolomonError:
            # Explicit systematic-byte fallback, never silently replace with oracle data.
            blocks.append(chunk[:k])
            failures.append(b)
    data = b''.join(blocks)[:length]
    return Decoded(metadata, data, blocks, failures, corrected,
                   digest(data).hex() == metadata['frame_sha256'])

def decode(wire):
    metadata = decode_header(wire)
    n,k=metadata['n'],metadata['k']
    blocks=np.frombuffer(wire[255:255+capacity(n,k)//k*n],np.uint8).reshape(-1,n)
    messages=blocks[:,:k].copy()
    bad=np.flatnonzero(np.any(parity(messages,n,k)!=blocks[:,k:],axis=1))
    corrected=0
    for b in bad:
        try:
            message,_,errata=codec(n,k).decode(blocks[b].tobytes())
        except ReedSolomonError as e:
            raise ValueError('uncorrectable block') from e
        messages[b]=np.frombuffer(message,np.uint8)
        corrected+=len(errata)
    data=messages.tobytes()[:metadata['length']]
    if digest(data).hex()!=metadata['frame_sha256']:
        raise ValueError('frame SHA mismatch')
    return Decoded(metadata,data,[],[],corrected,True)
