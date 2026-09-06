# Daily driver hardening

What separates "it works" from "I would take it instead of the laptop": trust
that an update cannot brick it, battery life, and surviving sleep. This page
tracks that work. Started 2026-09-06.

## Rollback: last known good kernel

The ESP (a 95 MB FAT on the `cache` partition, `sda27`, loop-mounted because
of the 512 byte sector mismatch) holds two kernels:

| entry | files | purpose |
| --- | --- | --- |
| `gts6` (default) | `/Image`, `/gts6.dtb` | current |
| `gts6-lastgood` "LAST KNOWN GOOD" | `/Image-lastgood`, `/gts6-lastgood.dtb` | proven fallback |

Both report `6.12.0`, so the module set is snapshotted separately in
`/lib/modules/6.12.0-lastgood`. `tools/tabs6-kernel` manages it:

```bash
tabs6-kernel status     # what is on the ESP, sizes, md5, which modules match
tabs6-kernel snapshot   # mark the CURRENT kernel+dtb+modules as last known good
tabs6-kernel rollback   # put last known good back as the default (keeps *-broken)
```

Rule: snapshot only after a kernel has survived a reboot, never right after
installing it. To boot the fallback without a shell, hold volume-down at power
on for the systemd-boot menu.

The ESP has ~19 MB free after this; a third Image does not fit. Kernel
backups from bring-up still litter `/root` and should be pruned.

## Power measurement

`userspace/tabs6-powerlog` writes one line to the journal from the SM5705 fuel
gauge (`cap= V= I= P=`), every 5 minutes by timer and from a systemd-sleep hook
as `sleep-pre` / `sleep-post`, so idle drain and suspend drain are read out of
the journal rather than guessed:

```bash
journalctl -t tabs6-powerlog -u tabs6-powerlog --no-pager -o cat
```

First numbers (2 s fuel gauge samples, Firefox open but idle):

| state | mean |
| --- | --- |
| screen on, idle | ~2.0 W (2.77 W with a page animating) |
| DPMS off | ~1.9 W |

Battery is ~27 Wh, so roughly 13 h screen-on idle in theory; real use will be
well under that. The panel runs at full backlight under software dimming
(hardware brightness needs the native display pipe, see DISPLAY.md), so the
display's true cost is not yet measured cleanly; the fuel gauge current is
noisy enough that 20 s A/B tests cannot resolve differences under ~0.3 W.

Tried and reverted, no measurable effect: disabling UFS clock scaling
(`clkscale_enable`), raising GPU devfreq polling from 50 to 250 ms.

## GPU: yes, it is really accelerated

Checked because the desktop load average looked high:

- `eglinfo`: OpenGL ES 3.2 / GL 4.6 on `FD640`; llvmpipe only as the unused
  fallback profile; no software-render environment anywhere.
- KWin, plasmashell, Xwayland, maliit and Firefox all hold `renderD128`.
- devfreq `simple_ondemand` 257..585 MHz, ramps under load.
- `glmark2-es2-wayland --fullscreen` at 1600x2560: **score 848** (build 1245,
  texture 925, shading 1018, bump 1100, effect2d 742, terrain 69 fps), GPU at
  585 MHz for most of the run.
- Truly idle: 0.0% GPU busy (msm fdinfo), 0 gpu-irq/s, minimum clock.

The high load was Firefox with a live page: the msm driver's per-job overhead
(~1000 gpu-irq/s and ~1200 RPMh votes/s while a page animates) shows up as
kworkers. Video decode is on the CPU: no venus, and Firefox would not use a
V4L2 decoder anyway.

## Suspend / resume survival (rtcwake 90 s test)

Survives: Wi-Fi (reassociates in ~2 s), Bluetooth, modem, ADSP and the speaker
sink, touch. Does not survive: the SLPI, which crashes at `PM: suspend exit`
every time and is recovered by remoteproc plus the udev chain in SENSORS.md
(~10 s without auto-rotate after wake).

Two log lines at every resume are noise: `qcom_scm: Assign memory protection
call failed -22` (fastrpc re-assigning its pool after the SLPI recovery, and
ath10k re-assigning `wlan_mem`; both already assigned) and
`dwc3-qcom: port-1 HS-PHY not in L2` (no cable).

### Do not stop the SLPI by hand

`echo stop > /sys/class/remoteproc/remoteproc0/state` is a refcount decrement:
every `echo start` issued on an already running remoteproc bumps
`rproc->power`, so a stop may silently do nothing. When it does stop, the
following `start` **times out** (`can't start rproc: -110`) and the SLPI stays
offline until reboot. Only the crash-recovery path restarts it. The same
happens after an injected debugfs crash. So: no pre-suspend stop hook, and
never poke that state file.

## Dev tooling on the tablet

Present: podman, toolbox, distrobox, git, tmux, htop, VS Code (Microsoft's
aarch64 repo). Bluetooth keyboard and mouse work; the on-screen keyboard has
real modifier keys.

## Open

- Suspend drain figure: read the overnight powerlog.
- Clean display power measurement: needs a long DPMS-off window.
- Idle power below ~2 W: audit what keeps three DSPs and the modem awake.
- SLPI clean restart failure (-110): needs the remoteproc/pas source; cortex
  was down.
- Prune `/root` backups; second `/lib/modules` copies.
