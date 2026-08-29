# Wi-Fi bring-up — WCN3990 (ath10k_snoc) on gts6lwifi / SM8150

Status: unfinished. The full QMI handshake to the WLAN firmware completes — we
read the real chip ID and firmware version off the silicon — and then the
WCN3990 firmware crashes about two seconds later during its own RF/radio init,
before it ever signals `FW_READY`. Every config-level cause has been ruled out by
matching Samsung's own decompiled device tree value by value. What remains is a
genuine hardware-init fault whose crash reason we have not yet been able to read,
because the failure hard-locks the whole SoC and three separate capture routes
are each blocked on this platform.

This document is a call for help as much as a record. If you have brought up
WCN3990 on a Samsung SM8150 device, skip to [The open blocker](#the-open-blocker).

Target: Samsung Galaxy Tab S6 Wi-Fi (SM-T860, `gts6lwifi`, SM8150P /
Snapdragon 855). Kernel 6.12 mainline. Bootloader: Aloha UEFI, booting
`Image` + `gts6-wifi*.dtb` from an ESP.

---

## 1. The mental model: SNOC, with the firmware living on the modem

This is the single most important thing to understand, and it is why Wi-Fi on
this device is not "load a driver and go."

The WCN3990 is an `ath10k_snoc` part. Unlike PCIe ath10k (QCA6174 etc.), it has
no on-card CPU running its own firmware image. Its WLAN firmware
(`wlanmdsp.mbn`) runs on the modem DSP — the Q6/mpss protection domain — not on
the apps CPU. `ath10k_snoc` on the apps side is only a QMI client that talks to a
firmware service (`wlfw`) hosted inside the modem.

That inverts the whole bring-up. Before `ath10k` can do anything, the entire
modem subsystem has to be alive and serving files and IPC. The dependency chain
is:

```text
1. mpss remoteproc (PAS) boots the Samsung-signed modem image
2. glink / smp2p / qrtr IPC comes up between apps and modem
3. rmtfs (userspace) serves the modem's EFS (modemst1/2, fsg, fsc, ssd)
4. tqftpserv (userspace) serves firmware files to the modem over QRTR/TFTP
5. the modem loads wlanmdsp.mbn and exposes the wlfw QMI service (svc 69)
6. ath10k_snoc does the wlfw QMI handshake (ind_register, host_cap, MSA, cap)
7. wlan0
```

Steps 1–6 all work today. Step 6 completes fully. The device dies at the very
end of step 6, when the modem-resident WLAN firmware initializes the actual
radio.

Practical consequence: you cannot debug this as "an ath10k problem." A failure
anywhere in 1–5 looks, from `ath10k`'s side, like "waiting for the wlfw
service." Most of the work below is modem plumbing, not Wi-Fi.

---

## 2. Firmware extraction list — everything comes from this device

None of the WLAN-path firmware is redistributable, and the generic Linux
firmware calibration is wrong for this board (see the board-data section). Pull
every file from the tablet's own vendor partitions. Back up the EFS partitions
first (`modemst1`, `modemst2`, `fsg`, `fsc`, `ssd`) with `dd` before `rmtfs`
ever touches them — they hold IMEI/RF calibration and are never
flashed/published; corruption is permanent.

| File(s) | Source on device | Destination | Notes |
|---|---|---|---|
| `modem.mdt` + ~30 `modem.b*` segments | `sda17` (`apnhlos`/`modem` FAT, `image/`) | `/lib/firmware/qcom/sm8150/gts6l/` | The PAS metadata + loadable segments. No `mba.mbn` needed (PAS loads `.mdt` directly). |
| `wlanmdsp.mbn` | `sda25` vendor `:/firmware/wlan/qca_cld/` | `/lib/firmware/qcom/sm8150/gts6l/` and `/lib/firmware/wlanmdsp.mbn` | The WLAN firmware the modem runs. `tqftpserv` serves it to the modem from the remoteproc's own fw dir. |
| `bdwlan.bin` (+ `bdwlan.bin1`, `bdwlan.bin2`) | `sda25` vendor `:/firmware/wlan/qca_cld/` | input to the board-2.bin repack (§4) | Samsung's real RF calibration. `bdwlan.bin` is `26328` bytes. |
| `regdb.bin` | `sda25` vendor `:/firmware/wlan/qca_cld/` | staged, currently unused by mainline | Regulatory DB. |
| `WCNSS_qcom_cfg.ini` | `sda25` vendor `:/firmware/wlan/qca_cld/` | reference only | Downstream INI; a suspect for a missing pre-RF step (see the open blocker). |
| `modemr.jsn` | `sda17` `image/` | `/lib/firmware/qcom/sm8150/gts6l/` | Modem PD JSON. |
| `board-2.bin`, `firmware-5.bin` | `linux-firmware` `ath10k/WCN3990/hw1.0/` | `/lib/firmware/ath10k/WCN3990/hw1.0/` | Both shipped `.xz` in Fedora — `unxz` them (the `FW_LOADER_COMPRESS` gotcha). `board-2.bin` gets replaced by the repack in §4. |

The modem image is the 2023 Samsung firmware, not the 2019 shipping image the
extracted DTBs describe — this matters for the DTS (§3.1).

---

## 3. DTS changes

Four independent changes to the board DTS. All are on top of stock
`sm8150.dtsi`, and each is justified below because several of them contradict
what "archaeology" of the extracted 2019 DTBs would tell you.

### 3.1 Extend `mpss_mem` — the modem grew 10 MB

PAS (`qcom,sm8150-mpss-pas`) loads `modem.mdt` directly and rejects it if any
loadable segment falls outside the reserved `mpss_mem` region:

```text
Booting fw image qcom/sm8150/gts6l/modem.mdt ...
segment outside memory range   -> -22, boot aborts
```

Ground truth is the ELF, not the DTB. Parsing `modem.mdt`'s program headers on
the device shows the loadable segments span `0x8d800000 .. 0x97800000` =
`0xa000000` (160 MB). Every extracted DTB (`kernel_dtb_0..4`, both DTBOs) says
`mpss_mem` is `0x9600000` (150 MB). Those DTBs are the 2019 shipped ones; the
`modem.mdt` on `sda17` is from Samsung's Aug-2023 firmware update, which grew the
modem by 10 MB. The image ran on Android on this exact device, so TZ accepts the
bigger span — trust the ELF.

Fix: extend `&mpss_mem` to `0xa000000` and move the reservations it would now
collide with out of the way. `&venus_mem` and `&slpi_mem` were relocated into the
free `0x99d00000 .. 0x9c400000` gap. Their consumers are disabled on this port,
so only the reservations had to stop overlapping:

```dts
&mpss_mem {
    reg = <0x0 0x8d800000 0x0 0x0a000000>;   /* was 0x09600000 */
};

/* venus_mem / slpi_mem relocated into the 0x99d00000–0x9c400000 free gap
   so they no longer overlap the extended mpss_mem */
```

### 3.2 Make `rmtfs_mem` dynamic — the fixed region is inside `removed_regions`

The mainline fixed `rmtfs_mem` base `0x89b00000` sits inside Samsung's
`removed_regions` and can never be HLOS-assigned — the SCM assign returns `-22`
at probe. Do not try to delete the vmid to dodge it: with no assign, the modem
faults on first access and the whole SoC hangs. TZ wants the assign, just in
guarded form and from RAM it is allowed to grant.

Make the region dynamic (allocated from plain free RAM) with guard pages:

```dts
&rmtfs_mem {
    /delete-property/ reg;
    size = <0x0 0x400000>;              /* 4 MB — see note */
    alignment = <0x0 0x200000>;
    alloc-ranges = <0x0 0xf0000000 0x0 0x08000000>;  /* high in free RAM */
    qcom,use-guard-pages;
};
```

Size note: the modem wants a full `0x200000` window (`shared memory not large
enough 0x200000 vs 0x1fe000`). The guard pages shave a page off each end, so size
the region 4 MB, not 2 MB, to leave a full 2 MB usable window inside the guards.
`qcom,use-guard-pages` is the upstream mechanism added for exactly this firmware
behavior; keep the `qcom,vmid` — it is the guard pages, not the absence of the
assign, that makes TZ accept it.

### 3.3 `qcom,msa-fixed-perm` on the wifi node — the MSA is TZ-owned

This is the SC7180 pattern (SC7180-trogdor, a shipping Chromebook, uses it). The
modem's `wlfw` reports its MSA (Modem Shared Area) as two regions —
`0x8bc00000/0x4000` with `SECURE=1`, and `0x8bc04000/0x17c000` non-secure — the
secure one pre-provisioned by Samsung TZ. The mainline default, a per-region
HLOS→`{MSS_MSA, WLAN, WLAN_CE}` assign via `qcom_scm_assign_mem`, returns `-22`
because HLOS may not re-assign memory TZ already owns. Do not chase the `-22`.
Skipping the assign is the correct answer on a production device where TZ
provisions the MSA:

```dts
&wifi {
    qcom,msa-fixed-perm;
    /* ...supplies (below)... */
    status = "okay";
};
```

This is verified correct: with `msa-fixed-perm` the entire QMI handshake past MSA
(`msa_ready`, `host_cap`, `cap`) completes. The `-22` is not the blocker.

### 3.4 Wi-Fi supplies — the HDK reference set

Samsung's downstream `icnss` node names the same rails as the `sm8150-hdk`
reference design. Our board DTS `&wifi` had Surface-Duo leftovers
(`vdda_wcss_pll`/`adcdac`); replace them with:

| Property | Rail | Voltage |
|---|---|---|
| `vdd-0.8-cx-mx` | pm8150 **l1a** | 752 mV |
| `vdd-1.8-xo` | pm8150 **l7a** | 1800 mV |
| `vdd-1.3-rfa` | pm8150l **l2c** | 1304 mV |
| `vdd-3.3-ch0` | pm8150l **l11c** | 3312 mV |

`ch0` is set to 3312 mV, not the pre-fix 3.0 V floor: Samsung's
`qcom,vdd-3.3-ch0-config` specifies a 3.104–3.312 V window, and our `l11c` had
been sitting under-volted at its 3.0 V floor. This was a real discrepancy and is
now corrected (l11c pinned to 3.312 V and always-on, with l2c/rfa also always-on).
It is a legitimate fix — but, as the findings below record, it did not stop the
radio-init crash, so ch0 voltage is not the cause.

`ch1` is unpopulated on this board — the `vdd-3.3-ch1 not found, using dummy
regulator` warning is benign and matches Samsung's `icnss` and every reference.
`ath10k` does no `regulator_set_voltage`, so the voltages written in the DTS are
authoritative.

---

## 4. Board-data repack — the generic calibration is wrong

`ath10k` requests board data by a bus/board/chip key:

```text
bus=snoc,qmi-board-id=ff,qmi-chip-id=30224
```

Fedora's generic `board-2.bin` only contains `qmi-chip-id=30214` (that is the
DB845C) plus a generic `qmi-board-id=ff` fallback. Our silicon is `30224`, so
`ath10k` misses the chip entry and falls back to the generic `board-id=ff` —
wrong RF/PA calibration. With that generic data the modem-resident firmware
faults on radio init (as a soft crash — see the findings).

The device's own calibration is `bdwlan.bin` (from §2). Repack it into a proper
`ath10k` `board-2.bin` container with `ath10k-bdencoder` (from
`qca-swiss-army-knife`), indexed under the exact names `ath10k` requests:

```bash
ath10k-bdencoder \
  -o board-2-gts6l.bin \
  -i bus=snoc,qmi-board-id=ff,qmi-chip-id=30224 bdwlan.bin \
  -i bus=snoc,qmi-board-id=ff                    bdwlan.bin
# install to /lib/firmware/ath10k/WCN3990/hw1.0/board-2.bin
# (back up the generic first)
```

The container is byte-valid (verified). `bdwlan.bin` carries an internal board
marker `0x00`; `bdwlan.bin1`/`.bin2` carry marker `0x0e`. Both variants have been
tried (see the open blocker).

---

## 5. Userspace daemons + the traps

The modem plumbing (steps 3–5 of the chain) is userspace, built from
`linux-msm`. Build them on an arm64 host with glibc at or below the target's
(Debian 13 arm64, glibc 2.41, runs on Fedora fine).

| Daemon | Role | Run as |
|---|---|---|
| `qrtr` (`qrtr-smd` + ns) | QRTR IPC transport to the modem | Fedora package / meson build |
| `rmtfs` | serves the modem's EFS partitions | `rmtfs -P -s` (see trap) |
| `tqftpserv` | serves firmware files to the modem over QRTR/TFTP | meson build |
| `pd-mapper` | redundant on 6.12 — do not run | see trap |

Bring-up order once modem is booted:

```bash
modprobe qcom_q6v5_pas
modprobe qrtr-smd
modprobe rmtfs_mem            # creates /dev/qcom_rmtfs_mem1
rmtfs -P -s &
tqftpserv &
modprobe ath10k_snoc
echo start > /sys/class/remoteproc/remoteproc0/state
```

### Traps

- Stale `qrtr.ko` shadows the builtin. `CONFIG_QRTR` went `module → built-in`
  (QRTR ns has been in-kernel since 5.7). A leftover `qrtr.ko` from an earlier
  build shadows the builtin and breaks loading:
  `exports duplicate symbol qrtr_endpoint_post`, `qrtr-smd` gets
  `Exec format error`. Remove the stale `.ko` and `depmod`. General rule: after
  any `m → y` config change, purge modules before untarring the new set.

- The in-kernel PD mapper makes userspace `pd-mapper` redundant and harmful.
  Kernel 6.12 has an in-kernel `qcom_pd_mapper` that auto-spawns with the rproc.
  Running the userspace `pd-mapper` on top of it causes duplicate locator
  registrations. Samsung's modem never queries the locator anyway (instrumented:
  zero queries). Do not run userspace `pd-mapper`; you do not need `wlanmdsp.jsn`
  either.

- `tqftpserv` serves from the remoteproc's own fw dir, not `/lib/firmware` root.
  `wlfw` (service 69) only appears once `wlanmdsp.mbn` is copied into
  `/lib/firmware/qcom/sm8150/gts6l/` — `translate_readonly` resolves relative to
  the modem's firmware directory.

### rmtfs operational notes

`rmtfs` is fiddly enough to be worth its own list — a misconfigured `rmtfs` is
the single easiest way to convince yourself a bad run "survived" (see the ch0
false positive below).

- It needs the `rmtfs_mem` module loaded first; that is what creates
  `/dev/qcom_rmtfs_mem1`. Without the device node `rmtfs` cannot map its shared
  region.
- Run it as `rmtfs -P -s`. `-P` is partition mode, serving the real EFS
  partitions from `/dev/disk/by-partlabel` (note `fsg` lives on a different LUN,
  so a naive `sda1..32` sweep can miss it).
- Do not pass `-o`. `-o` forces file mode, which cannot open `modemst1`; the
  modem then never reaches radio init and the run looks deceptively stable.
- Do not pass `-r` (SSR fd integration) — it fails `Failed to get rprocfd` on
  this setup.
- Only one `rmtfs` instance may run; a second one hits a `qrtr` bind conflict.
- The reserved-memory dynamic region (§3.2) must be intact. If something else
  has shrunk its `alloc-ranges` (an earlier ramoops experiment did exactly this),
  `rmtfs` fails to remap and the modem never gets its storage.

---

## How far it gets (proven)

Every stage below is confirmed working, on a single deterministic run from a
fresh reboot, instrumented at each QMI step:

1. The modem boots under mainline PAS — the Samsung-signed `modem.mdt` loads and
   `remoteproc0` reaches `running`. Voice/NAS/SMS/UIM/EFS services all appear on
   QRTR.
2. `rmtfs` serves the real EFS (`modemst1/2`, `fsg`, `fsc`, `ssd`) with the
   guarded dynamic region — the TZ assign passes.
3. `wlfw` (service 69) comes up on the modem once `wlanmdsp.mbn` is served.
4. The full `ath10k` QMI handshake completes:

   ```text
   ind_register  ret=0
   host_cap      ret=0
   msa_mem_info  ret=0
   setup_msa     ret=0   (fixed-perm skip, no assign)
   msa_ready     ret=0
   cap           ret=0   -> chip_id 0x30224, chip_family 0x4001,
                            board_id 0xff, soc_id 0x40060000,
                            fw_version 0x3204038e
                            WLAN.HL.3.2.0.c3-00910-QCAHLSWMTPLZ-1.493553.2.512591.6
   server_arrive COMPLETE
   ```

   That is the real silicon answering — the real chip ID `0x30224` and the real
   firmware version string, read over QMI from the modem-hosted firmware.

5. Then the WCN3990 firmware crashes on its own RF/radio init, before any
   `FW_READY` indication:
   - With generic board data: about 2.3 s after `cap`, a soft crash
     (`ath10k_snoc firmware crashed! (guid ...)` with all-zero crash regs — the
     firmware is entirely dead), then the SoC hangs and the watchdog reboots.
   - With the device's real Samsung calibration: an instant hard SoC bus-lock
     right after modem-up, before the QMI trace can even flush (a 0.2 s sync loop
     cannot fire in time). The firmware drives the actual radio and faults the
     SNOC/interconnect on its first real RF DMA/register access.

The hard-lock-versus-soft-crash split is the clearest clue we have. A soft crash
is software hitting an assertion. An instant hardware lock is a bus transaction
to something that never answers — the firmware driving a real radio component
that is not in the state it expects. Real calibration makes the firmware do real
RF hardware access, which faults the bus the instant it happens; generic
calibration does minimal RF and fails as software instead. That is the classic
signature of a missing hardware init that downstream `icnss`/`qca_cld` performs
and mainline `ath10k_snoc` does not.

---

## What's ruled out (verified against Samsung's own config)

Do not spend time re-checking these. Each was verified by matching Samsung's
decompiled device tree value by value, or the working `sm8150-hdk` reference:

- MSA permissions. `qcom,msa-fixed-perm` is correct (SC7180 precedent, a shipping
  Chromebook). The QMI handshake completes past MSA. The `-22` from the
  per-region assign is expected and correctly skipped. The MSA was a red herring.
- Rails. All four supplies are real and enabled at the correct voltages during
  load (continuous `regulator_summary` capture): cx-mx l1a 752 mV, xo l7a
  1800 mV, rfa l2c 1304 mV, ch0 l11c pinned to 3.312 V — each with an `ath10k`
  consumer, none a dummy. There is no ch1: the fourth rail is genuinely
  unpopulated on this board, and its dummy-regulator warning is benign (it
  matches Samsung's `icnss`, which also declares only the four cx-mx/xo/rfa/ch0
  rails and no ch1).
- ch0 voltage specifically. Samsung's config wants a 3.104–3.312 V window and
  ours had been under-volted at the 3.0 V floor — a real discrepancy. We pinned
  l11c to 3.312 V and always-on and retested. It did not fix the lock. (The first
  such test appeared to survive, but only because `rmtfs` was misconfigured that
  run and the modem never reached radio init; once `rmtfs` ran correctly the
  hard-lock returned.) So the under-volt was real, the fix is kept, and ch0
  voltage is not the cause.
- RF clock. `rf_clk2`/`rf_clk2_ao` on at 38.4 MHz. Not a clock issue.
- IOMMU. Standard `apps_smmu` SID `0x640`, the `use_tz` path — matches the
  reference.
- Coredump path. `coredump_mask=0` does not prevent the lock, so the hang is a
  hardware bus lock from the RF fault, not the `ath10k` register-dump path.
- Board-data container. The `ath10k-bdencoder` repack is byte-valid.

It is a genuine RF-init hardware fault, not a config error.

---

## The open blocker

The WCN3990 firmware faults during its own RF/radio initialization, and we cannot
read why.

The crash reason is unreadable because the hard lock eats the logs, and every
persistent-capture route is blocked on this platform:

- `ramoops` / persistent RAM fails. This bootloader (Aloha UEFI) does not
  preserve DDR across reboot — it retrains/clears RAM every boot, so the
  persistent-RAM region is wiped before anything can read it back. Confirmed
  empty after both a cold power-off and a warm auto-reboot. `/dev/mem` is blocked
  by `STRICT_DEVMEM`; `/dev/pmsg0` needs build-time `PSTORE_PMSG`.
- Adding `PSTORE_CONSOLE` or `NETCONSOLE` needs a kernel rebuild, and every
  rebuild currently panics at init (see below). So netconsole — stream the kernel
  log as UDP over the USB link — is blocked on that separate, unsolved problem.
- Console over USB serial (`console=ttyGS0`) only caught the login banner. The
  serial gadget transmits asynchronously, and the hard lock beats the queued
  bytes out the port, so nothing from the fault itself makes it across.

Three independent walls between us and the one piece of information that would end
the guessing.

A related platform note that matters for capture work: a clean kernel panic does
not auto-reboot here (the watchdog does not catch it) — set `kernel.panic=10` (or
`panic=10` on the cmdline) so panics warm-reboot on their own. A hard SoC
bus-lock (the Wi-Fi RF fault) does get a watchdog warm-reset, typically in about
3–4 minutes.

### The kernel-rebuild panic (a separate, unsolved problem)

This is the thing stopping the crash capture, and it is worth stating plainly
because it is not the Wi-Fi bug. The kernel currently running on the tablet (build
`#17`, the GPU one, built 14:01) boots fine. Every kernel rebuilt since then
panics at init — a data abort (`ESR=0x96000004`, `EC=0x25` DABT) right after
`Run /sbin/init`, at a fault address that changes from build to build. A changing
fault address is the signature of a bad pointer whose value depends on the
kernel's memory layout, not a bug at a fixed spot in the code.

It has been chased methodically, and a lot is ruled out:

- Research pointed hard at a known binutils RELR relocation bug, so we built with
  `CONFIG_RELR=n` — still panics.
- We installed the LLVM linker (LLD) and relinked with it — still panics.
- We did a full `make clean` rebuild from scratch to rule out stale objects —
  still panics.
- We checked whether the toolchain changed between the good build and the bad
  ones — it did not; same gcc and binutils throughout.

So it is not RELR alone, not the linker, not stale objects, not a toolchain
update. Same source, same config, same compiler: the one build from 14:01 boots
and everything after it does not, and we have not explained that yet. The next
real step is to read the panic's call trace off the screen (photo, or
`console=ttyGS0`), which names the faulting function; without it we are guessing.

Recovery is cheap, so bolder rebuilds are fine: `fastboot flash cache
esp-gts6-recovery-gpu.img` (`Image#17` + `gts6-wifi9.dtb`) restores a full,
GPU-accelerated boot in one flash, rootfs intact.

### Remaining leads (help wanted, all unproven)

Each of these costs a hard-lock and a recovery flash to test, so they have not
all been exercised:

1. Samsung's `qcom,smmu-s1-bypass`. Downstream `icnss` bypasses stage-1
   translation for the WCN; mainline translates through `apps_smmu`. If mainline
   faults on a WCN DMA with the context bank configured to stall on fault
   (`CB_SCTLR.CFCFG=1`), the transaction stalls — which is exactly this hard
   lock. Trying `s1-bypass` / adjusting the wifi `iommus` / the SMMU fault mode is
   the strongest remaining lead.
2. Matching `firmware-5.bin` feature-flags to the Samsung `wlanmdsp`. The generic
   `firmware-5.bin` feature bits may not pair with the modem-resident WLAN
   firmware this device actually runs.
3. Which of the three `bdwlan` revisions matches this exact unit. `bdwlan.bin`
   (marker `0x00`) versus `bdwlan.bin1`/`.bin2` (marker `0x0e`) — both `.bin` and
   `.bin1` hard-lock identically so far, but we have not confirmed which revision
   the modem's WLAN PD is actually meant to load.

### Reference points

No `gts6l`/Tab-S6 mainline port exists. Closest known-good references, all
same-SoC and/or same WCN3990 family:

- `aaronsb/sm-x800-linux` — Galaxy Tab S8+, same WCN3990 family; check for a
  working MSA/supply/SMMU recipe.
- `xiaomi-vayu` (Poco X3 Pro) — same SM8150 + WCN3990, working firmware repo.
- `guacamole` (OnePlus 7 Pro) — same SoC.
- `ianmacd/gts6lwifi` downstream `icnss.c` — the `smmu-s1-bypass`, the
  single-block MSA0 assign, and the vreg/GPIO tables to diff against.

If you can get WCN3990 past RF init on a Samsung SM8150 device — or you know what
the downstream driver does that mainline `ath10k_snoc` skips — please open an
issue or reach out. This is one hardware-init step away from `wlan0`.

---

## Current on-device state

Wi-Fi is blacklisted from autoload — all bring-up is manual and experimental. The
tablet boots clean to KDE with GPU acceleration intact. The ESP carries kernel
`#17` with the current DTB `gts6-ch0v3` (mpss extended, rmtfs dynamic 4 MB +
guard, wifi supplies fixed, `msa-fixed-perm`, ch0 pinned to 3.312 V and
always-on). `board-2.bin` is the Samsung `bdwlan.bin` repack. Userspace tools live
in `/usr/local/bin` (`tqftpserv`, plus `pd-mapper` kept only for reference, not
run); `rmtfs`/`qrtr` are Fedora packages. Firmware is staged under
`/lib/firmware/qcom/sm8150/gts6l/`. The recovery image `esp-gts6-recovery-gpu.img`
(`Image#17` + `gts6-wifi9.dtb`) restores a full GPU boot in one `fastboot flash`.
Each Wi-Fi test hard-locks the SoC; the watchdog usually recovers in about
3–4 minutes, occasionally needing a manual power-cycle.


---

## 2026-08-29: netconsole capture achieved; failure bounded

The crash is now observable and the picture changed materially. With an
instrumented ath10k (step markers through the QMI path) streaming over
netconsole, the entire host-side exchange is proven to complete: handshake,
MSA (fixed-perm), capability read, the BDF board-data download, and the
calibration report all return success. The SoC then hard-locks inside the
modem-resident WLAN PD's radio init — the modem once reported
'SFR Init: wdog or kernel error suspected' before the fabric died. This is a
modem-side firmware/hardware fault, not an ath10k or DT configuration bug.

Also resolved the same day: the kernel-rebuild panic (stale in-tree build
state plus stale modules across a struct-layout-changing config) and live
netconsole capture over the USB link.

Newly ruled out (captured runs): host SMMU translation (identity-DMA test),
all three bdwlan revisions, the downstream dynamic-feature-mask message
(mainline now sends it; accepted; no change), coredump reads (they hang the
bus — keep coredump_mask=0), and the linux-firmware HL.2.0 wlanmdsp (SDM845
build, wrong platform). Details in docs/DEVLOG.md.
