"""A small QR code maker (byte mode, versions 1-10, error level L or M), so Wi-Fi QR codes need nothing installed.
`make(text)` returns a square list of rows of booleans (True = dark), without the quiet border."""

# (EC codewords per block, [(blocks, data codewords per block), ...]) for versions 1..10
_M = [(10, [(1, 16)]), (16, [(1, 28)]), (26, [(1, 44)]), (18, [(2, 32)]), (24, [(2, 43)]), (16, [(4, 27)]), (18, [(4, 31)]),
      (22, [(2, 38), (2, 39)]), (22, [(3, 36), (2, 37)]), (26, [(4, 43), (1, 44)])]
_L = [(7, [(1, 19)]), (10, [(1, 34)]), (15, [(1, 55)]), (20, [(1, 80)]), (26, [(1, 108)]), (18, [(2, 68)]), (20, [(2, 78)]),
      (24, [(2, 97)]), (30, [(2, 116)]), (18, [(2, 68), (2, 69)])]
_ALIGN = [[], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]]
_FMT_BITS = {"L": 1, "M": 0}

_EXP, _LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i], _LOG[_x] = _x, _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return 0 if not a or not b else _EXP[_LOG[a] + _LOG[b]]


def _rs(data, n):
    gen = [1]
    for i in range(n):
        nxt = [0] * (len(gen) + 1)
        for j, c in enumerate(gen):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, _EXP[i])
        gen = nxt
    rem = [0] * n
    for b in data:
        f = b ^ rem[0]
        rem = rem[1:] + [0]
        for j in range(n):
            rem[j] ^= _mul(gen[j + 1], f)
    return rem


def _bch(value, poly, bits):
    v = value << bits
    top = poly.bit_length() - 1
    for i in range(v.bit_length() - 1, top - 1, -1):
        if v >> i & 1:
            v ^= poly << (i - top)
    return v


def _capacity(table, ver):
    ec, groups = table[ver - 1]
    return sum(n * d for n, d in groups)


def _pick(nbytes, level):
    table = _M if level == "M" else _L
    for ver in range(1, 11):
        bits = 4 + (8 if ver < 10 else 16) + 8 * nbytes
        if bits <= _capacity(table, ver) * 8:
            return ver
    raise ValueError("That is too long for a QR code here.")


def _codewords(text, ver, level):
    table = _M if level == "M" else _L
    data = text.encode("utf-8")
    bits = "0100" + format(len(data), "08b" if ver < 10 else "016b") + "".join(format(b, "08b") for b in data)
    cap = _capacity(table, ver) * 8
    bits += "0" * min(4, cap - len(bits))
    bits += "0" * (-len(bits) % 8)
    cw = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(cw) < cap // 8:
        cw.append(pad[(len(cw) - len(bits) // 8) % 2])
    ec, groups = table[ver - 1]
    blocks, i = [], 0
    for n, d in groups:
        for _ in range(n):
            blocks.append(cw[i:i + d])
            i += d
    eccs = [_rs(b, ec) for b in blocks]
    out = []
    for k in range(max(len(b) for b in blocks)):
        out += [b[k] for b in blocks if k < len(b)]
    for k in range(ec):
        out += [e[k] for e in eccs]
    return out


def _mask_fn(m):
    return [lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0, lambda r, c: (r + c) % 3 == 0,
            lambda r, c: (r // 2 + c // 3) % 2 == 0, lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
            lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0, lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0][m]


def _penalty(g):
    n, score = len(g), 0
    for rows in (g, [list(col) for col in zip(*g)]):
        for row in rows:
            run = 1
            for i in range(1, n):
                if row[i] == row[i - 1]:
                    run += 1
                    if run == 5:
                        score += 3
                    elif run > 5:
                        score += 1
                else:
                    run = 1
            s = "".join("1" if x else "0" for x in row)
            score += 40 * (s.count("10111010000") + s.count("00001011101"))
    for r in range(n - 1):
        for c in range(n - 1):
            if g[r][c] == g[r][c + 1] == g[r + 1][c] == g[r + 1][c + 1]:
                score += 3
    dark = sum(sum(1 for x in row if x) for row in g)
    return score + 10 * (abs(dark * 100 // (n * n) - 50) // 5)


def make(text, level="M"):
    ver = _pick(len(text.encode("utf-8")), level)
    n = 17 + 4 * ver
    cells = [[None] * n for _ in range(n)]          # None = free for data; True/False = function pattern

    def put(r, c, v):
        if 0 <= r < n and 0 <= c < n:
            cells[r][c] = v

    def finder(r0, c0):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                inside = 0 <= dr <= 6 and 0 <= dc <= 6
                put(r0 + dr, c0 + dc, inside and (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4)))
    finder(0, 0), finder(0, n - 7), finder(n - 7, 0)
    for i in range(8, n - 8):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    pos = _ALIGN[ver - 1]
    for r in pos:
        for c in pos:
            if (r, c) in ((6, 6), (6, pos[-1]), (pos[-1], 6)):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    put(r + dr, c + dc, max(abs(dr), abs(dc)) != 1)
    put(n - 8, 8, True)                                # the always-dark module
    reserved = [(8, i) for i in range(9)] + [(i, 8) for i in range(9)] + [(8, n - 1 - i) for i in range(8)] + [(n - 1 - i, 8) for i in range(7)]
    if ver >= 7:
        reserved += [(i, n - 11 + j) for i in range(6) for j in range(3)] + [(n - 11 + j, i) for i in range(6) for j in range(3)]
    for r, c in reserved:
        if cells[r][c] is None:
            cells[r][c] = False
    func = [[cells[r][c] is not None for c in range(n)] for r in range(n)]
    bits = []
    for b in _codewords(text, ver, level):
        bits += [(b >> k) & 1 for k in range(7, -1, -1)]
    idx, up = 0, True
    for c in range(n - 1, 0, -2):
        if c <= 6:
            c -= 1
        rows = range(n - 1, -1, -1) if up else range(n)
        for r in rows:
            for cc in (c, c - 1):
                if cells[r][cc] is None:
                    cells[r][cc] = bool(bits[idx]) if idx < len(bits) else False
                    idx += 1
        up = not up
    best = None
    for m in range(8):
        f = _mask_fn(m)
        g = [[(cells[r][c] != (f(r, c) and not func[r][c])) if not func[r][c] else cells[r][c] for c in range(n)] for r in range(n)]
        fmt = ((_FMT_BITS[level] << 3 | m) << 10 | _bch(_FMT_BITS[level] << 3 | m, 0x537, 10)) ^ 0x5412
        for i in range(15):
            bit = bool(fmt >> i & 1)
            g[i if i < 6 else i + 1 if i < 8 else n - 15 + i][8] = bit                      # down the left of the top-left finder, then the bottom-left copy
            g[8][n - 1 - i if i < 8 else 7 if i == 8 else 14 - i] = bit                    # along the top-right copy, then beside the top-left finder
        if ver >= 7:
            vi = ver << 12 | _bch(ver, 0x1F25, 12)
            for i in range(18):
                bit = bool(vi >> i & 1)
                g[i // 3][n - 11 + i % 3] = bit
                g[n - 11 + i % 3][i // 3] = bit
        p = _penalty(g)
        if best is None or p < best[0]:
            best = (p, g)
    return best[1]
