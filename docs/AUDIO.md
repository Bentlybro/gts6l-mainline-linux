# Audio: not working, and exactly where it stops

**Status: blocked at the first step.** The ADSP will not boot, and without it
nothing else in the chain can be attempted. This document records what the
hardware actually is, how far the boot gets, and the two corrections made along
the way, so the next attempt starts from facts rather than from my first guesses.

---

## The hardware is not what the kernel device tree says

The first two things I concluded about audio on this device were both wrong, for
the same reason, and the reason is worth stating before anything else.

**The board audio description lives in the DTBO overlay, not the kernel DTB.**
Searching `kernel_dtb_0_*.dts` finds a `tavil` (Qualcomm WCD9340) codec on
SLIMbus and no Cirrus parts at all, which leads straight to two false
conclusions: that the codec is a WCD9340, and that the `cs35l41` firmware sitting
in `/vendor/firmware` is generic Samsung content not used here.

The overlay says otherwise:

- the `tavil` node is `status = "disabled"`, as are `pahu`, `aqt1000`, all four
  WSA881x SoundWire speakers, `wcd9xxx-irq` and the `fsa4480` USB-C audio switch
- there are **four `cirrus,cs35l41` nodes** (I2C, addresses 0x40 to 0x43) and one
  **`cirrus,cs48l33`** codec on SPI
- the sound card is Samsung's own `qcom,sm8150-asoc-snd-cooke`, which *overwrites*
  the stock `sound-tavil` node

So: four Cirrus boosted speaker amplifiers and a Cirrus smart codec, and the
`cs35l41` firmware in vendor is exactly right rather than incidental.

This is the same trap as Bluetooth, whose node is also overlay-only. If you read
only the kernel DTB you will conclude this tablet has neither Bluetooth nor
Cirrus audio. See [BLUETOOTH.md](BLUETOOTH.md).

## Where it stops

Everything begins with the ADSP, because the q6 audio stack talks to it over
APR. `sm8150.dtsi` already describes `remoteproc_adsp` (`qcom,sm8150-adsp-pas`)
complete with a `glink-edge`; it is simply disabled and points `firmware-name` at
the Surface Duo path.

Pointed at this device's own firmware, the boot gets a long way and then stops in
one specific place:

```
remoteproc remoteproc1: powering up 17300000.remoteproc
remoteproc remoteproc1: Booting fw image qcom/sm8150/gts6l/adsp.mdt, size 8060
qcom_q6v5_pas 17300000.remoteproc: error -22 setting up firmware
remoteproc remoteproc1: can't start rproc: -22
```

That message is easy to misread as "the firmware was rejected". It is not. In
`drivers/soc/qcom/mdt_loader.c` the sequence is:

```c
ret = qcom_scm_pas_init_image(pas_id, metadata, metadata_len, ctx);
if (ret) { dev_err(dev, "error %d initializing firmware %s\n", ...); }   /* NOT hit */

if (relocate) {
        ret = qcom_scm_pas_mem_setup(pas_id, mem_phys, max_addr - min_addr);
        if (ret) { dev_err(dev, "error %d setting up firmware %s\n", ...); }  /* hit */
```

`pas_init_image` runs first and **succeeds**, so TrustZone has parsed and
authenticated Samsung's own signed ADSP image. The failure is the next call:
TZ accepts the firmware and then refuses to let us place it.

### What has been ruled out

| Attempt | Result |
|---|---|
| canonical `adsp_mem`, 0x8be00000, 26 MB | identical `-22` |
| 39 MB at 0x9f800000, inside a 520 MB hole with nothing reserved | identical `-22` |

So it is neither the address nor the size. Relocating the region did not disturb
Wi-Fi, the modem or anything else, which was the safety question worth answering.

### The unexplained contradiction, which is probably the thread to pull

Parsing `adsp.mdt`'s program headers with exactly the filter the kernel uses
(`mdt_phdr_valid`: PT_LOAD, not `QCOM_MDT_TYPE_HASH`, non-zero `p_memsz`) gives a
loadable span of **39 MB**, 0x8be00000 to 0x8e500000.

But `adsp_mem` is 26 MB in mainline **and in Samsung's own downstream device
tree**, and it cannot simply be grown in place because it would run into
`mpss_mem` at 0x8d800000, an address fixed by the signed modem image.

Downstream therefore boots this image into a region smaller than its own loadable
span. Either its PIL skips segments mainline counts (segment 12 is a 16 MB
`filesz=0` BSS block, and several high segments may live in ADSP-internal memory
rather than DDR), or the relocation works differently. Resolving that is likely
the same insight that unblocks `pas_mem_setup`.

## Firmware: present, complete, verified

Nothing is missing, which is worth knowing before hunting for it.

- **ADSP**: `adsp.mdt` plus 17 segments (~17.9 MB), build
  `ADSP.HT.5.0.c1-00046-SM8150-1`, from the **apnhlos** partition. The gaps at
  `b12`/`b17` are `p_filesz=0` NOBITS, so no files should exist for them.
- **Cirrus**: `cs35l41-dsp1-spk-prot.{wmfw,bin}` plus calibration, and
  `cs48l32-dsp1-ctrl.{wmfw,bin}`, in `/vendor/firmware`.

Note the `dsp` partition holds ADSP *codec modules* (`.so.1` files) and
`fastrpc_shell_0`, not the ADSP image. The image is in apnhlos, alongside the
modem's.

## What remains after the ADSP boots

Worth being honest that the ADSP is the first step, not the last:

1. **The APR/q6 subtree does not exist for SM8150.** There are no `apr`,
   `q6core`, `q6afe`, `q6asm`, `q6adm`, `q6routing` or `slim-ngd` nodes in
   `sm8150.dtsi` in 6.12 or in current master. `sdm845.dtsi` and `sm8250.dtsi`
   both have them, so this is a port rather than new driver work, but it is a
   substantial one.
2. **Then the Cirrus parts**: four CS35L41 on I2C plus the CS48L33 on SPI, with
   audio reaching the amps over Secondary TDM (AFE port 0x9010) and the codec
   over Quinary MI2S (SD0/SD1 on gpio150/gpio152).
3. **Then a sound card** binding the DAI links.

The drivers for the Cirrus parts are in good shape upstream. The gap is entirely
the Qualcomm DSP plumbing beneath them, and the ADSP refusing to start is the
first brick in that wall.
