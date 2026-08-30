#!/bin/bash
# Set the Bluetooth public address the controller does not know about itself.
#
# The WCN3990 comes up as an "Unconfigured controller": it has no public
# address, so BlueZ refuses to expose it at all and bluetoothctl reports "No
# default controller available" even though hci0 exists and setup completed.
#
# Samsung keeps the real per-device address in /efs/bluetooth/bt_addr on the
# Android EFS partition, and neither the NVM nor the device tree carries it.
# It is device identity, so it lives in a local file here rather than in the
# device tree, which is public.
set -u
ADDR_FILE=/etc/tabs6-bt-address
[ -r "$ADDR_FILE" ] || { echo "no $ADDR_FILE"; exit 0; }
ADDR=$(tr -d "[:space:]" < "$ADDR_FILE")
case "$ADDR" in
    [0-9A-Fa-f][0-9A-Fa-f]:*) ;;
    *) echo "not a MAC: $ADDR"; exit 1 ;;
esac
for i in $(seq 1 30); do
    [ -e /sys/class/bluetooth/hci0 ] && break
    sleep 1
done
if btmgmt info 2>/dev/null | grep -q "addr $ADDR"; then
    echo "already configured"; exit 0
fi
btmgmt --index 0 public-addr "$ADDR" 2>&1 | head -2
