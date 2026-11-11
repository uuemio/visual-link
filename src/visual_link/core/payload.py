import hashlib

def random_data(seed, size):
    """SHA256 counter stream: ASCII seed, colon, uint64 big-endian counter."""
    if seed < 0 or size < 0:
        raise ValueError('seed and size must be nonnegative')
    return b''.join(hashlib.sha256(str(seed).encode('ascii')+b':'+i.to_bytes(8,'big')).digest()
                    for i in range((size+31)//32))[:size]
