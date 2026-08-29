#!/bin/sh
# Keep the SM5705 charging at a sensible rate.
#
# Why this is needed: the charger powers up with USB safe defaults - 500 mA in,
# 100 mA into the battery. On a stock device the MUIC driver identifies the
# cable and tells the charger it may draw more. Nothing drives the MUIC here, so
# without this the tablet charges at a trickle regardless of what it is plugged
# into.
#
# The registers also reset when the cable is inserted or the charger changes
# mode, so this has to be maintained rather than set once.
#
# Encoding and clamps taken from the Silicon Mitus/Samsung driver:
#   VBUSCNTL  0x0D  offset = ((mA - 100) / 25) & 0x7F   max 3275 mA  (input)
#   CHGCNTL2  0x10  offset = ((mA - 100) / 50) & 0x3F   max 3250 mA  (battery)
#
# Asking for more than the supply can give is safe: AICL (automatic input
# current limit) walks the input back until the source voltage holds up, so a
# weak charger simply ends up supplying what it can rather than collapsing.
#
# It never touches anything while the port is sourcing power (USB_OTG), because
# in that mode the chip is a supply, not a charger.

set -eu

BUS=1
ADDR=0x49
REG_CNTL=0x0c
REG_VBUSCNTL=0x0d
REG_CHGCNTL2=0x10

OP_MODE_CHG_ON=5

# Targets. The tablet ships with a 15 W charger; 2000 mA in at 5 V is about
# 10 W, and 2000 mA into a 7040 mAh pack is roughly 0.28C - well within what
# this battery is charged at normally.
INPUT_MA=${TABS6_INPUT_MA:-2000}
CHARGE_MA=${TABS6_CHARGE_MA:-2000}

INPUT_OFF=$(( ((INPUT_MA - 100) / 25) & 0x7F ))
CHARGE_OFF=$(( ((CHARGE_MA - 100) / 50) & 0x3F ))

log() { logger -t tabs6-charge -- "$1"; }

log "target ${INPUT_MA}mA input (offset $INPUT_OFF), ${CHARGE_MA}mA charge (offset $CHARGE_OFF)"

while :; do
	mode=$(( $(i2cget -y $BUS $ADDR $REG_CNTL b 2>/dev/null || echo 0) & 7 ))

	if [ "$mode" = "$OP_MODE_CHG_ON" ]; then
		cur_in=$(i2cget -y $BUS $ADDR $REG_VBUSCNTL b 2>/dev/null || echo 0)
		cur_chg=$(i2cget -y $BUS $ADDR $REG_CHGCNTL2 b 2>/dev/null || echo 0)

		# Only ever raise. If something else has set a higher limit, leave it.
		if [ $((cur_in)) -lt $INPUT_OFF ]; then
			i2cset -y $BUS $ADDR $REG_VBUSCNTL $INPUT_OFF b
			log "raised input limit $(( (cur_in) * 25 + 100 ))mA -> ${INPUT_MA}mA"
		fi

		if [ $((cur_chg)) -lt $CHARGE_OFF ]; then
			i2cset -y $BUS $ADDR $REG_CHGCNTL2 $CHARGE_OFF b
			log "raised charge current $(( (cur_chg) * 50 + 100 ))mA -> ${CHARGE_MA}mA"
		fi
	fi

	sleep 10
done
