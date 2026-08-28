# Firmware — extract from your own device (nothing is shipped here)

This project needs a few Samsung/Qualcomm binaries that are **device‑specific and
proprietary**. They are deliberately **not** in this repo. Extract them from your
own SM‑T860 and keep them private.

## What you need and where it comes from

### Boot handoff

| File | What it is | Source |
|---|---|---|
| `stock-boot.img` | your stock `boot` partition (patch target) | `dd` from `/dev/block/by-name/boot` on a rooted device, or unpack the stock `AP` tar (`boot.img.lz4`) |
| `SM8150_EFI_NOSB.fd` | Project Aloha SM8150 UEFI firmware volume | build/download from [Project Aloha CI](https://github.com/Project-Aloha/mu_aloha_platforms) for `samsung-gts6lwifi` |
| `vbmeta` (flags 3) | verification‑disabled vbmeta | produced by Magisk when patching, or `avbtool make_vbmeta_image --flags 3` |
| stock Odin firmware | rollback safety net | your exact build (e.g. `T860XXS5DWH1`) from a firmware mirror |

### Runtime device firmware (extract from YOUR device's own partitions)

These are the device‑specific blobs the mainline drivers load. They are on the
Android `vendor`/`apnhlos`/`modem` partitions and are Samsung‑signed — the generic
copies in `linux-firmware` are **not** accepted by this device's TrustZone (GPU zap)
or don't match its radio calibration (Wi‑Fi board data). Mount the partitions
read‑only from a running Linux (or `dd` + loop‑mount) and copy:

| File(s) | For | Source partition/path |
|---|---|---|
| `a640_zap.mdt` + `.b00/.b01/.b02` | GPU zap shader (TZ‑signed) | `apnhlos` (sda16) `:/image/` → `/lib/firmware/qcom/sm8150/<board>/` |
| `a630_sqe.fw`, `a640_gmu.bin` | GPU microcode/GMU | `linux-firmware` (`unxz` if `.xz`; a640 uses the a630 SQE) |
| `modem.mdt` + `modem.b*` | modem (mpss) firmware, PAS | `apnhlos`/`modem` (sda17) `:/image/` → `/lib/firmware/qcom/sm8150/<board>/` |
| `wlanmdsp.mbn` | modem‑resident WLAN firmware | `vendor` (sda25) `:/firmware/` → `/lib/firmware/` |
| `bdwlan.bin` (+ `.bin1/.bin2`), `regdb.bin` | WCN3990 board data / regulatory | `vendor` `:/firmware/wlan/qca_cld/` — repack `bdwlan.bin` into an ath10k `board-2.bin` with `ath10k-bdencoder` |
| `board-2.bin`, `firmware-5.bin` | ath10k WCN3990 base | `linux-firmware` `ath10k/WCN3990/hw1.0/` (`unxz` if `.xz`) |

> The GPU and Wi‑Fi both fail silently or crash if you use the generic firmware:
> Samsung's TZ rejects the Qualcomm test‑signed zap shader, and the generic ath10k
> `board-2.bin` has no calibration entry for this device's radio. Always prefer the
> blobs from your own device. See [`../docs/GPU.md`](../docs/GPU.md) and
> [`../docs/WIFI.md`](../docs/WIFI.md).

## Extracting your stock boot (rooted)

```bash
adb shell su -c 'dd if=/dev/block/by-name/boot of=/sdcard/stock-boot.img'
adb pull /sdcard/stock-boot.img
sha256sum stock-boot.img     # record it
```

## Never publish

`efs`, `sec_efs`, `modemst*`, `modem`, `persist`, Qualcomm security partitions,
and anything containing your IMEI, serial, or per‑device keys. These identify your
hardware and can be abused. Keep this whole directory out of git (it is
`.gitignore`d).

## Firmware safety

The Project Aloha / SM8150 tutorials warn that using firmware from the **wrong**
device can permanently damage the PMIC. Only ever use binaries extracted from a
`gts6lwifi` (SM‑T860). Never flash BL/XBL/ABL/TZ/modem from another device.
