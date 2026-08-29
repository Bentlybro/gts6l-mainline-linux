# Mainline Linux on the Samsung Galaxy Tab S6 Wi‑Fi (Snapdragon 855, `gts6lwifi`)

Booting **real mainline Linux — Fedora 44 aarch64 with a full KDE Plasma desktop —
natively** on the Samsung Galaxy Tab S6 Wi‑Fi (SM‑T860, codename `gts6lwifi`,
Qualcomm **SM8150** / Snapdragon 855). Installed on the internal UFS. Not an Android
chroot, not a container, not postmarketOS‑as‑a‑dependency — a hand‑integrated
mainline kernel driving a real desktop userspace, with **GPU acceleration, working
multitouch, working Wi‑Fi, and a USB‑networking lifeline**.

This repository is the **shareable, reproducible subset** of that project: the boot
method, the device tree and kernel work, per‑subsystem bring‑up guides, packaging
and analysis tooling, and the full technical write‑ups. Samsung's SM8150 tablets
have no turn‑key mainline path, and several of the walls here (the locked ABL boot
handoff, a UFS clock that lies about being off, a display pipe you must **not**
touch, a Wi‑Fi carveout pointed at firmware‑owned memory that hard‑locked the whole
SoC) cost real time — this exists so the next person doesn't repeat them.

> **Sister project:** the Galaxy S20 Ultra (`z3s`, Exynos 990) reached a full
> GPU‑accelerated KDE Plasma desktop first. The *methodology* transfers (stock DT
> first, USB/serial lifeline, one subsystem per boot, hash before every flash,
> simpledrm + render‑only GPU); the *hardware does not* — the S20 is Exynos with an
> `lk3rd`/uniLoader chain, this tablet is Qualcomm with a completely different boot
> story.

## No proprietary data

No Samsung/Qualcomm firmware, no partition dumps, no `efs`/`sec_efs`/IMEI/serial, no
stock images are in this repo. Every firmware blob the port needs is extracted **from
your own device** — see [`firmware/README.md`](firmware/README.md). The `.gitignore`
is deliberately aggressive about this.

## Current status — honest

A **daily‑drivable desktop is up**, and as of 2026‑08‑29 it is on the network over
its own Wi‑Fi. One subsystem remains open: the **native display pipe** (and with it
panel brightness and DPMS), which needs kernel fixes newer than the 6.12 tree in use.
S Pen, Bluetooth and audio have not been started. The project has three layers;
**layers 1 and 3 are done and layer 2 is a working mainline kernel** with most of the
SoC brought up.

| Layer | Goal | Status |
|---|---|---|
| 1. Boot handoff | Samsung ABL → Project Aloha SM8150 UEFI → systemd‑boot | **✅ SOLVED** |
| 2. Kernel | mainline SM8150 `Image` + `gts6lwifi` DTB, EFI‑stub booted | **✅ working** (6.12) |
| 3. Userspace | Fedora 44 aarch64 + KDE Plasma on internal UFS | **✅ running** |

### Per‑subsystem

| Subsystem | Status | Notes |
|---|---|---|
| Boot handoff (Aloha UEFI → systemd‑boot on cache ESP) | ✅ | Volume‑Down = fastboot; iterate by re‑flashing the cache ESP |
| UFS internal storage | ✅ | needed a `BRANCH_HALT_SKIP` fix on 18 UFS/USB clocks (Aloha TZ lies about halt status) |
| Display (KDE Plasma visible) | ✅ | `simpledrm` on the untouched bootloader framebuffer @2560×1600; **do not** enable the DSI/DPU pipe |
| Multitouch | ✅ | STM `fts1ba90a` (Samsung SEC‑TS protocol), GPI‑DMA on QUP2 |
| GPU acceleration | ✅ | Adreno 640 render‑only via `msm`/freedreno, Mesa kmsro pairs it with simpledrm; Samsung‑signed zap shader |
| USB networking + SSH | ✅ | RNDIS+ACM configfs gadget → root SSH over USB (the dev lifeline) |
| Wi‑Fi (WCN3990) | ✅ | 802.11ac, 866.7 MBit/s link rate (VHT‑MCS 9, 80 MHz, 2 streams), auto‑connects at boot; fixed by relocating `wlan_mem` into HLOS‑owned DDR — see [`docs/WIFI.md`](docs/WIFI.md) |
| Native display / brightness / DPMS | 🚧 | dual‑DSI ANA38401 panel; needs the ≥6.16 bonded‑cmd‑mode DPU fixes (6.18 tree staged) |
| Battery level reporting | ✅ | voltage and percentage from `VPH_PWR` on pm8150's ADC, with a low‑battery warning; charge detection is impossible because the SPMI arbiter denies all access to pm8150b — see [`docs/BATTERY.md`](docs/BATTERY.md) |
| S Pen, Bluetooth, audio | ⬜ | not started |

Read [`docs/PORT.md`](docs/PORT.md) for the full hardware map and
[`docs/DEVLOG.md`](docs/DEVLOG.md) for the chronological story (what was tried, what
failed, what hurt, what we learned).

### The transferable methodology

Every wall on this device fell to the same discipline, taken from the S20 port:

1. **Never touch the bootloader display pipe.** simpledrm rides the framebuffer the
   bootloader already set up; the desktop is GPU‑accelerated by a *render‑only* GPU
   node (Adreno) paired to simpledrm via Mesa's kmsro. Enabling the real DSI/DPU
   pipe corrupted scan‑out every time until a ≥6.16 kernel.
2. **Keep a lifeline.** First a fastboot/cache‑ESP flash loop, then a root serial
   console over USB ACM, then root SSH over USB RNDIS. Every risky change is made
   over the lifeline, not by typing on the tablet.
3. **One subsystem per boot, verify the artifact.** Build the `.dtb`/module, grep
   the built artifact for the change *before* shipping it, `md5sum` before every
   flash, and bring up exactly one thing at a time.
4. **Read the device's own extracted device tree first.** The answers (panel
   timings, touch protocol, Wi‑Fi supplies, firmware paths) were in Samsung's own
   downstream DT, not upstream docs.
5. **Check who owns a reserved‑memory region before asking TrustZone to grant a
   device permissions on it.** Addresses inherited from an SoC `.dtsi` can land
   inside a vendor carveout that belongs to the firmware loader rather than to HLOS,
   and HLOS cannot give away memory it does not own. The `qcom_scm_assign_mem()`
   call then returns `-22`, and the tempting workaround — skipping the assignment —
   leaves the peripheral running against memory it has no rights to. The eventual
   failure looks nothing like a memory problem: here it was an instant, silent,
   log‑less SoC fabric lockup on the first firmware write, which pointed suspicion at
   the wrong step for days. Both Wi‑Fi and `rmtfs` on this device hit the identical
   bug; the fix in each case was a single line of device tree.

## The boot problem, in one paragraph

Samsung's ABL will not directly execute a mainline `Image`, and Samsung SM8150 has no
`lk2nd` target. The working path is **Project Aloha** (`mu_aloha_platforms` /
`DualBootKernelPatcher`): an SM8150 edk2/Project‑Mu UEFI firmware volume is injected
into the **stock** Samsung boot kernel, so ABL boots the stock container which
chain‑loads UEFI → its BDS scans FAT partitions for `\EFI\BOOT\BOOTAA64.EFI` →
systemd‑boot → the mainline EFI‑stub `Image` + `gts6lwifi` DTB. Earlier attempts fell
back to stock Android because the boot image was repacked with a packer that
**destroyed the Samsung `SEANDROIDENFORCE`/AVB trailer** ABL requires; the fix is
`magiskboot repack` (which preserves it) paired with a verification‑disabled
`vbmeta`. See [`docs/BOOT_METHOD.md`](docs/BOOT_METHOD.md).

## Device facts

```text
Model            SM-T860 (Wi-Fi), codename gts6lwifi
SoC              Qualcomm SM8150P v2 / Snapdragon 855
Stock firmware   T860XXS5DWH1 (Android 12, One UI 4.1), kernel 4.14.190
Bootloader       unlocked; Project Aloha SM8150 UEFI flashed to `boot` (sda20)
Storage          128 GB UFS; Fedora root on userdata (sda30, ext4)
Boot ESP         cache (sda27, FAT32) — systemd-boot + Image + DTB
Display          dual-DSI ANA38401 / AMSA05RB06 WQXGA, 2560x1600
Touch            STM FTS1BA90A (Samsung SEC-TS), i2c on QUP2 SE17
GPU              Adreno 640
Wi-Fi/BT         Qualcomm WCN3990 (ath10k_snoc / SNOC)
```

## Repo layout

```text
docs/
  DEVLOG.md         The full chronological story.
  PORT.md           Hardware map and per-subsystem status.
  BOOT_METHOD.md    Project Aloha SM8150 handoff, reproducible build.
  AVB_ANALYSIS.md   Byte-level boot-image / AVB trailer analysis.
  UFS.md            The lying-halt-bit clock fix that unlocked internal storage.
  DISPLAY.md        simpledrm-on-bootloader-framebuffer strategy (and what NOT to do).
  GPU.md            Adreno 640 render-only + Mesa kmsro bring-up.
  TOUCH.md          fts1ba90a + GPI-DMA bring-up.
  USB_NETWORKING.md The RNDIS+ACM lifeline (SSH + serial over USB).
  WIFI.md           WCN3990 bring-up: modem boot, ath10k QMI, and the wlan_mem fix.
  DESKTOP.md        Making it usable: on-screen keyboard with real modifier keys,
                    zram, touch text selection, Electron/Wayland, routing.
  BATTERY.md        Reading the battery through the one PMIC the SPMI arbiter
                    lets us touch.
kernel/
  dts/              sm8150-samsung-gts6lwifi board device tree.
  config/           The kernel .config used for the running build.
  patches/          Out-of-tree fixes (clock halt-skip, ath10k, etc.).
tools/              Boot-image AVB/trailer analyzers and Odin tar packers.
firmware/           How to extract SM8150/Samsung firmware from your OWN device. No blobs.
rootfs/             Fedora + KDE Plasma userspace glue.
```

## Safety

Only ever write the `boot`, `vbmeta`, and `cache` partitions (and the `userdata`
rootfs you choose). **Never** flash BL, XBL, ABL, TZ, modem, EFS, PIT, or repartition
— Samsung KG/Knox and a wrong firmware write can hard‑brick. Keep a full stock Odin
restore on hand. Never publish `efs`, `sec_efs`, or IMEI/serial data.

## License

Kernel/DTS/driver work is derived from Linux and is GPL‑2.0. Documentation is shared
under the same repo license. Proprietary firmware is not included and is not covered.
