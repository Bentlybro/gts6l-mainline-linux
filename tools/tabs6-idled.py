#!/usr/bin/env python3
"""
tabs6-idled - idle suspend for the Tab S6 port.

PowerDevil will not do this here. Its idle actions run on built-in defaults and
ignore the profile configuration entirely: writing the timeouts to
powermanagementprofilesrc (legacy nested groups), to powerdevilrc (Plasma 6 flat
keys), and to powerdevilrc with nested groups all leave it registering the stock
300s dim / 600s DPMS, and it registers no SuspendSession action at all. Setting
dimDisplayWhenIdle=false does not even stop the dim action registering, so the
file is not being read rather than the keys being wrong. kreadconfig6 reads the
same keys back correctly, so KConfig is fine - this is PowerDevil.

systemd cannot cover for it either: logind's IdleAction depends on the session
idle hint, and KWin never sets it (IdleHint stays "no" after half an hour with no
input at all).

So idleness is measured here, directly from the evdev nodes, which is the one
source that cannot be wrong: if no input device has produced an event for long
enough, nobody is using the tablet.

PowerDevil is still responsible for blanking the screen (its default 10 minute
DPMS works), and tabs6-powerkeyd still owns the power button. This only adds the
suspend that neither of them will do.
"""

import errno
import os
import select
import subprocess
import sys
import time

# Idle thresholds, seconds. Generous on AC because a plugged-in tablet on a desk
# is usually mid-task; much shorter on battery where sleep is the entire point.
IDLE_ON_AC = 60 * 60
IDLE_ON_BATTERY = 20 * 60

# After a failed suspend, wait this long before trying again rather than
# hammering it every poll.
FAILURE_BACKOFF = 5 * 60

# Do not re-arm instantly after waking: give the user a moment to touch the
# screen before the countdown restarts.
POST_RESUME_GRACE = 60

FUELGAUGE = "/sys/class/power_supply/sm5705-fuelgauge/status"
USER_BUS = "unix:path=/run/user/1000/bus"

POLL = 5.0


def log(msg):
    print(msg, flush=True)


def open_input_devices():
    """Open every evdev node readable. Returns {fd: path}."""
    devs = {}
    try:
        names = sorted(os.listdir("/dev/input"))
    except OSError as e:
        log("cannot list /dev/input: %s" % e)
        return devs
    for name in names:
        if not name.startswith("event"):
            continue
        path = "/dev/input/" + name
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log("skip %s: %s" % (path, e.strerror))
            continue
        devs[fd] = path
    return devs


def drain(fd):
    """Consume pending events so the fd stops being readable."""
    while True:
        try:
            if not os.read(fd, 4096):
                return
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return
            raise


def on_battery():
    try:
        with open(FUELGAUGE) as f:
            return f.read().strip() != "Charging"
    except OSError:
        return False


def kde_inhibited():
    """True if anything asked KDE to keep the system awake (video, etc)."""
    try:
        out = subprocess.run(
            ["dbus-send", "--session", "--print-reply",
             "--dest=org.kde.Solid.PowerManagement",
             "/org/kde/Solid/PowerManagement/PolicyAgent",
             "org.kde.Solid.PowerManagement.PolicyAgent.ListInhibitions"],
            capture_output=True, text=True, timeout=10,
            env=dict(os.environ, DBUS_SESSION_BUS_ADDRESS=USER_BUS),
        )
    except Exception:
        return False
    if out.returncode != 0:
        return False
    # An empty reply is "array [\n   ]"; anything else names an inhibitor.
    body = out.stdout.split("array", 1)[-1]
    return len([ln for ln in body.splitlines() if ln.strip() not in ("[", "]", "")]) > 0


def systemd_block_inhibited():
    """True if something holds a block-mode sleep/idle inhibitor.

    PowerDevil's own handle-power-key block is expected and ignored - it is
    about who handles the buttons, not about staying awake.
    """
    try:
        out = subprocess.run(["systemd-inhibit", "--list", "--no-legend"],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return False
    for line in out.stdout.splitlines():
        if "block" not in line:
            continue
        what = line.split()
        if not any(w.startswith("sleep") or w.startswith("idle") for w in what):
            continue
        if "org_kde_powerde" in line or "PowerDevil" in line:
            continue
        return True
    return False


def suspend():
    r = subprocess.run(["systemctl", "suspend"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        log("suspend failed: %s%s" % (r.stdout.strip(), r.stderr.strip()))
        return False
    return True


def main():
    devs = open_input_devices()
    if not devs:
        log("no input devices could be opened, nothing to measure - exiting")
        return 1
    log("watching %d input devices" % len(devs))

    last_activity = time.monotonic()
    blocked_until = 0.0
    last_beat = 0.0
    beats_seen = 0

    while True:
        # Heartbeat. This exists to prove the daemon actually sees input: if a
        # compositor ever took an EVIOCGRAB on the touchscreen we would observe
        # no events at all, and idle would climb while the tablet was in use.
        # If idle keeps resetting to ~0 while you are using it, evdev is fine.
        if time.monotonic() - last_beat >= 60:
            last_beat = time.monotonic()
            beats_seen += 1
            log("idle %.0fs / %ds (%s)"
                % (max(0.0, time.monotonic() - last_activity),
                   IDLE_ON_BATTERY if on_battery() else IDLE_ON_AC,
                   "battery" if on_battery() else "AC"))
        threshold = IDLE_ON_BATTERY if on_battery() else IDLE_ON_AC
        now = time.monotonic()
        idle = now - last_activity
        wait = POLL if idle + POLL < threshold else max(1.0, threshold - idle)

        try:
            ready, _, _ = select.select(list(devs), [], [], wait)
        except (OSError, select.error) as e:
            log("select failed: %s" % e)
            time.sleep(POLL)
            continue

        if ready:
            for fd in ready:
                try:
                    drain(fd)
                except OSError as e:
                    log("dropping %s: %s" % (devs.get(fd), e.strerror))
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    devs.pop(fd, None)
            last_activity = time.monotonic()
            continue

        now = time.monotonic()
        if now - last_activity < threshold or now < blocked_until:
            continue

        if kde_inhibited() or systemd_block_inhibited():
            # Something is deliberately keeping us awake. Do not fight it, and
            # do not spin: check again after a normal interval.
            last_activity = now
            continue

        log("idle %.0fs (threshold %ds, %s) - suspending"
            % (now - last_activity, threshold, "battery" if on_battery() else "AC"))

        ok = suspend()

        # Whether we suspended and came back, or failed outright, the clock
        # restarts here. A big monotonic jump is normal: s2idle freezes it.
        last_activity = time.monotonic() + (POST_RESUME_GRACE if ok else 0)
        if ok:
            log("resumed")
        else:
            blocked_until = time.monotonic() + FAILURE_BACKOFF


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
