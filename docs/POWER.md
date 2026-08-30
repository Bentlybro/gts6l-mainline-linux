# Power button, shutdown, and reboot

**Status: all working.** Power button locks/wakes and long-press opens the power menu.
Power Off cuts power and stays off. Reboot is reliable.

None of it worked at the start, and the reasons were four separate things stacked on top of
each other. The short version, if you only read one paragraph:

> **PSCI cannot be trusted on this device.** `SYSTEM_OFF` hangs in Samsung's firmware and
> `SYSTEM_RESET` hangs intermittently. Mainline reaches for both by default. Everything
> below is about going through the PMIC instead — and about the fact that you cannot *see*
> any of it happen, because the console dies first.

---

## 1. There was no power button at all

One misleading line in dmesg:

```
platform gpio-keys: deferred probe pending: gpio-keys: failed to get gpio
```

Two unrelated causes behind it:

- **Volume Up** is a PMIC GPIO, and `CONFIG_PINCTRL_QCOM_SPMI_PMIC` was off, so that GPIO
  controller never existed and `gpio-keys` deferred forever. (The pin itself was also wrong
  for a long time: it is `&pm8150l_gpios 12`, not the Surface Duo's `&pm8150_gpios 6`. See
  [DESKTOP.md](DESKTOP.md).)
- **The power button is not a GPIO.** On Qualcomm it is the PMIC PON block's KPDPWR input,
  with "resin" beside it for volume down. Upstream `pm8150.dtsi` declares both and leaves
  them `status = "disabled"`.

Three config options, and all three buttons appeared at once:

```
CONFIG_PINCTRL_QCOM_SPMI_PMIC=y   # PMIC GPIOs, so gpio-keys finds volume up
CONFIG_POWER_RESET_QCOM_PON=y     # the PON node itself
CONFIG_INPUT_PM8941_PWRKEY=y      # pwrkey + resin input drivers
```

`POWER_RESET_QCOM_PON` matters more than it looks: without it the `pon` node has no driver,
so nothing calls `devm_of_platform_populate()` and the `pwrkey` child is never created no
matter what the device tree says.

```
input: pm8941_pwrkey as .../pmic@0:pon@800:pwrkey/input/input0
input: pm8941_resin  as .../pmic@0:pon@800:resin/input/input1
input: gpio-keys     as /devices/platform/gpio-keys/input/input2
```

### Phone-style behaviour

A small root daemon owns the key outright (`rootfs/` — short press locks and blanks, next
press wakes, 1.5 s opens Plasma's power menu via `org.kde.LogoutPrompt.promptAll`).
Everything else that wants the key has to be silenced or Plasma queues a logout prompt
*behind* the lock screen: `HandlePowerKey=ignore` and `HandlePowerKeyLongPress=ignore` in
logind, `powerButtonAction=0` in all three PowerDevil profiles (**this one does nothing** — PowerDevil does not read its profile configuration on this machine at all, on 6.6.4 or 6.7.4; see [`SLEEP.md`](SLEEP.md). It is left in place as belt and braces, but the measures that actually work are logind's `HandlePowerKey=ignore` and the unbound global shortcuts), and PowerDevil's global
`PowerOff`/`PowerDown` shortcuts unbound.

---

## 2. The console dies before anything interesting happens

**This is the trap that cost the most time, and it will cost you the same time.**

`device_shutdown()` tears down simpledrm. This port scans out of the bootloader framebuffer
*through* simpledrm. So the display freezes on its last frame and **everything printed after
that point is invisible** — including the kernel's own `reboot: Power down`.

Its absence is the tell:

```
systemd-shutdown[1]: Powering off.        <- last thing you ever see
                                          <- no "reboot: Power down". Ever.
```

Every sys-off handler runs *after* `device_shutdown()`. So does every `pr_emerg` you add to
one. Photographing the screen tells you nothing, and a frozen screen does not mean the
kernel is frozen.

**Put diagnostics in a reboot notifier instead.** Those run at the top of
`kernel_shutdown_prepare()`, before `device_shutdown()` — console alive, SPMI alive. That
change immediately produced the first real evidence:

```
systemd-shutdown[1]: Rebooting.
qcom-pon: PS_HOLD type wanted 0x01, reads back 0x01
```

Handler ran, SPMI write succeeded, readback confirmed — and *then* it hung. Which located
the fault precisely.

---

## 3. Power is cut by PS_HOLD, and mainline had no node for it

Releasing the PS_HOLD pin is what cuts power on this SoC, and mainline has a driver for it
(`drivers/power/reset/msm-poweroff.c`, matching `qcom,pshold`) — but **mainline
`sm8150.dtsi` has no PS_HOLD node**, so the driver never bound and there was no power-off
handler at all. Address from Samsung's own device tree:

```dts
&soc {
	restart@c264000 {
		compatible = "qcom,pshold";
		reg = <0x0 0x0c264000 0x0 0x4>;
	};
};
```

### Releasing PS_HOLD does not mean "power off"

It means *"do whatever `PS_HOLD_RST_CTL` says"*. Read live over regmap debugfs:

```
085a: 01     PS_HOLD_RST_CTL  = 0x01 = WARM RESET
085b: 80     PS_HOLD_RST_CTL2 = RESET_EN
```

The bootloader leaves it on warm reset and **nothing in mainline ever changes it** —
`qcom-pon.c` writes the reboot *reason* (`PON_SOFT_RB_SPARE`), never the reset *type*. So a
power-off would reboot the machine even with PS_HOLD wired up.

`kernel/patches/qcom-pon-ps-hold-shutdown-and-smpl.patch` selects `0x04` (shutdown) for a
power off and `0x01` (warm reset) for a restart. Explicitly on **both** paths, because the
setting is sticky — once a power-off has selected shutdown, a later reboot that released
PS_HOLD would silently power the machine off instead.

---

## 4. SMPL powers it straight back up

With all of the above, the tablet powered off — and immediately came back on.

```
0808: 03     PON_REASON1 = HARD_RESET | SMPL
087f: 80     SMPL_CTL, SMPL_EN set
```

**SMPL** (Sudden Momentary Power Loss) makes the PMIC power the machine back up by itself
after a brief loss of supply. The bootloader leaves it armed, and releasing PS_HOLD looks
exactly like the event it is watching for.

This is the *second* time this family of bug has appeared across these ports — the
[z3s](https://github.com/…) had S-Boot leaving WTSR and SMPL armed on its S2MPS19, with the
phone re-powering ~20 s after every shutdown. **A PMIC watchdog that the vendor kernel
disables on shutdown and mainline knows nothing about is worth checking early.**

Offsets were confirmed against the live PMIC rather than assumed: `0x870` reads back as
pull-up control and `0x871` as debounce, matching the Qualcomm PON layout, which makes
`0x87f` SMPL_CTL. Disarmed on the power-off path only — a restart wants to come back.

> Powering off **while plugged in** still brings it back. That is the charger, and it is
> normal PMIC behaviour on basically every phone. Test on battery.

---

## 5. Breaking PSCI's two ties

Even with the mechanism correct, PSCI was still winning and hanging:

| path | PSCI | msm-poweroff | who won |
|---|---|---|---|
| restart | notifier priority **129** | `SYS_OFF_MODE_RESTART` priority **128** | PSCI |
| power off | legacy `pm_power_off`, wrapped at **`SYS_OFF_PRIO_DEFAULT`** | `SYS_OFF_MODE_POWER_OFF` at **`SYS_OFF_PRIO_DEFAULT`** | PSCI, on the tie |

Both ties have to be broken deliberately. In the end both paths were moved into the reboot
notifier (`kernel/patches/msm-poweroff-release-ps-hold-early.patch`), which sidesteps the
priority question entirely *and* runs before the stretch of shutdown that intermittently
hangs.

Result: reboot went from hanging often enough to need repeated hard resets, to **three for
three, down in ~2 s and back in 32–36 s**.

### The cost, stated honestly

Cutting power from the reboot notifier means **devices never get their shutdown callbacks**.
systemd has already unmounted, remounted read-only and synced by that point, so the exposure
is small — but it is a trade made to stop the hangs, not a free win. If the late path is
ever made reliable, the cleaner ordering is worth returning to.

---

## Deploy discipline (learned the hard way)

- **Verify a deployed kernel by checksum *and* `uname -v`.** A reboot once came back running
  the previous build while the ESP held the new one, which sent us hunting a bug in code
  that was not running.
- **When waiting for a reboot, wait for the machine to go DOWN first, then come back.** A
  loop that only waits for "up" connects to the machine that has not left yet, reports the
  old kernel, and looks exactly like a failed deployment.

---

## Sleep is a separate story

This document covers **shutdown and reboot** — cutting power via PMIC `PS_HOLD`
because PSCI hangs on this firmware. Suspend/resume has its own set of problems and
its own document: [SLEEP.md](SLEEP.md).

The short version, because the failure mode is misleading: `systemctl suspend`
reporting **"Access denied"** does not mean polkit. Fedora ships `sleep.target` and
`suspend.target` masked, and logind reports a masked unit as an access failure
rather than as unsupported. Check masking first.

Note the asymmetry with the shutdown path documented above. Powering *off* needed
kernel work (a reboot notifier driving `PS_HOLD`, because PSCI could not be
trusted). Suspending needed no kernel work at all — the kernel side already worked;
it was distribution policy and a desktop component ignoring its own configuration.
Worth remembering before assuming the next power problem is a driver problem.
