# Battery reporting on a PMIC you are not allowed to talk to

**Status: working, with caveats.** `/sys/class/power_supply/adc-battery` reports voltage,
a percentage, and a discharging status, so the desktop shows a battery and warns when it
gets low. It does this from the *wrong PMIC*, because the right one is unreachable — and
that turns out to be the whole story.

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

## What this is and is not

- The percentage is measured from **VPH_PWR, not the battery terminals**, so it reads
  slightly low and dips under load.
- The OCV curve is a **nominal 4.4 V Li-ion** one for the 7040 mAh pack, not a measured
  one. Absolute percentage is approximate; the trend is sound.
- **Charging cannot be detected at all**, for the same arbiter reason.
- The one calibration still outstanding: confirm the reading climbs to roughly 4.2–4.4 V
  on the charger, and adjust `ocv-capacity-table-0` if it does not.

Treat it as a good indicator, not a gauge. Given the alternative was no battery reading
whatsoever, that is a reasonable place to land.

## The transferable lesson

This is the same shape as the [Wi-Fi `wlan_mem` bug](WIFI.md) in different clothing: an
**ownership boundary between the applications processor and other firmware**, surfaced
only as a blunt errno from a low-level call. On Qualcomm platforms, when a memory or
register access is refused, ask *who owns it* before assuming the code is wrong.
