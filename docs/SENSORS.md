# Sensors and auto-rotate (SLPI)

Status: **accelerometer works**, desktop auto-rotates. Magnetometer and the
Rotation Vector are discovered by libssc. Light sensor is not offered by the
sensor core. Gyroscope is available in libssc but nothing consumes it.

## Where the sensors are

Nothing sensor-like is on any HLOS bus. Samsung's device tree carries one node,
`qcom,msm-ssc-sensors` with `qcom,firmware-name = "slpi"`. The LSM6DSO
accelerometer+gyro, AK0991x magnetometer and VEML3328 light sensor hang off the
**SLPI** (Sensor Low Power Island), a Hexagon DSP at `0x2400000`, and are read
over QRTR using Qualcomm's SNS protocol (protobuf inside QMI, service 400).

The persist partition names the parts: `/persist/sensors/registry/registry/`
holds `lsm6dso_0.*`, `ak0991x_0.*`, `veml3328_0.*` and calibration.

## The four layers

```
LSM6DSO --i2c--> SLPI (sensor_process)  <--QRTR/QMI svc 400-->  libssc  -->  iio-sensor-proxy  -->  tabs6-autorotate  -->  kscreen-doctor
                       ^
                       +-- FastRPC reverse tunnel <-- hexagonrpcd (serves registry + config from /usr/share/qcom)
```

### 1. Booting the SLPI (device tree only)

Firmware: `slpi.mdt` + 20 segments on the `apnhlos` FAT partition (sda16),
6.7 MB, ELF32, loadable span exactly 20 MB. Copied to
`/lib/firmware/qcom/sm8150/gts6l/`.

`sm8150.dtsi` already has `remoteproc_slpi` (`qcom,sm8150-slpi-pas`); enabling
it is `status = "okay"` + `firmware-name`. `qcom_q6v5_pas` is a module, so no
kernel rebuild.

The memory region is the whole problem. Same TZ rules as the ADSP (see
AUDIO.md: region >= ELF span, no overlap with anything TZ owns) **plus a third
one**: the region must lie inside the firmware zone.

| slpi_mem | result |
| --- | --- |
| 0x9f800000 + 20 MB (free in both maps, just past the last firmware region) | `error -22 setting up firmware` |
| 0x9b000000 + 20 MB (inside the zone, ends at the framebuffer) | **boots** |

Downstream's own address, 0x97300000, sits inside the 160 MB modem span on this
firmware, so it cannot be reused. To make room: `adsp_mem` trimmed to its exact
39 MB span (already proven accepted), the unused `venus_mem` reservation moved
to 0x9f800000, `slpi_mem = 0x9b000000/0x1400000`.

### 2. FastRPC without an SMMU context bank

Idle, the SLPI is stable. Any FastRPC attach (hexagonrpcd, with or without
`-s`) killed it instantly:

```
arm-smmu 15000000.iommu: Unhandled context fault: fsr=0x402, iova=0x1fffff000, cbfrsynra=0x5a1, cb=9
qcom_q6v5_pas 2400000.remoteproc: fatal error received: err_qdi.c:964:EX:sensor_process:0x1:frpck_0_0:0x5b
```

`sm8150.dtsi` gives the SLPI fastrpc node three `compute-cb@N` children with
`iommus = <&apps_smmu 0x5a1..0x5a3>`. The sensor process is hypervisor-bypassed
on the SMMU and uses physical addresses; attaching translated context banks to
its stream IDs makes its first access fault. `sdm845.dtsi`, the only upstream
SLPI with working sensors, does it differently and that is what works here:

```dts
fastrpc {
	qcom,vmids = <QCOM_SCM_VMID_HLOS QCOM_SCM_VMID_MSS_MSA
		      QCOM_SCM_VMID_SSC_Q6 QCOM_SCM_VMID_ADSP_Q6>;
	memory-region = <&fastrpc_mem>;
	/delete-node/ compute-cb@1; /delete-node/ compute-cb@2; /delete-node/ compute-cb@3;
	compute-cb@0 { compatible = "qcom,fastrpc-compute-cb"; reg = <0>; };
};
fastrpc_mem: fastrpc {
	compatible = "shared-dma-pool";
	alloc-ranges = <0x0 0xb0000000 0x0 0x40000000>;
	alignment = <0x0 0x400000>;
	size = <0x0 0x1000000>;
	reusable;
};
```

The 6.12 `fastrpc.ko` already understands `qcom,vmids` + `memory-region`
(`assigned reserved memory node fastrpc` in dmesg). Samsung TZ accepted the
assign because the pool is in HLOS-owned RAM.

### 3. hexagonrpcd

Built from https://github.com/linux-msm/hexagonrpc, installed under
`/usr/local`. Ships `hexagonrpcd-sdsp.service` (`User=fastrpc`, install to
`/etc/systemd/system/`, create the `fastrpc` user). Two local additions:

- `userspace/sensors/90-fastrpc.rules`: `/dev/fastrpc-*` is 0600 root by
  default; the rule makes it `fastrpc` group 0660.
- `userspace/sensors/hexagonrpcd-sdsp-override.conf`: adds
  `-R /usr/share/qcom/sm8150/Samsung/gts6lwifi`.

That tree is populated from the tablet's own Android partitions and must be
world readable (`chmod -R a+rX`), or the DSP asks for forty registry files and
gets "Permission denied" on each:

| in the tree | from |
| --- | --- |
| `sensors/config/` | `/vendor/etc/sensors/config/` |
| `sensors/registry/` | `/persist/sensors/registry/registry/*` |
| `sensors/sns_reg.conf` | `/vendor/etc/sensors/sns_reg_config` |
| `socinfo/` | copies of `/sys/devices/soc0/*` |
| `dsp/` | `/vendor/dsp/` (empty here) |

Registry and calibration are device identity; they are not in the repo.

### 4. libssc + iio-sensor-proxy 3.9

Fedora's iio-sensor-proxy 3.8 is built without SSC support and there is no
Fedora libssc package. Both built on the tablet:

- libssc 0.4.4 (https://codeberg.org/DylanVanAssche/libssc), needs
  `python3-devel` or meson dies with "Python dependency not found". Installed
  to `/usr/local` (add `/usr/local/lib64` to `ld.so.conf.d`).
- iio-sensor-proxy 3.9 with `-Dssc-support=enabled`, prefix `/usr/local`, and
  `userspace/sensors/iio-sensor-proxy-override.conf` pointing the Fedora unit
  at the `/usr/local` binary and adding `AF_QIPCRTR` to
  `RestrictAddressFamilies`. Upgrade-proof: RPM can replace the binary and unit
  in `/usr` without touching either.

Upstream's udev rule only tags `fastrpc-*` with `ssc-light ssc-compass`, so the
SSC accelerometer driver never probes. `userspace/sensors/81-tabs6-ssc-accel.rules`
adds `ssc-accel` for `fastrpc-sdsp`. Then:

```
$ monitor-sensor --accel
=== Has accelerometer (orientation: undefined, tilt: undefined)
    Tilt changed: face-up
```

### 5. Auto-rotate in KDE

KWin only auto-rotates outputs it believes are internal panels (eDP/DSI).
simpledrm's connector type is "Unknown", so KScreen says
`Auto Rotate Policy: incapable` regardless of the sensor.

`userspace/sensors/tabs6-autorotate` (python, user session) claims the
accelerometer on `net.hadess.SensorProxy`, maps orientation to a
`kscreen-doctor output.<name>.rotation.<rot>` call with a 400 ms debounce, and
ignores `undefined` (tablet flat). Unit: `/etc/systemd/user/tabs6-autorotate.service`,
`WantedBy=plasma-workspace.target`.

The IMU is mounted 180 degrees from what iio-sensor-proxy assumes ("turn left,
desktop goes right"), so the drop-in `tabs6-autorotate-map.conf` sets
`TABS6_ROTATE_MAP=normal=inverted,bottom-up=none,left-up=right,right-up=left`.
Touch `~/.config/tabs6-autorotate.lock` to freeze rotation, or use the tray
toggle: `userspace/sensors/tabs6-rotate-tray.py` puts a rotation-lock icon in
the system tray (StatusNotifierItem over D-Bus, same approach as the screenshot
button, because Qt's tray icon silently never appears here). One tap toggles the
lock file and swaps the icon between `rotation-allowed` and
`rotation-locked-landscape`; unlocking sends the daemon SIGUSR1 so it snaps to
the current orientation immediately. Unit: `tabs6-rotate-tray.service`.

## Boot order and crash recovery

hexagonrpcd is wanted by multi-user.target and starts as soon as
`/dev/fastrpc-sdsp` exists; iio-sensor-proxy is pulled in by udev and polls the
registry up to 100 times.

**The SLPI dies on every resume from suspend** (`PM: suspend exit` and
`sensor_process ... frpc_dsp:0x6f` in the same millisecond), and once
spontaneously 48 minutes into a session (`frpc_dsp:0x6e`, cause unknown). remoteproc recovers it in
under a second and hexagonrpcd re-attaches (`Restart=always`), but
**iio-sensor-proxy never re-discovers a sensor whose QRTR node vanished**
("Closing SSC accelerometer sensor failed: QRTR node was removed from the bus",
then `HasAccelerometer=false` for good). Two pieces make the chain self-heal:

- `82-tabs6-sensors-recover.rules` + `tabs6-sensors-recover.service`: every
  add of `/dev/fastrpc-sdsp` (boot, or a recovery) runs a oneshot that waits
  8 s for hexagonrpcd to re-attach and serve the registry, then
  `try-restart`s iio-sensor-proxy.
- `tabs6-autorotate` does not bind to one proxy object: it matches
  `PropertiesChanged` by path, watches the `net.hadess.SensorProxy` name owner,
  and re-claims the accelerometer whenever the name (re)appears or
  `HasAccelerometer` flips true, and retries every 2 s while unclaimed, because
  a freshly restarted proxy answers `HasAccelerometer=false` for about a second
  and does not reliably signal the flip. Verified at boot: recover unit fires at +8 s,
  daemon logs "iio-sensor-proxy is up ... claimed accelerometer".

After recovery the DSP also tries to write registry files (`sns_reg_config`,
`sns_tilt`, `ccd_*`); hexagonrpcd refuses ("Tried to open ... for writing")
and nothing breaks.

**Do not try to simulate a crash.** `echo stop`/`start` into
`/sys/class/remoteproc/remoteproc0/state` returns 0 and does nothing here, and
`echo 1 > /sys/kernel/debug/remoteproc/remoteproc0/crash` takes the watchdog
path, after which PAS start times out (-110) and the SLPI stays offline until a
reboot.

## Not done

- Light sensor: libssc asks for `ambient_light` and the core says none. The
  VEML3328 registry entries exist; untested whether the config needs enabling.
- Gyroscope/magnetometer: available through libssc, no consumer.
- The 0x1fffff000 fault is worth reporting upstream against `sm8150.dtsi`.
