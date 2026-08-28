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
| Wi-Fi (WCN3990) | 🚧 | modem + QMI handshake work; fw crashes on RF init — [`WIFI.md`](WIFI.md) |
| Native display / brightness / DPMS | 🚧 | needs ≥6.16 bonded-cmd-mode DPU; 6.18 tree staged |
| S Pen (Wacom W9021) | ⬜ | wacom@0x56 on i2c14, irq gpio 5, pdct 53, fwe 11 |
| Bluetooth (WCN3990 UART) | ⬜ | |
| Audio | ⬜ | |

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
- This **bootloader clears/retrains DDR on reboot** — persistent-RAM crash capture
  (ramoops) does not survive, which complicates debugging hard SoC locks.
