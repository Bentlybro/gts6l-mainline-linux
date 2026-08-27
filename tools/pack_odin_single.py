#!/usr/bin/env python3
"""Create a Samsung Odin tar.md5 containing exactly one image member."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tarfile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--member", required=True, help="partition member name, e.g. recovery.img")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not args.image.is_file():
        raise SystemExit(f"missing image: {args.image}")
    if "/" in args.member or "\\" in args.member or args.member in {"", ".", ".."}:
        raise SystemExit("member must be a single safe filename")

    tar_name = args.output.name[:-4] if args.output.name.endswith(".md5") else args.output.name + ".tar"
    tar_path = args.output.with_name(tar_name)
    tar_path.parent.mkdir(parents=True, exist_ok=True)
    if tar_path.exists():
        tar_path.unlink()

    with tarfile.open(tar_path, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        info = tarfile.TarInfo(args.member)
        info.size = args.image.stat().st_size
        info.mode = 0o644
        info.mtime = 0
        with args.image.open("rb") as source:
            archive.addfile(info, source)

    digest = hashlib.md5(tar_path.read_bytes()).hexdigest()
    footer = f"{digest}  {tar_path.name}\n".encode("ascii")
    with tar_path.open("ab") as output:
        output.write(footer)

    tar_path.replace(args.output)
    print(f"output: {args.output}")
    print(f"bytes: {args.output.stat().st_size}")
    print(f"member: {args.member} ({args.image.stat().st_size} bytes)")
    print(f"tar-md5: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
