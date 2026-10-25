import json
import zlib
from functools import lru_cache
from pathlib import Path

import numpy as np

D = 1024


@lru_cache(None)
def hv(tok):
    return np.random.default_rng(zlib.crc32(tok.encode())).choice(np.float32([-1, 1]), D)


def grams(w):
    w = f"<{w}>"
    return [w[i:i + 3] for i in range(len(w) - 2)]


def enc(text):
    v = np.zeros(D, np.float32)
    for w in text.lower().split():
        v += 2 * hv(w)
        for g in grams(w):
            v += hv("#" + g)
    return v / (np.linalg.norm(v) or 1)


def H(p):
    p = p[p > 1e-12]
    return float(-(p * np.log(p)).sum())


def kl(p, q):
    return float((p * np.log((p + 1e-9) / (q + 1e-9))).sum())


def dist(c, names):
    v = np.array([c.get(n, 0.0) for n in names]) + 1e-9
    return v / v.sum()


def spread(ps, w=None):
    psi = np.sqrt(ps)
    w = np.ones(len(ps)) if w is None else np.asarray(w, float)
    return H(np.linalg.eigvalsh((psi.T * (w / w.sum())) @ psi))


class Brain:
    def __init__(s, options, n=2048, sharp=3, path=None):
        s.opt, s.sharp, s.path, s.rng = list(options), sharp, path, np.random.default_rng()
        s.X, s.m = np.zeros((n, D), np.float32), np.zeros(n, np.int64)
        s.r, s.w, s.i = np.zeros(n, np.float32), np.zeros(n, np.float32), 0
        if path and Path(path).exists():
            z = np.load(path)
            k = len(z["m"])
            s.X[:k], s.m[:k], s.r[:k], s.w[:k], s.i = z["X"], z["m"], z["r"], z["w"], int(z["i"])

    def ab(s, x):
        k = np.clip(s.X @ x, 0, None) ** s.sharp * s.w
        hot = np.eye(len(s.opt), dtype=np.float32)[s.m]
        return 1 + (k * s.r) @ hot, 1 + (k * (1 - s.r)) @ hot

    def mean(s, x):
        a, b = s.ab(x)
        return a / (a + b)

    def lcb(s, x, z=2.0):
        a, b = s.ab(x)
        return a / (a + b) - z * np.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))

    def pick(s, x):
        return s.opt[int(np.argmax(s.rng.beta(*s.ab(x))))]

    def update(s, x, option, reward, weight=1.0):
        j = s.i % len(s.r)
        s.X[j], s.m[j], s.r[j], s.w[j] = x, s.opt.index(option), reward, weight
        s.i += 1
        if s.path and s.i % 16 == 0:
            s.save()

    def save(s):
        Path(s.path).parent.mkdir(parents=True, exist_ok=True)
        k = min(s.i, len(s.r))
        np.savez(s.path, X=s.X[:k], m=s.m[:k], r=s.r[:k], w=s.w[:k], i=s.i)


class Memo:
    def __init__(s, n=256, path=None):
        s.path, s.vals, s.i, s.X = path, [None] * n, 0, np.zeros((n, D), np.float32)
        if path and Path(path).exists():
            z = np.load(path)
            k = len(z["X"])
            s.X[:k], s.vals[:k], s.i = z["X"], json.loads(str(z["vals"])), int(z["i"])

    def get(s, x, tau):
        j = int(np.argmax(s.X @ x))
        return s.vals[j] if s.X[j] @ x >= tau else None

    def put(s, x, v):
        j = s.i % len(s.vals)
        s.X[j], s.vals[j] = x, v
        s.i += 1
        if s.path:
            Path(s.path).parent.mkdir(parents=True, exist_ok=True)
            k = min(s.i, len(s.vals))
            np.savez(s.path, X=s.X[:k], vals=json.dumps(s.vals[:k]), i=s.i)
