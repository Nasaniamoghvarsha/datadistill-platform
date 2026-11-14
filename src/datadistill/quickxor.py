"""QuickXorHash: the content hash OneDrive for Business and SharePoint report for every file
(driveItem.file.hashes.quickXorHash, base64 of 20 bytes). Algorithm as published by Microsoft
(https://learn.microsoft.com/onedrive/developer/code-snippets/quickxorhash).

Used to prove that the copy DataDistill downloaded is byte-identical to the file in the cloud before anything
there is touched. If the hash does not match, deletion is refused: a wrong hash only ever blocks, it never allows.
"""
import base64
import struct

WIDTH = 160
SHIFT = 11
CELLS = (WIDTH - 1) // 64 + 1            # 3 words of 64 bits; the last one holds only 32 bits
LAST_BITS = WIDTH - 64 * (CELLS - 1)     # 32
MASK = (1 << 64) - 1


class QuickXorHash:
    def __init__(self):
        self.data = [0] * CELLS
        self.length = 0
        self.shift_so_far = 0

    def update(self, buf):
        buf = bytes(buf)
        size = len(buf)
        if not size:
            return self
        shift = self.shift_so_far
        idx, off = shift // 64, shift % 64
        for i in range(min(size, WIDTH)):
            last = idx == CELLS - 1
            bits = LAST_BITS if last else 64
            if off <= bits - 8:
                x = 0
                for j in range(i, size, WIDTH):
                    x ^= buf[j]
                self.data[idx] ^= (x << off) & MASK
            else:
                x = 0
                for j in range(i, size, WIDTH):
                    x ^= buf[j]
                low = bits - off
                self.data[idx] ^= (x << off) & MASK
                nxt = 0 if last else idx + 1
                self.data[nxt] ^= (x >> low) & MASK
            off += SHIFT
            while off >= bits:
                idx = 0 if last else idx + 1
                off -= bits
                last = idx == CELLS - 1
                bits = LAST_BITS if last else 64
        self.shift_so_far = (self.shift_so_far + SHIFT * (size % WIDTH)) % WIDTH
        self.length += size
        return self

    def digest(self):
        raw = bytearray(struct.pack("<QQQ", *self.data)[: (WIDTH - 1) // 8 + 1])      # 20 bytes
        ln = struct.pack("<Q", self.length)
        for i in range(8):
            raw[len(raw) - 8 + i] ^= ln[i]
        return bytes(raw)

    def b64(self):
        return base64.b64encode(self.digest()).decode()


def quickxor_file(path, chunk=1 << 20):
    h = QuickXorHash()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.b64()
