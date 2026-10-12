"""Public synthetic BioLogic cycling baselines; no laboratory recordings."""

import struct

NAMES = ["mode", "error", "time/s", "Ewe/V", "Ecell/V", "I/mA", "cycle number", "Ns"]
ROWS = [
    [1, 0, 0, 1.7, 1.5, 10, 0, 0],
    [1, 0, 3600, 1.7, 1.5, 10, 0, 0],
    [2, 0, 3600, 1.7, 1.5, 5, 0, 1],
    [2, 0, 7200, 1.7, 1.5, 5, 0, 1],
    [3, 0, 7200, 1.65, 1.45, 0.1, 0, 2],
    [3, 0, 7300, 1.6, 1.4, 0.1, 0, 2],
    [1, 0, 7300, 1.4, 1.2, -8, 0, 3],
    [1, 0, 10900, 1.4, 1.2, -8, 0, 3],
]


def mpt(path, rows=None, names=None, *, comma=False, encoding="utf-8", magic="EC-Lab ASCII FILE"):
    rows, names = rows or ROWS, names or NAMES
    lines = [
        magic,
        "Nb header lines : 5",
        "Galvanostatic cycling with potential limitation",
        "Synthetic public baseline",
        "\t".join(names),
    ]
    lines += ["\t".join(str(v).replace(".", ",") if comma else str(v) for v in row) for row in rows]
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode(encoding))
    return path


def mpr(path, *, version=2):
    # Exercise galvani itself, including its packed mode/error flags and module decoding.
    from galvani import BioLogic
    import numpy as np

    ids = [1, 3, 4, 6, 27, 8, 24, 131]
    dtype, _ = BioLogic.VMPdata_dtype_from_colIDs(ids)
    data = np.zeros(len(ROWS), dtype=dtype)
    for index, row in enumerate(ROWS):
        mode, error, t, ewe, cell, current, cycle, ns = row
        data[index] = (mode | (error << 3), t, ewe, cell, current, cycle, ns)
    header_size = 406 if version == 3 else 405
    body = struct.pack("<IB", len(data), len(ids)) + struct.pack("<" + "H" * len(ids), *ids)
    body = body.ljust(header_size, b"\0") + data.tobytes()

    def module(name, content, module_version):
        header = np.zeros(1, BioLogic.VMPmodule_hdr_v1)
        header[0] = (name, b"Synthetic cycling baseline", len(content), module_version, b"10/07/26")
        return b"MODULE" + header.tobytes() + content

    path.write_bytes(
        BioLogic.MPR_MAGIC + module(b"VMP Set   ", b"", 0) + module(b"VMP data  ", body, version)
    )
    return path


# Independent synthetic charge intervals, not derived from laboratory records.
INTERVAL_ROWS = [
    # mode, error, Ns, t/s, Ewe/V, dq/mAh, half-cycle
    [3, 0, 0, 0, 1.5, 0, 0],
    [1, 0, 1, 360, 1.6, 1, 0],
    [2, 0, 2, 1080, 1.7, 2, 0],
    [1, 0, 3, 1260, 1.4, -0.5, 1],
    [1, 0, 3, 1800, 1.3, -1.5, 1],
    [3, 0, 4, 1810, 1.3, 0, 1],
]


def mpr_intervals(path, *, version=2, rows=None, charge_id=7, measured_current=False):
    from galvani import BioLogic
    import numpy as np

    rows = INTERVAL_ROWS if rows is None else rows
    ids = [1, 3, 131, 4, 6, charge_id, 468] + ([8] if measured_current else [])
    dtype, _ = BioLogic.VMPdata_dtype_from_colIDs(ids)
    data = np.zeros(len(rows), dtype=dtype)
    for index, (mode, error, ns, t, v, dq, half) in enumerate(rows):
        values = [mode | (error << 3), ns, t, v, dq, half]
        if measured_current:
            values.append(100 if half == 0 else -100)
        data[index] = tuple(values)
    body = struct.pack("<IB", len(data), len(ids)) + struct.pack("<" + "H" * len(ids), *ids)
    body = body.ljust(406 if version == 3 else 405, b"\0") + data.tobytes()

    def module(name, content, module_version):
        header = np.zeros(1, BioLogic.VMPmodule_hdr_v1)
        header[0] = (name, b"Synthetic charge intervals", len(content), module_version, b"10/11/26")
        return b"MODULE" + header.tobytes() + content

    path.write_bytes(
        BioLogic.MPR_MAGIC + module(b"VMP Set   ", b"", 0) + module(b"VMP data  ", body, version)
    )
    return path
