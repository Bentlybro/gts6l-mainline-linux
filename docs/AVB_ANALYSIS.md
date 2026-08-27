# Tab S6 boot image: Android header, Qualcomm envelope, and AVB trailer

Byte‑level breakdown of the stock `boot` partition and why modified images were
rejected by Samsung ABL. Reproduce with `tools/avb_analyze.py <image>` and
`tools/avb_desc.py <image>`.

## Stock `boot` layout (exact 64 MiB = 67,108,864 B)

```text
0x0000000  Android boot header, version 1, page 4096, name "SRPSD11A005"
           kernel_size 50,836,849   ramdisk_size 0   kernel_addr 0x8000
           tags_addr 0x1e00000
0x0001000  kernel: Qualcomm 'UNCOMPRESSED_IMG' envelope
             +0x00  "UNCOMPRESSED_IMG"        (16 bytes)
             +0x10  little-endian raw size    (48,842,768)
             +0x14  raw ARM64 Image           (magic 'ARMd' at inner +0x38)
             followed by the appended vendor DTB (1,994,061 B)
0x307d000  "SEANDROIDENFORCE"                 Samsung enforcement marker
0x307e000  AVB0 vbmeta blob (2112 B): algo SHA256_RSA2048, flags 0,
             HASH descriptor partition="boot", image_size 50,844,176, sha256 digest
0x3ffffc0  AVBf footer (last 64 B): orig_image_size 50,843,664,
             vbmeta_offset 0x307e000, vbmeta_size 2112   ← points at the AVB0 above
```

The structure is self‑referential and complete: footer → vbmeta → boot hash
descriptor. Samsung ABL parses this trailer as part of accepting the image.

## The vbmeta partition

The top‑level `vbmeta` carries HASH descriptors for `abl`/`boot`/`hyp`/`tz`/`xbl`,
CHAIN descriptors for `recovery`/`dtbo`/`product`, and HASHTREE for
`system`/`vendor`. The `boot` HASH descriptor's `image_size` (50,844,176) and
sha256 digest are what a verifying bootloader checks the boot partition against.

- **Stock vbmeta: `flags = 0`** → verification enabled. A modified boot fails the
  digest check.
- **Magisk/patched vbmeta: `flags = 3`** → DISABLE_VERIFICATION | DISABLE_HASHTREE
  → the boot digest is not checked. This is the only mechanism that lets a modified
  boot pass AVB; a self‑signed footer does **not** help (our key isn't Samsung's).

## Why the earlier candidates were rejected

| Image | Trailer | Verdict |
|---|---|---|
| stock `boot.img` | SEANDROID + AVB0 + AVBf, self‑consistent | accepted (baseline) |
| `boot-uefi-tab-v0` | **none** — SEANDROID/AVB0/AVBf all stripped | rejected → fallback |
| `boot-uefi-tab-v1` | AVBf present but stock footer copied verbatim; `vbmeta_offset` lands inside the enlarged kernel | invalid → fallback |
| `new-boot-uefi-tab-v2` | SEANDROID + AVB0 + AVBf via `magiskboot repack`, footer self‑consistent, 64 MiB | structurally valid ✓ |

The `boot_recovery=1` fallback happens at ABL parse/verify time — **before** the
kernel or the Aloha shellcode runs. That is why a broken trailer, not the kernel
payload, was the blocker, and why the fix is a correct repack + a `flags = 3`
vbmeta rather than another kernel variant.

## Key numbers

```text
boot partition size      67,108,864 B (64 MiB)
page size                4096
stock kernel_size        50,836,849 (raw Image 48,842,768 + appended DTB 1,994,061)
UEFI FD (SM8150_EFI_NOSB) ~3 MiB (0x300000), matches DualBoot.Sm8150 StackSize
UEFI load address        0x9FC00000 (StackBase)
```
