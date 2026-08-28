# USB Networking + Serial Lifeline (gts6lwifi / SM8150)

Headless development on the Tab S6 mainline port: **SSH into the tablet over the
single USB-C cable**, plus a serial console on the same cable as a fallback for
when the network side is mid-reconfigure. Once this lifeline exists you never
touch fastboot for kernel/DTB iteration again — you loop-mount the cache ESP on
the *running* tablet over SSH and reboot.

---

## 1. Why a lifeline matters

The tablet has no keyboard, no reliable display bring-up during early port work,
and a single USB-C port. Everything — flashing, logs, iteration — otherwise
funnels through fastboot reboots, which are slow and blind. A USB gadget turns
that one cable into two independent channels at once:

- **RNDIS** → a virtual Ethernet link → `usb0` on the tablet, an "RNDIS adapter"
  on the PC → **SSH, scp, rsync**.
- **ACM** → a USB serial port → `/dev/ttyGS0` on the tablet, a COM port on the
  PC → a **root console** that survives even when the network is down.

With both up you can log in, drop in a new `Image`/DTB, and reboot — all over the
one cable, all without fastboot.

### The dwc3 controller actually works

Early boot logs show a scary line from the dwc3 core:

```
dwc3 a600000.usb: failed to initialize core
```

**This is not a real failure.** It is a *stale deferred-probe snapshot*: dwc3
probes before the QMP USB3+DP **combo PHY** is ready, defers, and the failure
text from that first attempt lingers in the log. Once the QMP combo PHY driver
finishes probing, dwc3 re-probes and the controller comes up cleanly. Do not
chase this line — confirm the PHY arrives, then confirm the UDC registers.

The controller is run in **peripheral (gadget) mode**, not host or OTG:

```
dr_mode = "peripheral";
```

so the PC is always the host and the tablet is always the device.

---

## 2. The gadget recipe: RNDIS + ACM + MS-OS descriptors

A single **configfs COMPOSITE gadget** exposes both functions on one device.
The important trick is the **Microsoft OS descriptors**, which make Windows
auto-bind its in-box RNDIS driver with **no manual driver install and no
`.inf`** — this exact recipe was lifted from the sister **Galaxy S20 (kona)**
port.

> **MACs / IDs below are placeholders.** Generate your own *locally-administered*
> MACs (second-least-significant bit of the first octet = 1, e.g. `02:...`) and
> pick your own VID:PID. Do not copy real device MACs or serials.

```sh
#!/bin/sh
# /usr/local/sbin/usb-gadget.sh — run once at boot (see keep-alive note below)
set -eu

G=/sys/kernel/config/usb_gadget/g1
UDC=a600000.usb            # SM8150 dwc3 controller (the UDC name in /sys/class/udc)

modprobe libcomposite

mkdir -p "$G"
cd "$G"

# --- Device identity -------------------------------------------------------
echo 0x18d1 > idVendor          # placeholder VID
echo 0x0106 > idProduct         # BUMP THIS after changing functions (see quirks)
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

# IAD is required so Windows treats this as a multi-function (composite) device
echo 0xEF   > bDeviceClass       # Misc
echo 0x02   > bDeviceSubClass
echo 0x01   > bDeviceProtocol

mkdir -p strings/0x409
echo "0000deadbeef0000"   > strings/0x409/serialnumber   # placeholder
echo "gts6lwifi"          > strings/0x409/manufacturer
echo "Tab S6 dev gadget"  > strings/0x409/product

# --- Microsoft OS descriptors (device level) -------------------------------
# Makes Windows request the extended compat IDs and auto-bind RNDIS.
echo 1      > os_desc/use
echo 0xcd   > os_desc/b_vendor_code
echo MSFT100 > os_desc/qw_sign

# --- RNDIS function (network) ----------------------------------------------
mkdir -p functions/rndis.usb0
echo "02:00:00:00:00:0a" > functions/rndis.usb0/dev_addr   # tablet-side MAC (placeholder)
echo "02:00:00:00:00:0b" > functions/rndis.usb0/host_addr  # PC-side MAC   (placeholder)
# The MS-OS compatible IDs that tell Windows "this is RNDIS":
echo RNDIS    > functions/rndis.usb0/os_desc/interface.rndis/compatible_id
echo 5162001  > functions/rndis.usb0/os_desc/interface.rndis/sub_compatible_id

# --- ACM function (serial console) -----------------------------------------
mkdir -p functions/acm.GS0

# --- Configuration ---------------------------------------------------------
mkdir -p configs/c.1/strings/0x409
echo "RNDIS + ACM" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower

# Bind the MS-OS descriptors to this config, then link both functions.
ln -s os_desc                configs/c.1                 2>/dev/null || true
ln -s functions/rndis.usb0   configs/c.1/
ln -s functions/acm.GS0      configs/c.1/

# --- Bind to the UDC -> gadget goes live -----------------------------------
echo "$UDC" > UDC
```

What each piece buys you:

| Piece | Effect |
|-------|--------|
| `rndis.usb0` | Virtual Ethernet → `usb0` on tablet, RNDIS adapter on PC. |
| `acm.GS0` | `/dev/ttyGS0` on tablet, COM port on PC. |
| `os_desc/use=1`, `b_vendor_code=0xcd`, `qw_sign=MSFT100` | Device advertises MS OS descriptors; Windows asks for the compat IDs. |
| `compatible_id=RNDIS`, `sub_compatible_id=5162001` | Windows binds its in-box RNDIS driver automatically — no `.inf`, no prompt. |
| IAD (`bDeviceClass=0xEF/0x02/0x01`) | Windows accepts the composite (RNDIS **and** ACM) device. |

After bind, `usb0` appears on the tablet:

```sh
ip link set usb0 up
ip addr add 192.168.137.2/24 dev usb0     # see addressing, below
```

---

## 3. Addressing: two options (ICS vs APIPA)

The tablet's `usb0` always gets a **static IP**. What the PC end looks like
depends on whether you can enable Internet Connection Sharing.

### Option A — ICS (needs admin, gives the tablet internet)

On Windows, open **Network Connections**, right-click the PC's *internet*
adapter (Wi‑Fi/Ethernet) → **Properties** → **Sharing** → *Allow other network
users to connect…* → pick the **RNDIS adapter** as the home/shared network.

ICS is opinionated and hardcodes the PC side:

- PC RNDIS adapter is forced to **192.168.137.1/24**.
- Windows NATs the tablet's traffic out through the internet adapter, so the
  **tablet gets real internet** (pacman/apt, git clone, etc.).

Tablet side:

```sh
ip addr add 192.168.137.2/24 dev usb0
ip route add default via 192.168.137.1
# DNS: echo "nameserver 192.168.137.1" > /etc/resolv.conf   (or a public resolver)
```

SSH target: `ssh root@192.168.137.2` from the PC (PC is `.137.1`).

### Option B — APIPA (no admin, no PC-network changes)

If you can't or don't want to run ICS (it needs admin, and it reshuffles the
PC's sharing config), just let Windows do nothing: with no DHCP and no static
config, Windows auto-assigns a link-local **APIPA** address `169.254.x.x` to the
RNDIS adapter. Put the tablet on the same `/16`:

```sh
ip addr add 169.254.10.2/16 dev usb0
# no default route via this link — it's link-local only, no internet
```

This costs you nothing on the PC network, but it has **two sharp edges**:

1. **Windows re-rolls the APIPA on every re-enumeration.** Each time the gadget
   re-plugs (reboot, UDC rebind, idProduct bump), Windows may pick a *new*
   `169.254.x.x`. Re-check `ipconfig` on the PC after any re-enumeration and
   update your SSH target accordingly.
2. **Multiple `169.254` interfaces → you must source-bind.** A laptop with
   Wi‑Fi often *also* has a `169.254` address on another adapter. Link-local has
   no gateway, so Windows can send your packets out the wrong interface. Pin the
   source address explicitly:

   ```
   ssh -b <pc-rndis-apipa> root@169.254.10.2
   ping -S <pc-rndis-apipa> 169.254.10.2
   ```

   Without `-b` / `-S`, traffic silently leaves the wrong NIC and the link looks
   "dead" when it isn't.

**Rule of thumb:** ICS if you want the tablet online and have admin; APIPA for a
zero-config, zero-privilege link where you only need SSH/scp.

---

## 4. Windows quirks worth knowing

- **No usable CDC-NCM driver on this PC.** NCM is the cleaner, more modern gadget
  class, but on this machine the NCM device always came up as **Error** in Device
  Manager (no in-box class driver bound). **RNDIS was the only option that
  worked** — hence the RNDIS + MS-OS-descriptor recipe above rather than
  `ncm.usb0`.
- **Windows caches the device config per VID:PID.** Windows keys its stored
  descriptor/driver state on `idVendor:idProduct`. If you change the gadget's
  *functions* (add/remove ACM, flip NCM↔RNDIS, reorder) but keep the same
  VID:PID, Windows serves the **stale cached config** and the device mis-binds or
  shows errors. **Bump `idProduct`** (e.g. `0x0106` → `0x0107`) to force Windows
  to treat it as a new device and re-enumerate cleanly. Treat idProduct as a
  "config generation" counter while iterating on the gadget shape.

---

## 5. Serial fallback (`/dev/ttyGS0`)

The ACM function is your **out-of-band console** for exactly the moments the
network is being torn down and rebuilt.

- **Tablet:** `/dev/ttyGS0`, with a `serial-getty` set to **autologin as root**:

  ```ini
  # /etc/systemd/system/serial-getty@ttyGS0.service.d/override.conf
  [Service]
  ExecStart=
  ExecStart=-/sbin/agetty --autologin root --keep-baud 115200,57600,38400,9600 %I $TERM
  ```

  ```sh
  systemctl enable serial-getty@ttyGS0.service
  ```

- **PC:** the ACM shows up as a **COM port**; open it with PuTTY / `screen` /
  `minicom` at 115200. You get a root shell even with `usb0` unconfigured.

**Caveat — don't rebind the UDC live.** The serial console rides the *same*
gadget. A live UDC rebind (`echo "" > UDC; echo a600000.usb > UDC`) tears down
**all** functions, which **kills the very serial session you're typing in**. So:

> **Change the gadget's identity (idProduct, functions, MACs) by editing the
> setup script and rebooting — never by rebinding the UDC live.** Reboots are
> cheap; a self-severed console mid-edit is not.

Use the serial console to *watch* a reboot and *reconfigure networking*, not to
hot-swap the gadget under your own feet.

---

## 6. SSH + loop-mount-ESP iteration workflow

This is the payoff. Once SSH works, kernel/DTB iteration never touches fastboot
again.

### One-time: install your key over serial

Generate a **dedicated** keypair on the PC (keep it separate from your normal
identity):

```
ssh-keygen -t ed25519 -f ~/.ssh/gts6lwifi -C gts6lwifi-dev
```

Over the **serial console** (no network needed), paste the *public* key into
both accounts:

```sh
install -d -m700 /root/.ssh
cat >> /root/.ssh/authorized_keys <<'EOF'
ssh-ed25519 AAAA...your-public-key... gts6lwifi-dev
EOF
chmod 600 /root/.ssh/authorized_keys

# and for the normal user
install -d -m700 -o <user> -g <user> /home/<user>/.ssh
# append the same pubkey to /home/<user>/.ssh/authorized_keys, chown to <user>
```

Then SSH in:

```
ssh -i ~/.ssh/gts6lwifi root@192.168.137.2      # (or the APIPA target)
```

### Iterating: loop-mount the cache ESP on the running tablet

Instead of `fastboot flash`, mount the **cache ESP partition** *while the tablet
is running*, drop in a fresh `Image`/DTB, and reboot. The boot chain reads the
kernel from that ESP, so the next boot picks up your changes.

There's a **block-size mismatch to work around**: the UFS device presents a **4K
logical block**, but the FAT filesystem on that ESP was **formatted with a 512-byte
sector size**. A plain mount fails because the sector sizes disagree. Force a
512-byte loop device on top of the partition:

```sh
# /dev/sda27 = the cache ESP holding Image + DTB
LOOP=$(losetup -f --sector-size 512 --show /dev/sda27)
mkdir -p /mnt/esp
mount "$LOOP" /mnt/esp

# drop in the new kernel + DTB (scp'd from the PC over the same USB link)
cp /root/build/Image      /mnt/esp/Image
cp /root/build/gts6lwifi.dtb /mnt/esp/dtb/gts6lwifi.dtb

sync
umount /mnt/esp
losetup -d "$LOOP"

reboot
```

From the PC the loop looks like:

```
scp -i ~/.ssh/gts6lwifi Image gts6lwifi.dtb root@192.168.137.2:/root/build/
ssh -i ~/.ssh/gts6lwifi root@192.168.137.2 '/root/deploy-and-reboot.sh'
```

Edit → build → scp → loop-mount → reboot → SSH back in. **No fastboot in the
loop.**

---

## 7. Keep the link alive: mask suspend

The USB gadget lives only as long as the controller is powered. When the
desktop session idle-suspends, the SoC drops the gadget and **your SSH and
serial link both die** — Plasma's idle-suspend is the usual culprit on this
image. Mask the sleep targets so the tablet never suspends out from under you:

```sh
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

Also disable Plasma's own idle "Suspend session" energy-saving action (Power
Management settings), since that path can bypass the systemd targets. With
suspend masked, the gadget stays bound, `usb0` stays up, and the lifeline
survives an idle tablet on your desk.

---

### Quick reference

| Channel | Tablet | PC | Use |
|---------|--------|----|-----|
| RNDIS | `usb0`, static IP | RNDIS adapter (`192.168.137.1` via ICS, or `169.254.x.x` APIPA) | SSH / scp / rsync |
| ACM | `/dev/ttyGS0`, autologin root | COM port @ 115200 | Console when network is down |

| Gotcha | Fix |
|--------|-----|
| dwc3 "failed to initialize core" | Ignore — stale deferred-probe log; PHY arrives, dwc3 re-probes. |
| Changed gadget functions, Windows mis-binds | Bump `idProduct` to force clean re-enumeration. |
| NCM shows Error in Device Manager | Use RNDIS; no in-box NCM driver on this PC. |
| APIPA target keeps changing | Windows re-rolls `169.254.x.x` on every re-enumeration — re-check `ipconfig`. |
| Traffic leaves wrong NIC (multiple `169.254`) | Source-bind: `ssh -b <pc-apipa>`, `ping -S <pc-apipa>`. |
| Serial session dies when reconfiguring gadget | Never rebind UDC live — edit setup script + reboot. |
| ESP won't mount (4K vs 512 sectors) | `losetup -f --sector-size 512 /dev/sda27`, mount the loop. |
| Link dies when tablet idles | `systemctl mask sleep.target suspend.target …` + disable Plasma idle-suspend. |
