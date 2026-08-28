# DEVLOG - mainline Linux on the Galaxy Tab S6 (`gts6lwifi`)

This is the whole story of putting real mainline Linux - Fedora 44 with a full KDE
Plasma desktop - onto a Samsung Galaxy Tab S6 Wi-Fi (SM-T860, codename `gts6lwifi`,
Qualcomm SM8150 / Snapdragon 855). It is written to be read start to finish: every
major thing we tried, the wins and the losses, the red herrings we chased, and the
handful of insights that actually moved the project forward.

The authoritative, unabridged log - with device-private details - lives in the
working tree. This is the shareable narrative. It contains no firmware blobs, no
partition dumps, and no device identity. Per-subsystem deep dives are linked from
each chapter.

---

## Day zero: knowing what you are actually holding

The project began with a wrong assumption. We had just finished a mainline port on a
Galaxy S20 Ultra, an Exynos device, and half-expected the Tab S6 to be more of the
same. It is not. A few read-only Android properties settled it immediately: the
SM-T860 is a Qualcomm SM8150 / Snapdragon 855 tablet (`ro.board.platform = msmnile`),
running Android 12 / One UI 4.1 on a stock 4.14.190 kernel. The S20's engineering
*discipline* transfers - read the device's own device tree first, keep a USB lifeline,
one subsystem per boot, hash before every flash. Its Exynos *code* does not. This is a
Qualcomm tablet with a completely different boot story, and everything below is that
story.

The partition layout is standard Qualcomm-Samsung, with `boot` on a 64 MiB partition
and the usual Qualcomm boot-chain LUNs. We recorded the by-name map and moved on.

## Unlock, root, and a ground-truth backup

Unlocking the bootloader on this device is not the `adb reboot download` most guides
imply - that jumps straight to Odin flashing mode and skips the unlock decision. The
correct dance is Volume-Up plus Volume-Down held while plugging in USB, which reaches
Device Unlock Mode. After that: OEM unlocking enabled, `vbmeta.device_state = unlocked`,
`verifiedbootstate = orange`.

Rooting followed the official Magisk Samsung procedure - patch the full stock `AP` tar
*on the device*, then flash the original bootloader plus the patched AP plus CSC. A
first detour into hand-building a boot-only Magisk tar failed (Odin rejected our MD5
footer, then choked on a stray PAX header, then finally wrote an image that dropped the
tablet into "Can't load Android system"); we retired that artifact and did it the
documented way. One device-specific gotcha worth flagging: the first Magisk boot
required an Android-Recovery **Format data** before it would come up. The CSC wipe
alone was not enough on this unit. After that, `uid=0(root)` over adb confirmed
superuser.

With root we captured a complete, SHA-256-verified backup of every named partition
except `userdata`, plus the raw Qualcomm boot-chain LUNs and both GPT copies - about
10 GB of core images. This backup is the source of truth for everything that follows,
and it stays private; none of it is in this repository.

## The dead ends before the breakthrough

Getting a mainline kernel to run at all meant getting *past Samsung's ABL*, and that
took a long time and several completely closed routes. They are worth recording, because
each one taught us something and none of them is worth anyone repeating.

**Custom recovery images (closed).** The least invasive idea was to drop a mainline
kernel plus a tiny initramfs into the recovery partition. Eight-plus variants
(`recovery-linux-v1` through `v7`, plus wrapper and UEFI variants) all did the same
thing: Samsung splash with Download/Odin text overlaid, never reaching the initramfs or
USB. Along the way we did learn real things - Samsung's stock kernel is wrapped in
Qualcomm's `UNCOMPRESSED_IMG` envelope and a raw `Image` is rejected; the recovery DTBO
page size on this device is 4096, not 2048; an initramfs whose PID 1 exits makes the
kernel reboot; Samsung recovery needs a specific USB peripheral-role setup. None of it
mattered, because recovery was simply the wrong slot. We closed it permanently.

**Stock-kernel kexec (closed).** The next idea was to boot stock and `kexec` into
mainline. That requires building Samsung's 4.14 vendor kernel, which needs Samsung's
x86-only Clang, DTC, and build scripts. On our ARM64 build host this failed in roughly
twenty different ways - Python2 wrappers, hardcoded `-Werror`, a `cpu_soft_restart`
signature mismatch, dangerous relocations, and tightly coupled fscrypt/RKP/techpack/USB
subsystems that could not be removed independently. Emulating the x86 toolchain did not
work either. A full day of compiling produced no `Image`. The conclusion was simple:
this is not the Samsung vendor-kernel build host, and kexec is not the route.

**Direct ABL boot of a mainline image (closed).** Flashing a mainline kernel and a
no-op DTBO to the normal `boot` partition wrote byte-for-byte, but Android booted stock
4.14.190 with `ro.boot.boot_recovery=1`. Adding a `text_offset` fix and even a
verification-disabled `vbmeta` changed nothing. We retired the direct-mainline-ABL
permutations. This one, it turned out much later, was a clue rather than just a
failure.

## The right route appears: Project Aloha SM8150 UEFI

Research converged on **Project Aloha** (`mu_aloha_platforms`, `DualBootKernelPatcher`),
which is a real, device-named `samsung-gts6lwifi` SM8150 UEFI target built on Microsoft's
edk2 / Project Mu. The intended chain is: the stock Samsung boot kernel gets a UEFI
firmware volume injected into it, so ABL boots Samsung's own container, which chain-loads
UEFI, whose boot manager then loads Linux. We built the patcher, downloaded the Aloha CI
UEFI image for `gts6lwifi`, produced a patched-stock-kernel boot image, and flashed it.

Result: Android booted stock 4.14.190, `boot_recovery=1`, again. The UEFI handoff did
not run.

## The AVB trailer discovery, and the packer that was destroying it

Rather than spin another blind variant, we did a byte-level analysis of the boot image
against the stock one (`tools/avb_analyze.py`). The stock `boot` image ends with a
`SEANDROIDENFORCE` marker, an AVB `AVB0` vbmeta block, and an `AVBf` footer, all
self-consistent - and Samsung's ABL parses that trailer to decide whether to accept a
boot image. Our candidate had none of it: the hand-written fixed-size packer had
stripped the entire trailer. A later attempt copied the stock footer verbatim, so its
recorded `vbmeta_offset` pointed into the middle of the now-larger kernel - structurally
invalid. Either way the container was broken.

The documented Aloha method does not build a boot image by hand at all. It uses
**`magiskboot repack`** - the one tool that preserves the Samsung `SEANDROIDENFORCE`/AVB
trailer, and the exact tool that had already produced a working Magisk boot on this
tablet during rooting. We rebuilt the image the documented way:

```text
magiskboot unpack stock-boot.img
DualBootKernelPatcher ./kernel SM8150_EFI_NOSB.fd ./patched_kernel <cfg> <shellcode>
cp patched_kernel kernel ; magiskboot repack stock-boot.img new-boot-uefi.img
```

Offline verification showed the corrected image was byte-structurally a valid Samsung
boot image - `UNCOMPRESSED_IMG` envelope intact, the UEFI firmware volume embedded,
`SEANDROIDENFORCE` and `AVB0` restored, and an `AVBf` footer whose offset correctly
pointed at its own vbmeta. Along the way we cleared two red herrings: the SM8250 vs
SM8150 patcher config (byte-identical) and the hand-written unconditional shellcode (a
legitimate simplification of Aloha's key-selector shellcode). Full detail is in
[`BOOT_METHOD.md`](BOOT_METHOD.md) and [`AVB_ANALYSIS.md`](AVB_ANALYSIS.md).

## The real gate: one 32-byte field in `misc`

The corrected image was flashed. Android booted stock again. Same "fallback" as always.
But this time, with a still-rooted device, we inspected instead of assuming - and found
the true cause of *every* boot-partition failure in the project.

The boot partition readback proved Odin had written our image correctly; it was sitting
in `boot`. The live `vbmeta` genuinely had verification disabled. Our boot shellcode was
unconditional, meaning if ABL had executed our boot partition, UEFI would have run and
full Android could not appear. Full Android was running. Therefore ABL was **not
executing the boot partition at all**.

The smoking gun was the `misc` partition's Bootloader Control Block: its `command` field
held the literal string `boot-recovery`. That is the classic Magisk-in-recovery setup
used on ramdisk-less Samsung devices - stock `boot` has no ramdisk, so Magisk patches
recovery, and the BCB is left pointing at recovery so every power-on boots the Magisk
recovery (which then boots system-as-root, with root). It also meant ABL loaded the
recovery partition on every boot and never looked at `boot`. Our boot images, malformed
and well-formed alike, had never once been executed. The AVB analysis was correct and
worth having, but it had been solving a problem that was not the blocker.

There was a sting in the tail. Clearing the BCB from a rooted shell did change the
behavior for the first time - the tablet finally executed our boot partition - but the
UEFI hung at the splash, and recovering from that revealed that `misc` is **not
Odin-flashable** on this device (it is not in the flashable PIT), so the BCB can only be
toggled from a rooted, running Android. Clearing it without a proven-bootable fallback in
`boot` strands the device. We took the safe path: a full stock restore, and then, after
re-confirming OEM-unlock online (VaultKeeper re-arms and blocks custom flashing after any
full restore until you do), the device came up clean-stock with a *normal* BCB.

With a normal BCB, ABL booted our boot partition. The tablet showed a different logo, then
a UEFI screen with a "plug in USB" recovery prompt, and on the PC the Samsung Android USB
device disappeared and a Microsoft WinUSB device appeared in its place. That is the
Project Mu UEFI recovery mode. **We were past Samsung ABL** - the wall this project had
fought since day one. UEFI had simply found no OS to boot, which was exactly the expected
state, because we had not staged a Linux payload yet.

## Giving UEFI a Linux payload

The first payload attempt used the UEFI's bundled Qualcomm `LinuxLoader` over fastboot.
It acknowledged `fastboot boot` but never handed off - Qualcomm's ABOOT-in-EDK2 quietly
bails on DTB selection and verified boot. The route that worked is the ordinary UEFI one:
the Project Mu boot manager scans FAT partitions for `\EFI\BOOT\BOOTAA64.EFI`, so we gave
it systemd-boot on a small FAT ESP, which loads a mainline EFI-stub `Image`, a
`gts6lwifi` DTB, and an initramfs. UEFI found it and booted the kernel.

Getting a *readable* console took a couple of tries. The kernel had no framebuffer
console enabled at first, so early boots were blind; we turned on simpledrm and pointed a
`simple-framebuffer` DT node at the framebuffer UEFI had already lit (2560x1600, BGRX).
The first attempt used the wrong stride and the text sheared; the real stride is
`2560 * 4`. Fixed, we had a clean kernel log on the tablet's own screen, confirming the
kernel boots the SM8150 - SCM, the SCSI/UFS layer, the PMIC SPMI arbiter, timers, DRM
console - and reaches userspace.

Userspace then failed to start with `Failed to execute /init (error -8)`. That was
`ENOEXEC`, and the cause was our own minimal, tinyconfig-derived kernel config: it had
`CONFIG_BINFMT_ELF` and `CONFIG_BINFMT_SCRIPT` both off, so the kernel could not exec
*any* userspace binary or script. With those enabled, we reached a BusyBox shell on the
panel. First real mainline userspace on the SM-T860.

That shell also showed the road ahead: no block devices (UFS was not probing) and no USB
device controller (so the shell was view-only, no keyboard). Chasing those on the minimal
"7.2.0" fork turned into config whack-a-mole, each rebuild exposing another core option
the tinyconfig had dropped. We abandoned the fork for a real mainline **6.12** tree, which
has full SM8150 support, and rebuilt from the arm64 `defconfig` plus forced-builtin
essentials. That was the right call: `defconfig` gives the complete, tested driver set in
one move instead of hand-enabling each driver and discovering the next gap on the next
boot.

One scare on the way had to be understood rather than worked around. Any kernel that
actually probed the PMICs hit a fault on the very first SPMI register read
(`pmic-spmi ... failed with error -5`), and both the pristine mainline tree and the fork
did it identically - so it was not the kernel. The Aloha/Samsung TrustZone environment
leaves the SPMI arbiter in a state mainline's driver does not handle. UFS does not need
direct-SPMI PMIC access (it runs off RPMh regulators), so we disabled the SPMI PMIC
sub-drivers to silence the noise and kept going. A cluster of similar quirks came from the
same root - GPIO0 reads hang because it is TZ-protected (fixed by reserving it in the DT),
and a serial console with no cable attached blocks every later printk forever, which for a
while made random unrelated devices look like they were hanging. Dropping the serial
console to `console=tty0` alone made the boot fly through.

## Internal UFS storage: the clock that lies about being off

With 6.12 booting to a shell, internal storage still refused to come up. UFS failed at its
very first clock:

```text
ufshcd-qcom 1d84000.ufshc: core_clk prepare enable failed, -16 (EBUSY)
gcc_ufs_phy_axi_clk status stuck at 'off'
```

The clock framework enables a branch clock and then polls its halt-status bit to confirm
it is running. Here the bit never reported "running," so the framework declared the clock
stuck and `ufshcd` aborted. The thing is, we *knew* the clock was running: Aloha's UEFI had
just read our ESP off this exact UFS fabric moments earlier. The clock is on. It is the
halt-status **readback** that returns the wrong value under the Samsung/TrustZone
access-domain firmware - the same family of quirk as the SPMI fault and the GPIO0 hang.

This is precisely what `BRANCH_HALT_SKIP` exists for: it tells the clock framework to trust
the enable and skip polling the lying bit. We set it on the 18 UFS and USB branch clocks in
`gcc-sm8150.c`. On the next boot the UFS linked, queried the device, and read the full
Samsung partition table - a Samsung 128 GB UFS 2.1 part, all LUNs present. The proof that
this was a real fix and not masking a dead clock: if the AXI clock were genuinely off, the
UFS link-up would have timed out at the PHY handshake instead of succeeding. The controller
linked and read the GPT, so the fabric was genuinely alive. Full write-up:
[`UFS.md`](UFS.md).

## Installing Fedora on the internal UFS

With storage alive, we built a real Fedora 44 KDE Plasma desktop rootfs (the desktop spin,
not Cloud or Server) on the aarch64 build host - no emulation needed. We chose ext4 for the
on-device root rather than the image's native btrfs, because ext4 is builtin in our kernel
and mounts with no initramfs; built and installed the matching kernel modules into it; set
up autologin into the Plasma Wayland session; and minimized the image for transfer, with a
first-boot service to grow it back to fill the partition.

The layout avoids repartitioning entirely and never touches a protected partition. The Mu
boot manager scans FAT partitions in GPT order and boots the first `BOOTAA64.EFI` it finds,
so we put the boot ESP on the `cache` partition (which precedes `userdata`) and Fedora's
root on the large `userdata` partition. Android's system/vendor/product partitions were left
in place - and later became a useful parts shop for firmware.

Flashing the multi-gigabyte rootfs had its own lesson. `fastboot flash userdata` tried to
load the entire raw image into RAM before sparsing it and died with `std::bad_alloc`. The
fix was to pre-convert to an Android sparse image so fastboot streams it, and even then this
particular Aloha UEFI stalled on large segments, so it had to be flashed in small (~128 MB)
segments. With that, Fedora wrote to internal UFS, booted the kernel, mounted the root,
reached `graphical.target`... and went black at the desktop.

## The display saga: black screen to a real desktop

This was the longest chapter, and it ended somewhere completely different from where it
started.

The black screen came with a clear cause in the journal: KWin could not drive simpledrm.
Its DRM backend looped forever on "Failed to find a working output layer configuration."
Simpledrm is a dumb firmware-framebuffer shadow - one fixed mode, no gamma - and KWin's
modeset test rejected it. Our reading at the time was that simpledrm was a dead end for a
real desktop and the answer had to be the real Qualcomm display pipe: the msm DRM driver
driving the DSI panel directly.

So we went and built that, and it was genuinely hard. The panel is a dual-DSI ANA38401 /
AMSA05RB06, 2560x1600, and mainline has no driver for it. We wrote one from the device's own
downstream device tree. The saga, compressed:

- The first driver used the wrong ANA38401 variant node from the bundled overlay
  (1440x2560, single-DSI, with DSC). It lit the panel, but the image was garbled.
- The real `gts6l` panel is dual-DSI - two 1280x1600 links bonded to 2560x1600 - with no
  DSC and command mode. We rewrote the driver and DT for that. Both links bound, but every
  frame timed out at kickoff.
- The kickoff timeout meant the DPU was waiting for the panel's hardware tearing-effect
  (TE) pulse and never getting it. We wired the TE GPIO and told the panel to emit TE.
  Still timing out.
- Web research on a working Tab S8+ port (same Anapass TCON family) revealed the panel's
  T-CON takes ~330 ms to boot after reset, and our init was firing far too early into a
  still-booting controller - every command silently dropped. We added a TCON-ready
  handshake. Still timing out.
- The same reference port explained the persistent white-bars pattern: the bootloader's
  display is *live*, and unless simpledrm's framebuffer node declares and holds the display
  clocks and the MDSS power domain, the bootloader's scanout gets torn down and the
  framebuffer degenerates into ordinary RAM, rendering as accumulating garbage. We declared
  the clocks. This finally gave a clean console on the panel under the display DTB - a
  first.
- With the pipe held, the true root cause of the frame timeouts surfaced with a clean
  backtrace: the pixel-clock RCGs could not latch, because the bootloader display was
  running off the old UEFI-programmed PHY PLL and msm's PHY reset killed that PLL before the
  new one was enabled. A qcom RCG needs a ticking source to latch its update. The fix was
  `CLK_OPS_PARENT_ENABLE` on the byte and pixel clocks - and, as independent validation,
  upstream added exactly this to the same clocks in a later kernel.
- That fix worked; the pixel clocks latched; KWin came up running on the msm KMS device.
  And the frames still would not complete.

The definitive verdict came from a research pass across the mainline history: **bonded
dual-DSI in command mode was structurally broken in mainline until a fix series landed
between v6.10 and v6.16.** Pre-6.16, the DPU fires only the master CTL and the slave link
never starts, which produces exactly our kickoff/frame-done timeouts on DPU 5.0 (which is
sm8150). No in-tree device even exercises bonded-plus-command mode; every upstream dual-DSI
user is video mode, so nobody had ever hit this bug. We were driving an untested path.

We migrated the whole stack to a 6.18 LTS kernel that has all those fixes. It booted Fedora
cleanly - and msm takeover still ended black.

That is when the user asked the question that cracked the whole thing: the display works
through the entire boot, so why does it break the moment Linux tries to drive it? The
answer reframed everything. The bootloader's display pipe is alive and pushing frames the
whole time; we watch the console scroll on it for a full minute. Every attempt to bring up
the msm pipe *tears down that working pipe* and rebuilds it from scratch, where any one of
dozens of undocumented Samsung parameters differing yields a black screen. Meanwhile
simpledrm rides the untouched pipe flawlessly.

And there was a self-inflicted wound hiding in plain sight. KWin-on-simpledrm had **never
been tested clean.** Before the very first GUI boot we had baked software-rendering
environment hacks into the rootfs - `KWIN_COMPOSE=Q`, `LIBGL_ALWAYS_SOFTWARE=1`,
`KWIN_DRM_NO_AMS=1` - and every later "fix" only added more flags. Stock Plasma 6 runs on
simpledrm routinely; every live Linux ISO on an unsupported GPU does it. Our own env soup
was the prime suspect for the original "no working output layer configuration" failure.

We stripped every one of those environment hacks out of the rootfs and booted the original
pre-display kernel and DTB - `dispcc` disabled, the bootloader pipe never touched,
`clk_ignore_unused` and `pd_ignore_unused` keeping its clocks alive, simpledrm as the KMS
scanout. The KDE Plasma desktop appeared on the panel. Fedora 44 KDE, mainline Linux,
internal UFS root, 2560x1600, no Android anywhere in the chain.

The winning architecture, in one line: **never touch the bootloader display pipe.**
simpledrm rides what the bootloader already set up; stock KWin config drives it fine on
software GL until the GPU arrives. The months of native-panel work were not wasted - the
vendor-exact ANA38401 driver, the dual-DSI wiring, the clock-hold pattern, and the RCG fix
are all retained for the day the native pipe is finished (which is only needed for
brightness and DPMS control). But the daily-driver display is simpledrm. The full account,
including what not to do, is in [`DISPLAY.md`](DISPLAY.md).

## Multitouch: the right driver and the GSI-only trap

A desktop you can only look at is a demo, not a device. Touch was next, and the device's own
downstream DT gave the ground truth: an STM `FTS1BA90A` touchscreen at address 0x49 on the
QUP2 SE17 i2c bus, with a known IRQ and supply rails.

The first instinct - mainline's `stmfts` driver - was wrong, and we caught it before wasting
a flash. Mainline `stmfts` speaks the older Xperia-era FTS protocol (8-byte events); the
FTS1BA90A is a Samsung variant that speaks Samsung's SEC-TS protocol entirely (16-byte
events, different command set, IC-resident firmware with no download). Binding `stmfts` to
it just probes and times out. The correct driver is a mainline-style port of Samsung's
`fts_ts` from the same Tab S8+ project we had leaned on for the display, and the Tab S6
downstream confirmed the identical chip and driver directory.

The harder fight was the bus. The touch i2c controller deferred its probe forever. The cause
was that the QUP2 serial engines run in GPI DMA mode, and `CONFIG_QCOM_GPI_DMA` was a module
that never loaded; building it in got further, but then the DMA channel came back with no
device. The real root cause was two layers down: the boot firmware sets this specific engine
to GSI-only (FIFO mode is impossible on it), and the GPI DMA controller node it needs is
disabled by default in the SoC dtsi. Enabling that DMA controller and restoring the channel
references was the unlock. The touch controller then answered over GPI-mode i2c with its full
identity - chip id, firmware version, panel dimensions, the lot.

Probe still failed on one point: the driver expected a scan-enable echo event that this
firmware generation does not emit. We made that echo non-fatal (the identity read is the real
liveness proof) and touch input went live on the Plasma desktop. After a couple of DTB-only
calibration passes (the digitizer is portrait 1600x2560 behind a landscape 2560x1600 panel,
so the axes need swapping and one inversion), the tablet was interactive. The user then
touched their way through the Plasma welcome wizard and typed the Wi-Fi password on the
on-screen keyboard themselves - which also neatly kept any credential out of the automation.
Details in [`TOUCH.md`](TOUCH.md).

## The USB-networking lifeline

Up to this point every kernel iteration meant a fastboot flash and reading results off a
photo of the screen - the "flash-photo era." Ending it was a project of its own, and it
transformed the pace of everything after.

The USB device controller had a false alarm attached to it. For a long time dwc3 printed
"failed to initialize core," and it appeared in every deferred-probe snapshot. Instrumenting
the init path showed the truth: after some early `EPROBE_DEFER` rounds waiting on the combo
PHY, the *final* attempt succeeds completely. The scary error was a stale early-boot
snapshot, printed before the PHY arrived; dwc3 had been fine for a while and we simply had not
re-tested USB after the PHY config evolved. The lesson - deferred-pending "reasons" are
point-in-time strings, always confirm against the device's final state.

With dwc3 healthy we built a configfs composite gadget: an RNDIS network interface plus an ACM
serial console. Getting Windows to bind it cleanly took the usual dance - a CDC NCM attempt
failed for lack of a host driver, and Windows cached a broken binding until we bumped the
gadget's product id to force a fresh enumeration. To avoid disturbing the PC's networking at
all (no admin rights in the shell), instead of forcing Windows onto a chosen subnet we read
the link-local address Windows had auto-assigned and put the tablet on that same subnet - zero
changes to the PC. We installed an ed25519 public key over the serial side-door, and then:
root SSH into the tablet over the USB cable, with an ACM serial console as the fallback.

That link changed the workflow completely. The cache ESP is loop-mountable from the running
tablet (with a 512-byte sector-size override, because the UFS exposes 4K logical blocks while
the FAT was formatted 512), so from that point on, swapping the kernel and DTB is done over
SSH and a reboot - no fastboot per change. We also masked the suspend and sleep targets early,
because Plasma's idle suspend killed the USB link once and needed a power-button reboot to
recover. The lifeline story is in [`USB_NETWORKING.md`](USB_NETWORKING.md).

## GPU acceleration: Adreno 640 as a render-only device

The desktop was software-rendered on llvmpipe. The plan for acceleration, carried straight
from the S20 project, is a render-only GPU: keep simpledrm as the scanout, bring up the
Adreno 640 as a separate render device, and let Mesa's kmsro pair the two so KWin's Wayland
compositor does the cross-device dance. All of this was done over SSH, with no fastboot and no
hands on the tablet.

The one component we expected to be a wall - the Adreno SMMU under Samsung TrustZone - probed
clean. The zap shader (a small TrustZone-authenticated blob the GPU needs to leave secure
mode) was not available in a form Samsung's TZ would accept from linux-firmware, but Samsung's
own signed version was sitting on the Android firmware partition; we copied it into the
rootfs, and it authenticated silently.

Then four small kernel fixes, each found by reading the source after a specific symptom:

1. **Headless DRM master.** With the display pipe (mdss) disabled, there is no DRM master for
   the GPU-only configuration, and the GPU component waits forever. Mainline only registers a
   headless master device for one compatible string; a two-line gate extension to also do it
   for the GPU's own compatible, plus that compatible on our GPU node, bound the GPU.
2. **Speed-bin fallback.** Every sm8150 GPU OPP is gated by `opp-supported-hw`, and our
   earlier deletion of the speed-bin nvmem cell made the driver find no supported OPPs at all.
   The fix: treat a missing speed-bin as bin 0 (the standard Snapdragon 855 mask, which is
   exactly what a Tab S6 is) and continue, instead of skipping the supported-hw setup.
3. **Forced MMU.** The "using 16m VRAM carveout" path fires because `msm_use_mmu()` checks
   only the master device, and our headless dummy has no iommu - even though the GPU's own
   SMMU demonstrably works. We forced it true for this port.
4. **64-bit DMA mask on simpledrm** - a preemptive fix carried from an S20 swiotlb lesson.

A firmware-format gotcha rounded it out: Fedora ships the Adreno microcode xz-compressed, and
our kernel has no compressed firmware loader, so the files had to be decompressed in place
(and the a640 uses the a630 SQE microcode). With that, and a hard session recycle to get KWin
to actually re-open the render node, the result was measurable rather than eyeballed: KWin
holds `renderD128` with `drm-driver: msm`, the GPU engine counters advance under load, zero
GPU faults, the Samsung-signed zap authenticated silently, and a fresh reboot comes up with the
GPU auto-loaded and KWin on it with no manual steps. The user's summary was more direct than the
counters: the desktop is visibly, obviously smoother. Full bring-up in [`GPU.md`](GPU.md).

## Wi-Fi: the open frontier

Wi-Fi is the hardest problem on this device and the one still open. It is worth telling in full,
because the shape of the wall is unusual and someone with the right piece of reference data may
be able to finish it.

The Tab S6's Wi-Fi is a Qualcomm WCN3990, driven by mainline's `ath10k_snoc`. The first
surprise is architectural: the WCN3990's firmware does not run on the tablet's main CPU. It runs
on the modem - the same Q6/mpss processor that handles the cellular baseband on phones - inside a
protection domain. So before Wi-Fi can do anything, the modem has to boot, and getting a Samsung
modem to boot under mainline Linux is a project in itself.

We got it to boot. The modem loads through mainline's PAS remoteproc path using Samsung's own
signed modem firmware, lifted off the tablet's own firmware partition. The one real fight there
was memory: the 2023 Samsung modem image is 160 MB and wants a region 10 MB larger than every
device tree we could find declared. We read the true span straight out of the firmware's ELF
program headers and extended the reserved region to match; without that, the loader aborts with
"segment outside memory range." The modem also needs its EFS - its little private filesystem,
where per-device radio calibration lives - served to it from userspace by the `rmtfs` daemon.
That fought us too: the fixed memory address mainline uses sits inside a region Samsung's
TrustZone owns and will not hand over, so we made the region dynamically allocated instead. Once
all that was in place the modem booted fully and, crucially, brought up its WLAN protection
domain: the `wlfw` service appeared on the QMI bus, which is the modem announcing that the Wi-Fi
firmware is alive.

Then came the MSA detour, which ate a day and turned out to be a red herring. MSA is the shared
memory window the Wi-Fi firmware uses, and mainline tries to hand ownership of it from the main
CPU to the modem with a secure "assign" call. On this device that call fails with error -22,
every time, because TrustZone already owns the region. We chased it hard - dropping the ownership
tag, adding guard pages, making the region dynamic - and each variant either failed the same way
or, worse, let the modem touch memory it was not granted and hard-locked the whole tablet. The
answer was a single device-tree flag, `qcom,msa-fixed-perm`, which tells the driver to skip the
assign entirely and trust that TrustZone has already set the permissions. That is the correct
pattern for locked-down devices - the same one shipping Chromebooks use - and the entire -22
chase was chasing something that was never meant to be done.

With MSA settled, the driver's QMI handshake ran to completion, and this is the part that still
feels like it should have been the finish line. The tablet reads the real chip id (0x30224) and
the real firmware version string (`WLAN.HL.3.2.0.c3-00910`) straight off the hardware. Every stage
of the negotiation returns success. The Wi-Fi firmware is genuinely running and talking.

And then it dies. About two seconds after the handshake completes - before the firmware signals
it is ready - the WCN3990 firmware crashes while bringing up its own radio. With the generic
calibration data from linux-firmware it is a soft crash the system survives. With the tablet's
real Samsung calibration data - the data that actually matches this hardware - it is an instant,
total lock-up of the entire SoC. That difference is itself the clearest clue we have: a soft crash
is software hitting an assertion, but an instant hardware lock is a bus transaction to something
that never answers - the firmware driving a real radio component that is not in the state it
expects. The failure is isolated to one specific step: the firmware powering up the radio
front-end.

The problem is that we cannot see why. To read a kernel crash you need the last few log lines
before it dies, and this lock-up is so abrupt that nothing reaches disk in time. We tried three
ways to capture it and each hit a wall of its own. Streaming the log over the network cable
(netconsole) needs a rebuilt kernel, and every kernel we rebuild panics at boot for a completely
separate reason (below). Writing the log to a RAM region that survives a reboot (ramoops) does not
work here, because this bootloader retrains and clears DDR on every restart, wiping the region.
Routing the console out the USB serial port only ever captured the login banner, because the
serial gadget transmits asynchronously and the lock-up beats it. Three independent walls between us
and the one piece of information that would end the guessing.

Without the crash reason we did the only thing left: match the tablet's configuration to Samsung's
own, value by value, and rule things out. The power rails, their voltages, the reference clock, the
memory permissions, and the SMMU setup all match either Samsung's device tree or the working
Qualcomm reference board. The one genuine discrepancy we found was the 3.3 V radio rail: Samsung's
config specifies a minimum of 3.104 V but ours was sitting at 3.0 V, under-volted. That looked like
the answer. We pinned it to Samsung's higher value, and for one glorious run the tablet survived a
Wi-Fi bring-up for the first time - but it was a false alarm. `rmtfs` had been misconfigured that
run, so the modem never actually reached radio init. Once `rmtfs` was fixed and the firmware did
reach radio init, it hard-locked exactly as before. The voltage was wrong, but it was not the
cause.

That is where Wi-Fi sits. The entire stack works up to the firmware's radio init. Everything we can
compare against Samsung matches. The firmware crashes powering up the radio, and the crash is
invisible to us. The remaining leads are real but unproven: Samsung's downstream uses an SMMU
stall-bypass mode that mainline translates differently, and a stalled SMMU fault would produce
exactly this kind of hard lock; there is the question of matching the exact firmware feature-flags
file; and there is the question of which of Samsung's several calibration blobs matches this exact
unit. Each one costs a hard-lock and a recovery flash to test. The full forensic account is in
[`WIFI.md`](WIFI.md).

### The kernel-rebuild panic that blocks the crash capture

Running underneath the Wi-Fi problem is a second, separate one we also have not solved, and it is
the thing stopping us from seeing the Wi-Fi crash. The kernel image currently running on the tablet
(the GPU build) boots fine. But every kernel we have rebuilt since then panics at boot - a data
abort at the very start of userspace, right after "Run /sbin/init," at a memory address that
changes from build to build. A changing fault address is the signature of a bad pointer whose value
depends on the kernel's memory layout, not a bug at a fixed spot in the code.

We chased it methodically and ruled out a lot. Research pointed hard at a known linker bug in recent
binutils that mishandles a certain relocation format, so we disabled that format - still panics. We
relinked with the LLVM linker - still panics. We did a full clean rebuild from scratch to rule out
stale objects - still panics. We confirmed the compiler had not changed between the good build and
the bad ones. Same source, same config, same compiler: the one build boots and everything after it
does not, and we have not explained that yet. The next real step is to read the panic's call trace
off the screen, which names the faulting function; without it we are guessing, and guessing here
means bricking the tablet and reflashing each time.

## Current status, honestly

None of the open problems touches the rest of the tablet. What runs today is a daily-drivable,
GPU-accelerated Fedora 44 KDE Plasma desktop on the Tab S6's own internal UFS storage, at
2560x1600, with calibrated multitouch and an on-screen keyboard, reachable over USB by root SSH -
and no Android anywhere in the boot chain. The boot handoff is solved and reproducible; the mainline
kernel brings up the SoC, storage, display scanout, touch, USB, and the GPU; Fedora is installed and
autologs into a Wayland session.

Two subsystems are still open. **Wi-Fi** works all the way up to the firmware's radio
initialization and then crashes there, invisibly, and matching Samsung's configuration value by
value has not moved it. The **native display pipe** (needed only for brightness and DPMS control)
has all its pieces built and a 6.18 kernel with the necessary bonded-command-mode fixes staged, but
the daily driver deliberately rides the bootloader's framebuffer instead. S Pen, Bluetooth, and audio
are not started.

If you know the WCN3990 / ath10k_snoc path - especially the SMMU fault/stall behavior under a
locked-down TrustZone, or how to make a hard SoC bus-lock leave a readable trace on a bootloader
that clears DDR on reset - that is exactly where help would move this forward. The wall is narrow and
well-characterized: the firmware is real, it is running, it reads the real silicon, and it dies in one
specific step that we cannot yet see. [`WIFI.md`](WIFI.md) has everything we know, and
[`PORT.md`](PORT.md) has the full hardware map. Patches and reference data welcome.
