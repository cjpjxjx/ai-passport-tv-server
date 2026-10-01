"""IMA ADPCM encoder for the FAV1 audio packet.

A block is a 4-byte header followed by one 4-bit code per sample, the earlier
sample of each pair in the low nibble. The header is the encoder state before the
first sample: predictor as big-endian s16, step index as u8, then a zero byte. The
decoder restarts from that header on every block, so a lost block does not carry
into the next. This is not the WAV layout, which puts sample 0 in the header.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence

STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767,
)
INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8)
INDEX_MAX = len(STEP_TABLE) - 1
HEADER_BYTES = 4


class Encoder:
    """Carries the predictor and step index from one block to the next."""

    def __init__(self, predictor: int = 0, index: int = 0):
        self.predictor = predictor
        self.index = index

    def encode(self, samples: Sequence[int]) -> bytes:
        """Encode an even number of samples into one block, header first."""
        if len(samples) % 2:
            raise ValueError("a block holds an even number of samples")
        header = struct.pack(">hBB", self.predictor, self.index, 0)
        pred, index = self.predictor, self.index
        codes = []
        for sample in samples:
            step = STEP_TABLE[index]
            diff = sample - pred
            sign = 0
            if diff < 0:
                sign = 8
                diff = -diff
            code = 0
            delta = step >> 3
            if diff >= step:
                code = 4
                diff -= step
                delta += step
            step >>= 1
            if diff >= step:
                code |= 2
                diff -= step
                delta += step
            step >>= 1
            if diff >= step:
                code |= 1
                delta += step
            pred += -delta if sign else delta
            pred = -32768 if pred < -32768 else 32767 if pred > 32767 else pred
            index += INDEX_TABLE[code]
            index = 0 if index < 0 else INDEX_MAX if index > INDEX_MAX else index
            codes.append(code | sign)
        self.predictor, self.index = pred, index
        packed = bytes(low | high << 4 for low, high in zip(codes[0::2], codes[1::2]))
        return header + packed

    def encode_pcm(self, pcm: bytes) -> bytes:
        """Encode little-endian signed 16-bit mono PCM."""
        if len(pcm) % 4:
            raise ValueError("PCM must hold an even number of 16-bit samples")
        return self.encode(struct.unpack(f"<{len(pcm) // 2}h", pcm))


def decode(block: bytes, count: int) -> list[int]:
    """Decode `count` samples of one block; the reference the encoder is checked against."""
    pred, index, zero = struct.unpack_from(">hBB", block)
    if zero or index > INDEX_MAX:
        raise ValueError("invalid block header")
    out = []
    for at in range(count):
        byte = block[HEADER_BYTES + at // 2]
        code = byte >> 4 if at % 2 else byte & 0x0F
        step = STEP_TABLE[index]
        delta = step >> 3
        if code & 4:
            delta += step
        if code & 2:
            delta += step >> 1
        if code & 1:
            delta += step >> 2
        pred += -delta if code & 8 else delta
        pred = -32768 if pred < -32768 else 32767 if pred > 32767 else pred
        index += INDEX_TABLE[code & 7]
        index = 0 if index < 0 else INDEX_MAX if index > INDEX_MAX else index
        out.append(pred)
    return out
