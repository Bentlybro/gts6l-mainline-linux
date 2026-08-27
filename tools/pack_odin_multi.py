#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, tarfile
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--output',type=Path,required=True)
p.add_argument('members',nargs='+',help='member=path')
a=p.parse_args()

tar_name=a.output.name[:-4] if a.output.name.endswith('.md5') else a.output.name+'.tar'
tar_path=a.output.with_name(tar_name)
tar_path.parent.mkdir(parents=True,exist_ok=True)
if tar_path.exists(): tar_path.unlink()
with tarfile.open(tar_path,'w',format=tarfile.USTAR_FORMAT) as out:
    for spec in a.members:
        member, raw = spec.split('=',1)
        if '/' in member or '\\' in member: raise SystemExit('unsafe member')
        src=Path(raw)
        info=tarfile.TarInfo(member); info.size=src.stat().st_size; info.mode=0o644; info.mtime=0
        with src.open('rb') as f: out.addfile(info,f)
digest=hashlib.md5(tar_path.read_bytes()).hexdigest()
with tar_path.open('ab') as f: f.write(f'{digest}  {tar_path.name}\n'.encode())
tar_path.replace(a.output)
print(a.output, a.output.stat().st_size, digest)
