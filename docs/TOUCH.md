# Multitouch Bring-up — gts6lwifi (SM8150)

Bring-up notes for the touchscreen on the Samsung Galaxy Tab S6 Wi-Fi
(`gts6lwifi`, SM8150 / Snapdragon 855) mainline port.

This device fought back on almost every layer of the stack: the wrong
in-tree driver, a firmware quirk that aborts probe, a GSI-only I2C bus
that forbids FIFO mode, and a regulator-cleanup path that silently cuts
power out from under the panel. Each of those is documented below in the
order you hit them during bring-up.

---

## 1. Identifying the controller and protocol

The touch controller is an **STMicroelectronics FTS1BA90A**.

The trap here is the vendor. STM ships a mainline driver — `stmfts`
(`drivers/input/touchscreen/stmfts.c`) — and the part number *looks* like
it should match it. **It does not.** The FTS1BA90A on this device does
**not** speak the protocol `stmfts` expects. It speaks the **Samsung
SEC-TS protocol** (16-byte touch events), the same protocol Samsung uses
across its recent tablets/phones. `stmfts` is the **WRONG driver** — it
will not parse events from this controller.

Use the **`fts1ba90a.c`** driver instead — a Samsung SEC-TS-style driver.
It was ported for the Galaxy Tab S8+ (`SM-X800`) mainline port and works
on the Tab S6 controller with one small device-specific patch (see §2).

Source: the `fts1ba90a.c` driver from the
[`aaronsb/sm-x800-linux`](https://github.com/aaronsb/sm-x800-linux)
Tab S8+ port.

**Summary of the identification decision:**

| Property | Value |
|---|---|
| Silicon | STM FTS1BA90A |
| Protocol | Samsung SEC-TS (16-byte events) |
| Firmware id (this device) | `0x0037` |
| Correct driver | `fts1ba90a.c` (SEC-TS style, from `aaronsb/sm-x800-linux`) |
| **Wrong** driver | mainline `stmfts` — do **not** use |
| `compatible` | `st,fts1ba90a` |

---

## 2. The driver + the non-fatal-echo patch

Drop `fts1ba90a.c` into `drivers/input/touchscreen/` and wire it into the
kernel config / Makefile as usual.

### Device-specific patch: make the "no echo for cmd a0" check non-fatal

The Tab S6 firmware (id **`0x0037`**) has a quirk in how it handles the
**scan-on command `a0`**: it emits **no echo** for that command. The
stock driver treats a missing echo for `a0` as a hard error, so probe
fails with **`-110` (`-ETIMEDOUT`)** and the touchscreen never comes up.

Fix: make that check **non-fatal** — downgrade it from an error return to
a `dev_warn()` and `return 0` (success), so probe continues.

Conceptually:

```c
/* scan-on (cmd 0xa0): Tab S6 fw 0x0037 emits NO echo — do not treat as fatal */
ret = fts1ba90a_wait_for_echo(ts, CMD_SCAN_ON /* 0xa0 */);
if (ret) {
        /* Was: dev_err(...); return ret;  -> probe aborts with -110 */
        dev_warn(&ts->client->dev,
                 "no echo for scan-on cmd 0xa0 (fw 0x0037 quirk); continuing\n");
        return 0;
}
```

**Symptom if you skip this patch:** probe fails `-110`.

---

## 3. The big trap: SE17 is GSI-only → GPI DMA is mandatory

This is the trap that costs the most time, so read it before touching the
DTS.

### The bus

The touch controller lives on **i2c17**, which is **QUP2 SE17** at MMIO
**`0xc80000`**. The device is at address **`0x49`**. Interrupt is
**TLMM GPIO 87**, **level-low**.

### Why FIFO mode is impossible

On this device the **boot firmware configures SE17 as GSI-only** — it sets
the SE's `GENI_IF_DISABLE` bit. That disables the programmed-I/O / FIFO
path of the QUP SE. The consequence is absolute: **FIFO-mode I2C on SE17
cannot work.** The SE **requires GPI DMA** for every transfer.

### What you must enable — BOTH of these

1. **`CONFIG_QCOM_GPI_DMA=y`** — build the Qualcomm GPI DMA engine driver.

2. **Enable the `gpi_dma2` controller node.** The relevant DMA engine is
   `dma-controller@c00000` (`gpi_dma2`). In `sm8150.dtsi` this node is
   **`status = "disabled"` by default**, so the I2C controller can never
   acquire a DMA channel until you turn it on:

   ```dts
   &gpi_dma2 {
           status = "okay";
   };
   ```

Missing **either** of these produces the classic failure fingerprint:

- an **I2C `-EPROBE_DEFER` loop** (the I2C controller keeps deferring
  because it can't get a DMA channel), followed by
- **`Failed to setup GPI DMA mode`** / **`Failed to get tx DMA ch`**.

If you see that pair, the fix is `CONFIG_QCOM_GPI_DMA=y` **and**
`&gpi_dma2 { status = "okay"; };` — not anything in the touch driver.

---

## 4. Supplies and the `regulator_late_cleanup` hazard

### Supplies

| Rail | Regulator | Voltage |
|---|---|---|
| `vdd` (digital) | `pm8150_l14a` | 1.8 V |
| `avdd` (analog) | `pm8150_l17a` | 3.0 V |

### The hazard

The kernel's **unused-regulator cleanup** (`regulator_late_cleanup`, which
runs at late init) will **disable any regulator that has no in-kernel
consumer holding it on**. During bring-up — and even afterward, depending
on probe timing — this can **cut `vdd` (l14a) and/or `avdd` (l17a)** out
from under the touch controller.

These rails are **shared with the panel** on this device, so losing them
takes the display down too, not just touch.

Fix: mark **both** rails **`regulator-always-on`** so late cleanup leaves
them alone:

```dts
&vreg_l14a_1p8 {
        regulator-always-on;
};

&vreg_l17a_3p0 {
        regulator-always-on;
};
```

This both keeps touch powered and **protects the shared panel rails**.

---

## 5. The touchscreen DTS node

Add the touch controller under the `i2c17` bus node. Example:

```dts
&i2c17 {
        status = "okay";

        touchscreen@49 {
                compatible = "st,fts1ba90a";
                reg = <0x49>;

                interrupt-parent = <&tlmm>;
                interrupts = <87 IRQ_TYPE_LEVEL_LOW>;

                avdd-supply = <&vreg_l17a_3p0>;   /* 3.0 V analog */
                vdd-supply  = <&vreg_l14a_1p8>;   /* 1.8 V digital */

                touchscreen-size-x = <1600>;
                touchscreen-size-y = <2560>;
                touchscreen-swapped-x-y;
                touchscreen-inverted-x;
        };
};
```

Don't forget the two out-of-node prerequisites from the sections above:

```dts
&gpi_dma2      { status = "okay"; };      /* §3 — GPI DMA is mandatory   */
&vreg_l14a_1p8 { regulator-always-on; };  /* §4 — keep touch/panel powered */
&vreg_l17a_3p0 { regulator-always-on; };
```

---

## 6. Orientation calibration

Getting orientation right is **trial-and-error per device** — the DT
`touchscreen-*` transform properties that match a given panel/digitizer
mounting are not derivable in advance, you rotate and re-test until
touch tracks the cursor.

For the Tab S6 in the **KDE portrait/landscape** setup, the final working
combination was:

- `touchscreen-swapped-x-y`
- `touchscreen-inverted-x`
- `touchscreen-size-x = <1600>`
- `touchscreen-size-y = <2560>`

These are the values that matched this device. Treat them as the known-
good answer for `gts6lwifi`; if you're porting to a sibling with a
differently-mounted digitizer, expect to redo this step.

---

## Quick failure → fix reference

| Symptom | Cause | Fix |
|---|---|---|
| Events don't parse / garbage input | `stmfts` bound (wrong protocol) | Use `fts1ba90a.c` (SEC-TS), `compatible = "st,fts1ba90a"` — §1 |
| Probe fails `-110` (`-ETIMEDOUT`) | fw `0x0037` emits no echo for scan-on `a0` | Make the "no echo for cmd a0" check non-fatal (`dev_warn` + `return 0`) — §2 |
| I2C `-EPROBE_DEFER` loop, then `Failed to setup GPI DMA mode` / `Failed to get tx DMA ch` | SE17 is GSI-only (fw sets `GENI_IF_DISABLE`); FIFO impossible | `CONFIG_QCOM_GPI_DMA=y` **and** `&gpi_dma2 { status = "okay"; }` — §3 |
| Touch (and panel) lose power at late init | `regulator_late_cleanup` disables unclaimed rails | `regulator-always-on` on `l14a` (vdd) and `l17a` (avdd) — §4 |
| Touch tracks but axes wrong/mirrored | Digitizer mounting orientation | `touchscreen-swapped-x-y` + `touchscreen-inverted-x` — §6 |
