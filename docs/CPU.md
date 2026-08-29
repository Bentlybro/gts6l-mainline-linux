# CPU: topology, DVFS and scheduling

This is one of the few subsystems on this port that needed **no work at all**. It
is written down because "it already works" is worth being able to prove, and
because the contrast with the S20 (`z3s`) project — where DVFS was a whole
bring-up effort of its own — says something useful about the two SoCs.

Related: [PORT.md](PORT.md), [BATTERY.md](BATTERY.md), [SLEEP.md](SLEEP.md).

---

## What the hardware is

SM8150 / Snapdragon 855. Eight cores in **three** clusters, not the two that
"big.LITTLE" usually implies:

| Policy | CPUs | Core | ARM part | Max clock | Capacity |
|---|---|---|---|---|---|
| `policy0` | 0–3 | Kryo 485 **Silver** | `0x805` (Cortex‑A55) | 1.786 GHz | 306 |
| `policy4` | 4–6 | Kryo 485 **Gold** | `0x804` (Cortex‑A76) | 2.419 GHz | 871 |
| `policy7` | 7 | Kryo 485 **Gold Prime** | `0x804` (Cortex‑A76) | 2.842 GHz | 1024 |

So: **four efficiency cores, three performance cores, and one prime core** that
clocks ~18% higher than the other three Golds. All eight are online.

`lscpu` reports this slightly confusingly — it prints two "Model name" lines
(`Kryo-4XX-Gold` and `Kryo-4XX-Silver`) and a single "Core(s) per socket: 4",
because it is describing two *implementations* rather than three *policies*. The
cpufreq policies above are the real boundaries; the prime core is a Gold with its
own clock domain, not a third microarchitecture.

Confirming the split without trusting a marketing name:

```bash
# 0x805 = A55-derived Silver, 0x804 = A76-derived Gold
grep -E "processor|CPU part" /proc/cpuinfo | paste - -

# the actual DVFS domains
for p in /sys/devices/system/cpu/cpufreq/policy*; do
    echo "$(basename $p): $(cat $p/related_cpus) -> $(cat $p/cpuinfo_max_freq)"
done
```

## DVFS

Driver is **`qcom-cpufreq-hw`** — the hardware DVFS block (OSM/EPSS). Frequency
selection happens in hardware from a hint, rather than the kernel writing PLL
registers itself, which is why this needed nothing: mainline already supports it
and the firmware already set it up.

Operating points, per cluster:

```
policy0   300000 … 1785600 kHz   (18 steps)
policy4   710400 … 2419200 kHz   (17 steps)
policy7   825600 … 2841600 kHz   (20 steps)
```

Note the silver cluster idles all the way down to 300 MHz while the Golds floor
at 710/825 MHz — the little cores are where sustained low-power work belongs, and
the scheduler knows it (below).

Governor is **`schedutil`** on all three policies, which is the correct choice
here: it is driven by the scheduler's own utilisation signal rather than sampling
load after the fact, so it reacts in the same pass that decides placement.
`conservative`, `ondemand`, `userspace` and `performance` are also built in.

## Scheduling: EAS is live

The part that actually matters for a battery-powered tablet. Two things have to be
true for the scheduler to place work by *energy* rather than by load alone, and
both are:

```
$ ls /sys/kernel/debug/energy_model/
cpu0  cpu4  cpu7                     <- a perf/power model per cluster

$ cat /sys/devices/system/cpu/cpu{0,4,7}/cpu_capacity
306   871   1024                     <- asymmetric capacities
```

An energy model for each policy plus asymmetric CPU capacity is what turns on
Energy Aware Scheduling. With it, a light task lands on a Silver core and stays
there instead of being spread across Golds, and the Gold cluster is only woken
when a task genuinely will not fit. Without it the kernel load-balances as if all
eight cores were equal, which on this SoC costs a lot of power for no throughput.

Nothing was done to enable any of this. It works because the DT describes the
capacities and OPPs correctly and mainline does the rest.

## Thermals

Idle at the desktop, with the charger connected:

```
44 °C  cluster0-thermal / cluster1-thermal / compute-thermal / cpu6-top-thermal
43 °C  cpu7-top-thermal
```

Comfortable, and consistent across zones — no single hot spot, and nothing near
throttling. Sustained load has not been characterised; if it ever is, that belongs
here.

## Why this was free, when it was not on the S20

Worth recording the contrast, because it is a useful heuristic for the next port.

On the S20 (Exynos 990) CPU performance was a real bring-up problem: DVFS goes
through the **ACPM** firmware mailbox, and until that was driven the cores sat at
boot clocks, which is exactly the "everything is sluggish for no visible reason"
symptom. See `z3s-mainline-linux/docs/CPU_PERF_DVFS.md`.

On SM8150 the same job is done by a hardware block that mainline has supported for
years, with the operating points published in the SoC DT that upstream already
carries. The lesson is not "Qualcomm good, Exynos bad" — it is that **the amount
of work a subsystem needs depends almost entirely on whether upstream already
carries the SoC-level description**, not on how exotic the device is. The parts of
this port that were painful (Wi-Fi memory carveouts, PS_HOLD shutdown, the SM5705
battery) were all *board*-level things that no upstream DT could have known.
