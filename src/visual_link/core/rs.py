from reedsolo import RSCodec, ReedSolomonError
def codec(n, k):
    if not 1 <= k < n <= 255:
        raise ValueError('require 1 <= k < n <= 255')
    return RSCodec(n - k, nsize=n)

from functools import lru_cache
import numpy as np
import reedsolo
@lru_cache(maxsize=16)
def parity_table(n, k):
    rs = codec(n,k)
    generator = rs.gen[n-k]
    table = np.array([[reedsolo.gf_mul(value, g) for g in generator[1:]]
                      for value in range(256)], dtype=np.uint8)
    return table

def parity(messages, n, k):
    """Independent GF(256) LFSRs in parallel, same generator as reedsolo."""
    table = parity_table(n,k)
    result = np.zeros((len(messages),n-k), dtype=np.uint8)
    for column in range(k):
        coefficient = messages[:,column] ^ result[:,0]
        result[:,:-1] = result[:,1:]
        result[:,-1] = 0
        result ^= table[coefficient]
    return result
