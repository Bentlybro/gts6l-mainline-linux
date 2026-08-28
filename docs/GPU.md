# Adreno 640 GPU acceleration (gts6lwifi / SM8150)

Target: Samsung Galaxy Tab S6 Wi-Fi, SM-T860 / `gts6lwifi`, Qualcomm SM8150P
(Snapdragon 855), Adreno 640 GPU + GMU.

Goal: hardware-accelerated KDE Plasma (KWin Wayland) on the mainline kernel,
with the `msm` DRM driver rendering on the Adreno 640 while scanout stays on the
firmware framebuffer. This is the same render-only topology the sister S20 port
(`z3s`) used for its Mali G77/panfrost bring-up; only the render driver differs.

This document assumes the display path is already up as a fixed-mode
`simpledrm` framebuffer (`card0`) handed over by ABL. GPU acceleration is added
*after* display and input are stable, per the Phase 4 bring-up order.

## Render-only topology

There is no working mainline DPU/MDSS scanout on this board. Instead of blocking
GPU work on the full Qualcomm display stack, the GPU is brought up as a pure
**render node** that is decoupled from scanout:

```text
scanout   : simpledrm      -> card0                (firmware framebuffer, fixed mode)
render    : msm / a6xx     -> card1 + renderD128   (Adreno 640, no display)
Mesa      : freedreno (render) + kmsro (glue) -> simpledrm (display)
compositor: KWin Wayland composites on renderD128, scans out via card0
```

`simpledrm` owns the physical framebuffer ABL left running and exposes it as the
primary DRM device (`card0`). The `msm` driver is configured as a **display-less
(headless) render device**: it drives the Adreno 640 and GMU only, exposes
`card1` + `renderD128`, and has no CRTC/connector of its own.

Mesa bridges the two with the **kmsro** (KMS render-only) layer: it pairs
`render=freedreno` with `display=simpledrm`, so an application (or KWin) renders
on the Adreno and presents the result through the simpledrm scanout buffer.
KWin's Wayland session opens `renderD128` for GPU composition and hands finished
frames to `card0` for display.

This is exactly the z3s architecture. On z3s the render driver was `panfrost`
(Mali G77); here it is `msm`/`freedreno` (Adreno 640). Everything about the
split — simpledrm scanout, headless render master, kmsro pairing, compositor on
`renderD128` — transfers unchanged.

## DTS changes

Upstream `sm8150.dtsi` already describes the GPU and GMU, but both ship
`status = "disabled"`. The board DTS must enable them and give the GPU the
device's own signed zap firmware.

Enable the two nodes (`adreno-640.1` / `adreno-gmu-640.1` in `sm8150.dtsi`):

```dts
&gmu {
	status = "okay";
};

&gpu {
	status = "okay";

	/* Headless render master trigger — see kernel patch (1). */
	compatible = "qcom,adreno-640.1", "qcom,adreno", "qcom,kgsl-3d0";

	zap-shader {
		memory-region = <&gpu_mem>;
		/* Device's OWN Samsung-signed zap, NOT linux-firmware's. */
		firmware-name = "qcom/sm8150/gts6lwifi/a640_zap.mdt";
	};
};
```

Two things here are non-default and load-bearing:

1. **The `qcom,kgsl-3d0` compatible is added to `&gpu`.** This is what arms the
   headless-master path in the driver (kernel patch 1 below). Without a DPU/mdss
   node to act as the DRM master, the GPU component would otherwise wait forever
   for a master that never appears.

2. **`zap-shader/firmware-name` points at the device's own signed zap**, staged
   under the board firmware directory (see the next section). Do not point it at
   the `qcom/a640_zap.mdt` that `linux-firmware` ships.

Keep the GPU's `memory-region` / reserved GPU carveout from the ported
reserved-memory map; the zap loader and the GPU MMU both depend on it.

## The zap-shader firmware trick

The zap shader is a small TrustZone-signed blob that unlocks the GPU out of
secure mode at boot. On Qualcomm SoCs it is loaded through the PAS
(Peripheral Authentication Service) in TrustZone, which verifies the blob's
signature against the OEM chain fused into the device.

`linux-firmware` ships a **Qualcomm test-signed** `a640_zap`. On a Samsung
production device that blob **fails Samsung's TrustZone PAS authentication** —
TrustZone rejects the signature and the GPU never leaves secure mode, so `msm`
fails to bring up the pipe.

The fix is to use the **device's own Samsung-signed zap**, which is already on
the tablet. It lives on the `apnhlos` (modem/hlos) partition:

```text
partition : sda16  (apnhlos)
path      : /image/a640_zap.mdt
            /image/a640_zap.b00
            /image/a640_zap.b01
            /image/a640_zap.b02
```

Copy the full split-loadable set (the `.mdt` metadata/hash-table plus every
`.bNN` segment) into the board firmware directory:

```sh
install -Dm644 a640_zap.mdt a640_zap.b0? \
    /lib/firmware/qcom/sm8150/gts6lwifi/
```

Because this blob is signed by the same chain TrustZone trusts, PAS
authenticates it **silently** — no auth failure in `dmesg`, GPU leaves secure
mode, and the pipe comes up. This is the single most important non-obvious step;
the driver and DTS are otherwise stock.

> The signed zap is device firmware extracted from the tablet. Keep it out of
> the public source tree, exactly like the private WLAN firmware.

## Kernel patches

Mainline's `msm` driver assumes a normal Qualcomm SoC with a DPU/MDSS scanout
block acting as the DRM master. A headless-DPU (display-less) GPU config on a
simpledrm board hits four separate assumptions. Each needs a fix, and each has a
distinct failure mode if omitted.

### (1) Register a headless DRM master for `qcom,kgsl-3d0`

File: `drivers/gpu/drm/msm/adreno/adreno_device.c`

The GPU is a *component* of a DRM device; it needs a DRM master to bind to. On a
normal board that master is the DPU/mdss node. This board has no such node, so
mainline's `adreno_device_register_headless()` — the dummy-master path — must
fire instead. Mainline only calls it for the `"amd,imageon"` compatible.

Extend the headless-master trigger to **also** fire for `"qcom,kgsl-3d0"`, and
add that compatible to the `&gpu` node (done in the DTS above):

```c
/* Register a headless master for imageon AND kgsl-3d0 boards that have
 * no DPU/mdss node to act as the DRM master. */
if (of_device_is_compatible(np, "amd,imageon") ||
    of_device_is_compatible(np, "qcom,kgsl-3d0"))
	return adreno_device_register_headless();
```

Failure mode without this: the GPU component binds nothing, the DRM master never
appears, and probe hangs indefinitely — the GPU "waits forever."

### (2) Tolerate `-ENOENT` from the speedbin read

File: `drivers/gpu/drm/msm/adreno/a6xx_gpu.c`

`a6xx_set_supported_hw()` reads the fused speed bin via
`adreno_read_speedbin()` to pick which OPPs are usable. On this board that read
returns `-ENOENT` (no `speedbin` nvmem cell wired up), and mainline **returns
early** on that error.

But the sm8150 `gpu_opp_table` gates **every** OPP with `opp-supported-hw`.
Returning early leaves the supported-hw mask unset, so **zero** OPPs qualify,
and `adreno_gpu_init()` later fails hard with:

```text
no supported OPPs
```

Fix: on `-ENOENT` specifically, set `speedbin = 0` and continue instead of
bailing. Bin 0 is the standard SM8150 (855) part, which enables the full OPP
table up to the 585 MHz top bin:

```c
ret = adreno_read_speedbin(dev, &speedbin);
if (ret == -ENOENT) {
	/* No speedbin fuse wired up on this board. Fall back to bin 0
	 * (standard 855, up to 585 MHz) so the opp-supported-hw gated
	 * OPP table still yields usable operating points. */
	speedbin = 0;
} else if (ret) {
	return ret;
}
```

Failure mode without this: `adreno_gpu_init` fails with "no supported OPPs" and
the GPU never probes.

### (3) Force `msm_use_mmu()` true on the headless master

File: `drivers/gpu/drm/msm/msm_drv.c`

`msm_use_mmu()` decides whether the GPU uses its IOMMU/SMMU or falls back to a
contiguous VRAM carveout. It decides by checking whether the **master device**
has an `iommu` — but here the master is the headless *dummy* device from patch
(1), which has no `iommu` property. So it returns false and the driver falls
back to a tiny **16 MB VRAM carveout**, which the GPU immediately starves on.

Force it true — the Adreno 640 SMMU is real and works on this board:

```c
static bool msm_use_mmu(struct drm_device *dev)
{
	/* The headless dummy master has no iommu of its own, but the
	 * a640 SMMU is present and functional on this board. Force MMU
	 * mode so we don't fall back to the 16MB VRAM carveout. */
	return true;
}
```

Failure mode without this: GPU runs on a 16 MB carveout, exhausts it almost
immediately, and allocations fail under any real workload.

### (4) Set a 64-bit DMA mask on simpledrm

File: `drivers/gpu/drm/tiny/simpledrm.c`

Buffers rendered on the Adreno and imported into the simpledrm scanout path can
be allocated **above the 4 GB boundary**. `simpledrm` never sets a DMA mask, so
it defaults to 32-bit; imported high buffers then bounce through `swiotlb`,
which is slow and can exhaust the bounce buffer.

Set a 64-bit coherent DMA mask in probe:

```c
/* GPU-imported buffers can live above 4GB; without a 64-bit mask
 * they bounce through swiotlb. */
ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(64));
if (ret)
	return ret;
```

This is the same lesson learned on the z3s (`z3s`) port. Failure mode without
it: correct output but swiotlb bounce-buffer thrash and, under load, allocation
failures on high buffers.

## Firmware and format gotchas

### The a640 uses the a630 SQE

There is no `a640_sqe.fw`. The Adreno 640 loads the **a630** SQE microcode. Make
sure `a630_sqe.fw` is present and readable.

### Decompress the xz-compressed firmware in place

Fedora ships some Qualcomm firmware xz-compressed:

```text
/lib/firmware/qcom/a630_sqe.fw.xz
/lib/firmware/qcom/sm8150/a640_gmu.bin.xz
```

The kernel firmware loader can transparently decompress these **only** if built
with `CONFIG_FW_LOADER_COMPRESS`. This kernel was not, so the loader looks for
the uncompressed name and fails. Decompress them in place:

```sh
unxz /lib/firmware/qcom/a630_sqe.fw.xz
unxz /lib/firmware/qcom/sm8150/a640_gmu.bin.xz
```

(Alternatively enable `CONFIG_FW_LOADER_COMPRESS` + `CONFIG_FW_LOADER_COMPRESS_XZ`
and rebuild. Decompressing in place is the quicker path for bring-up.)

Final GPU firmware inventory:

```text
/lib/firmware/qcom/a630_sqe.fw                          (a640 uses a630 SQE)
/lib/firmware/qcom/sm8150/a640_gmu.bin                  (GMU microcode)
/lib/firmware/qcom/sm8150/gts6lwifi/a640_zap.mdt        (device-signed zap)
/lib/firmware/qcom/sm8150/gts6lwifi/a640_zap.b00
/lib/firmware/qcom/sm8150/gts6lwifi/a640_zap.b01
/lib/firmware/qcom/sm8150/gts6lwifi/a640_zap.b02
```

## Build gotchas

### Build with `make modules`, not a single-target `.ko`

Building just the one object:

```sh
make .../msm.ko        # WRONG for verification
```

throws **false** modpost "undefined symbol" errors for symbols that actually
live in *other* modules the single target doesn't see. Build the module set:

```sh
make modules
```

This resolves cross-module symbols correctly. A single-target `.ko` build is not
a valid way to check that `msm` links.

### `msm` as a module, blacklisted first (z3s autoload pattern)

Follow the same safe-autoload discipline as z3s: build `msm` as a **module**,
keep it out of autoload for the first manual test, then enable it deliberately
once proven.

Blacklist for the first boot so nothing loads it automatically:

```text
# /etc/modprobe.d/blacklist-msm.conf
blacklist msm
```

Test it by hand:

```sh
modprobe msm
```

Once it comes up clean, remove the blacklist and enable ordered autoload:

```text
# /etc/modules-load.d/msm.conf
msm
```

And gate the display manager on the render node existing, so KWin never starts
before `renderD128` is present:

```ini
# display-manager.service drop-in
[Service]
ExecStartPre=/bin/sh -c 'for i in $(seq 1 50); do [ -e /dev/dri/renderD128 ] && exit 0; sleep 0.1; done; exit 0'
```

## Session recycle gotcha

`systemctl restart <displaymanager>` does **not** recycle KWin — the compositor
keeps the **same PID** across the restart, so it will still be holding whatever
device it opened before your change. To actually force a fresh KWin that reopens
`renderD128`, hard-recycle the whole user session:

```sh
systemctl stop <displaymanager>
pkill -9 -u <user>
systemctl reset-failed <displaymanager>
systemctl start <displaymanager>
```

Verify the KWin PID actually changed before trusting any before/after result.

## How to verify it is really the GPU (not llvmpipe)

Do not eyeball smoothness — measure. Software rendering (llvmpipe) can look
fine on a light UI and will lie to you. Confirm all of the following:

**1. KWin holds the render node, and it is the msm driver.** Find KWin's PID,
confirm it has `/dev/dri/renderD128` open, and check the driver in its fdinfo:

```sh
pid=$(pgrep -x kwin_wayland)
ls -l /proc/$pid/fd | grep renderD128
grep -H '^drm-driver' /proc/$pid/fdinfo/*
# expect: drm-driver:  msm
```

If the driver reads anything other than `msm`, or KWin has no `renderD128` fd,
you are on llvmpipe.

**2. GPU engine time grows under UI activity.** In the same fdinfo, watch the
engine nanosecond counter climb while you drag/animate windows:

```sh
grep drm-engine-gpu /proc/$pid/fdinfo/*
# drm-engine-gpu: <nanoseconds>   -- must increase between reads under load
```

A static `drm-engine-gpu` under active compositing means nothing is hitting the
GPU.

**3. GPU IRQ count climbs.** The Adreno interrupt must be firing:

```sh
grep -i 'gpu\|kgsl\|adreno' /proc/interrupts
# the count column must increase across UI activity
```

**4. Zero GPU faults.** A working pipe produces no fault spew. Check `dmesg`:

```sh
dmesg | grep -i 'msm\|adreno\|gpu\|gmu'
# expect: GMU/zap/SQE loaded, NO "gpu fault", NO PAS/zap auth failure
```

Any `gpu fault`, iova fault, or zap authentication failure means the pipe is not
actually working even if something renders.

Pass criteria, all four together: KWin holds `renderD128` with
`drm-driver: msm`, `drm-engine-gpu` nanoseconds grow under load, the GPU IRQ
count climbs, and `dmesg` shows zero GPU faults.

## Fixed-mode display warning

Scanout is a **fixed-mode** `simpledrm` framebuffer inherited from ABL. It has
one resolution, one refresh rate, and no modesetting. Consequences to keep in
mind:

- **No mode changes, no DPMS.** The resolution KWin sees is whatever ABL left.
  You cannot change resolution, rotate at the CRTC, or power the panel off/on
  through DRM. Any KDE display-configuration change that implies a modeset will
  either no-op or confuse the compositor.
- **Rotation, scaling, HiDPI are compositor-side only.** Handle geometry in KWin
  (transforms / scale), not via DRM modes.
- **This is scanout, not GPU.** Accelerated rendering above is entirely separate
  from the fixed-mode limitation — freedreno still renders on the Adreno; it
  just presents into the one framebuffer simpledrm owns.

Real modesetting requires bringing up the mainline Qualcomm DPU/MDSS + DSI path
and the Tab S6 AMOLED panel sequence (Phase 4, display path). Until that lands,
treat the framebuffer geometry as fixed and do all display adjustment in the
compositor.
