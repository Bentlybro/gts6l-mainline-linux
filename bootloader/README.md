# bootloader/ — Project Aloha SM8150 UEFI handoff

The Tab S6 boots Linux by chain‑loading Project Aloha's SM8150 UEFI from a patched
**stock** boot kernel. The full explanation is in
[`../docs/BOOT_METHOD.md`](../docs/BOOT_METHOD.md); this directory is the practical
recipe and tooling.

## Inputs (bring your own — none are shipped)

- `stock-boot.img` — your stock `boot` partition (see `../firmware/README.md`)
- `SM8150_EFI_NOSB.fd` — Aloha SM8150 UEFI firmware volume for `samsung-gts6lwifi`
- `DualBootKernelPatcher` — built from
  [Project‑Aloha/DualBootKernelPatcher](https://github.com/Project-Aloha/DualBootKernelPatcher)
- `magiskboot` — from any recent Magisk (aarch64 build runs on a Linux ARM64 host)
- `ShellCode.TabS6UEFI.bin` — unconditional `b _UEFI` shellcode (or an official
  SM8150 key‑selector like `ShellCode.Cepheus.bin`)
- `vbmeta-flags3.img` — a verification‑disabled vbmeta (Magisk‑patched, or
  `avbtool make_vbmeta_image --flags 3`)

## Build

`build-boot-image.sh` runs the documented unpack → patch → repack and prints the
offline structure check. Then pack the Odin tar:

```bash
./build-boot-image.sh stock-boot.img SM8150_EFI_NOSB.fd ShellCode.TabS6UEFI.bin new-boot.img
python ../tools/avb_analyze.py new-boot.img          # confirm the trailer is valid
python ../tools/pack_odin_multi.py --output boot-uefi.tar.md5 \
       boot.img=new-boot.img vbmeta.img=vbmeta-flags3.img
```

Flash `boot-uefi.tar.md5` as **AP** in Odin. Keep BL/CP empty. Never touch other
partitions. Read back `getprop ro.boot.boot_recovery` and `uname -a`.

## The shellcodes are dual‑boot key selectors

Aloha's per‑SoC shellcodes read a device‑specific GPIO (a volume key) and jump to
UEFI only if it is held, otherwise they fall through to the stock kernel. For first
bring‑up we use the unconditional variant so UEFI is always attempted. Once UEFI is
confirmed working, switch to a key‑selector to keep Android dual‑bootable (the
correct GPIO for `gts6lwifi`'s volume key still needs to be identified from the
stock DT pinctrl).
