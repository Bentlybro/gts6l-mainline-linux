# USB host mode, VBUS, and the SuperSpeed dead end

**Status: host mode works, VBUS works, SuperSpeed does not.** A 4 TB bus-powered USB-C SSD
runs off the tablet and mounts — at USB 2.0 speeds (25 MB/s). Everything on the tablet side
that could explain that has been eliminated; the remaining suspects are documented below so
nobody repeats the search.

This matters more on a tablet than it sounds: with no built-in keyboard, the port is the
only way to attach one.

---

## 1. Making the port dual-role

It was pinned to peripheral, because the RNDIS + ACM gadget was the development lifeline
back when nothing else worked:

```dts
&usb_1_dwc3 {
	dr_mode = "otg";
	usb-role-switch;
};
```

That registers a role switch under `/sys/class/usb_role/` — **with no way to operate it**.
The directory has `uevent`, `power`, `device`, `subsystem`, and no `role` file at all.

The reason is in `drivers/usb/roles/class.c`: `usb_role_switch_is_visible()` hides the
attribute unless the switch sets `allow_userspace_control`, and dwc3 never sets it.

```c
/* kernel/patches/dwc3-allow-userspace-role-control.patch */
dwc3_role_switch.allow_userspace_control = true;
```

dwc3 is *right* to hide it in the general case — normally a Type-C controller or an ID pin
knows which way the port should face. This board has neither wired to anything mainline
understands, so the only thing that can know is the person holding it.

**The default stays peripheral on every boot, deliberately.** The gadget is the fallback way
in if Wi-Fi ever breaks, and it should survive a reboot without anyone remembering to restore
it. `tools/usb-role` makes switching a deliberate act.

---

## 2. VBUS: nothing was powering the port

Host mode alone gets you an xHCI controller and a dead port. A bus-powered device plugged
straight in never wakes up.

There is **no VBUS boost GPIO** — checked Samsung's own device tree, the only "boost" hits
there are unrelated network tuning tables. The 5 V supply is inside the **SM5705 charger**,
enabled over I²C.

### Getting the register map rather than guessing

Poking write bits into the chip that controls battery charging by inference is a bad idea.
Web search found nothing useful; **GitHub code search found the entire vendor driver
immediately** (`ananjaser1211/Helios_7870`, and the exynos7870 kernel).

```
SM5705_REG_CNTL      = 0x0C   bits 2:0  operation mode
SM5705_REG_CHGCNTL6  = 0x14   bits 3:2  OTG current limit
SM5705_REG_FLEDCNTL6 = 0x20   bits 3:0  boost output voltage

OP_MODE_CHG_ON  = 0x5     BST_OUT_4500mV    = 0x5
OP_MODE_USB_OTG = 0x7     BST_OUT_5100mV    = 0xB
                          OTG_CURRENT_900mA = 0x2
```

**Then cross-check it against the live part before writing anything.** This is the step that
made it safe rather than reckless:

| register | live chip | vendor table says (at rest, charger attached) |
|---|---|---|
| `0x0C` | `0x05` | `CHG_ON = 0x5` ✓ |
| `0x20` | `0x05` | `BST_OUT_4500mV = 0x5` ✓ |
| `0x14` | `0x08` | OTG current bits = `0x2` = 900 mA ✓ |

Three registers agreeing with an independently obtained table is enough to write against.

`usb-role` also **refuses to source VBUS while charging**. The vendor's own table only ever
selects `USB_OTG` with VBUS absent, and asking the part to source and sink simultaneously is
not a state worth exploring. Do not remove that check.

---

## 3. SuperSpeed: what has already been ruled out

The SSD enumerates at 480 Mbit/s on the USB2 root hub. **Do not repeat any of this:**

- **Not power.** The drive runs.
- **Not the host controller.** The SS root hub exists with a port (`usb2`, `maxchild=1`,
  10000 Mbit/s) and xHCI reports *"Host supports USB 3.1 Enhanced SuperSpeed"*.
- **Not a speed cap.** No `maximum-speed` property anywhere in the live tree; dwc3 has both
  `usb2-phy` and `usb3-phy`.
- **Not the redriver being disabled.** Samsung wire `combo,redriver_en` to GPIO 97 and
  `combo,con_sel` to GPIO 38. GPIO 97 was **already active** at boot.
- **Not the redriver's configuration.** The PTN36502 at `0x1a` on `i2c10` identifies
  correctly (chip id `0x02`, rev `0x12`), accepts `MODE_CTRL1` and holds it. Cross-checked
  against mainline `drivers/usb/typec/mux/ptn36502.c`: for `TYPEC_STATE_USB` that driver
  writes `MODE_CTRL1` **and nothing else** — exactly what was done by hand.
- **Not orientation.** All four combinations were tried: physical cable flip × the redriver's
  `PLUG_ORIENT` bit, with `con_sel` driven both ways. Every one gave 480 Mbit/s.

The device is willing — its BOS descriptor advertises SuperSpeed *and* SuperSpeedPlus
(`wSpeedsSupported = 0x000e`). **But that descriptor is read over the USB 2.0 link**, so it
proves the drive supports SS, not that the cable carries the SS pairs.

### The two remaining suspects

1. **The cable.** Most USB-C cables are USB 2.0 only — every phone charging cable, for a
   start. Cheapest thing to eliminate, and it has not been eliminated yet.
2. **The PHY's orientation is pinned.** `phy-qcom-qmp-combo.c` sets
   `qmp->orientation = TYPEC_ORIENTATION_NORMAL` at probe and registers a Type-C switch for
   something else to correct. Nothing drives that switch here — there is no Type-C port
   manager, because the PD controller is an **s2mm005** with no mainline driver. Flipping
   the redriver does not help if the PHY behind it disagrees. This is the real work.

---

## 4. Using it

```sh
usb-role            # show current role
usb-role host       # host + VBUS on  (keyboard, SSD, hub)
usb-role device     # back to the RNDIS/ACM gadget, VBUS off
```

Resets to `device` on every boot by design.

Driving VBUS from a shell script is a **stopgap**. The right home for it is a charger driver
exposing a VBUS regulator that the role switch drives — the same driver that would give
charge control, on a chip that is already reachable at `0x49`.
