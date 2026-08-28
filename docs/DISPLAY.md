# DISPLAY — Getting KDE Plasma Visible on gts6lwifi (SM8150)

How the Galaxy Tab S6 (Wi-Fi, `gts6lwifi`, Snapdragon 855 / SM8150) mainline
port puts a **visible KDE Plasma desktop** on screen — without owning the
display hardware.

The one-sentence version: **inherit the framebuffer the bootloader already lit,
paint into it with `simpledrm`, and touch nothing else.**

---

## 1. Strategy: never touch the bootloader display pipe

The Aloha UEFI firmware brings the panel up for us. By the time the kernel
runs, the **ANA38401 dual-DSI panel is already lit at 2560x1600** and scanning
out of a framebuffer in memory. Mainline's job is simply to keep drawing into
that framebuffer and to avoid disturbing anything that keeps it alive.

So the port deliberately does **not** drive the display:

- **`CONFIG_DRM_SIMPLEDRM=y`** — a generic DRM driver bound to a
  `simple-framebuffer` DT node that points at the UEFI framebuffer. This is the
  entire display stack the daily driver uses.
- **`dispcc` is DISABLED.** This is the load-bearing decision. If the display
  clock controller is present, `clk_disable_unused` (which runs late in boot)
  will gate the clocks feeding the still-active display, and the picture dies.
  With `dispcc` absent, there is nothing for the unused-clock cleanup to turn
  off, so the bootloader's clocks keep running untouched.
- **No MSM display nodes.** DPU, DSI, DSI PHY — all left disabled. The moment
  mainline's `msm` display driver probes, it resets and reprograms the pipe the
  bootloader set up, and the inherited framebuffer is lost. We want mainline to
  have *no opinion* about the display.

### The regulator hazard (`regulator_late_cleanup`)

The same failure mode exists for power rails. Any regulator that feeds
bootloader-powered display hardware must be pinned on, or the unused-regulator
cleanup (`regulator_late_cleanup`) will cut power to the live panel:

- `panel_vddlcd` (GPIO 113)
- `l14a`
- `l17a`

Mark every such node **`regulator-always-on`**. If the screen goes black a few
seconds into boot (rather than never coming up at all), suspect a regulator (or
a clock) being reclaimed by late cleanup.

**Rule of thumb:** clocks, regulators, GPIOs, panels — for anything the
bootloader touched to light the screen, mainline's job is to *leave it exactly
as it found it.*

---

## 2. The `simple-framebuffer` DT node

Mainline draws into the framebuffer the UEFI firmware allocated. On this device
that framebuffer is:

| Property | Value |
|----------|-------|
| Base address | `0x9c400000` |
| Resolution | 2560 x 1600 |
| Stride | 2560 * 4 = 10240 bytes |
| Format | `a8r8g8b8` |

Two things are required: a **reserved-memory** region so the kernel never
allocates over the live framebuffer, and a **`simple-framebuffer`** node that
describes it to `simpledrm`.

```dts
/ {
	reserved-memory {
		#address-cells = <2>;
		#size-cells = <2>;
		ranges;

		/* UEFI framebuffer left live by Aloha: 2560*1600*4 = 0x00fa0000 */
		cont_splash_mem: framebuffer@9c400000 {
			reg = <0x0 0x9c400000 0x0 0x00fa0000>;
			no-map;
		};
	};

	chosen {
		#address-cells = <2>;
		#size-cells = <2>;
		ranges;

		framebuffer0: framebuffer@9c400000 {
			compatible = "simple-framebuffer";
			reg = <0x0 0x9c400000 0x0 0x00fa0000>;
			width = <2560>;
			height = <1600>;
			stride = <(2560 * 4)>;
			format = "a8r8g8b8";
			/*
			 * Intentionally NO clocks / power-domains / regulators here.
			 * We do not want simplefb/simpledrm to reference dispcc or any
			 * rail, so nothing it touches can be reclaimed by late cleanup.
			 */
		};
	};
};
```

Notes:

- Size `0x00fa0000` = `2560 * 1600 * 4` = 16,384,000 bytes. Reserve at least
  the full framebuffer; round up if unsure.
- `no-map` keeps the region out of the kernel's linear map so it is never
  reused.
- Do **not** wire clocks/regulators into the `simple-framebuffer` node on this
  device. It must be a pure "memory here, draw here" description. The whole
  point is that mainline holds no references that late cleanup could act on.

If everything is correct you should see the kernel log line where `simple-fb`/
`simpledrm` binds to the framebuffer, and console/plymouth output appears on the
panel with no `msm` display probe in between.

---

## 3. KWin gotcha: run the STOCK compositor, hack nothing

Target userland is the **Fedora 44 KDE spin**, which is **Wayland-only**. On top
of `simpledrm`, **KWin 6.x runs with completely stock configuration** — no
special environment, no tuning.

This is the part that bit us. Pre-emptively "helping" KWin along with
compositor environment variables **broke it**. Setting any of:

- `KWIN_COMPOSE=Q`
- `LIBGL_ALWAYS_SOFTWARE=1`
- `KWIN_DRM_NO_AMS=1`

produced:

```
Failed to find a working output layer configuration
```

and no desktop. **Removing all of those variables and letting KWin start with
its stock defaults made the Plasma desktop appear.**

**Lesson: test the stock compositor first. Never pre-emptively hack compositor
environment variables.** On a simple, well-behaved fixed-mode DRM device, KWin's
own defaults already do the right thing; the "fixes" are cargo-culted from other
platforms and actively steer it into failure. Only reach for environment
overrides if the *stock* configuration genuinely fails, and then one variable at
a time with evidence.

---

## 4. DANGER: never reconfigure the output

`simpledrm` is a **fixed-mode** driver. It exposes exactly **one** mode — the
hardcoded geometry it inherited from the bootloader framebuffer (2560x1600).
There is no modesetting engine behind it; the "mode" is just a description of
memory that is already scanning out.

**Any attempt to change the resolution or scale of the output hard-crashes the
SoC.** This is not a soft failure — the device locks up hard. It has been proven
on the sister Galaxy S20 port.

Concretely, **do not**:

- change resolution or scale in **System Settings → Display Configuration**
- run **`kscreen-doctor`** to set a mode, scale, or position on the output
- use any tool that issues a DRM modeset / `setcrtc` against the `simpledrm`
  connector

To make the UI bigger or smaller, adjust it in userland only, where nothing
touches the output mode:

- **Force Fonts DPI** (System Settings → Text & Fonts) — the primary lever.
- Plasma / Qt scaling via config and font sizes.
- Per-app font and toolbar-icon sizing.

**Never scale the output; only scale what's painted into it.** Treat the
`simpledrm` connector as read-only geometry.

---

## 5. The future native-display path (a separate quest)

`simpledrm` + a **render-only GPU** (Adreno 640 for compositing/GL, no KMS) is
the **daily driver** today, and it is deliberately so. Bringing up the real
panel is a distinct, later effort — not a tweak to the above.

The native display is:

- **ANA38401 dual-DSI, command mode**, bonded **2x 1280x1600** (= 2560x1600),
  **no DSC**.
- Control GPIOs: **TCON-ready GPIO 140**, **reset GPIO 59**,
  **vddlcd GPIO 113**, **TE GPIO 8**.

Why it is future work: **mainline bonded dual-DSI command mode was broken until
Linux v6.16** — it needed the single-CTL + PHY-usecase series to land. So the
real pipe requires a **>= 6.16 kernel** plus a proper DPU/DSI/PHY + panel driver
bring-up. Until that is done and stable, we stay on `simpledrm`.

Consequences of not owning the pipe, which the native path is what unlocks:

- **No brightness control** — the backlight is wherever the bootloader left it.
- **No DPMS / blanking** — the panel cannot be powered down or re-enabled from
  the OS.

Both **brightness and DPMS require the native pipe**; there is no way to bolt
them onto `simpledrm`.

When someone does take on the native display, note that this is the point where
Sections 1–4 **invert**: you *will* enable `dispcc`, DPU, DSI, and the panel
driver, you *will* let regulators be managed normally, and the output becomes a
real modesettable KMS device. That transition must be done wholesale on a
>= 6.16 kernel — you cannot half-enable the MSM display stack while still
leaning on the inherited framebuffer, because the first `msm` probe tears the
inherited framebuffer down.

---

### TL;DR

- Keep the bootloader's framebuffer alive: `simpledrm` only, `dispcc` off, no
  `msm` display nodes, display regulators `always-on`.
- Point `simpledrm` at `0x9c400000`, 2560x1600, stride 2560*4, `a8r8g8b8`.
- Run **stock** KWin on Wayland — no `KWIN_COMPOSE` / `LIBGL_ALWAYS_SOFTWARE` /
  `KWIN_DRM_NO_AMS`.
- **Never** change the output's resolution/scale — it hard-crashes the SoC.
  Scale with font DPI instead.
- Native ANA38401 dual-DSI command mode is a separate future port needing a
  >= 6.16 kernel; it's what will bring brightness and DPMS.
