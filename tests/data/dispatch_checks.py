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
json.dump(out, sys.stdout)
