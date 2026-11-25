"""Run by tests/test_dispatch_headers.py against the module built from dispatch_module.cpp; prints one JSON object."""
import json
import sys
import warnings

import numpy as np

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    import dispatch_module as d

out = {"import_warnings": [f"{w.category.__name__}: {w.message}" for w in caught]}


def outcome(f):
    try:
        return f()
    except BaseException as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {str(exc).splitlines()[0]}"


p, u8, f32 = d.Pic(), np.zeros((2, 3), np.uint8), np.zeros((2, 3), np.float32)
out["same_object"] = [d.add_inplace(p, 1) is p, d.add_inplace(u8, 1) is u8, d.add_inplace(f32, 1) is f32]
out["returning_lambda_same_object"] = d.add2_inplace(f32, 1) is f32
out["values"] = [p.v, int(u8[0, 0]), float(f32[0, 0])]

out["enum_name"] = outcome(lambda: d.add_inplace(p, 0, mode="EACH") is p)
out["enum_member"] = outcome(lambda: d.add_inplace(p, 0, mode=d.Mode.EACH) is p)
out["enum_bad_name"] = outcome(lambda: d.add_inplace(p, 0, mode="bad"))
out["enum_wrong_type"] = outcome(lambda: d.add_inplace(p, 0, mode=3).__class__.__name__)

ro = np.zeros((2, 3), np.uint8)
ro.flags.writeable = False
out["refused"] = {
    "float64": outcome(lambda: d.add_inplace(np.zeros((2, 3), np.float64), 1)),
    "int32": outcome(lambda: d.add_inplace(np.zeros((2, 3), np.int32), 1)),
    "read_only": outcome(lambda: d.add_inplace(ro, 1)),
    "non_contiguous": outcome(lambda: d.add_inplace(np.zeros((2, 6), np.uint8)[:, ::2], 1)),
    "list": outcome(lambda: d.add_inplace([1, 2, 3], 1)),
}

b = np.full((2, 3), 5, np.float32)
a = np.ones((2, 3), np.float32)
out["peer_ok"] = [d.mix_inplace(a, b) is a, float(a[0, 0])]
out["peer_refused"] = {
    "uint8_with_float32": outcome(lambda: d.mix_inplace(np.zeros((2, 3), np.uint8), b)),
    "pic_with_array": outcome(lambda: d.mix_inplace(d.Pic(), b)),
}
out["chain"] = d.Pic().add_inplace(1).add2_inplace(2).v
out["bad_inplace_pic"] = outcome(lambda: d.bad_inplace(d.Pic()))
out["bad_inplace_array"] = outcome(lambda: d.bad_inplace(np.zeros((2, 3), np.float32)) is not None)

src8 = np.arange(6, dtype=np.uint8).reshape(2, 3)
srcf = np.arange(6, dtype=np.float32).reshape(2, 3)
r8, rf = d.scaled(src8, 2, "each"), d.scaled(srcf, 2)
ro8 = np.arange(6, dtype=np.uint8).reshape(2, 3)
ro8.flags.writeable = False
pic = d.Pic()
pic2 = pic.scaled(2)
out["new"] = {
    "u8": [r8.dtype.name, list(r8.shape), r8.ravel().tolist(), r8 is src8, bool(np.shares_memory(r8, src8))],
    "f32": [rf.dtype.name, list(rf.shape), rf.ravel().tolist(), rf is srcf, bool(np.shares_memory(rf, srcf))],
    "input_unchanged": [src8.ravel().tolist(), srcf.ravel().tolist()],
    "read_only_input": d.scaled(ro8, 1).ravel().tolist(),
    "pic": [pic2.v, pic2 is pic, pic.v, type(pic2).__name__],
    "chain": d.Pic().scaled(2).scaled(3).v,
    "shape_of_the_argument": [
        list(d.scaled(src8.reshape(2, 3, 1), 1).shape),
        list(d.scaled(src8, 1).shape),
        list(d.bytes(srcf.reshape(2, 3, 1)).shape),
        list(d.blended(src8.reshape(2, 3, 1), src8.reshape(2, 3, 1)).shape),
    ],
    "refused": {
        "float64": outcome(lambda: d.scaled(np.zeros((2, 3), np.float64), 1)),
        "int32": outcome(lambda: d.scaled(np.zeros((2, 3), np.int32), 1)),
        "non_contiguous": outcome(lambda: d.scaled(np.zeros((2, 6), np.uint8)[:, ::2], 1)),
        "list": outcome(lambda: d.scaled([1, 2, 3], 1)),
    },
    "enum": [outcome(lambda: d.scaled(src8, 0, mode="EACH").ravel().tolist()), outcome(lambda: d.scaled(src8, 0, mode="bad"))],
    "other_kind": [d.bytes(srcf).dtype.name, d.bytes(srcf).ravel().tolist(), d.bytes(src8).dtype.name, d.bytes(d.Pic()).v],
    "blended": [d.blended(srcf, srcf).ravel().tolist(), d.blended(src8, src8).dtype.name, d.blended(d.Pic(), d.Pic()).v],
    "blended_refused": [
        outcome(lambda: d.blended(src8, srcf)),
        outcome(lambda: d.blended(d.Pic(), srcf)),
    ],
}
# a number as the result: an int for bytes and a Pic, a double for floats
out["new"]["numbers"] = [d.total(src8), type(d.total(src8)).__name__, d.total(srcf), type(d.total(srcf)).__name__, d.total(d.Pic()),
                         type(d.total(d.Pic())).__name__]
# more channels than the argument: always (H, W, 3); the same for (H, W, 1)
out["new"]["more_channels"] = [list(d.tripled(src8).shape), list(d.tripled(src8.reshape(2, 3, 1)).shape), d.tripled(src8)[1, 2].tolist()]
# the memory of a result is owned by the array: it is still right after the garbage collector ran
import gc  # noqa: E402

keep = [d.scaled(srcf, 3) for _ in range(50)]
gc.collect()
out["new"]["owned_memory"] = all(k.ravel().tolist() == [0.0, 3.0, 6.0, 9.0, 12.0, 15.0] for k in keep)
json.dump(out, sys.stdout)
