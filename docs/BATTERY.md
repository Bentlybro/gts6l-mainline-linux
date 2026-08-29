# Battery reporting on a PMIC you are not allowed to talk to

**Status: working properly, via the SM5705 fuel gauge.** `/sys/class/power_supply/sm5705-fuelgauge`
reports real state of charge, voltage, open-circuit voltage and **current**, so charging is
detected correctly and upower gives time-to-full and time-to-empty.

> **Most of this document describes a dead end, and it is kept because the dead end is
> instructive.** The premise below — that the only way to see the battery is a Qualcomm PMIC
> ADC, with the real gauge locked behind an unreachable pm8150b — is *wrong*. The battery is
> not on the Qualcomm PMIC at all. Samsung fit a **Silicon Mitus SM5705** (charger + fuel
> gauge + MUIC) on I²C and use that instead. Skip to [The SM5705](#the-sm5705-the-actual-answer)
> for what actually works; read the middle for why pm8150b is a closed door, which is still
> true and still worth knowing.

---

## The starting point

Nothing reported a battery at all. `/sys/class/power_supply` was empty and upower had
only its synthetic DisplayDevice.

The immediate cause is a kernel config one, and it is a good trap to know about:
`CONFIG_MFD_SPMI_PMIC` was off. **The PMICs enumerate on the SPMI bus regardless**, so
`/sys/bus/spmi/devices/` lists all six functions (`0-00` … `0-05`) and the bus looks
perfectly healthy — but without the MFD driver none of their *child* functions are ever
instantiated. No ADC, no battery, no RTC, and nothing to indicate why.

With `CONFIG_MFD_SPMI_PMIC` and `CONFIG_QCOM_SPMI_ADC5` enabled, two ADCs appear:

```
iio:device0 -> c440000.spmi:pmic@0:adc@3100   (pm8150)
iio:device1 -> c440000.spmi:pmic@4:adc@3100   (pm8150l)
```

Note which one is missing.

## pm8150b belongs to somebody else

pm8150b is the PMIC carrying the charger and the fuel gauge, and the only one the battery
is physically wired to. It never probed:

```
spmi spmi-0: pmic_arb_wait_for_done: 0x2 0x104: transaction failed (0x3) reg: 0x3408
pmic-spmi 0-02: probe with driver pmic-spmi failed with error -5
```

SID 2 is pm8150b and `0x104` is `PMIC_TYPE`, the first register the MFD driver reads to
identify the part. The SPMI arbiter refuses it. This is neither a driver bug nor a
device-tree mistake: the arbiter assigns each peripheral to an execution environment, and
that APID is not owned by the applications processor. Something else on this SoC owns
pm8150b, and the hardware is saying so.

The consequence is out of all proportion to the cause. Upstream treats a failed revid read
as fatal and returns before `devm_of_platform_populate()`, so **not one child of that PMIC
is created**. The revision number is only used to describe the part in a debug print; it
is not needed to reach anything. One denied read of a cosmetic register removes the entire
battery. (As a side effect SID 3 then sits in `deferred probe pending` forever, waiting on
drvdata SID 2 never got far enough to set.)

`kernel/patches/mfd-qcom-spmi-pmic-tolerate-denied-revid.patch` makes that non-fatal —
warn, mark the revision unknown, carry on — so each child gets to prove for itself whether
it can reach its peripheral. The answer here was unambiguous:

```
pmic-spmi 0-02: revid read failed (-5), continuing without it
qcom-spmi-adc5 c440000.spmi:pmic@2:adc@3100: probe ... failed with error -5
```

The ADC block was refused too. It is not the revision register that is off-limits, it is
**the whole of SID 2**. `VBAT_SNS` and the fuel gauge are permanently out of reach on this
device, and so is any way to detect charging.

Keep the patch anyway. It converts a silent, inexplicable absence into an explicit error,
and it unsticks SID 3.

> Qualcomm's own fuel gauge (QG) has no mainline driver in any case, so even a reachable
> pm8150b would not have given a state-of-charge reading directly.

## Reading the battery from the PMIC that does work

`VPH_PWR` is each PMIC's own supply rail, and on a tablet that rail is the battery. pm8150
(SID 0) probes normally, so the battery can be read there instead. Upstream's
`pm8150.dtsi` does not declare the channel, so add it:

```dts
&pm8150_adc {
	channel@83 {
		reg = <ADC5_VPH_PWR>;
		qcom,pre-scaling = <1 3>;
		label = "vph_pwr";
	};
};
```

`qcom,pre-scaling = <1 3>` is **not optional** — the signal arrives through a
divide-by-three network, and without it the driver reports a third of the real voltage.

It is unmistakably a real battery rail:

| state | reading |
|---|---|
| idle | 3.478 V, 3.505 V, 3.528 V |
| 8-core load | 3.218 V, 3.253 V, 3.266 V |
| recovered | 3.497 V, 3.523 V, 3.523 V |

A ~260 mV sag under load with a clean recovery is battery internal resistance, and no
stuck or noisy channel produces that. The 1.25 V reference channel on the same ADC held
1.2497 V throughout, which validates the calibration path too.

### The units trap

That reference channel also settles the units. It reads `1250216`, which can only be
**microvolts** — `qcom-spmi-adc5` does not follow IIO's convention that a processed
voltage is in millivolts. This matters because `generic-adc-battery` multiplies its
channel reading by 1000 on exactly that assumption, and would report thousands of volts.

## Turning volts into a percentage

`generic-adc-battery` (compatible `adc-battery`) exposes STATUS, VOLTAGE_NOW, CURRENT_NOW,
POWER_NOW and TEMP — **but not CAPACITY**, so on its own the desktop has nothing to show.
The power-supply core does export `power_supply_batinfo_ocv2cap()`, which maps an
open-circuit voltage to a percentage through a table in the battery description.

`kernel/patches/adc-battery-report-capacity-and-status.patch` adds three things:

1. `POWER_SUPPLY_PROP_CAPACITY`, answered from that lookup, offered only when a
   `monitored-battery` is described.
2. An exponential moving average over the voltage before the lookup. The rail sags 260 mV
   under load, so a raw sample would send the reported percentage lurching every time a
   compile started; the battery itself cannot change quickly, so a slow average is closer
   to the truth than the instantaneous value.
3. A status fix. Upstream tests `power_supply_am_i_supplied()` with `if (!ret)`, but it
   returns `-ENODEV` when nothing claims to supply the battery — always the case here,
   since the charger is on the unreachable pm8150b — and a negative value is not zero, so
   the status was pinned to "Charging" forever. **That is not cosmetic: the desktop only
   warns about a low battery while the status is discharging**, so it would never have
   warned at all. "No supplier known" now means discharging.

Plus the battery description and consumer in the board DTS:

```dts
gts6l_battery: battery {
	compatible = "simple-battery";
	voltage-min-design-microvolt = <3400000>;
	voltage-max-design-microvolt = <4400000>;
	charge-full-design-microamp-hours = <7040000>;
	ocv-capacity-celsius = <25>;
	ocv-capacity-table-0 = <4400000 100>, /* ... */ <3400000 0>;
};

adc-battery {
	compatible = "adc-battery";
	io-channels = <&pm8150_adc ADC5_VPH_PWR>;
	io-channel-names = "voltage";
	monitored-battery = <&gts6l_battery>;
};
```

with `CONFIG_GENERIC_ADC_BATTERY=y`.

## The SM5705: the actual answer

Everything above is built on the belief that the Qualcomm PMIC is the only way to see the
battery. Samsung's own dtbo overlay says otherwise:

```
sm5705-fuelgauge@71   on qupv3_se11_i2c    compatible "sm5705-fuelgauge"
sm5705@49             on qupv3_se4_i2c     compatible "sm,sm5705"
muic-sm5705@25        on qupv3_se4_i2c
battery               compatible "samsung,sec-battery"
```

A **Silicon Mitus SM5705** — combined charger, fuel gauge and MUIC — the same class of part
as the MAX77705 on the z3s. Enabling `i2c11` put the gauge on the bus immediately, and the
charger and MUIC were already reachable on `i2c4`.

### Working out the registers without a datasheet

There is no public datasheet and no mainline driver. The map was established by reading the
live part against a reference we already trusted — the calibrated ADC reading of the rail —
and checking each candidate *tracked reality over time*:

| reg | value | decoded | check |
|---|---|---|---|
| `0x07` | `0x200a` | 4.005 V | ADC said 4.039 V |
| `0x06` | `0x1e82` | 3.813 V | below terminal, as OCV should be while charging |
| `0x05` | `0x2e0c` | 46.23 % | climbed 46.23 → 46.29 → 46.34 → 46.39 |
| `0x08` | `0x0919` | 1.14 A | plausible charge current |

Voltage agreed within 30 mV on every sample, SOC only ever rose while charging, current
stayed sane. **Only these four registers are used** — the rest of the map is left alone
rather than guessed at.

Driver: `kernel/drivers/sm5705_fuelgauge.c`, `CONFIG_BATTERY_SM5705`.

### What it changes

```
status      = Charging          <- the ADC battery could never know this
capacity    = 48                <- the voltage curve claimed 68
voltage_now = 3999511
voltage_ocv = 3822265
current_now = 1210937
```

Two things worth drawing out:

- **Status is now a measurement.** A voltage-only source cannot distinguish charging from
  discharging at all, so the honest choice there was to always claim discharging.
- **The accuracy gap was large.** 46 % against 68 %. Charging lifts terminal voltage well
  above open-circuit — visible right in the registers, 4.00 V terminal vs 3.81 V OCV — so a
  voltage-derived percentage reads high *exactly when it is plugged in*.

The ADC-derived battery has been **deleted** from the device tree rather than left alongside;
two batteries only confuse the desktop, and this one is strictly better.

upower needs no help:

```
state: charging   percentage: 48%   energy-rate: 4.21738 W
energy: 14.87 Wh of 30.976 Wh       time to full: 3.8 hours
```

### It also makes battery life arithmetic

With only a voltage, runtime was unanswerable without measuring a discharge slope for an
hour. With current it is a sum — watts = volts × amps, hours = remaining Wh ÷ watts — and it
takes seconds.

## Measuring power without fooling yourself

Once current decodes correctly, power is `V × I` and runtime is arithmetic. Getting a
*trustworthy* number is harder than it looks, and this went wrong twice.

**Do not drive the measurement over SSH.** Every poll wakes the CPU, and that noise is larger
than most effects worth measuring. A ten-sample burst at one fixed brightness ranged from
−2.60 to −3.35 W — **±0.37 W of jitter on a single reading**.

The giveaway that the method was broken, rather than the hardware being strange:

```
screen ON, 100% brightness   -2.28 W
screen OFF (DPMS)            -2.50 W     <- impossible
```

A blanked panel cannot draw more than a lit one. When a control comes out backwards, stop and
fix the method.

**The expensive mistake:** a matched pair of hand-driven readings (100% at −2.95 W, minimum at
−2.17 W) looked like a clean 0.78 W saving and was reported as one. The clean on-device run
then measured the *same* 100% condition at **−2.286 W**. That 0.66 W gap between two
measurements of one identical condition is the SSH overhead — larger than the claimed effect.
Two readings taken "the same way" prove nothing if the thing perturbing them varies.

Use `tools/brightness-power-test.sh`: runs entirely on the device, 45 s settle, 120 s averaged
per level, and **repeats a level at the end** so the run can be checked against itself.

### What it actually measured

```
brightness 100%   -2.286 W
brightness 50%    -2.224 W
brightness 10%    -2.327 W
screen off        -2.19  W
```

Within ~0.1 W and **not monotonic** — no measurable brightness effect, and the whole display
accounts for roughly 0.1 W of a 2.2 W system.

That is not a contradiction of AMOLED physics. There genuinely is no backlight, and dimming
genuinely does cut emission current — but an AMOLED only spends power on *lit* pixels, and a
dark KDE desktop is mostly unlit, so there is little to give back. On a full-screen white page
the saving would be far larger. Brightness matters in proportion to how much of the screen is
actually lit.

> Caveat: with no real panel driver, DPMS-off through simpledrm probably blanks scan-out
> rather than commanding the panel down. So 0.1 W measures what the *content* costs, and
> likely understates what a truly powered-down panel would save.

Idle draw is **~2.2–2.3 W**, giving roughly **12 hours** from a 27.1 Wh pack — better than the
7 h figure that earlier SSH-driven measurements suggested. Read every SSH-measured power
figure as inflated by about half a watt.

## Charge rate: the MUIC was never going to grant permission

The charger powers up with USB safe defaults:

```
input limit (0x0D) = 500 mA
fast charge (0x10) = 100 mA
```

On a stock device the **MUIC** identifies the cable — wall charger, PC port, whatever — and
the driver raises the limits to match. Nothing drives the MUIC here, so nothing ever grants
permission to draw more, and the tablet trickles no matter what it's plugged into. This is
the same shape of problem as the z3s, where the charger defaulted low and the current had to
be raised deliberately.

Encoding and clamps straight from the vendor driver, so this is arithmetic rather than
guesswork:

```
VBUSCNTL 0x0D   offset = ((mA - 100) / 25) & 0x7F    max 3275 mA   (input)
CHGCNTL2 0x10   offset = ((mA - 100) / 50) & 0x3F    max 3250 mA   (into battery)
```

`tools/tabs6-charge.sh` (+ its service) maintains 2000 mA in and 2000 mA to the battery.
Three things about it matter:

- **Maintained, not set once.** The registers reset when the cable is inserted or the charger
  changes mode.
- **Only ever raises.** If something else sets a higher limit, it's left alone.
- **Never touches the chip in `USB_OTG` mode.** There the SM5705 is a *supply*, not a charger,
  and writing charge limits at it is meaningless. See [`USB_HOST.md`](USB_HOST.md).

**And it has to actually start at boot**, which for a long time it did not. The unit
carried `After=multi-user.target` *and* `WantedBy=multi-user.target` — ordered after
the very target that wants it — so its start job sat queued and the service came up
about **two minutes into every boot**:

    Active: inactive (dead)
       Job: 215

Which means the tablet spent the busiest part of every boot charging at the default
**500 mA** input rather than 2000 mA. It is an easy thing to miss because nothing
fails: the service does start, just late, and by the time you look it is running.

Dropping the `After=` line fixed it; charge control now applies at boot:

    23:06:25 Started tabs6-charge.service
    23:06:25 target 2000mA input (offset 76), 2000mA charge (offset 38)

Worth knowing generally: `systemctl enable --now` proves nothing about whether a
unit starts at boot, because it starts the unit directly and bypasses ordering
entirely. Only a reboot counts — and `journalctl -b | grep "ordering cycle"` is
worth a look every time a unit is added.

Asking for 2000 mA from a source that can't deliver it is safe: **AICL** walks the input
current back until the supply voltage holds up, so a weak charger simply ends up giving what
it can.

> You cannot charge and use USB host mode at once — one port, and in host mode the chip is
> sourcing power rather than accepting it.

### Still to do

The **charger** side of the SM5705 (0x49) and the **MUIC** (0x25) are reachable but undriven.
Beyond charge control, the charger holds the **VBUS boost** — which is what a bus-powered
USB device needs when the port is in host mode (see [`POWER.md`](POWER.md)).

## The transferable lesson

This is the same shape as the [Wi-Fi `wlan_mem` bug](WIFI.md) in different clothing: an
**ownership boundary between the applications processor and other firmware**, surfaced
only as a blunt errno from a low-level call. On Qualcomm platforms, when a memory or
register access is refused, ask *who owns it* before assuming the code is wrong.
