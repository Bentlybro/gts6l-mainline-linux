# Wi-Fi bring-up — WCN3990 (ath10k_snoc) on gts6lwifi / SM8150

Status: working. `wlan0` associates, gets a DHCP lease, passes traffic at
802.11ac rates, and comes up automatically on boot.

Target: Samsung Galaxy Tab S6 Wi-Fi (SM-T860, `gts6lwifi`, SM8150P /
Snapdragon 855). Kernel 6.12 mainline, Fedora 44 userspace. Bootloader: Aloha
UEFI, booting `Image` + `gts6-wifi*.dtb` from an ESP.

The fix is one device-tree line. No driver patch is required — the working
configuration runs stock mainline `ath10k_snoc`. If you only want the answer,
read [section 7](#7-the-root-cause-one-wrong-memory-region). If you want to
reproduce the port, read the whole thing in order.

---

## 1. The result, and the evidence

Verified end to end on 2026-08-29, from a cold boot with no manual steps:

| Check | Result |
|---|---|
| Association | WPA2-PSK AP on 5 GHz, associated |
| Addressing | DHCP lease obtained, default route installed |
| Ping to gateway | 3 packets transmitted, 3 received, 0% loss |
| Ping to `1.1.1.1` | 3 packets transmitted, 3 received, 0% loss |
| HTTPS | `curl` returns HTTP 200 with real body content |
| Link rate | 866.7 MBit/s — VHT-MCS 9, 80 MHz, 2 spatial streams (802.11ac) |
| Signal | −36 to −42 dBm |
| Boot behaviour | auto-connects, no manual intervention |
| Remote access | the tablet is reachable over SSH on its own Wi-Fi |

866.7 MBit/s at MCS 9 / 80 MHz / NSS 2 is the full advertised capability of the
WCN3990's 2x2 ac radio, so this is not a degraded or fallback link. Both bands
scan and associate.

---

## 2. The mental model: SNOC, with the firmware living on the modem

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
6. ath10k_snoc does the wlfw QMI handshake (ind_register, host_cap, MSA,
   cap, BDF download, cal report) and TrustZone grants the WLAN hardware
   access to the Modem Shared Area
7. FW_READY -> wlan0
```

The practical consequence is that you cannot debug this as "an ath10k problem."
A failure anywhere in 1–5 looks, from `ath10k`'s side, like "waiting for the
wlfw service," and a failure in the memory ownership at step 6 looks like the
whole SoC falling over with no log at all. Most of the work below is modem
plumbing and memory-map work, not Wi-Fi.

The second thing to internalise, because it is what actually solved this: the
memory regions in this chain are *owned*. TrustZone tracks who owns every
carveout, and it will not let HLOS hand out access to memory HLOS does not own.
Both of the two hard bugs in this port were the same mistake — pointing a
Linux-side reserved region at an address that belongs to Samsung's firmware.

---

## 3. Device-tree configuration

All changes are on top of stock `sm8150.dtsi`, in the board DTS. There are four
of them, and one of them is the fix.

### 3.1 Relocate `wlan_mem` into HLOS-owned DDR — this is the fix

`sm8150.dtsi` places `wlan_mem` at `0x8bc00000`. On this board that address is
`pil_wlan_fw_region` in Samsung's own memory map: it belongs to the firmware
loader, not to HLOS. `ath10k` asks TrustZone to grant the WLAN hardware
read/write on the Modem Shared Area via `qcom_scm_assign_mem()`, and TrustZone
refuses with `-22` (`EINVAL`) — correctly, because HLOS cannot give away memory
it does not own.

Move the region into ordinary DDR that HLOS actually owns:

```dts
&wlan_mem {
	reg = <0x0 0xc0000000 0x0 0x00100000>;
};
```

`0xc0000000` sits inside the large System RAM block and is clear of every
firmware carveout. The size, `0x100000` (1 MB), matches both `ath10k`'s own
`.msa_size` hardware parameter and Samsung's downstream
`qcom,wlan-msa-memory`. The 1.5 MB figure that appears in older notes was simply
the size of the carveout we were wrongly pointing at, not the size the firmware
wants.

With that one line, `qcom_scm_assign_mem()` returns `0`, the WLAN hardware has
permission on its own shared memory, the calibration report is harmless,
`FW_READY` arrives, and `wlan0` appears.

Two things follow from this and are worth stating explicitly:

- `qcom,msa-fixed-perm` must be **removed**. It was a workaround that skipped
  the failing assign; with the region owned correctly the assign succeeds and
  skipping it is exactly wrong (see section 7).
- The standard `iommus` and `memory-region` properties from `sm8150.dtsi` are
  **kept**. Do not delete `memory-region` to make `ath10k` self-allocate; that
  path works briefly and then breaks on the SMMU (section 7 again).

### 3.2 The `&wifi` node — status plus four supplies

That is the entire node. There is nothing else to add.

```dts
&wifi {
	status = "okay";

	vdd-0.8-cx-mx-supply = <&vreg_l1a_0p75>;	/* 752 mV */
	vdd-1.8-xo-supply    = <&vreg_l7a_1p8>;		/* 1800 mV */
	vdd-1.3-rfa-supply   = <&vreg_l2c_1p3>;		/* 1304 mV */
	vdd-3.3-ch0-supply   = <&vreg_l11c_3p3>;	/* 3312 mV */
};
```

These are the rails Samsung's downstream `icnss` node names, which are also the
`sm8150-hdk` reference set. If your board DTS has Surface-Duo leftovers
(`vdda_wcss_pll` / `adcdac`), replace them.

`l11c` is pinned to 3312 mV and marked always-on, and `l2c` is always-on too.
Samsung's `qcom,vdd-3.3-ch0-config` specifies a 3.104–3.312 V window, and stock
`l11c` otherwise sits at its 3.0 V floor. `ath10k` never calls
`regulator_set_voltage`, so the voltages written in the DTS are authoritative.

There is no `ch1` rail on this board — it is genuinely unpopulated, exactly as
in Samsung's `icnss`. The `vdd-3.3-ch1 not found, using dummy regulator` warning
is benign; ignore it.

### 3.3 Extend `mpss_mem` — the modem grew 10 MB

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
`modem.mdt` on the `apnhlos` partition is from Samsung's Aug-2023 firmware
update, which grew the modem by 10 MB. That image ran on Android on this exact
device, so TrustZone accepts the bigger span — trust the ELF.

Extend `&mpss_mem` and move the reservations it would now collide with out of
the way. `&venus_mem` and `&slpi_mem` were relocated into the free
`0x99d00000 .. 0x9c400000` gap; their consumers are disabled on this port, so
only the reservations had to stop overlapping.

```dts
&mpss_mem {
	reg = <0x0 0x8d800000 0x0 0x0a000000>;	/* was 0x09600000 */
};

/* venus_mem / slpi_mem relocated into the 0x99d00000-0x9c400000 free gap
   so they no longer overlap the extended mpss_mem */
```

### 3.4 Make `rmtfs_mem` dynamic — the same ownership bug, one layer down

This is the first instance of the pattern that eventually solved Wi-Fi, and it
is worth reading with that in mind.

The mainline fixed `rmtfs_mem` base `0x89b00000` sits inside Samsung's
`removed_regions` — memory HLOS does not own — so the SCM assign returns `-22`
at probe. Do not try to delete the `qcom,vmid` to dodge it: with no assign, the
modem faults on first access and the whole SoC hangs. TrustZone wants the
assign; it just wants it from RAM that HLOS is allowed to grant.

Make the region dynamic, allocated from plain free RAM, with guard pages:

```dts
&rmtfs_mem {
	/delete-property/ reg;
	size = <0x0 0x400000>;			/* 4 MB - see note */
	alignment = <0x0 0x200000>;
	alloc-ranges = <0x0 0xf0000000 0x0 0x08000000>;
	qcom,use-guard-pages;
};
```

Size note: the modem wants a full `0x200000` window
(`shared memory not large enough 0x200000 vs 0x1fe000`). The guard pages shave a
page off each end, so size the region 4 MB, not 2 MB, to leave a full 2 MB
usable window inside the guards. `qcom,use-guard-pages` is the upstream
mechanism added for exactly this firmware behaviour. Keep the `qcom,vmid` — it
is the guard pages, not the absence of the assign, that makes TrustZone accept
the region.

---

## 4. Firmware extraction — everything comes from this device

None of the WLAN-path firmware is redistributable, and the generic Linux
firmware calibration is wrong for this board. Pull every file from the tablet's
own vendor partitions and keep them local.

Back up the EFS partitions first (`modemst1`, `modemst2`, `fsg`, `fsc`, `ssd`)
with `dd` before `rmtfs` ever touches them. They hold this unit's RF calibration
and identity data, they are never flashed or published, and corruption is
permanent. Locate partitions by `partlabel` rather than trusting device-node
numbers; `fsg` in particular lives on a different LUN, so a naive `sda1..32`
sweep can miss it.

| File(s) | Source on device | Destination | Notes |
|---|---|---|---|
| `modem.mdt` + ~30 `modem.b*` segments | `apnhlos` (`sda16` here), `:/image/` | `/lib/firmware/qcom/sm8150/gts6l/` | PAS metadata plus loadable segments. No `mba.mbn` needed — PAS loads `.mdt` directly. |
| `wlanmdsp.mbn` | vendor (`sda25` here), `:/firmware/wlan/qca_cld/` | `/lib/firmware/qcom/sm8150/gts6l/` and `/lib/firmware/wlanmdsp.mbn` | The WLAN firmware the modem runs. `tqftpserv` serves it from the remoteproc's own firmware directory. |
| `bdwlan.bin` (+ `bdwlan.bin1`, `.bin2`) | vendor, `:/firmware/wlan/qca_cld/` | input to the board-data repack (section 5) | Samsung's real RF calibration. `bdwlan.bin` is 26328 bytes. |
| `regdb.bin` | vendor, `:/firmware/wlan/qca_cld/` | staged, unused by mainline | Regulatory database. |
| `modemr.jsn` | `apnhlos`, `:/image/` | `/lib/firmware/qcom/sm8150/gts6l/` | Modem PD JSON. |
| `board-2.bin`, `firmware-5.bin` | `linux-firmware`, `ath10k/WCN3990/hw1.0/` | `/lib/firmware/ath10k/WCN3990/hw1.0/` | Both ship `.xz` in Fedora — `unxz` them (the `FW_LOADER_COMPRESS` gotcha). `board-2.bin` is then replaced by the repack below. |

Note that the modem image on the device is the 2023 Samsung firmware, not the
2019 shipping image the extracted DTBs describe. That is what drives the
`mpss_mem` change in section 3.3.

---

## 5. Board-data repack

`ath10k` requests board data by a bus/board/chip key:

```text
bus=snoc,qmi-board-id=ff,qmi-chip-id=30224
```

Fedora's generic `board-2.bin` contains `qmi-chip-id=30214` (the DB845C) plus a
generic `qmi-board-id=ff` fallback. This silicon is `30224`, so `ath10k` misses
the chip entry and falls back to generic RF/PA calibration.

Repack the device's own `bdwlan.bin` into a proper `ath10k` container with
`ath10k-bdencoder` (from `qca-swiss-army-knife`), indexed under the exact names
`ath10k` asks for:

```bash
ath10k-bdencoder \
  -o board-2-gts6l.bin \
  -i bus=snoc,qmi-board-id=ff,qmi-chip-id=30224 bdwlan.bin \
  -i bus=snoc,qmi-board-id=ff                    bdwlan.bin
# back up the generic file, then install as
# /lib/firmware/ath10k/WCN3990/hw1.0/board-2.bin
```

`bdwlan.bin` carries an internal board marker `0x00`; `bdwlan.bin1` and `.bin2`
carry `0x0e`. The `.bin` revision is what the working configuration uses. All
three were tested during debugging and none of them was ever the problem
(section 8).

---

## 6. Userspace daemons and the systemd unit

The modem plumbing — steps 3–5 of the chain — is userspace. `rmtfs` and `qrtr`
are Fedora packages; `tqftpserv` is built from `linux-msm`. Build on an arm64
host whose glibc is at or below the target's (Debian 13 arm64, glibc 2.41, runs
on Fedora 44 fine).

| Daemon | Role | How to run it |
|---|---|---|
| `qrtr` (`qrtr-smd` + ns) | QRTR IPC transport to the modem | Fedora package; the name service is in-kernel |
| `rmtfs` | serves the modem's EFS partitions | `rmtfs -P -s` |
| `tqftpserv` | serves firmware files to the modem over QRTR/TFTP | built from `linux-msm` |
| `pd-mapper` | redundant on 6.12 — do not run | in-kernel `qcom_pd_mapper` replaces it |

Manual bring-up sequence, for debugging:

```bash
modprobe qcom_q6v5_pas
modprobe qrtr-smd
modprobe rmtfs_mem            # creates /dev/qcom_rmtfs_mem1
rmtfs -P -s &
tqftpserv &
echo start > /sys/class/remoteproc/remoteproc0/state
modprobe ath10k_snoc
```

### The systemd unit

Normal operation does not use the manual sequence. A `tabs6-wifi.service` unit
boots the modem and brings up `ath10k` automatically at boot: load the modules,
start `rmtfs` and `tqftpserv`, start `remoteproc0`, then `modprobe
ath10k_snoc`. NetworkManager takes `wlan0` from there and connects with the
saved profile. From a cold boot this needs no interaction at all.

### Traps

These cost real time, so they are listed individually.

- **`rmtfs` argument flags.** Run it as `rmtfs -P -s`. `-P` is partition mode,
  serving the real EFS partitions from `/dev/disk/by-partlabel`. Do **not** pass
  `-o`: that forces file mode, which cannot open `modemst1`, and the modem then
  never reaches radio init — the run looks deceptively stable while doing
  nothing. Do **not** pass `-r` (SSR fd integration): it fails with
  `Failed to get rprocfd` on this setup.

- **Only one `rmtfs` instance.** A second one hits a `qrtr` bind conflict. This
  is easy to do accidentally when a systemd unit and a shell both start it.

- **`rmtfs` needs `rmtfs_mem` loaded first.** That module creates
  `/dev/qcom_rmtfs_mem1`; without the device node `rmtfs` cannot map its shared
  region. It also needs the dynamic reserved region from section 3.4 intact — if
  something else has shrunk its `alloc-ranges` (an early ramoops experiment did
  exactly this), `rmtfs` fails to remap and the modem never gets its storage.

- **Stale `qrtr.ko` shadows the builtin.** `CONFIG_QRTR` moved from module to
  built-in (the QRTR name service has been in-kernel since 5.7). A leftover
  `qrtr.ko` from an earlier build shadows the builtin:
  `exports duplicate symbol qrtr_endpoint_post`, and `qrtr-smd` gets
  `Exec format error`. Remove the stale `.ko` and re-run `depmod`. General rule
  for this port: after any `m -> y` config change, purge the old modules before
  untarring the new set.

- **Do not run userspace `pd-mapper`.** Kernel 6.12 has an in-kernel
  `qcom_pd_mapper` that auto-spawns with the remoteproc; running the userspace
  daemon on top of it causes duplicate locator registrations. Samsung's modem
  never queries the locator anyway (instrumented: zero queries), and you do not
  need `wlanmdsp.jsn`.

- **`tqftpserv` serves from the remoteproc's own firmware directory**, not the
  `/lib/firmware` root. `wlfw` (service 69) only appears once `wlanmdsp.mbn` has
  been copied into `/lib/firmware/qcom/sm8150/gts6l/` — `translate_readonly`
  resolves relative to the modem's firmware directory.

---

## 7. The root cause: one wrong memory region

This section is the reason the document exists. The bug generalises, and
recognising the pattern is worth more than the specific address.

### What was actually wrong

The board DTS inherited `wlan_mem` from `sm8150.dtsi` at `0x8bc00000`. In
Samsung's memory map that address is `pil_wlan_fw_region` — memory belonging to
the firmware loader, not to HLOS. `ath10k` asks TrustZone to grant the WLAN
hardware read/write on the MSA via `qcom_scm_assign_mem()`. TrustZone refused
with `-22`, and it was right to: HLOS cannot give away memory it does not own.

Everything else was downstream of that single fact:

1. The `-22` was worked around with `qcom,msa-fixed-perm`, which **skips** the
   grant instead of fixing it.
2. So the WLAN hardware had no permission on its own shared memory.
3. `QMI_WLFW_CAL_REPORT_REQ_V01` then told the firmware to run a cold-boot RF
   calibration, whose results are written into the MSA.
4. That first write landed on ungranted memory, faulted at the bus/XPU level,
   and took the entire SoC fabric down in as little as 29 ms — with no ramdump,
   no error path and no log.

The absence of any diagnostic output is not incidental; it is what made this
take days. There was nothing to read, because the fabric died before anything
could write.

### Why `cal_report` looked guilty for so long

A clean four-quadrant bisect using module parameters (BDF push on/off crossed
with cal report on/off) proved that `cal_report` alone was both necessary and
sufficient to lock the SoC, and that the BDF push was innocent. That is a
correct experimental result and it is also completely misleading: `cal_report`
was simply the first operation that touched the unpermitted memory. Killing the
messenger — suppressing `cal_report` — kept the modem alive but never produced
`FW_READY`, because the firmware genuinely needs the calibration step.

The lesson: a bisect tells you which operation trips the failure, not which
condition causes it. When the "culprit" turns out to be a step the system
provably cannot do without, the real cause is in the state that step touches.

### The path that got there

Recorded honestly, including the wrong turns, because the sequence is the
useful part.

1. **Discovered the architecture.** The WCN3990's firmware runs on the modem
   (Q6/mpss), not the apps CPU. So the modem has to boot first: PAS loading the
   Samsung-signed `modem.mdt` from `apnhlos`, `mpss_mem` extended to `0xa000000`
   per the firmware's own ELF program headers, `rmtfs` serving the modem's EFS
   and `tqftpserv` serving file requests.
2. **Hit the ownership bug for the first time, in `rmtfs`.** Its fixed region at
   `0x89b00000` sat inside Samsung-owned memory and would not assign. Making the
   region dynamic fixed it. This was the same bug as the Wi-Fi one, one layer
   down, and recognising the pattern the second time is what solved Wi-Fi.
3. **Built the four-quadrant bisect** described above, which identified
   `cal_report` and exonerated the BDF push.
4. **Read Samsung's downstream `icnss` driver** and found that it sends no
   `host_cap`, no `BDF_DOWNLOAD` and no `CAL_REPORT` at all. Made mainline match
   that sequence exactly: the modem stayed alive, but never reached `FW_READY`.
   That proved the firmware really does need the calibration step, so
   suppressing it was not a fix.
5. **Narrowed to the one remaining difference.** With the message sequence
   matched, the MSA permission assign was the only thing downstream did that we
   did not — which led straight to the question of who owns `0x8bc00000`.
6. **Confirmed with an intermediate experiment.** Deleting `memory-region` so
   that `ath10k` self-allocates the MSA with `dmam_alloc_coherent()` also
   worked, and produced the first-ever `FW_READY`. But it is incompatible with
   the SMMU: self-allocation returns an IOVA, while the modem-side firmware only
   accepts physical addresses (`msa info req rejected: 68`). That experiment
   confirmed the diagnosis while ruling itself out as the fix.

The fixed HLOS-owned carveout at `0xc0000000` is the only configuration that
delivers all three required properties at once: a physical address the modem
firmware will accept, HLOS ownership so the grant succeeds, and SMMU
translation for the copy-engine and HTT DMA rings.

### The generalisable pattern

On a Qualcomm device with a locked-down TrustZone, an SCM assign returning `-22`
is almost never something to work around. It is TrustZone telling you that the
region you are pointing at is not yours. The correct response is to find out who
owns that address in the vendor memory map and move your region somewhere HLOS
actually owns — not to skip the assign, not to delete the `vmid`, and not to
suppress whatever operation happens to trip over the missing permission first.
It cost this port two separate multi-day investigations to learn that once.

---

## 8. Ruled out — do not retry these

Every item here was tested and is not the cause. They are listed so nobody
spends time on them again.

- **Interconnect / NoC bandwidth votes.** `ath10k_snoc` has no interconnect code
  on any device, and Samsung's downstream does not vote for WLAN either.
- **MSA size.** 1 MB versus 1.5 MB was tested directly; 1 MB alone (at the wrong
  address) was actually worse. Size was never the issue — ownership was.
- **`host_cap` with `cal_done=1`.** No effect.
- **The `PIN_CONNECT_RESULT` indication.** Mainline defines it, but this
  firmware never emits it. Waiting for it is a dead end.
- **XO calibration data.** Samsung's device tree has none either.
- **Board data.** All three `bdwlan` revisions (`.bin`, `.bin1`, `.bin2`) and the
  generic `linux-firmware` board data were tried. The repacked container is
  byte-valid. None of this was the fault.
- **`qcom,msa-fixed-perm`.** It was a workaround for the symptom and is actively
  wrong once the region is placed correctly. Remove it.
- **Deleting `memory-region` so `ath10k` self-allocates.** It boots the firmware
  but breaks on the SMMU with `msa info req rejected: 68`. See section 7.
- **Rails and clocks.** All four supplies are real, enabled, and at the correct
  voltages during load (verified with continuous `regulator_summary` capture).
  `rf_clk2` / `rf_clk2_ao` run at 38.4 MHz. Neither was ever implicated.

---

## 9. Known cosmetic issue

The kernel log spams:

```text
chan info: invalid frequency 0 (idx 41 out of bounds)
```

This is harmless. Both bands scan correctly, association works, and throughput
is at full rate. It is noise in the channel-info parsing, not a functional
defect. No fix is needed for a working system; anyone who wants to clean it up
should look at the channel list `ath10k` builds from the firmware's survey
data, not at the DTS.

---

## 10. Summary of what to change

For anyone reproducing this on a similar Samsung SM8150 device, the complete
delta from stock mainline is:

1. `&wlan_mem` relocated to HLOS-owned DDR (`0xc0000000`, size `0x100000`).
2. `&wifi` enabled with the four HDK-reference supplies, and **no**
   `qcom,msa-fixed-perm`.
3. `&mpss_mem` extended to `0xa000000`, with `venus_mem` / `slpi_mem` moved out
   of the way.
4. `&rmtfs_mem` made dynamic with guard pages.
5. Device-extracted `modem.mdt` + segments, `wlanmdsp.mbn`, and a `board-2.bin`
   repacked from the device's own `bdwlan.bin`.
6. `rmtfs -P -s` and `tqftpserv`, wired into a systemd unit alongside the modem
   remoteproc start and the `ath10k_snoc` load.

No kernel patch. No driver change. Stock mainline `ath10k` code paths
throughout.
