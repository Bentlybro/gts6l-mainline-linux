# Getting Internal UFS Storage Working — Galaxy Tab S6 (gts6lwifi, SM8150)

This document covers bringing up the **internal UFS storage** on the Samsung Galaxy
Tab S6 Wi-Fi (`gts6lwifi`, Qualcomm SM8150 / Snapdragon 855) under a mainline Linux
port booted via Project Aloha (Samsung TrustZone + Aloha UEFI). It records the exact
failure, the real root cause, the one-line-per-clock fix, how to verify it, and — most
importantly — the transferable lesson about this platform.

If you only remember one thing from this page:

> **On this platform the TrustZone firmware lies about clock halt-status bits.**
> A clock branch can report "off" in its CBCR halt-status register while it is
> physically running. Any driver that polls that bit to confirm a clock will hang or
> fail with `-EBUSY`, even though the hardware is fine.

---

## The symptom

UFS refused to probe. The UFS host controller could not enable its core clock, and the
boot stalled / errored out here:

```
ufshcd-qcom 1d84000.ufshc: clk_prepare_enable(core_clk) failed, -16
```

`-16` is `-EBUSY`. Turning on clock debugging showed the actual culprit inside the Qualcomm
GCC clock driver:

```
gcc_ufs_phy_axi_clk status stuck at 'off'
clk_branch_wait: gcc_ufs_phy_axi_clk failed to enable
```

The failing clock is `GCC_UFS_PHY_AXI_CLK` (the UFS controller's AXI/core clock). The generic
clock framework tried to enable it, `clk_branch_wait()` polled the branch's halt-status bit,
the bit never flipped to "running," and after the timeout the framework returned `-EBUSY` up
the stack to UFS.

---

## The root cause

This is **not** a real clock failure. The clock is running.

On this device the low-level bring-up is done by **Project Aloha** running under Samsung's
TrustZone firmware. Aloha had already read the eSP (embedded storage profile) off the UFS
device moments earlier during its own boot — the storage, its PHY, and the AXI clock were
demonstrably alive. Yet the moment Linux's GCC driver polled the clock branch's **CBCR
halt-status bit**, the readback claimed the branch was still halted.

The halt-status readback is simply **unreliable under this firmware**. The clock reaches the
requested state in hardware; the status bit reported through the register interface does not
reflect that. `clk_branch_wait()` believes the lie, spins until timeout, and reports the
clock as stuck "off."

This is the **same quirk family** as the other TrustZone-mediated readback faults seen on this
platform:

- **SPMI arbiter faults** — the PMIC arbiter reports bogus state through the same kind of
  firmware-mediated register path.
- **The GPIO0-read hang** — reading a TZ-protected GPIO wedges because the readback goes
  through firmware that does not answer honestly.

All three share one shape: **a status/readback the firmware is supposed to report faithfully,
and doesn't.** The write side works, the hardware does the thing, but the confirmation you
read back is garbage.

### The transferable lesson

When you are porting to a device where TrustZone/secure firmware brought the hardware up before
Linux started, **treat "status bit says it didn't work" as suspect, not as ground truth** —
especially when independent evidence (here: Aloha already used the UFS) proves the hardware is
working. The fix is almost never to fight the poll harder (longer timeouts, retries); it is to
**stop trusting the poll** for the specific bits the firmware handles badly.

---

## The fix

Make the Qualcomm GCC branch-clock code **skip the halt-status poll** for the affected clocks.
The clock framework supports exactly this: setting a branch's `.halt_check` to
`BRANCH_HALT_SKIP` tells `clk_branch_wait()` not to poll the (untrustworthy) status bit and to
assume the clock reached its requested state.

Edit `drivers/clk/qcom/gcc-sm8150.c`. For each affected branch clock, set:

```c
.halt_check = BRANCH_HALT_SKIP,
```

Apply it to the **18 UFS + USB branch clocks** below. (USB is included because it sits on the
same PHY/clock island and exhibits the same readback quirk; skipping the poll there avoids the
identical hang once USB is enabled.)

**UFS clocks (5):**

- `gcc_ufs_phy_axi_clk`
- `gcc_ufs_phy_ahb_clk`
- `gcc_ufs_phy_unipro_core_clk`
- `gcc_ufs_phy_ice_core_clk`
- `gcc_ufs_phy_phy_aux_clk`

**UFS aggregate clocks (`gcc_aggre_ufs_phy_*`):**

- `gcc_aggre_ufs_phy_axi_clk`

**USB clocks:**

- `gcc_usb30_prim_master_clk`
- `gcc_usb30_prim_mock_utmi_clk`
- `gcc_usb3_prim_phy_aux_clk`
- `gcc_usb3_prim_phy_com_aux_clk`
- `gcc_usb3_prim_phy_pipe_clk`
- `gcc_aggre_usb3_prim_axi_clk`
- `gcc_cfg_noc_usb3_prim_axi_clk`

### Example diff

Each affected branch already looks something like this; add (or change) the `.halt_check` line:

```c
static struct clk_branch gcc_ufs_phy_axi_clk = {
	.halt_reg = 0x77010,
	.halt_check = BRANCH_HALT_SKIP,   /* TZ readback of the halt bit is unreliable */
	.clkr = {
		.enable_reg = 0x77010,
		.enable_mask = BIT(0),
		.hw.init = &(struct clk_init_data){
			.name = "gcc_ufs_phy_axi_clk",
			.parent_hws = (const struct clk_hw*[]){
				&gcc_ufs_phy_axi_clk_src.clkr.hw,
			},
			.num_parents = 1,
			.flags = CLK_SET_RATE_PARENT,
			.ops = &clk_branch2_ops,
		},
	},
};
```

Repeat the `.halt_check = BRANCH_HALT_SKIP,` change for each of the 18 clocks listed above. No
other logic changes are required — `BRANCH_HALT_SKIP` is a first-class value in the branch
`halt_check` enum and is handled by `clk_branch_wait()` directly.

> **Why not a bigger timeout or a retry loop?** Because the clock is already on. The status
> bit will never flip no matter how long you wait — the firmware is not going to report it
> honestly. Skipping the poll is the correct fix, not a workaround.

---

## Additional config / DTS required for a clean boot

UFS coming up is necessary but not sufficient. On this device the following are also required
to reach a stable boot without silent hangs:

- **`EFI_STUB`** — the kernel is launched by Aloha UEFI as an EFI application.
- **Disable the serial console entirely.** There is no serial cable attached, so if a console
  is configured, every later `printk` blocks forever waiting on a UART that no one drains.
  Remove `console=` and do not enable a serial console in the config.
- **`gpio-reserved-ranges = <0 4>, <126 4>;`** in the SoC pinctrl node. **GPIO0 is
  TZ-protected** — touching it triggers the GPIO0-read hang described above. Reserving those
  ranges keeps Linux from probing pins the firmware owns.
- **Disable `dispcc`.** If the display clock controller is enabled, `clk_disable_unused()`
  gates the display clock the bootloader left running, killing the framebuffer / display the
  moment unused clocks are reaped.
- **Disable the SPMI PMIC drivers.** UFS (and the rest of the RPMh-managed rails) come up via
  **RPMh, not SPMI**, and the SPMI arbiter faults on this platform (same quirk family). Leaving
  SPMI PMIC drivers out avoids the arbiter fault path entirely.
- **Kernel command line:**

  ```
  fw_devlink=permissive clk_ignore_unused pd_ignore_unused
  ```

  - `fw_devlink=permissive` — don't hard-fail probe ordering on the many firmware-owned
    suppliers that never appear as Linux devices.
  - `clk_ignore_unused` — belt-and-suspenders against `clk_disable_unused()` gating something
    the firmware left on that we depend on.
  - `pd_ignore_unused` — same idea for power domains.

---

## How to verify

With the fix in place, UFS probes and the internal storage enumerates. You should see the disk
and all of its partitions in the kernel log:

```
scsi 0:0:0:0: Direct-Access     SAMSUNG  <model>          ...
sd 0:0:0:0: [sda] 250085376 512-byte logical blocks: (128 GB/119 GiB)
sd 0:0:0:0: [sda] Write Protect is off
sd 0:0:0:0: [sda] Attached SCSI disk
 sda: sda1 sda2 ... sda30 ...
```

Quick checks from userspace once booted:

```sh
# Disk present and correct size (~128 GB)
cat /sys/block/sda/size          # multiply by 512 for bytes
lsblk /dev/sda

# Confirm no clock came back with an -EBUSY / "stuck at off" error
dmesg | grep -iE 'ufs|clk_branch|gcc_ufs|EBUSY'

# The UFS clocks should be present and enabled
grep -E 'gcc_ufs|gcc_aggre_ufs' /sys/kernel/debug/clk/clk_summary
```

Success looks like a clean `sd 0:0:0:0: [sda] ... 128 GB`, all partitions listed, and **no**
`stuck at 'off'` or `-16` messages.

---

## Partition map (main LUN → `sda`)

The main data LUN enumerates as `/dev/sda`. Key partitions:

| Partition   | Node    | Role                                              |
|-------------|---------|---------------------------------------------------|
| userdata    | `sda30` | ~112 GB — **Fedora root, ext4**                   |
| system      | `sda24` | Android system image                              |
| vendor      | `sda25` | Android vendor image                              |
| product     | `sda26` | Android product image                             |
| cache       | `sda27` | 400 MB FAT32 — used as the **boot ESP**           |
| boot        | `sda20` | Aloha UEFI (boot chain)                           |
| recovery    | `sda21` | Recovery                                          |
| apnhlos     | `sda16` | FAT                                               |
| modem       | `sda17` | FAT                                               |

The Fedora userspace lives on `sda30`; the bootloader reads the kernel/EFI payload from the
400 MB FAT32 ESP at `sda27`.

---

## Partition safety — read this before you flash anything

UFS being writable means you can also brick the device. **Never flash, erase, or repartition
the following.** These are owned by TrustZone / the secure boot chain, and damaging them can
produce an unrecoverable (or JTAG-only) brick:

- `abl`, `xbl` — bootloaders
- `tz`, `hyp`, `aop` — TrustZone, hypervisor, always-on processor firmware
- `modem` firmware, `efs`, `sec_efs` — modem + EFS (IMEI/calibration/secure filesystem)
- `persist` — persistent calibration data
- `PIT` — the partition table itself
- **The entire bootloader LUN** — do not touch it at all.

Safe to work with: the data partitions listed in the map above (`userdata`/`sda30`, the
`cache`/`sda27` ESP, `system`/`vendor`/`product`). Everything in the "never" list stays exactly
as Samsung/Aloha left it.

> Rule of thumb: if a partition is part of the secure boot chain or holds device-unique
> calibration (`abl`, `xbl`, `tz`, `hyp`, `aop`, `modem`, `efs`, `sec_efs`, `persist`, `PIT`),
> treat it as read-only forever. When in doubt, don't write.
