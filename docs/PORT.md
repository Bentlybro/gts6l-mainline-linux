# Tab S6 (`gts6lwifi`) hardware map and port status

SM8150P v2 / Snapdragon 855. Addresses taken from the device's own extracted stock
device tree (`msm-id 0x169/0x20000`, `board-id 0x10008/0x00000002`). Upstream mainline
already has the SM8150 SoC dtsi and the Adreno/DRM/ath10k paths; there is no ready
`gts6lwifi` board DTS upstream, so `kernel/dts/sm8150-samsung-gts6lwifi.dts` is ours.

## Boot chain (working)

```text
AArch64 XBL → 32-bit ARM ABL → stock boot container (Aloha UEFI FD injected)
  → edk2/Project-Mu UEFI → BDS scans FAT for \EFI\BOOT\BOOTAA64.EFI
  → systemd-boot (on the cache ESP, sda27) → mainline EFI-stub Image + gts6lwifi DTB
```

Enter fastboot: hold **Volume-Down** at the UEFI boot screen. Iterate by re-flashing
the cache ESP (`fastboot flash cache <esp>.img`) or, once SSH is up, by loop-mounting
the cache ESP on the running tablet. See [`BOOT_METHOD.md`](BOOT_METHOD.md).

## Block addresses (from the device's own DT)

| Block | Address | Notes |
|---|---|---|
| USB primary / DWC3 | `0xa600000` | first lifeline (ACM serial + RNDIS net); `dr_mode = peripheral` |
| UFS controller | `0x1d84000` | internal storage / root — see [`UFS.md`](UFS.md) |
| UFS PHY / ICE | `0x1d87000` / `0x1d90000` | |
| MDSS top / DPU | `0xae00000` | **left disabled** — simpledrm rides the bootloader framebuffer instead |
| DSI0 | `0xae94000` | dual-DSI ANA38401 panel link (native path is a future quest) |
| Adreno 640 GPU | `0x2c00000` | render-only via `msm`/freedreno — see [`GPU.md`](GPU.md) |
| Adreno GMU | `0x2c6a000` | |
| Touch i2c (QUP2 SE17) | `0xc80000` | STM FTS1BA90A @0x49 — see [`TOUCH.md`](TOUCH.md) |
| GPI DMA (QUP2) | `0xc00000` | **must be enabled** — SE17 is GSI-only |
| WCN3990 Wi-Fi | `0x18800000` | ath10k_snoc / SNOC — see [`WIFI.md`](WIFI.md) |
| mpss remoteproc (modem Q6) | `0x4080000` | hosts the WLAN firmware (PAS) |
| WLAN MSA (`wlan_mem`) | `0xc0000000` | 1 MB, moved into HLOS-owned DDR; the `sm8150.dtsi` default at `0x8bc00000` is Samsung's firmware-owned `pil_wlan_fw_region` |

> **Correction from earlier notes:** the Wi-Fi is a **WCN3990** at `0x18800000`
> (ath10k_snoc), *not* a QCA6390 at `0xa0000000`. The `0xa0000000` `qcom,wil6210`/
> QCA6390 node in the stock DT is for an unfitted part; ignore it.

The bootloader framebuffer (what simpledrm binds to) is at `0x9c400000`, 2560×1600,
stride `2560*4`, format `a8r8g8b8`.

Display panel: dual-DSI **ANA38401 / AMSA05RB06** WQXGA, 2×1280×1600 command mode, no
DSC (from the device's downstream panel node).

## Per-subsystem status

| Subsystem | Status | Notes / next step |
|---|---|---|
| Boot handoff (ABL → Aloha UEFI → systemd-boot) | ✅ | [`BOOT_METHOD.md`](BOOT_METHOD.md) |
| UFS internal storage / root | ✅ | `BRANCH_HALT_SKIP` clock fix — [`UFS.md`](UFS.md) |
| USB DWC3 + gadget (SSH/serial lifeline) | ✅ | RNDIS+ACM — [`USB_NETWORKING.md`](USB_NETWORKING.md) |
| Display (KDE Plasma visible) | ✅ | simpledrm on bootloader FB — [`DISPLAY.md`](DISPLAY.md) |
| Multitouch | ✅ | fts1ba90a + GPI-DMA — [`TOUCH.md`](TOUCH.md) |
| Adreno 640 GPU acceleration | ✅ | render-only + Mesa kmsro — [`GPU.md`](GPU.md) |
| Fedora 44 + KDE Plasma on UFS | ✅ | Wayland session, autologin |
| Wi-Fi (WCN3990) | ✅ | 802.11ac, 866.7 MBit/s link (VHT-MCS 9, 80 MHz, 2 streams); auto-connects at boot — [`WIFI.md`](WIFI.md) |
| Native display / brightness / DPMS | 🚧 | needs ≥6.16 bonded-cmd-mode DPU; 6.18 tree staged |
| Battery level reporting | ✅ | now from the **SM5705 fuel gauge** (below). The earlier `VPH_PWR`-on-pm8150-ADC estimate is superseded and its `adc-battery` node is deleted — [`BATTERY.md`](BATTERY.md) |
| Power button (lock / wake / menu) | ✅ | PMIC PON KPDPWR, not a GPIO — [`POWER.md`](POWER.md) |
| Shutdown + reboot | ✅ | via PMIC PS_HOLD from a reboot notifier; **PSCI `SYSTEM_OFF`/`SYSTEM_RESET` both hang here** and must be overtaken. SMPL disarmed or it powers straight back up — [`POWER.md`](POWER.md) |
| Suspend / resume / idle sleep | ✅ | s2idle, resume in 1.5 s (was 6.5 s until the touchscreen echo timeout was fixed); the touchscreen survives it and the power button wakes it. Fedora ships `sleep.target`/`suspend.target` **masked**, which logind reports misleadingly as "Access denied". PowerDevil ignores its own profile config (on 6.6.4 *and* 6.7.4), so idle sleep is `tools/tabs6-idled.py` — [`SLEEP.md`](SLEEP.md) |
| Screenshots | ✅ | power + volume-down chord, and a system-tray button; both copy to the clipboard — [`DESKTOP.md`](DESKTOP.md) |
| RTC / system clock | 🚧 | reads and alarms work on `rtc-pm8xxx`, which is what suspend needs. `CONFIG_RTC_DRV_EFI` must stay **off** — rtc-efi claimed `rtc0` and could not be read at all, which made `timedatectl` fail outright. The counter can never be written: the SPMI arbiter denies writes to `0x6046`. systemd-timesyncd covers the gap — [`SLEEP.md`](SLEEP.md) |
| Fuel gauge + charge detection | ✅ | **SM5705** on I²C, not the Qualcomm PMIC — real SOC, OCV, voltage and current, so charging is detected properly. Driver written from scratch, `kernel/drivers/sm5705_fuelgauge.c` — [`BATTERY.md`](BATTERY.md) |
| USB host (keyboard, SSD, hub) | ✅ | `dr_mode = otg` + role switch + VBUS from the SM5705 boost; `usb-role host`. Runs at USB 2.0 only — SuperSpeed unsolved, see [`USB_HOST.md`](USB_HOST.md) |
| USB SuperSpeed | 🚧 | host, PHY and redriver all check out; orientation exhausted. Prime suspects: the cable, then `phy-qcom-qmp-combo.c` pinning orientation to NORMAL with no Type-C port manager to correct it |
| Charge control / charging current | ✅ | 2000 mA input / 2000 mA into the battery, maintained (the registers reset when the cable moves) by `tools/tabs6-charge.sh`. AICL walks it back on a weak supply, so overshooting is safe — [`BATTERY.md`](BATTERY.md) |
| S Pen (Wacom W9021) | ⬜ | wacom@0x56 on i2c14, irq gpio 5, pdct 53, fwe 11 |
| Bluetooth (WCN3990 UART) | ✅ | `hci_qca` over `serial@c8c000` (QUP2 SE3), a UART personality mainline never declared. Needs an alias for the port index, the device's own crbtfw21/crnv21 under the name the driver derives, and a BD address from `/efs/bluetooth/bt_addr` — [`BLUETOOTH.md`](BLUETOOTH.md) |
| Audio (speakers) | ✅ | ADSP + APR + Secondary TDM to four `cirrus,cs35l41` amps (I2C 0x40-0x43), ALSA UCM profile for the desktop sink — [`AUDIO.md`](AUDIO.md) |
| Mic codec (CS48L33) | ✅ | probes and registers its DAIs. 6.18 gained a CS48L32 driver, backported to 6.12; the CS48L33 differs only by the part number in DEVID (0x48a33 vs 0x48a32) — [`AUDIO.md`](AUDIO.md) |
| Microphones (ALSA capture) | ✅ | Quinary MI2S from cs48l32-asp1, codec FLL1/SYSCLK, MICBIAS routed via audio-routing, analogue PGA gain — [`AUDIO.md`](AUDIO.md) |
| Microphone as a PipeWire source | 🚧 | a UCM capture device breaks the whole card and drops the sink to Dummy Output; cause not found — [`AUDIO.md`](AUDIO.md) |
| Audio (headphones) | ⬜ | not started |
| Hardware buttons | ✅ | power = PMIC PON KPDPWR, volume down = PON RESIN, volume up = **`&pm8150l_gpios 12`** active low with a pull up. The inherited Surface Duo `&pm8150_gpios 6` produced no evdev events at all, which reads as a desktop bug and is not one. The cell is 1-based: spmi-gpio `of_xlate` subtracts `PMIC_GPIO_PHYSICAL_OFFSET` — [`DESKTOP.md`](DESKTOP.md) |
| Boot time | ✅ | ~38 s cold power-on to desktop. Controllable time 56.6 s → 23.9 s, `graphical.target` 46.95 s → 17.44 s. Two of our own units had ordering bugs and the console was rendering 561 debug prints per boot into an unaccelerated 2560x1600 framebuffer — see the boot section of [`DEVLOG.md`](DEVLOG.md) |

Current state: the tablet is a self-sufficient machine. It boots from its own internal
storage into Fedora 44 + KDE Plasma with working touch, GPU acceleration and its own
Wi-Fi, and it is reachable over SSH without the USB lifeline attached. Wi-Fi needs no
driver patch — the fix was a single device-tree line relocating `wlan_mem` into DDR that
HLOS actually owns, after which stock mainline `ath10k_snoc` completes the QMI handshake
and `wlan0` comes up.

Two subsystems are still in progress. The **native display pipe** — DPU/DSI driving the
ANA38401 panel directly — is what *hardware* brightness and DPMS would depend on; until
it lands the panel runs at the bootloader's fixed backlight level. In practice this
matters less than it sounds: KWin dims in software and blanks to black on idle, and on an
AMOLED a black frame is pixels not emitting rather than a backlight shining through, so
the screen does effectively blank and the power cost is small. The other is **USB
SuperSpeed**, which still negotiates only high speed.

S Pen is not started rather than blocked — nothing found so far
suggests they need anything the other subsystems did not.

## Lessons carried from the S20 (`z3s`) project (all held true here)

- Read the device's own extracted DT first; never guess hardware.
- USB ACM/RNDIS + serial as the first lifeline; keep networking unmanaged so a bad
  rootfs can't lock you out.
- Backup + hash before every flash; read back after every write.
- One subsystem per boot; verify the built artifact before shipping it.
- Never touch the bootloader display pipe — simpledrm + a render-only GPU is the
  daily-driver topology.
- Never use `/dev/mem` as a substitute for real DT work.

## SM8150-specific gotchas discovered here

- The Aloha/Samsung TrustZone firmware **lies about clock halt-status bits** — UFS/USB
  clocks report "stuck off" while actually running (fix: `BRANCH_HALT_SKIP`).
- **GPIO0 is TZ-protected** — reading it hangs pinctrl (`gpio-reserved-ranges = <0 4>`).
- A **serial console with no cable attached blocks every later printk forever** —
  disable it entirely (`console=tty0` only).
- **simpledrm is fixed-mode** — reconfiguring resolution/scale hard-crashes the SoC.
- The stock kernel config here shipped **without `CONFIG_ZRAM`**, so Fedora's
  zram-generator silently produced no swap at all. On a 4.8 GiB device that hurts;
  enabling ZRAM/ZSWAP and configuring zstd-compressed swap is worth doing early. Note
  that Kconfig **force-selects LZO** (`CONFIG_ZRAM_BACKEND_FORCE_LZO`) when no backend
  is picked explicitly, so `compression-algorithm = zstd` then quietly falls back to
  lzo-rle — enable `CONFIG_ZRAM_BACKEND_ZSTD` and confirm with
  `cat /sys/block/zram0/comp_algorithm` (the active one is in `[brackets]`).
- **The ESP cannot be mounted directly**, which matters because every kernel update
  goes through it. `mount /dev/sda27` fails with *"FAT-fs: logical sector size too
  small for device (logical sector size = 512)"*: the filesystem was created with
  512-byte logical sectors and UFS reports 4096, a combination the FAT driver refuses.
  Go through a loop device, which presents 512-byte sectors regardless — and always
  with a cleanup trap, because a stale loop device on the ESP will leave the tablet
  wedged mid-shutdown, unable to remount `/` read-only:

  ```bash
  LOOP=$(losetup -f --show /dev/sda27); mount "$LOOP" /mnt/esp
  ...; umount /mnt/esp; losetup -d "$LOOP"
  ```

  The ESP filesystem is also only 95 MB even though the cache partition is 400 MB, and
  the kernel `Image` alone is ~38 MB — there is room for one Image and little else, so
  keep the rollback copy off the ESP.

  And the trap on the other side of that: the boot chain reads **only** the cache ESP,
  while Fedora's own kernel packages install into `/boot` on the root filesystem, which
  nothing in the boot path ever looks at. Two distro kernels (6.19.10 and 7.1.10) are
  sitting there right now, installed and never booted, while the running kernel is our
  own build on the 6.12 base. `dnf` updating the kernel and rebooting therefore changes
  nothing — only an `Image` copied onto the ESP takes effect. Verify with `uname -v`
  afterwards, never with the package list.
- **SPMI PMIC children are not instantiated** unless `MFD_SPMI_PMIC` is enabled. The
  PMICs enumerate on the bus regardless, which makes it look like SPMI is fine while
  every PMIC function (ADC, battery, RTC) is silently absent.
- This **bootloader clears/retrains DDR on reboot** — persistent-RAM crash capture
  (ramoops) does not survive, which complicates debugging hard SoC locks.
- **A `reserved-memory` region inherited from `sm8150.dtsi` may sit in memory the
  firmware owns, not HLOS.** `qcom_scm_assign_mem()` then returns `-22` (EINVAL),
  because HLOS cannot grant away memory it does not hold. Check the address against
  Samsung's own memory map before assuming the inherited default is usable, and fix it
  by relocating the region into ordinary DDR rather than by working around the failed
  assign. This bit us twice: first `rmtfs_mem`, fixed at `0x89b00000` inside a
  Samsung-owned range (made dynamic instead), and then the WLAN MSA `wlan_mem` at
  `0x8bc00000`, which is Samsung's `pil_wlan_fw_region` (moved to `0xc0000000`). The
  second case was near-silent: skipping the grant left the hardware unpermitted on its
  own shared memory, and the firmware's first write during RF calibration took the SoC
  fabric down in about 29 ms with no ramdump and no log.
