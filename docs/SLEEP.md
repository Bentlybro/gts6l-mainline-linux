# Sleep, wake, and the clock

Suspend on this port works. Getting there involved one masked systemd unit, one
self-inflicted driver bug, and one desktop component that ignores its own
configuration file.

Related: [POWER.md](POWER.md) (shutdown and reboot), [BATTERY.md](BATTERY.md),
[TOUCH.md](TOUCH.md).

---

## Summary

| Thing | State |
|---|---|
| s2idle suspend/resume | **Works** |
| Touchscreen across suspend | **Works**, resume path fixed |
| Wake by power button | **Works** (armed wakeup source) |
| Wake by RTC alarm | **Works** |
| Idle suspend | **Works**, via `tools/tabs6-idled.py` — *not* PowerDevil |
| Screen blanking on idle | Works, PowerDevil default (10 min) |
| `timedatectl` | **Fixed** (was failing outright) |
| Setting the RTC counter | **Impossible** — firmware owns the register |

Only `s2idle` is available; there is no `deep`/S3 on this platform.

    # cat /sys/power/mem_sleep
    [s2idle]

---

## The masked units

Everything at the kernel level worked long before sleep did. `rtcwake` suspended
and resumed cleanly. What failed was asking for sleep the normal way:

    $ systemctl suspend
    Call to Suspend failed: Access denied

That message sends you straight to polkit and seats, and it is a red herring.
The session was fine — attached to `seat0`, `Active=yes`, and `pkcheck
--action-id org.freedesktop.login1.suspend` returned authorized. The real cause:

    $ systemctl show -p LoadState --value suspend.target
    masked

Fedora ships these masked on this image:

    hibernate.target
    hybrid-sleep.target
    sleep.target
    suspend.target

So logind answered `CanSuspend -> no`, and PowerDevil — which asks logind before
doing anything — silently did nothing. The fix is one command:

```bash
systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

After which `CanSuspend -> yes` and suspend works from every path.

**Worth remembering:** logind reports a masked sleep unit as an *access* failure,
not as "unsupported". If `systemctl suspend` says "Access denied", check for
masking before you go near polkit.

---

## The touchscreen resume bug

Resume logged this on every wake:

    fts1ba90a 4-0049: resume: ready wait failed: -110

It looks like the touchscreen is dead after resume. It is not — the driver logs
and carries on, and the reconfigure that follows succeeds. The error was real,
but it was ours, and it was pure waste.

`probe()` and `resume()` had drifted apart:

    probe:  power_on -> system_reset -> read_ids -> configure
    resume: power_on -> wait_for_ready -> system_reset -> configure
                        ^^^^^^^^^^^^^^ cannot succeed

The controller does not post a ready event because its rails came back. It posts
one **in response to the reset**. So the resume path waited for an event that was
never coming, for `FTS_RETRY_COUNT * 15` iterations of `msleep(20)` — roughly
three seconds of wake time — and then ignored the `-ETIMEDOUT` anyway.
`system_reset()` already does its own ready wait.

Fix: delete it, so resume matches probe.
See `kernel/patches/fts1ba90a-resume-drop-redundant-ready-wait.patch`.

A clean cycle now looks like this, with only the same benign line probe prints:

    [61.11] PM: suspend entry (s2idle)
    [67.78] fts1ba90a 4-0049: no echo for cmd a0 (ignored): 00 00 ...
    [68.48] PM: suspend exit

**Lesson:** an error in a log is not automatically the failure you are chasing.
This one cost three seconds a wake and nothing else, while looking like a dead
touchscreen.

---

## The clock

Two independent faults. One fixed, one is a hardware wall.

### rtc-efi was claiming rtc0, and is broken

    hwclock: ioctl(RTC_RD_NAME) to /dev/rtc0 to read the time failed: Input/output error

Because it was `rtc0`, it was what systemd talked to, so `timedatectl` did not
merely report a bad time — it failed outright:

    Failed to query server: Failed to read RTC: Input/output error

Fix: `# CONFIG_RTC_DRV_EFI is not set`. The PMIC RTC then becomes `rtc0`:

    rtc0 = rtc-pm8xxx c440000.spmi:pmic@0:rtc@6000

`timedatectl` works, reads work, and alarms work — which is all suspend needs.

### The counter cannot be written

Setting the time failed with "No such device". That is `-ENODEV`, and it comes
from the driver, not the hardware:

```c
if (rtc_dd->allow_set_time)  rc = __pm8xxx_rtc_set_time(rtc_dd, secs);
else                         rc = pm8xxx_rtc_update_offset(rtc_dd, secs);

static int pm8xxx_rtc_update_offset(...)
{
    if (!rtc_dd->nvmem_cell)
        return -ENODEV;
```

Without `allow-set-time` the driver refuses to touch the counter and stores a
correction offset in an nvmem cell instead — and no offset cell is wired up on
this board.

Adding `allow-set-time` to the DT gets past that and into the real wall:

    spmi spmi-0: disallowed SPMI write to sid=0, addr=0x6046

`0x6046` is the RTC control register, and the firmware owns write access — the
same SPMI ownership boundary that keeps pm8150b (charger/gauge) off limits.

**`allow-set-time` was therefore reverted.** It buys nothing and costs a lot:
`CONFIG_RTC_SYSTOHC=y` targets `rtc0`, so the kernel retries the write every 11
minutes forever — **602 denied SPMI writes in the first 122 seconds of uptime**
on the one boot it was enabled. Without the property the driver returns `-ENODEV`
immediately and quietly. The DTS carries a comment so nobody adds it back.

### It does not actually matter

systemd covers the gap:

    [12.79] rtc-pm8xxx: setting system clock to 1976-10-22T23:29:49 UTC
    [12.95] systemd: System time advanced to timestamp on
            /var/lib/systemd/timesync/clock: Sat 2026-08-29 21:34:51 BST
    [23.06] systemd-timesyncd started -> exact over NTP

The clock is wrong for about 0.16 s of boot, then correct to within minutes of
the last shutdown, then exact once the network is up.

---

## PowerDevil ignores its own configuration

With sleep unmasked, `systemctl suspend` worked but **idle suspend never fired**.
PowerDevil reports:

    Loading profile for plugged AC
    DimDisplay: registering idle timeout after 300000ms
    DPMS: registering idle timeout after 600000ms

Those are its built-in defaults, and **no SuspendSession action is registered at
all**. Three config layouts were tried, restarting `plasma-powerdevil` each time:

| File | Layout | Result |
|---|---|---|
| `powermanagementprofilesrc` | `[AC][DPMSControl] idleTime=` | ignored |
| `powerdevilrc` | `[AC] turnOffDisplayIdleTimeoutSec=` | ignored |
| `powerdevilrc` | `[AC][DimDisplay] idleTime=` | ignored |

The key names are not the problem — they were read out of the binary itself, and
`kreadconfig6` reads the same keys back correctly, so KConfig is fine. There are
no system-wide overrides and no `[$i]` immutability markers. The clincher:
setting `dimDisplayWhenIdle=false` does **not** stop the dim action registering.
The file is not being read.

> Note for anyone doing this archaeology: PowerDevil's config strings are
> `QStringLiteral`, i.e. UTF-16 in `.rodata`. A plain `strings(1)` pass shows
> zero occurrences of `DPMSControl`, `SuspendSession` and `idleTime` and makes it
> look like the legacy schema is gone. Use `strings -el`. (And note `strings -el`
> defaults to a 4-character minimum, so the group name `AC` will not show either.)

systemd cannot cover for it: `logind`'s `IdleAction` depends on the session idle
hint, and KWin never sets it — `IdleHint` stayed `no` after half an hour with no
input at all.

---

## tabs6-idled

So idle suspend is done separately, in `tools/tabs6-idled.py`, measured straight
off the evdev nodes — the one source that cannot be wrong. If no input device has
produced an event, nobody is using the tablet.

- 60 min on AC, 20 min on battery (`TABS6_IDLE_AC` / `TABS6_IDLE_BATTERY`)
- stands down if KDE's PolicyAgent has an inhibition (video playback etc.) or a
  systemd **block** inhibitor names sleep/idle
- PowerDevil keeps blanking the screen — its default 10 min DPMS does work
- `tabs6-powerkeyd` keeps the power button

It logs a heartbeat once a minute on purpose:

    idle 0s / 75s (AC)
    idle 60s / 75s (AC)

This is the check that matters. If a compositor ever took an `EVIOCGRAB` on the
touchscreen, the daemon would see no events and idle would climb *while the
tablet was in use*. As long as idle resets to ~0 while you are touching the
screen, the approach is sound. It was verified end to end with the threshold
temporarily at 75 s: the tablet suspended on idle and dropped off the network
entirely (the gateway reported the host unreachable), because with Wi-Fi down
nothing was left to wake it.

### Before enabling any of this

Check the power key is an armed wakeup source, or auto-sleep will strand you:

```bash
cat /sys/devices/platform/soc@0/c440000.spmi/spmi-0/0-00/c440000.spmi:pmic@0:pon@800:pwrkey/power/wakeup
```

It reads `enabled` here, along with the RTC and the modem remoteproc.

---

## The screen stays lit through suspend

Expected, not a fault. simpledrm scans out a fixed buffer, there is no backlight
device (`/sys/class/backlight` is empty), and with the compositor frozen nothing
is left to turn anything off — the last frame just stays on screen.

It costs little on this panel. It is AMOLED, so a black frame is not a backlight
shining through black pixels, it is pixels not emitting. KDE blanks to black
before it suspends, so a normally-entered suspend goes in dark. A suspend
triggered from a live desktop (e.g. a manual `rtcwake`) leaves the desktop
visible, which is what it looks like when testing.

---

## Wake sources

    4080000.remoteproc                       enabled
    alarmtimer.0.auto                        enabled
    c440000.spmi:pmic@0:pon@800:pwrkey       enabled   <- lock button
    c440000.spmi:pmic@0:pon@800:resin        enabled   <- volume down
    c440000.spmi:pmic@0:rtc@6000             enabled

### Tapping the screen will not wake it — press the lock button

This is by design, not a gap. The touchscreen has no wakeup entry at all:

    /sys/bus/i2c/devices/4-0049/power/wakeup    <- does not exist

and `fts1ba90a_suspend()` calls `fts1ba90a_power_off()`, which drops the
controller's regulators. It is powered down, so it cannot signal anything. Use
the **lock button** (volume down also works, since `resin` is armed too).

Waking it costs a little more than it looks: the resume path power-cycles and
re-initialises the controller, which is why `no echo for cmd a0 (ignored)` shows
up in the log on every wake.

### Which key is which

`resin` is not self-describing, so decode the evdev key bitmaps rather than
assuming:

    event0  pm8941_pwrkey   KEY=10000000000000  -> bit 116  KEY_POWER
    event1  pm8941_resin    KEY= 4000000000000  -> bit 114  KEY_VOLUMEDOWN
    event2  gpio-keys       KEY= 8000000000000  -> bit 115  KEY_VOLUMEUP

Volume **down** sits on the PMIC PON block next to the power key; volume **up**
is a separate GPIO. That is what makes an Android-style power+volume-down
screenshot chord easy — both halves are PMIC keys `tabs6-powerkeyd` already
reads. See [DESKTOP.md](DESKTOP.md).

Because s2idle is a light sleep and the network stays armed, an active SSH
session or other traffic will wake the tablet almost immediately. That is not a
fault either — it just makes suspend look like it "did not stick" when testing
over the network. Test with the RTC alarm and a single held-open session rather
than a reconnect loop.
