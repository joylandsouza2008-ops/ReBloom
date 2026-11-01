# Minimal QR Code encoder (byte mode, versions 1-6), after Project Nayuki's reference design.
import sys

ECC_PER_BLOCK = {"L": [-1, 7, 10, 15, 20, 26, 18], "M": [-1, 10, 16, 26, 18, 24, 16]}
NUM_BLOCKS = {"L": [-1, 1, 1, 1, 1, 1, 2], "M": [-1, 1, 1, 1, 2, 2, 4]}
FORMAT_BITS = {"L": 1, "M": 0}

def raw_modules(v):
    r = (16 * v + 128) * v + 64
    if v >= 2:
        n = v // 7 + 2
        r -= (25 * n - 10) * n - 55
    return r

def data_codewords(v, e):
    return raw_modules(v) // 8 - ECC_PER_BLOCK[e][v] * NUM_BLOCKS[e][v]

def gf_mul(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF

def rs_divisor(deg):
    res = [0] * (deg - 1) + [1]
    root = 1
    for _ in range(deg):
        for j in range(deg):
            res[j] = gf_mul(res[j], root)
            if j + 1 < deg:
                res[j] ^= res[j + 1]
        root = gf_mul(root, 0x02)
    return res

def rs_remainder(data, div):
    res = [0] * len(div)
    for b in data:
        f = b ^ res.pop(0)
        res.append(0)
        for i, c in enumerate(div):
            res[i] ^= gf_mul(c, f)
    return res

def align_positions(v):
    if v == 1:
        return []
    n = v // 7 + 2
    step = (v * 8 + n * 3 + 5) // (n * 4 - 4) * 2
    res = [v * 4 + 10 - i * step for i in range(n - 1)] + [6]
    return sorted(res)

def encode(text, ecl="M"):
    data = text.encode("utf-8")
    for v in range(1, 7):
        cap = data_codewords(v, ecl) * 8
        need = 4 + (8 if v < 10 else 16) + 8 * len(data)
        if need <= cap:
            break
    else:
        raise ValueError("too long")
    bits = []
    def put(val, n):
        bits.extend((val >> i) & 1 for i in range(n - 1, -1, -1))
    put(0b0100, 4); put(len(data), 8)
    for b in data: put(b, 8)
    put(0, min(4, cap - len(bits)))
    put(0, (-len(bits)) % 8)
    pad = 0xEC
    while len(bits) < cap:
        put(pad, 8); pad ^= 0xEC ^ 0x11
    cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    # split into blocks, add ECC, interleave
    nb, eb = NUM_BLOCKS[ecl][v], ECC_PER_BLOCK[ecl][v]
    raw = raw_modules(v) // 8
    nshort = nb - raw % nb
    short_len = raw // nb
    div = rs_divisor(eb)
    blocks, k = [], 0
    for i in range(nb):
        dl = short_len - eb + (0 if i < nshort else 1)
        d = cw[k:k + dl]; k += dl
        e = rs_remainder(d, div)
        if i < nshort: d = d + [None]
        blocks.append(d + e)
    final = []
    for i in range(len(blocks[0])):
        for j, b in enumerate(blocks):
            if b[i] is not None:
                final.append(b[i])

    size = v * 4 + 17
    m = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]
    def setf(x, y, dark):
        m[y][x] = dark; fn[y][x] = True
    for i in range(size):
        setf(6, i, i % 2 == 0); setf(i, 6, i % 2 == 0)
    def finder(cx, cy):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    d = max(abs(dx), abs(dy))
                    setf(x, y, d not in (2, 4))
    finder(3, 3); finder(size - 4, 3); finder(3, size - 4)
    al = align_positions(v)
    for i in al:
        for j in al:
            if (i == 6 and j == 6) or (i == 6 and j == al[-1]) or (i == al[-1] and j == 6):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(i + dx, j + dy, max(abs(dx), abs(dy)) != 1)
    def draw_format(mask):
        d = FORMAT_BITS[ecl] << 3 | mask
        rem = d
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        b = (d << 10 | rem) ^ 0x5412
        g = lambda i: (b >> i) & 1 == 1
        for i in range(6): setf(8, i, g(i))
        setf(8, 7, g(6)); setf(8, 8, g(7)); setf(7, 8, g(8))
        for i in range(9, 15): setf(14 - i, 8, g(i))
        for i in range(8): setf(size - 1 - i, 8, g(i))
        for i in range(8, 15): setf(8, size - 15 + i, g(i))
        setf(8, size - 8, True)
    draw_format(0)

    # place data
    allbits = [(final[i >> 3] >> (7 - (i & 7))) & 1 for i in range(len(final) * 8)]
    i = 0
    right = size - 1
    while right >= 1:
        if right == 6: right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if not fn[y][x] and i < len(allbits):
                    m[y][x] = allbits[i] == 1; i += 1
        right -= 2

    MASKS = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0,
             lambda x, y: (x // 3 + y // 2) % 2 == 0, lambda x, y: x * y % 2 + x * y % 3 == 0,
             lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0, lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]
    def apply(mask):
        for y in range(size):
            for x in range(size):
                if not fn[y][x] and MASKS[mask](x, y):
                    m[y][x] = not m[y][x]
    def penalty():
        p = 0
        lines = [row[:] for row in m] + [[m[y][x] for y in range(size)] for x in range(size)]
        for line in lines:
            run = 1
            for k in range(1, size + 1):
                if k < size and line[k] == line[k - 1]: run += 1
                else:
                    if run >= 5: p += run - 2
                    run = 1
            s = "".join("1" if c else "0" for c in line)
            for pat in ("10111010000", "00001011101"):
                p += 40 * sum(1 for k in range(len(s) - 10) if s[k:k + 11] == pat)
        for y in range(size - 1):
            for x in range(size - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]: p += 3
        dark = sum(sum(r) for r in m); total = size * size
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        return p + k * 10
    best, bestp = 0, None
    for mk in range(8):
        apply(mk); draw_format(mk)
        pp = penalty()
        if bestp is None or pp < bestp: best, bestp = mk, pp
        apply(mk)
    apply(best); draw_format(best)
    return m


def svg(text, dark="#0a0a0a", light="#ffffff", border=4):
    m = encode(text)
    n = len(m)
    path = "".join(f"M{x},{y}h1v1h-1z" for y in range(n) for x in range(n) if m[y][x])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-{border} -{border} {n + 2 * border} {n + 2 * border}" '
            f'shape-rendering="crispEdges"><rect x="-{border}" y="-{border}" width="{n + 2 * border}" height="{n + 2 * border}" '
            f'fill="{light}"/><path fill="{dark}" d="{path}"/></svg>')
