# Bluetooth: WCN3990 over UART

**Status: working.** Cold boot to a powered controller with no manual steps.
Scanning finds devices, BR/EDR and LE both come up, and Wi-Fi keeps its full
866.7 MBit/s alongside it.

This also removes the tablet's worst practical constraint. There is one USB-C
port, and in host mode the SM5705 sources power rather than accepting it, so a
wired keyboard and charging were mutually exclusive. A Bluetooth keyboard ends
that. See [BATTERY.md](BATTERY.md) and [USB_HOST.md](USB_HOST.md).

Related: [WIFI.md](WIFI.md) (same chip, completely different transport).

---

## What the hardware is

The WCN3990's Bluetooth side is nothing like its Wi-Fi side. Wi-Fi is
`ath10k_snoc` talking to firmware that runs on the modem DSP. Bluetooth is a
plain HCI UART that mainline's `hci_qca` drives directly, on the application
processor. The only thing they share is the power rails.

| Piece | Value | Where it came from |
|---|---|---|
| Transport | `serial@c8c000`, QUP2 SE3 | the only enabled HS UART downstream |
| Pins | gpio43 CTS, 44 RTS, 45 TX, 46 RX, function `qup13` | downstream `qupv3_se13_uart_pins` |
| Interrupt | `GIC_SPI 585` (`0x249`) | matches downstream exactly |
| Supplies | `s4a_1p8`, `l7a_1p8`, `l2c_1p3`, `l11c_3p3` | the same rails `&wifi` uses |
| Firmware | `qca/crbtfw21.tlv`, `qca/crnv21.bin` | this device's `/vendor/firmware/` |

Kernel config needed nothing: `BT_HCIUART_QCA=y`, `SERIAL_QCOM_GENI=y`,
`SERIAL_DEV_BUS=y` and `BT_HIDP=m` (for keyboards) were already set.

## Four things had to line up

### 1. Mainline never declared the UART

`sm8150.dtsi` describes `0xc8c000` only as `i2c13` and `spi13`. It is one QUP
serial engine that can be i2c, spi *or* uart, and upstream simply never declared
the uart personality. This is the same shape as the touchscreen fight, where the
engine existed but the mode we needed was undescribed (see [TOUCH.md](TOUCH.md)).

Identifying the right engine is easy once you look at the right thing: downstream
has exactly one high-speed UART with `status = "okay"`, and it is this one. A good
cross-check afterwards is the interrupt number: the node compiles to `0x249`,
which is what downstream lists. That distinguishes the right SE from a plausible
neighbour.

> Current torvalds master already adds `uart13` at `0xc8c000` verbatim, so this
> addition matches upstream's own eventual fix.

### 2. `Invalid line -19`

The UART probed and died immediately:

    qcom_geni_serial c8c000.serial: Invalid line -19

`-19` is `-ENODEV` from `of_alias_get_id()`. `qcom_geni_serial` takes its port
index from an alias, trying `serial` then `hsuart`, and with neither present it
fails with that message, which does not mention aliases at all. One line:

```dts
aliases {
	serial1 = &uart13;
};
```

The non-console driver has its own 3-entry port array, so `serial1` does not
collide with `serial0` on the debug console.

### 3. The firmware filename, and a wrong theory worth recording

The chip reports ROM version `0x1001`, so `hci_qca` computes `rom_ver = 0x01` and
requests `qca/crbtfw01.tlv`. The device ships `crbtfw21.tlv`. Result:

    QCA Downloading qca/crbtfw01.tlv
    Direct firmware load for qca/crbtfw01.tlv failed with error -2
    QCA Failed to download patch (-2)

Immediately before the version read there is a `Frame reassembly failed (-84)`,
so the obvious theory is a corrupted response. **That theory was wrong**, and it
cost a rebuild to disprove: the reported values are byte-identical across boots,
and corruption does not repeat.

What settles it is the vendor firmware's own build string, `BTFM.CHE.2.1.4`.
Cherokee 2.1 *is* the `21` firmware, so mainline is deriving a different filename
for this chip rather than misreading it. Supplying the device's own file under the
name the driver asks for works:

```bash
cd /lib/firmware/qca
cp crbtfw21.tlv crbtfw01.tlv
cp crnv21.bin  crnv01.bin
```

```
QCA Downloading qca/crbtfw01.tlv
QCA Downloading qca/crnv01.bin
QCA setup on UART is completed
```

The frame reassembly error disappears once the patch loads, which is a side
effect and not the cause.

> Note the firmware must be uncompressed. linux-firmware ships these as `.xz` and
> this kernel has no compressed firmware loader, the same trap as the Adreno
> microcode in [GPU.md](GPU.md).

### 4. The controller has no address

Loading the patch made things look *worse*. Before it, BlueZ ran happily with a
bogus `00:00:00:00:5A:AD`. After it, `bluetoothctl` reported "No default
controller available" and `btmgmt info` said "Index list with 0 items", while
`/sys/class/bluetooth/hci0` plainly existed.

`btmgmt config` is the command that explains it:

```
Unconfigured index list with 1 item
hci0:	Unconfigured controller
	supported options: public-address
	missing options: public-address
```

The WCN3990 has no public address of its own. Samsung keeps the real per-device
one in `/efs/bluetooth/bt_addr`, and neither the NVM nor the device tree carries
it. Extract it from your own device:

```bash
# the EFS image wants journal recovery, so mount it noload
sudo mount -o loop,ro,noload efs.img /mnt/efs
cat /mnt/efs/bluetooth/bt_addr        # -> AA:BB:CC:DD:EE:FF
```

Then apply it at boot:

```bash
echo "AA:BB:CC:DD:EE:FF" > /etc/tabs6-bt-address
chmod 600 /etc/tabs6-bt-address
systemctl enable --now tabs6-bt-addr    # tools/tabs6-bt-addr.sh
```

**The address is deliberately NOT in the device tree.** `local-bd-address` would
be the tidier mechanism, but a BD address is device identity and this device tree
is published. It lives in a local file instead, and this document tells you how to
find yours rather than shipping mine.

## Verifying

```
$ bluetoothctl show
Controller AA:BB:CC:DD:EE:FF (public)
	Powered: yes
$ btmgmt info
hci0:	Primary controller
	current settings: powered bondable ssp br/edr le secure-conn
```

If `bluetoothctl` says there is no controller, run `btmgmt config`. An
"Unconfigured controller" line means the address step above has not run.
