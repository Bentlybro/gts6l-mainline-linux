# Tab S6 (`gts6lwifi`) hardware map and port status

SM8150P v2 / Snapdragon 855. Addresses taken from the active stock device tree
(`msm-id 0x169/0x20000`, `board-id 0x10008/0x00000002`). Upstream mainline already
has the SM8150 SoC dtsi and the Adreno/DRM path; there is no ready `gts6lwifi`
board DTS upstream, so `kernel/dts/sm8150-samsung-gts6lwifi.dts` is ours.

## Boot chain

```text
AArch64 XBL  ->  32-bit ARM ABL  ->  boot (Android boot image v1)
```

The 32‑bit ABL does not mean a 32‑bit kernel; it means a proposed loader must
match ABL's Android‑image hand‑off contract. See [`BOOT_METHOD.md`](BOOT_METHOD.md).

## Block addresses (from stock DT)

| Block | Address | Notes |
|---|---|---|
| PMIC SPMI arbiter | `0xc440000` | |
| USB primary / DWC3 | `0xa600000` | `androidboot.usbcontroller=a600000.dwc3`; first lifeline (ACM/RNDIS) |
| UFS controller | `0x1d84000` | internal storage / root |
| UFS PHY | `0x1d87000` | |
| UFS ICE | `0x1d90000` | |
| SDHCI | `0x8804000` | microSD |
| MDSS | `0xae00000` | display top |
| DSI0 | `0xae94000` | panel link |
| CNSS (QCA6390‑class) | `0xa0000000` | Wi‑Fi/BT |

Memory: live Android reports `MemTotal ≈ 5,583,396 kB`. The stock `/memory` node is
zero‑sized (Samsung patches it at runtime); mainline needs an explicit range
(`reg = <0x0 0x80000000 0x1 0x80000000>`, i.e. 6 GiB at 0x80000000).

Display panel: ANA38401 / AMSA05RB06 WQXGA (from vendor QDCM calibration).
WLAN: QCA6390‑class under `/firmware/wlan/qca_cld/`.

## Per‑subsystem status

| Subsystem | Status | Blocker / next step |
|---|---|---|
| Boot handoff (ABL → UEFI) | in progress | corrected image built; awaiting device test — [`BOOT_METHOD.md`](BOOT_METHOD.md) |
| Mainline kernel `Image` + board DTB | builds | not yet handed off by UEFI LinuxLoader |
| USB DWC3 (ACM/RNDIS lifeline) | planned | the first observability path once a kernel runs |
| UFS storage / root | planned | |
| MDSS / DSI / panel | planned | ANA38401 panel graph + firmware |
| Touch / S Pen | planned | |
| Adreno 640 (Freedreno) | planned | after display |
| Wi‑Fi / BT (QCA6390) | planned | firmware extraction |
| Fedora + KDE Plasma | design | on‑screen keyboard / tablet mode decision |

## Lessons carried from the S20 (`z3s`) project

- Stock DT/DTBO first, never hardware guesses.
- USB ACM/RNDIS + serial as the first lifeline; keep networking unmanaged so a bad
  rootfs can't lock you out.
- Backup + hash before every flash; read back after every write.
- One subsystem per boot.
- Never use `/dev/mem` as a substitute for real DT work.
- Build gently (`nice -n 19 make -j3`), never `-j$(nproc)`.
