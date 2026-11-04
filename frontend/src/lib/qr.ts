/**
 * A small QR code encoder (byte mode, error correction level M, versions 1 to 10: up to 213 bytes), so the page can show
 * "scan to call from your phone" without a package or a request to anybody. Follows ISO/IEC 18004; the structure is the
 * usual one (function patterns, Reed-Solomon over GF(256), eight masks scored by the four penalty rules).
 */

// [data codewords per block group 1 (count, size), group 2 (count, size), ecc codewords per block]
const BLOCKS: Record<number, [number, number, number, number, number]> = {
  1: [1, 16, 0, 0, 10], 2: [1, 28, 0, 0, 16], 3: [1, 44, 0, 0, 26], 4: [2, 32, 0, 0, 18], 5: [2, 43, 0, 0, 24],
  6: [4, 27, 0, 0, 16], 7: [4, 31, 0, 0, 18], 8: [2, 38, 2, 39, 22], 9: [3, 36, 2, 37, 22], 10: [4, 43, 1, 44, 26],
};
const ALIGN: Record<number, number[]> = {
  1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
};

function dataCapacity(v: number): number {
  const [c1, s1, c2, s2] = BLOCKS[v];
  return c1 * s1 + c2 * s2;
}

function gfMul(x: number, y: number): number {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z;
}

function rsDivisor(degree: number): number[] {
  const result = new Array<number>(degree).fill(0);
  result[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) {
      result[j] = gfMul(result[j], root);
      if (j + 1 < degree) result[j] ^= result[j + 1];
    }
    root = gfMul(root, 0x02);
  }
  return result;
}

function rsRemainder(data: number[], divisor: number[]): number[] {
  const result = new Array<number>(divisor.length).fill(0);
  for (const b of data) {
    const factor = b ^ (result.shift() as number);
    result.push(0);
    divisor.forEach((coef, i) => {
      result[i] ^= gfMul(coef, factor);
    });
  }
  return result;
}

const bit = (n: number, i: number) => ((n >>> i) & 1) !== 0;

/** The modules of a QR code for `text` (true = dark), `size` x `size`, or throws when the text is longer than 213 bytes. */
export function qrModules(text: string, forceMask?: number): boolean[][] {
  const bytes = Array.from(unescape(encodeURIComponent(text)), (c) => c.charCodeAt(0)); // UTF-8
  let version = 0;
  for (let v = 1; v <= 10; v++) {
    const bits = 4 + (v <= 9 ? 8 : 16) + 8 * bytes.length;
    if (bits <= dataCapacity(v) * 8) {
      version = v;
      break;
    }
  }
  if (!version) throw new Error("Text too long for the QR code");
  const size = 17 + 4 * version;

  // data bits
  const bits: number[] = [];
  const put = (value: number, len: number) => {
    for (let i = len - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  put(0b0100, 4);
  put(bytes.length, version <= 9 ? 8 : 16);
  bytes.forEach((b) => put(b, 8));
  const capBits = dataCapacity(version) * 8;
  put(0, Math.min(4, capBits - bits.length));
  put(0, (8 - (bits.length % 8)) % 8);
  for (let pad = 0xec; bits.length < capBits; pad ^= 0xec ^ 0x11) put(pad, 8);
  const data: number[] = [];
  for (let i = 0; i < bits.length; i += 8) data.push(parseInt(bits.slice(i, i + 8).join(""), 2));

  // blocks, error correction, interleave
  const [c1, s1, c2, s2, ecc] = BLOCKS[version];
  const divisor = rsDivisor(ecc);
  const blocks: { data: number[]; ecc: number[] }[] = [];
  let at = 0;
  for (let i = 0; i < c1 + c2; i++) {
    const n = i < c1 ? s1 : s2;
    const d = data.slice(at, at + n);
    at += n;
    blocks.push({ data: d, ecc: rsRemainder(d, divisor) });
  }
  const words: number[] = [];
  for (let i = 0; i < Math.max(s1, s2); i++) blocks.forEach((b) => i < b.data.length && words.push(b.data[i]));
  for (let i = 0; i < ecc; i++) blocks.forEach((b) => words.push(b.ecc[i]));

  // the matrix
  const modules = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const fn = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const set = (x: number, y: number, dark: boolean) => {
    modules[y][x] = dark;
    fn[y][x] = true;
  };
  for (let i = 0; i < size; i++) {
    set(6, i, i % 2 === 0);
    set(i, 6, i % 2 === 0);
  }
  const finder = (cx: number, cy: number) => {
    for (let dy = -4; dy <= 4; dy++)
      for (let dx = -4; dx <= 4; dx++) {
        const dist = Math.max(Math.abs(dx), Math.abs(dy));
        const x = cx + dx;
        const y = cy + dy;
        if (x >= 0 && x < size && y >= 0 && y < size) set(x, y, dist !== 2 && dist !== 4);
      }
  };
  finder(3, 3);
  finder(size - 4, 3);
  finder(3, size - 4);
  const pos = ALIGN[version];
  pos.forEach((cy, i) =>
    pos.forEach((cx, j) => {
      if ((i === 0 && j === 0) || (i === 0 && j === pos.length - 1) || (i === pos.length - 1 && j === 0)) return;
      for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) set(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    })
  );
  const drawFormat = (mask: number) => {
    const d = (0 << 3) | mask; // level M is 00
    let rem = d;
    for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    const f = ((d << 10) | rem) ^ 0x5412;
    for (let i = 0; i <= 5; i++) set(8, i, bit(f, i));
    set(8, 7, bit(f, 6));
    set(8, 8, bit(f, 7));
    set(7, 8, bit(f, 8));
    for (let i = 9; i < 15; i++) set(14 - i, 8, bit(f, i));
    for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(f, i));
    for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(f, i));
    set(8, size - 8, true);
  };
  drawFormat(0);
  if (version >= 7) {
    let rem = version;
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
    const v = (version << 12) | rem;
    for (let i = 0; i < 18; i++) {
      const a = size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      set(a, b, bit(v, i));
      set(b, a, bit(v, i));
    }
  }
  // codewords, zigzag from the bottom right
  let k = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++)
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const y = ((right + 1) & 2) === 0 ? size - 1 - vert : vert;
        if (!fn[y][x] && k < words.length * 8) {
          modules[y][x] = bit(words[k >>> 3], 7 - (k & 7));
          k++;
        }
      }
  }

  const apply = (mask: number) => {
    for (let y = 0; y < size; y++)
      for (let x = 0; x < size; x++) {
        const inv =
          mask === 0 ? (x + y) % 2 === 0 : mask === 1 ? y % 2 === 0 : mask === 2 ? x % 3 === 0 : mask === 3 ? (x + y) % 3 === 0
          : mask === 4 ? (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0 : mask === 5 ? ((x * y) % 2) + ((x * y) % 3) === 0
          : mask === 6 ? (((x * y) % 2) + ((x * y) % 3)) % 2 === 0 : (((x + y) % 2) + ((x * y) % 3)) % 2 === 0;
        if (!fn[y][x] && inv) modules[y][x] = !modules[y][x];
      }
  };
  const penalty = (): number => {
    let p = 0;
    const lines = (get: (a: number, b: number) => boolean) => {
      for (let a = 0; a < size; a++) {
        let run = 1;
        for (let b = 1; b < size; b++) {
          if (get(a, b) === get(a, b - 1)) run++;
          else {
            if (run >= 5) p += 3 + (run - 5);
            run = 1;
          }
        }
        if (run >= 5) p += 3 + (run - 5);
        for (let b = 0; b + 10 < size; b++) {
          const w = Array.from({ length: 11 }, (_, i) => get(a, b + i));
          const pat1 = [true, false, true, true, true, false, true, false, false, false, false];
          const pat2 = [false, false, false, false, true, false, true, true, true, false, true];
          if (w.every((v, i) => v === pat1[i])) p += 40;
          if (w.every((v, i) => v === pat2[i])) p += 40;
        }
      }
    };
    lines((r, c) => modules[r][c]);
    lines((c, r) => modules[r][c]);
    for (let y = 0; y < size - 1; y++)
      for (let x = 0; x < size - 1; x++) {
        const c = modules[y][x];
        if (c === modules[y][x + 1] && c === modules[y + 1][x] && c === modules[y + 1][x + 1]) p += 3;
      }
    const dark = modules.reduce((n, row) => n + row.filter(Boolean).length, 0);
    p += (Math.ceil(Math.abs(dark * 20 - size * size * 10) / (size * size)) - 1) * 10;
    return p;
  };
  let best = forceMask ?? 0;
  if (forceMask === undefined) {
    let bestScore = Infinity;
    for (let m = 0; m < 8; m++) {
      apply(m);
      drawFormat(m);
      const score = penalty();
      if (score < bestScore) {
        best = m;
        bestScore = score;
      }
      apply(m); // undo
    }
  }
  apply(best);
  drawFormat(best);
  return modules;
}
