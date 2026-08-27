# Booting past Samsung ABL on the Tab S6 (SM8150) — the Project Aloha UEFI handoff

This is the hard part of the whole project, so it gets its own document.

## Why Samsung SM8150 is different

On a *reference* Qualcomm SM8150 device (Xiaomi Mi 9 `cepheus`, OnePlus 7T
`hotdog`), you can `fastboot boot` or `fastboot flash boot` an arbitrary Android
boot image and ABL runs it. Samsung is not that:

- **No fastboot.** Samsung ABL only has Download Mode (Odin), no `fastboot boot`.
- **No `lk2nd` target.** `lk2nd` (the usual "secondary bootloader for locked
  Qualcomm" trick) has no SM8150 port. Porting it is a whole bootloader project.
- **A strict boot‑image contract.** Samsung ABL expects an Android boot image
  **v1** whose kernel is wrapped in Qualcomm's `UNCOMPRESSED_IMG` envelope, and
  which carries a Samsung `SEANDROIDENFORCE` marker plus an **AVB** footer/vbmeta
  trailer. Hand a Samsung ABL a boot image missing that trailer and it rejects it
  and falls back to the recovery/stock path (`ro.boot.boot_recovery=1`, stock
  4.14.190) — *before your kernel ever executes*.

That last point is what cost the earlier attempts weeks. See
[`AVB_ANALYSIS.md`](AVB_ANALYSIS.md) for the byte‑level proof.

## The working route: patch the stock kernel, don't replace it

Project Aloha (`mu_aloha_platforms`, a port of `mu_andromeda_platforms`) builds an
**SM8150 UEFI firmware volume** (`SM8150_EFI_NOSB.fd`) and a **LinuxLoader** boot
app. The Samsung‑safe way to launch it is the **DualBootKernelPatcher**:

```text
stock Samsung boot kernel  (keeps Samsung's v1 header + UNCOMPRESSED_IMG + AVB trailer)
  └─ DualBootKernelPatcher injects the UEFI FD after the kernel and rewrites the
     ARM64 entry to a small shellcode
       └─ shellcode copies the UEFI FD to a fixed DRAM address and jumps to it
            └─ UEFI → DefaultBDSBootApp = LinuxLoader → Linux kernel + DTB
```

Because the **stock** Samsung container is preserved, Samsung ABL still accepts
the image. Do **not** try to flash Aloha's raw `samsung-gts6lwifi_NOSB.img`: it is
a gzip kernel in an AOSP boot image **v0** with no Samsung trailer — fine for
Mi 9/OnePlus fastboot, rejected by Samsung ABL.

## The shellcode

Aloha ships per‑SoC shellcodes that are really **dual‑boot key selectors**
(`ShellCode.Cepheus.S`, `ShellCode.Andromeda.S`, …): they read a device‑specific
GPIO (a volume key), and if it is held jump to UEFI, otherwise fall through to the
stock kernel. The shared tail (`CommonTail.S`) does the real work:

```asm
_UEFI:
    adr x4, _KernelHead      // start of the loaded kernel
    ldr x5, _KernelSize      // patcher stored the kernel size here
    add x4, x4, x5           // x4 = end of kernel = where the FD was appended
    ldr x5, _StackBase       // 0x9FC00000  (from DualBoot.Sm8150.cfg)
    ldr x6, _StackSize       // 0x00300000  (= FD size, 3 MiB)
    bl  _CopyLoop            // copy the FD to StackBase
    br  x5                   // jump into UEFI
```

For a first bring‑up we want UEFI **unconditionally**, so we use an
`b _UEFI`‑only shellcode (`ShellCode.TabS6UEFI.S`) that shares the same tail. Once
UEFI boots we can switch to the key‑selector form to keep Android dual‑bootable.

> Open risk: `_StackBase = 0x9FC00000` is the standard SM8150 UEFI load address
> used on other devices. If it is not free on the Samsung memory map at ABL
> hand‑off, the jump faults. This is the next thing to check if a
> structurally‑valid image still doesn't reach UEFI.

## The reproducible build (the fix)

The mistake in earlier attempts was repacking with a hand‑written fixed‑size
packer that **stripped the Samsung `SEANDROIDENFORCE`/AVB trailer**. Use
`magiskboot` instead — it records the trailer on unpack and restores it on repack.
Magisk patching a Samsung boot image is a routine, proven operation (it already
worked on this exact tablet for root).

```bash
# host: aarch64 Linux with magiskboot and the Aloha DualBootKernelPatcher built
MB=./magiskboot
PAT=DualBootKernelPatcher/output/DualBootKernelPatcher
CFG=DualBootKernelPatcher/Config/DualBoot.Sm8150.cfg   # StackBase 0x9FC00000 / StackSize 0x300000
SC=ShellCode.TabS6UEFI.bin                             # unconditional b _UEFI (or ShellCode.Cepheus.bin)
FD=SM8150_EFI_NOSB.fd                                  # Aloha SM8150 UEFI firmware volume

$MB unpack stock-boot.img          # -> kernel (UNCOMPRESSED_IMG kept), kernel_dtb, records SEANDROID+VBMETA
# patcher arg order is INPUT FD OUTPUT CFG SHELLCODE  (it patches arg1, writes arg3)
$PAT ./kernel $FD ./patched_kernel $CFG $SC
cp patched_kernel kernel
$MB repack stock-boot.img new-boot.img   # restores the Samsung trailer, pads to 64 MiB
```

### Verify offline before flashing (must all hold)

```text
size            = 67,108,864 (exact 64 MiB boot partition)
ANDROID! v1, page 4096
UNCOMPRESSED_IMG at 0x1000              (Samsung kernel envelope intact)
_FVH present                            (UEFI firmware volume embedded)
SEANDROIDENFORCE present near kernel end
AVB0 vbmeta present
AVBf footer at end-64, and its vbmeta_offset points at the real AVB0  (self-consistent)
```

`tools/avb_analyze.py` prints all of these.

## Flashing (Odin)

The vbmeta *partition* still contains a `boot` hash descriptor that will not match
the modified kernel, so flash a **verification‑disabled** vbmeta in the same
session (`flags = 3` = disable‑verification + disable‑hashtree):

```bash
python tools/pack_odin_multi.py --output boot-uefi.tar.md5 \
    boot.img=new-boot.img  vbmeta.img=vbmeta-flags3.img
```

Odin routes members by filename. Flash the tar as **AP**. Keep BL/CP empty; never
touch other partitions. Then read back:

```bash
adb shell getprop ro.boot.boot_recovery      # 1 = still fell back to stock/recovery
adb shell uname -a                            # 4.14.190 = stock kernel ran, not UEFI
```

### Interpreting the result

- **Different splash / LinuxLoader / new USB device** → past ABL, UEFI is running.
  Move to layer 2 (feed LinuxLoader a mainline kernel + `gts6lwifi` DTB).
- **Still stock 4.14.190 + `boot_recovery=1`** → a valid trailer *and* disabled
  AVB were both satisfied, so ABL is refusing for another reason. Investigate the
  shellcode UEFI load address (`0x9FC00000`) against the Samsung SM8150 memory map
  and whether orange‑state ABL enforces a signature we can't satisfy. Do **not**
  spin another blind image variant.

## Hard rules

- Recovery partition experiments are permanently closed — they never reached UEFI.
- Never flash BL/XBL/ABL/TZ/modem/EFS/PIT or repartition.
- Never flash until the offline structure check above passes.
- Keep a full stock Odin restore available for rollback.
