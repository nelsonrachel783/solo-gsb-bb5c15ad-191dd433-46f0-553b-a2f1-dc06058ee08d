"""对齐内核加载器：优先使用预编译共享库，缺失时即时编译，最终回退纯 Python。"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

from .align_py import align_chars as _align_python

_NATIVE_DIR = Path(__file__).resolve().parent / "native"
_SRC = _NATIVE_DIR / "align_c.c"
_LIB = _NATIVE_DIR / "libalign.so"

_lib = None
_backend = "python"


def _try_load(path: Path) -> ctypes.CDLL | None:
    if not path.exists():
        return None
    try:
        lib = ctypes.CDLL(str(path))
    except OSError:
        return None
    lib.align_chars.argtypes = [
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_int,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_int,
        ctypes.POINTER(ctypes.c_int32),
    ]
    lib.align_chars.restype = ctypes.c_int
    return lib


def _compile() -> Path | None:
    """在镜像构建阶段或本地开发时由 gcc 预编译；此处兜底再试一次。"""
    cc = os.environ.get("CC", "gcc")
    try:
        subprocess.run(
            [cc, "-O3", "-fPIC", "-shared", "-o", str(_LIB), str(_SRC)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return _LIB


def _init() -> None:
    global _lib, _backend
    if os.environ.get("ALIGN_NATIVE", "1") == "0":
        _backend = "python"
        return
    lib = _try_load(_LIB)
    if lib is None and os.environ.get("ALIGN_COMPILE", "1") == "1":
        lib = _try_load(_compile()) if _compile() else None
    if lib is not None:
        _lib = lib
        _backend = "native"
    else:
        _backend = "python"


_init()


def backend_name() -> str:
    return _backend


def align_chars(source: str, target: str) -> list[int]:
    """全局对齐。返回 target 每个字符对应的 source 下标，-1 表示新增。"""
    if _lib is None:
        return _align_python(source, target)

    m, n = len(source), len(target)
    SArr = ctypes.c_uint32 * max(m, 1)
    TArr = ctypes.c_uint32 * max(n, 1)
    s_arr = SArr(*(ord(ch) for ch in source)) if m else SArr()
    t_arr = TArr(*(ord(ch) for ch in target)) if n else TArr()
    out = (ctypes.c_int32 * max(n, 1))()

    rc = _lib.align_chars(s_arr, m, t_arr, n, out)
    if rc == -1:
        raise MemoryError("对齐内核内存不足")
    if rc != 0:
        raise ValueError("对齐内核参数非法")
    return [out[j] for j in range(n)]
