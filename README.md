# Mainline Linux on the Samsung Galaxy Tab S6 Wi‑Fi (Snapdragon 855, `gts6lwifi`)

An in‑progress effort to boot **real mainline Linux — Fedora aarch64 with KDE
Plasma — natively** on the Samsung Galaxy Tab S6 Wi‑Fi (SM‑T860, codename
`gts6lwifi`, Qualcomm **SM8150** / Snapdragon 855). Not an Android chroot, not a
container, not postmarketOS‑as‑a‑dependency — a hand‑integrated mainline kernel
with a real desktop userspace.

This repository is the **shareable, reproducible subset** of that project: the
boot method, the device tree and kernel work, the packaging/analysis tooling, and
the full technical write‑ups. It exists because Samsung's SM8150 tablets have no
turn‑key mainline path, and the boot‑chain problem in particular (getting past
Samsung's locked‑down ABL) burned a lot of time that nobody should have to repeat.

> **Sister project:** the [Galaxy S20 Ultra (`z3s`, Exynos 990)](../z3s-mainline-linux)
> reached a full GPU‑accelerated KDE Plasma desktop. The *methodology* transfers
> (stock DT first, USB/serial lifeline, one subsystem per boot, hash before every
> flash); the *hardware does not* — the S20 is Exynos with an `lk3rd`/uniLoader
> chain, this tablet is Qualcomm with a completely different boot story.

## No proprietary data

No Samsung/Qualcomm firmware, no partition dumps, no `efs`/`sec_efs`/IMEI/serial,
no stock images are in this repo. See [`firmware/README.md`](firmware/README.md)
for how to extract what you need **from your own device**. The `.gitignore` is
deliberately aggressive about this.

## Current status — honest

This is a **boot‑bring‑up in progress**, not a working desktop yet. The project is
three layers; we are still on layer 1.

| Layer | Goal | Status |
|---|---|---|
| 1. Boot handoff | Samsung ABL → Project Aloha SM8150 UEFI | **✅ SOLVED** — UEFI (edk2/Project Mu) boots and reaches its USB/fastboot screen on real SM‑T860 hardware |
| 2. Kernel | mainline SM8150 `Image` + `gts6lwifi` DTB booted by UEFI | next — provide an EFI‑bootable payload (ESP/USB) with kernel + DTB + initramfs |
| 3. Userspace | Fedora aarch64 + KDE Plasma on internal UFS | design only |

**Layer 1 is cracked.** The chain `patched stock boot kernel → DualBootKernelPatcher
shellcode → SM8150 UEFI FD @0x9FC00000 → edk2` executes on the SM‑T860. The two
things that made it finally work: (a) `magiskboot repack` to preserve the Samsung
`SEANDROIDENFORCE`/AVB trailer, and (b) understanding the boot routing (the BCB and
Magisk‑in‑recovery). See [`docs/DEVLOG.md`](docs/DEVLOG.md).

### The boot problem, in one paragraph

Samsung's ABL will not directly execute a mainline `Image`, and Samsung SM8150 has
no `lk2nd` target. The community path is **Project Aloha** (`mu_aloha_platforms` /
`DualBootKernelPatcher`): inject an SM8150 UEFI firmware volume into the **stock**
Samsung boot kernel so ABL boots the stock container, which then chain‑loads UEFI
→ `LinuxLoader` → Linux. Every earlier attempt fell back to stock Android because
the boot image was repacked with a hand‑written packer that **destroyed the
Samsung `SEANDROIDENFORCE`/AVB trailer** ABL requires. The fix is to use
`magiskboot repack` (which preserves that trailer) and to pair the flash with a
verification‑disabled `vbmeta`. See [`docs/BOOT_METHOD.md`](docs/BOOT_METHOD.md)
and [`docs/DEVLOG.md`](docs/DEVLOG.md) for the full investigation.

## Device facts

```text
Model            SM-T860 (Wi-Fi), codename gts6lwifi
SoC              Qualcomm SM8150P v2 / Snapdragon 855
Active msm-id    0x169 / 0x20000     board-id 0x10008 / 0x00000002
Stock firmware   T860XXS5DWH1 (Android 12, One UI 4.1), kernel 4.14.190
Bootloader       unlocked, orange verified-boot state
Boot chain       AArch64 XBL -> 32-bit ARM ABL -> boot (Android boot image v1)
Display          ANA38401 / AMSA05RB06 WQXGA
Boot partition   /dev/block/sda20, 64 MiB
```

## Repo layout

```text
docs/         Write-ups: DEVLOG (the full story), the boot method, the hardware
              map/port status, and the AVB/boot-image format analysis.
bootloader/   Project Aloha handoff: the build recipe, the packer/analysis tools,
              and notes. Aloha UEFI/patcher are upstream; device firmware is not.
kernel/
  dts/        sm8150-samsung-gts6lwifi board device tree (mainline-style).
  config/     Lean launch kernel .config.
tools/        Boot-image AVB/trailer analyzers and the Odin tar packers.
firmware/     How to extract SM8150/Samsung firmware from your own device. No blobs.
rootfs/       Fedora + KDE Plasma userspace glue (planned).
```

## Where to start

- [`docs/DEVLOG.md`](docs/DEVLOG.md) — chronological story: what was tried, what
  failed, what hurt, what we learned.
- [`docs/BOOT_METHOD.md`](docs/BOOT_METHOD.md) — the Project Aloha SM8150 handoff,
  why Samsung ABL is special, and the exact reproducible build.
- [`docs/AVB_ANALYSIS.md`](docs/AVB_ANALYSIS.md) — byte‑level Android boot header,
  `UNCOMPRESSED_IMG` envelope, and AVB footer/vbmeta breakdown.
- [`docs/PORT.md`](docs/PORT.md) — hardware map and per‑subsystem status.

## Safety

Only ever write the `boot` and `vbmeta` partitions (and later a rootfs partition
you choose). **Never** flash BL, XBL, ABL, TZ, modem, EFS, PIT, or repartition —
Samsung KG/Knox and a wrong firmware write can hard‑brick. Keep a full stock Odin
restore on hand. Never publish `efs`, `sec_efs`, or IMEI/serial data.

## License

Kernel/DTS/driver work is derived from Linux and is GPL‑2.0. Documentation is
shared under the same repo license. Proprietary firmware is not included and is
not covered.
