# Firmware — extract from your own device (nothing is shipped here)

This project needs a few Samsung/Qualcomm binaries that are **device‑specific and
proprietary**. They are deliberately **not** in this repo. Extract them from your
own SM‑T860 and keep them private.

## What you need and where it comes from

| File | What it is | Source |
|---|---|---|
| `stock-boot.img` | your stock `boot` partition (patch target) | `dd` from `/dev/block/by-name/boot` on a rooted device, or unpack the stock `AP` tar (`boot.img.lz4`) |
| `SM8150_EFI_NOSB.fd` | Project Aloha SM8150 UEFI firmware volume | build/download from [Project Aloha CI](https://github.com/Project-Aloha/mu_aloha_platforms) for `samsung-gts6lwifi` |
| `vbmeta` (flags 3) | verification‑disabled vbmeta | produced by Magisk when patching, or `avbtool make_vbmeta_image --flags 3` |
| stock Odin firmware | rollback safety net | your exact build (e.g. `T860XXS5DWH1`) from a firmware mirror |

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
