# DEVLOG — mainline Linux on the Tab S6 (`gts6lwifi`)

The story of this port: what we tried, what failed, what hurt, and what we learned.
The authoritative, unabridged log (with device‑private details) lives in the
working tree; this is the shareable narrative. Newest sections at the bottom.

---

## Day zero — identify the target

An early assumption that the Tab S6 might be Exynos (like our S20 project) was
wrong. The SM‑T860 is **Qualcomm SM8150 / Snapdragon 855** (`ro.board.platform =
msmnile`), Android 12 / One UI 4.1, stock kernel `4.14.190`, build
`T860XXS5DWH1`. The S20's *engineering discipline* transfers; its Exynos code does
not. Standard Qualcomm‑Samsung partition layout, `boot` on `sda20` (64 MiB).

## Unlock, root, and a ground‑truth backup

Bootloader unlocked the SM‑T860 way (Vol‑Up + Vol‑Down while plugging in USB →
Device Unlock Mode). Verified `vbmeta.device_state = unlocked`,
`verifiedbootstate = orange`. Rooted via the official Magisk Samsung procedure
(patch the full stock `AP` tar on‑device, flash BL + patched‑AP + CSC). One
device‑specific gotcha: the first Magisk boot required an Android‑Recovery
**Format data** before it would boot — the CSC wipe alone wasn't enough.

Then captured a complete, SHA‑256‑verified backup of every named partition except
`userdata`, plus the raw Qualcomm boot‑chain LUNs and both GPT copies. This backup
is the source of truth for everything that follows. (Kept private; never shared.)

## Dead end #1 — custom recovery images (permanently closed)

The first idea was the least invasive: put a mainline kernel + tiny initramfs in
the **recovery** partition. Eight+ variants (`recovery-linux-v1..v7`,
wrapper/UEFI variants) all did the same thing: Samsung splash with Download/Odin
text overlaid, never reaching initramfs or USB. Along the way we learned real
things:

- Samsung's stock kernel is wrapped in Qualcomm's `UNCOMPRESSED_IMG` envelope; a
  raw mainline `Image` is rejected. (Fixed the envelope — still failed.)
- Recovery DTBO page size is 4096 on this device, not 2048.
- A custom initramfs whose PID 1 exits makes the kernel reboot; bind the shell to
  `/dev/console` and keep PID 1 alive.
- Samsung recovery needs a specific USB peripheral‑role setup for the gadget.

None of it mattered, because **recovery was the wrong slot**. After confirming the
same failure with the Aloha UEFI handoff placed in recovery, recovery experiments
were **permanently closed**. Lesson: change one variable at a time, and don't keep
polishing a route whose premise is wrong.

## Dead end #2 — stock‑kernel kexec (closed)

Idea: boot stock, `kexec` into mainline. This required building the Samsung 4.14
vendor kernel, which needs Samsung's **x86‑only** Clang/DTC/build scripts. On the
ARM64 build host this failed ~20 different ways (Python2 wrappers, hardcoded
`-Werror`, vendor `cpu_soft_restart` signature mismatch, dangerous relocations,
tightly‑coupled fscrypt/RKP/techpack/USB subsystems, hidden non‑Kconfig
assumptions). QEMU‑emulating the x86 toolchain didn't work either. A full day of
compiling produced no `Image`. Closed: this host is not the Samsung vendor‑kernel
build host.

## Dead end #3 — direct ABL boot of a mainline image (closed)

Flashed mainline kernel + no‑op DTBO to the normal `boot` partition. It wrote
byte‑for‑byte, but Android booted stock `4.14.190` with `ro.boot.boot_recovery=1`
— ABL rejected the payload and fell back. Adding `text_offset=0x80000` and even a
`flags=3` (verification‑disabled) vbmeta didn't change it. Direct‑mainline‑ABL
permutations retired. (This later turns out to be a clue, not just a failure.)

## The right route appears — Project Aloha SM8150 UEFI

Research converged on **Project Aloha** (`mu_aloha_platforms`,
`DualBootKernelPatcher`): a real, device‑named `samsung-gts6lwifi` SM8150 UEFI
target. The intended chain is:

```text
stock Samsung boot kernel → DualBootKernelPatcher injects a UEFI firmware volume +
shellcode → UEFI LinuxLoader → Linux
```

We built the patcher on the ARM64 host, downloaded the Aloha CI UEFI image for
`gts6lwifi`, and produced a patched‑stock‑kernel boot image. Flashed it to `boot`
as `boot-uefi-tab-v0`. Result: **Android booted stock 4.14.190, `boot_recovery=1`
again.** The UEFI handoff didn't run.

## The breakthrough — the repack was destroying the Samsung trailer

Instead of spinning another variant, we did a byte‑level AVB/trailer analysis
(`tools/avb_analyze.py`, `avb_desc.py`). The findings:

- The stock `boot` image ends with `SEANDROIDENFORCE` + an AVB `AVB0` vbmeta + an
  `AVBf` footer, all self‑consistent. Samsung ABL parses this trailer to accept a
  boot image.
- **`boot-uefi-tab-v0` had none of it** — the hand‑written fixed‑size packer had
  stripped the entire trailer. `v1` copied the stock footer verbatim so its
  `vbmeta_offset` pointed into the middle of the enlarged kernel — invalid.
- So ABL was rejecting the image at parse time, **before the Aloha shellcode ever
  ran**. The fallback was never about the kernel or the shellcode; it was the
  broken container.

Reading the Aloha docs confirmed the canonical method uses **`magiskboot repack`**
— the one tool that preserves the Samsung `SEANDROIDENFORCE`/AVB trailer (and which
had already produced a working Magisk boot on this exact tablet). We also ruled out
two red herrings: the SM8250 vs SM8150 config (byte‑identical) and the hand‑written
unconditional shellcode (a legitimate simplification of Aloha's key‑selector
shellcode). And we learned why the raw Aloha `NOSB.img` can't be flashed directly:
it's a gzip kernel in an AOSP boot image **v0** with no Samsung trailer, built for
devices with real fastboot.

Rebuilt the image the documented way:

```text
magiskboot unpack stock-boot.img
DualBootKernelPatcher ./kernel SM8150_EFI_NOSB.fd ./patched_kernel DualBoot.Sm8150.cfg ShellCode.TabS6UEFI.bin
cp patched_kernel kernel ; magiskboot repack stock-boot.img new-boot-uefi-tab-v2.img
```

Offline verification of `new-boot-uefi-tab-v2.img`: exact 64 MiB, `ANDROID!` v1,
`UNCOMPRESSED_IMG` envelope intact, UEFI `_FVH` embedded, `SEANDROIDENFORCE` +
`AVB0` restored, and an `AVBf` footer at end‑64 whose `vbmeta_offset` correctly
points at its own vbmeta. The first boot candidate that is a **structurally valid
Samsung boot image AND carries the Aloha UEFI handoff.**

Packaged it with a `flags=3` vbmeta (so the vbmeta partition's `boot` hash
descriptor can't reject the modified kernel) into a single Odin AP tar. This is the
decisive test: it removes both remaining variables — trailer validity and AVB —
at once. **Not yet flashed.** See [`BOOT_METHOD.md`](BOOT_METHOD.md) for the exact
build and the flash/read‑back procedure, and how to interpret the two possible
outcomes.

## Where we are

Layer 1 (boot handoff) has a real, evidence‑backed candidate awaiting a device
test. Layers 2 (mainline kernel + `gts6lwifi` DTB via UEFI LinuxLoader) and 3
(Fedora + KDE Plasma) follow once ABL is crossed.
