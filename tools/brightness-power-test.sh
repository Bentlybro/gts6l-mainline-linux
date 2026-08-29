#!/bin/sh
# Measure how much power the display actually costs at different brightness
# levels, on a panel that is AMOLED and therefore has no backlight to dominate.
#
# Runs entirely on the device on purpose. Driving it over SSH failed: each poll
# woke the CPU, and that noise was larger than the effect being measured -
# screen-off came out drawing MORE than screen-on at full brightness, which is
# impossible and was the clue that the method, not the panel, was the problem.
#
# Long settle, long sample, and nothing else talking to the machine.

OUT=/root/brightness-power.txt
P=/sys/class/power_supply/sm5705-fuelgauge
U=1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$U/bus
export XDG_RUNTIME_DIR=/run/user/$U
export WAYLAND_DISPLAY=wayland-0

SETTLE=45
SAMPLES=60      # at 2s = 120s of averaging per level

setb() {
	runuser -u fedora -- dbus-send --session \
		--dest=org.kde.Solid.PowerManagement \
		/org/kde/Solid/PowerManagement/Actions/BrightnessControl \
		org.kde.Solid.PowerManagement.Actions.BrightnessControl.setBrightness \
		int32:"$1" >/dev/null 2>&1
}

dpms() {
	runuser -u fedora -- kscreen-doctor --dpms "$1" >/dev/null 2>&1 &
	sleep 5
}

measure() { # label
	sleep $SETTLE
	tot=0
	i=0
	while [ $i -lt $SAMPLES ]; do
		v=$(cat $P/voltage_now)
		c=$(cat $P/current_now)
		tot=$(awk -v t=$tot -v v=$v -v c=$c 'BEGIN{printf "%.5f", t+(v/1e6)*(c/1e6)}')
		i=$((i + 1))
		sleep 2
	done
	awk -v t=$tot -v n=$SAMPLES -v l="$1" \
		'BEGIN{printf "%-24s %+.3f W\n", l, t/n}' >> $OUT
}

{
	echo "start $(date -Is)  battery $(cat $P/capacity)%  status=$(cat $P/status)"
	echo "settle ${SETTLE}s, then $((SAMPLES * 2))s averaged per level"
	echo
} > $OUT

setb 10000; measure "brightness 100%"
setb 5000;  measure "brightness 50%"
setb 1000;  measure "brightness 10%"
dpms off;   measure "screen off (DPMS)"
dpms on
setb 5000;  measure "brightness 50% (repeat)"

{
	echo
	echo "end $(date -Is)  battery $(cat $P/capacity)%"
	echo "NOTE: an AMOLED draws current per lit pixel, so the saving depends"
	echo "entirely on what is on screen. A dark desktop has little to save."
} >> $OUT
