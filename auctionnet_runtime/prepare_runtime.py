"""Clear the obsolete executable-stack flag in the private PyTorch 1.12 library.

Equivalent to patchelf --clear-execstack for this one local ELF file. Recent
systems reject its executable-stack request before torch can be imported.
"""

import hashlib
import json
from pathlib import Path
import struct


def main():
    root = Path(__file__).resolve().parent
    target = root / ".venv/lib/python3.9/site-packages/torch/lib/libtorch_cpu.so"
    before_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    with target.open("r+b") as handle:
        header = handle.read(64)
        if header[:6] != b"\x7fELF\x02\x01":
            raise RuntimeError("Expected a little-endian ELF64 library")
        offset = struct.unpack_from("<Q", header, 32)[0]
        size, count = struct.unpack_from("<HH", header, 54)
        for index in range(count):
            position = offset + index * size
            handle.seek(position)
            kind, flags = struct.unpack("<II", handle.read(8))
            if kind == 0x6474E551:  # PT_GNU_STACK
                if flags & 1:  # PF_X
                    handle.seek(position + 4)
                    handle.write(struct.pack("<I", flags & ~1))
                break
        else:
            raise RuntimeError("No GNU_STACK program header found")
    report = {
        "library": str(target.relative_to(root)),
        "operation": "clear GNU_STACK executable flag in private environment",
        "before_sha256": before_hash,
        "after_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "gnu_stack_flags_before": flags,
        "gnu_stack_flags_after": flags & ~1,
    }
    output = root / "runtime-compatibility.json"
    if not output.exists() or flags & 1:
        output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
