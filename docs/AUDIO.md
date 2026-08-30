# Audio

Audio works: speakers play, and the desktop has a real sink. What follows is
both the answer and the wrong turns, because the wrong turns are the useful
part: every one of them looked like a dead end and none of them was.

| piece | state |
| --- | --- |
| ADSP (remoteproc, `qcom,sm8150-adsp-pas`) | **works** |
| GLINK edge to the ADSP | **works** |
| APR + q6core / q6afe / q6asm / q6adm | **works** |
| Secondary TDM back end, CS35L41 amps, sound card | **works** |
| Desktop sink (PipeWire via ALSA UCM) | **works** |
| headphone jack (CS48L33) | not reachable upstream today, see section 6 |

---

## 1. The hardware is not what the kernel device tree says

The single most expensive mistake in this subsystem, made twice.

`sm8150.dtsi` describes a WCD9340 (`tavil`) and mainline points the ADSP at the
Surface Duo's firmware path. Neither is right for this board:

* `tavil` is `status = "disabled"` on this device.
* the real parts are four **`cirrus,cs35l41`** speaker amps and a
  **`cirrus,cs48l33`**.
* the `cs35l41` firmware sitting in the vendor partition is exactly right for
  this device, not generic Samsung filler.

All of it lives in the **DTBO overlay**, which is also where the Bluetooth node
hides, and where the *correct* TDM timing hides (section 5). Searching the kernel
DTB and concluding "this hardware is not described" is wrong three times over on
this device. Search the overlay.

## 2. The ADSP: TrustZone authenticates the image, then refuses to place it

The symptom for days:

```
remoteproc remoteproc1: Booting fw image qcom/sm8150/gts6l/adsp.mdt, size 8060
qcom_q6v5_pas 17300000.remoteproc: error -22 setting up firmware
```

That message reads like a rejected image. It is not.
`qcom_scm_pas_init_image()` runs **first** and **succeeds**, so TZ has parsed and
authenticated Samsung's own signed firmware. The failure is the next call:

```c
qcom_scm_pas_mem_setup(pas_id, mem_phys, max_addr - min_addr);
```

TZ accepts the firmware and then refuses to let us place it.

### The rule

The size argument is the image's **loadable span read out of the ELF**, and has
nothing to do with how big the reserved region is. For this adsp.mdt that span is
`0x2700000`, 39 MB. TZ then enforces two things at once:

1. the region must be **at least** the full span, and
2. the region must not **overlap** anything TZ already owns.

The canonical `adsp_mem` at `0x8be00000` fails both. Declared 26 MB it is too
small; grown to 39 MB it runs into `mpss_mem` at `0x8d800000` where the modem is
already live. **There is no size that works at that address**, which is exactly
why every experiment came back looking identical.

### Why "-22" told us nothing

`qcom_scm_pas_mem_setup()` ends with `return ret ? : res.result[0]`, and
`qcom_scm_call()` runs TZ's status through `qcom_scm_remap_error()`, which knows
five small codes and flattens everything else onto `-EINVAL`. TZ is not returning
one of those five. Making the same call as a bare `arm_smccc_smc()` shows:

```
0xffcfffba  (-3145798)   valid peripheral, refused
0xffcfffeb  (-3145749)   valid peripheral, not in the initialised state
```

Sweeping a raw `MEM_SETUP` across PAS ids 0..20 splits them cleanly between those
two codes, and the ids answering `0xffcfffeb` line up with the `qcom,pas-id`
values downstream declares. That is how we know pas_id 1 is recognised and the
problem was never the peripheral id.

### Measured, not reasoned

All after a successful `pas_init_image`:

| address | size | result |
| --- | --- | --- |
| 0x8be00000 | 26 MB | refused |
| 0x8be00000 | 39 MB | refused (overlaps the live modem) |
| 0x98900000 | 26 MB | refused (smaller than the span) |
| **0x98900000** | **39 MB** | **accepted** |
| 0x98900000 | 40 MB | accepted |
| 0x98900000 | 45 MB | accepted |

### The fix

Four device tree lines. `adsp_mem` moves to `0x98900000` with 40 MB, which is the
cdsp/venus/slpi block and none of those run here; venus and slpi shuffle up
behind it; and the now unused `cdsp_mem` is parked on the old `0x8be00000`
carveout so that region stays out of System RAM, because Samsung's firmware still
believes the ADSP lives there.

Do not tidy this back to the canonical address.

There is also a ceiling: usable DRAM ends near `0x9ff90000` with a hole above it,
so a 39 MB region at `0x9f800000` runs off the end of RAM and is refused for a
completely different reason. An earlier attempt failed there and that failure was
misread as evidence that the address did not matter.

### The same bug, already solved once

`mpss_mem` is 160 MB because modem.mdt's span is exactly `0xa000000`. Identical
rule, different subsystem, fixed weeks earlier by widening the region without
writing down the general statement. **The region must equal the ELF span, and the
span comes from the image, not from downstream's device tree.**

### How it was actually found

Not by reasoning. `qcom_scm_pas_init_image`, `qcom_scm_pas_mem_setup`,
`qcom_scm_pas_shutdown`, `qcom_scm_assign_mem` and `qcom_mdt_read_metadata` are
all `EXPORT_SYMBOL_GPL`, so a throwaway out-of-tree module can make exactly the
calls the remoteproc driver makes, with parameters chosen at insmod time, and
return `-EAGAIN` from its init so it never stays loaded. That turned a 40 minute
build-and-reboot per hypothesis into a one second insmod, and the answer came out
of about twenty of them on a single boot.

One warning from that work: a test that ends in `qcom_scm_pas_auth_and_reset()`
releases a DSP from reset. Doing that when TZ has *not* been told where the image
lives wedged the tablet hard enough to need a physical power cycle. Read the
error code first; only start the processor through the real driver, which has the
watchdog and fatal interrupt handlers wired up.

## 3. Things that look like signals and are not

* `qcom_scm_pas_supported()` returns false for **every** id 0..20 on this
  firmware, including the modem that is running. It is noise.
* `qcom_scm_assign_mem()` HLOS to HLOS on the carveout is refused, but this TZ
  refuses the rmtfs assign too. That is a trait of the firmware, not a fact about
  the ADSP.
* `IS_CALL_AVAIL` reports all five PIL commands present, so nothing is blocked at
  the SCM level.

## 4. APR

With the DSP up, the packet router follows:

```
qcom,apr ...glink-edge.apr_audio_svc: Adding APR/GPR dev: aprsvc:service:4:3
                                      ... 4:4, 4:7, 4:8
drv=qcom-q6core   drv=qcom-q6afe   drv=qcom-q6asm   drv=qcom-q6adm
```

No kernel rebuild was needed: `CONFIG_QCOM_APR` and the whole QDSP6 stack are
already `=m`. sm8150.dtsi has no `apr` node, so ours is the sdm845/sm8250 shape,
which ports cleanly because the ADSP side of APR is firmware rather than SoC.

Two deliberate deviations from upstream, both worth knowing:

* **`qcom,protection-domain` is omitted.** Upstream sets `"avs/audio"` and
  `"msm/adsp/audio_pd"`, which makes apr wait on a PDR lookup served by
  pd-mapper. It is optional, and leaving it out keeps a userspace dependency out
  of the boot path.
* **the q6asm `iommus` SID is inferred.** sdm845 pairs fastrpc compute-cb
  `0x1823` with q6asm `0x1821`; our fastrpc cbs are `0x1b23`/`0x1b24`/`0x1b25`,
  so `0x1b21`. If that is wrong it shows up as an `apps_smmu` context fault
  during playback, not at probe.

## 5. The speaker path

No mainline SM8150 board has audio: not the HDK, not the MTP, not the Surface
Duo, not the Sony Kumano boards, and nothing in 6.18 either. There is nothing to
copy, so this is new work rather than a port.

From the overlay:

* four `cirrus,cs35l41` on **QUP SE1 I2C** (`i2c@884000`, already enabled for the
  da7280 haptics) at 0x40..0x43
* shared reset on tlmm **gpio148**, shared interrupt on tlmm **gpio133**
* VA and VP go to a `dummy_vreg`, an always-on fixed regulator
* `cirrus,boost-peak-milliamp` = 4100 mA, and the driver range checks it
  (1600..4500).
* **the inductor and capacitor are required, despite not being in Samsung's
  overlay.** I first read `bst_ind = -1` on an absent property as "use the OTP
  defaults" and wrote that down here. That was wrong: `cs35l41_boost_config()`
  switches on the value and rejects anything outside 1000/1200/1500/2200 with
  `Invalid boost inductor value: -1 nH`. 1000 nH is the datasheet reference
  design and what every cs35l41 device in mainline uses (Sony Xperia on sm8250,
  sm8350, sm8450). The capacitance only picks a coefficient range and 0..19 uF
  all land in range 0. **The inductor is the one genuine assumption in this
  subsystem**; the peak current, which is what actually protects anything, is
  Samsung's own number.
* **Secondary** TDM, not Quaternary: sck/ws gpio126/127, din gpio128, dout
  gpio129, function `sec_mi2s`
* bit clock 6.144 MHz, which is exactly 4 slots x 32 bits x 48 kHz and agrees
  with `cirrus,fixed-width = <0x20>` and the four amps

### The base DTB lies about the TDM timing

The base kernel DTB and the overlay **disagree**: base has clk-rate `0x177000`,
sync-mode 1, invert-sync 1; the overlay replaces them with `0x5dc000`,
sync-mode 0, invert-sync 0. The overlay is what runs. Reading the base DTB
produces a plausible and wrong configuration.

### The pinmux trap

`&tlmm` carried `gpio-reserved-ranges = <0 4>, <126 4>;`, inherited from the
Surface Duo. That reserves gpio126..129, which is precisely sck, ws, din and dout
of the Secondary TDM bus. The pinmux would have refused to hand them out, giving
no clock and no data, with nothing in dmesg pointing at the cause.

### Machine driver

`sound/soc/qcom/sm8250.c` is the generic q6 machine driver and is 194 lines;
`sdm845.c` is the only upstream one that handles TDM at all and it hardcodes
Quaternary plus db845c's "Left"/"Right" WSA amps. So the Secondary TDM case and a
`qcom,sm8150-sndcard` compatible go into the generic one, which is both smaller
and closer to something upstreamable.

`cs35l41` has no `set_tdm_slot` op; per amp slot selection goes through
`set_channel_map` instead. The slot order (amp N on slot N, in dai-link order) is
an assumption, not something read out of downstream.

### Four things that each failed by naming something else

**Four identical codecs collide on control names.** `control ... Digital PCM
Volume ... is already present` and the card refuses to instantiate with -16. Each
amp needs `sound-name-prefix`. Samsung's `cirrus,mfd-suffix` decodes the layout
for free: 0x41 `""` left, 0x40 `_r` right, 0x43 `_b` bottom left, 0x42 `_br`
bottom right. So L, R, BL, BR, listed in the dai-link in slot order.

**The DSP wants a slot map, not just slot geometry.** `AFE enable for port 0x9010
failed -22`, from command 0x100ef which is `AFE_PORT_CMD_SET_PARAM_V2` - the port
*config*, not the start. `q6afe_tdm_port_prepare()` also sends an
`afe_param_id_slot_mapping_cfg` built from `tdm->ch_mapping`, and that array is
filled only by `set_channel_map` **on the CPU dai**. Call it on the codecs alone
and it goes out all zeroes. Note the units differ: q6afe takes byte offsets into
the frame, cs35l41 takes a slot index.

**The amps need two separate clock calls.** `Enable(1) failed: -110` reads like a
dead amp and is an unconfigured clock. cs35l41 has a *component* level
`set_sysclk` that aims its PLL at the ASP bit clock, and a *DAI* level one that
sets the clock monitor window. Without both, `cs35l41_global_enable()` polls for
PUP_DONE and times out.

**The slot width must equal the sample width.** This is the one that produced
noise rather than music. `cs35l41_pcm_hw_params()` writes `params_width()` into
BOTH the ASP slot width and the ASP word length. Driving 32 bit slots (Samsung's
6.144 MHz) while each amp believes they are 16 makes the amps read the wrong bit
positions: some speakers get real data, the rest get padding and neighbouring
samples, which sounds loud and crunchy at *minimum* amp gain.

cs35l41 supports only S16_LE and S24_LE, and `q6tdm_set_tdm_slot()` accepts only
16 or 32 bit slots, so **16 everywhere is the only self consistent combination**.
The bit clock follows at 3.072 MHz, half of Samsung's. The backend format is
pinned to S16_LE in `be_hw_params_fixup` so a 24 bit stream cannot put it back
out of step.

## 5a. The desktop

PipeWire found the card and produced only a "Dummy Output". WirePlumber was on
the ACP profile path (`api.alsa.use-acp`), which recognises nothing on a q6 card
with 1130 controls. The fix is an **ALSA UCM profile**: one verb enabling
`SEC_TDM_RX_0 Audio Mixer MultiMedia1`, one Speaker device on
`hw:${CardId},0`. WirePlumber then switches to `api.alsa.open.ucm = "true"` and a
real sink appears as the default.

Two placement details worth keeping: the card file is named after the card
**longname** (`Samsung-TabS6WIFI-gts6lwifi-MTP`), and `conf.d` is keyed by the
**driver** name, so the symlink lives under `conf.d/sm8250/` even though the SoC
is sm8150 - our machine driver is the sm8250 one with a Secondary TDM case added.

Those files live inside a directory `alsa-ucm-conf` owns, so they are stashed in
`/usr/local/share/tabs6/ucm2` and restored at boot by
`tabs6-desktop-patches.py`, the same mechanism that guards the lock screen.
Verified by deleting them and watching the boot script put them back.

Nothing plays at boot: the amps only power up for a stream and a cold boot shows
zero cs35l41 power events, and Plasma's login sound is already disarmed in this
build.

## 6. The headphone jack

The `cs48l33` has **no mainline driver**. `sound/soc/codecs` has the `madera`
family (cs47l35/85/90/92) and nothing for this part, so the jack is not reachable
upstream today without writing one.

## 7. Firmware

Nothing is missing. `adsp.mdt` plus 17 segments (build
`ADSP.HT.5.0.c1-00046-SM8150-1`) lives in the **apnhlos** partition, not in the
partition named `dsp`, which holds ADSP codec modules and `fastrpc_shell_0`. The
Cirrus `wmfw`/`bin` tuning files are in vendor.
