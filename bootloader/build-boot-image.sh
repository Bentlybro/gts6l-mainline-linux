#!/usr/bin/env bash
# Build a Project Aloha UEFI-handoff boot image for the Samsung Tab S6 (gts6lwifi)
# by patching the STOCK kernel and repacking with magiskboot (preserves the
# Samsung SEANDROIDENFORCE/AVB trailer that Samsung ABL requires).
#
# Usage: build-boot-image.sh <stock-boot.img> <SM8150_EFI_NOSB.fd> <shellcode.bin> <out.img>
#
# Env overrides:
#   MAGISKBOOT   path to magiskboot (default: magiskboot on PATH)
#   PATCHER      path to DualBootKernelPatcher binary
#   CFG          DualBoot config (default: DualBoot.Sm8150.cfg beside PATCHER)
set -euo pipefail

STOCK="${1:?stock-boot.img}"; FD="${2:?SM8150_EFI_NOSB.fd}"
SC="${3:?shellcode.bin}";     OUT="${4:?out.img}"

MAGISKBOOT="${MAGISKBOOT:-magiskboot}"
PATCHER="${PATCHER:?set PATCHER to the DualBootKernelPatcher binary}"
CFG="${CFG:-$(dirname "$PATCHER")/DualBoot.Sm8150.cfg}"

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
cp "$STOCK" "$work/stock-boot.img"
cd "$work"

echo "== unpack stock (records SEANDROIDENFORCE + AVB trailer) =="
"$MAGISKBOOT" unpack stock-boot.img

echo "== patch stock kernel: inject UEFI FD + shellcode =="
# NOTE: patcher arg order is  INPUT  FD  OUTPUT  CFG  SHELLCODE  (it patches arg1 -> arg3)
"$PATCHER" ./kernel "$FD" ./patched_kernel "$CFG" "$SC"
cp patched_kernel kernel

echo "== repack (restores the Samsung trailer, pads to 64 MiB) =="
"$MAGISKBOOT" repack stock-boot.img out.img
cd - >/dev/null
cp "$work/out.img" "$OUT"
echo "== wrote $OUT =="
sha256sum "$OUT" 2>/dev/null || shasum -a 256 "$OUT"
echo "Now verify with tools/avb_analyze.py: expect UNCOMPRESSED_IMG + _FVH +"
echo "SEANDROIDENFORCE + AVB0 + a self-consistent AVBf footer at end-64, size 64 MiB."
