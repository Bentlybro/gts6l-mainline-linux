#!/usr/bin/env python3
"""Tab S6 power-key daemon: phone-style power button behaviour.

Watches the PMIC power key (pm8941_pwrkey on the pm8150 PON block) and the
volume-down key (pm8941_resin on the same block) and gives:

  - short press               lock the session and turn the screen off
  - next short press          turn the screen back on, at the lock screen
  - long press (1.5 s)        Plasma's full power menu: shut down, restart,
                              log out, sleep
  - power + volume down       screenshot, Android style, straight to a file

logind is configured HandlePowerKey=ignore and PowerDevil's own button
actions are disabled, so this daemon is the only thing acting on the key.
If two things handle it, Plasma queues a logout prompt *behind* the lock
screen and the tablet looks wedged.

The keys are read, never grabbed (no EVIOCGRAB), so KWin still sees them and
normal volume handling is untouched. The cost is that the volume-down half of
the screenshot chord also reaches the desktop as a volume-down press. There is
no audio on this port yet, so that currently does nothing; if audio ever lands
and it becomes annoying, the fix is to grab the resin device and re-emit lone
presses through uinput, which is a lot of machinery for a small annoyance.

Runs as root for /dev/input access and loginctl; anything Plasma-facing is
dispatched into the desktop user's Wayland session.
"""

import os
import re
import selectors
import struct
import subprocess
import threading
import time

KEY_POWER = 116
KEY_VOLUMEDOWN = 114
EV_KEY = 0x01
EVENT_FMT = "qqHHi"  # sec, usec (s64 each), type u16, code u16, value s32
EVENT_SIZE = struct.calcsize(EVENT_FMT)

LONG_PRESS_S = float(os.environ.get("POWERKEY_LONG_PRESS", "1.5"))
DESKTOP_USER = os.environ.get("POWERKEY_USER", "fedora")
DESKTOP_UID = os.environ.get("POWERKEY_UID", "1000")

# How far apart the two keys of the screenshot chord may be pressed. A finger
# cannot hit both at the same instant, and Android is similarly forgiving.
COMBO_WINDOW_S = float(os.environ.get("POWERKEY_COMBO_WINDOW", "0.6"))

SHOT_DIR = os.environ.get("POWERKEY_SHOT_DIR",
                          f"/home/{DESKTOP_USER}/Pictures/Screenshots")

# Names the kernel gives the PMIC keys. Matched against
# /proc/bus/input/devices rather than hardcoding event numbers, because the
# numbering moves when input devices probe in a different order.
PWRKEY_NAME = os.environ.get("POWERKEY_NAME", "pm8941_pwrkey")
VOLDOWN_NAME = os.environ.get("POWERKEY_VOLDOWN_NAME", "pm8941_resin")


def log(msg):
    subprocess.run(["logger", "-t", "tabs6-powerkeyd", "--", msg])


def clock():
    # BOOTTIME advances across suspend, unlike monotonic.
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def find_key_device(name):
    """Return /dev/input/eventN for the input device called `name`."""
    try:
        with open("/proc/bus/input/devices") as f:
            blocks = f.read().split("\n\n")
    except OSError as e:
        log(f"cannot read /proc/bus/input/devices: {e}")
        return None

    for block in blocks:
        if f'Name="{name}"' not in block:
            continue
        m = re.search(r"Handlers=.*?(event\d+)", block)
        if m:
            return "/dev/input/" + m.group(1)
    return None


def find_dpms_attr():
    """The connector's dpms attribute, so screen state is read, not assumed."""
    base = "/sys/class/drm"
    try:
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry, "dpms")
            if os.path.exists(path):
                return path
    except OSError:
        pass
    return None


DPMS_ATTR = find_dpms_attr()


# Whether we put the screen to sleep. This is tracked rather than derived,
# because pressing the power key is itself input: KWin wakes the panel on the
# press, so by the time this daemon reads DPMS it already says "On" and every
# press would look like a fresh lock request. That produced the bug where a
# wake press lit the screen for a moment and then blanked it again.
soft_sleep = False


def screen_is_on():
    """Read the real DPMS state. Assume on if it cannot be determined."""
    if not DPMS_ATTR:
        return True
    try:
        with open(DPMS_ATTR) as f:
            return f.read().strip().lower() == "on"
    except OSError:
        return True


def is_asleep():
    """Trust our own flag first, but believe DPMS if it says the panel is off."""
    return soft_sleep or not screen_is_on()


def desktop_command(cmd):
    return ["runuser", "-u", DESKTOP_USER, "--", "env",
            f"XDG_RUNTIME_DIR=/run/user/{DESKTOP_UID}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{DESKTOP_UID}/bus",
            "XDG_SESSION_TYPE=wayland",
            "QT_QPA_PLATFORM=wayland",
            "WAYLAND_DISPLAY=wayland-0"] + cmd


def as_desktop_async(cmd):
    # kscreen-doctor can stay blocked while DPMS is off. Never wait on it from
    # the input loop, or the wake press is queued and handled seconds late.
    subprocess.Popen(desktop_command(cmd), stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def set_dpms(state):
    as_desktop_async(["kscreen-doctor", "--dpms", state])
    log(f"dpms {state} dispatched")


def prompt_power_menu():
    as_desktop_async(["dbus-send", "--session", "--type=method_call",
                      "--dest=org.kde.LogoutPrompt", "/LogoutPrompt",
                      "org.kde.LogoutPrompt.promptAll"])


# Spectacle's command line has changed across releases, and this has to keep
# working across a distro upgrade without anyone remembering to come back and
# fix it. So try the known-good spellings in order, newest first.
#
# --new-instance leads deliberately. Spectacle is KDBusService::Unique: if an
# instance is already running, our process hands its argv to that instance and
# exits 0 *immediately*, while the real capture happens asynchronously over
# there. Two nasty consequences without --new-instance:
#
#   * the file does not exist when our process returns, so a naive check calls
#     it a failure and tries the next spelling - firing several more captures
#   * SpectacleCore::activate() calls deleteWindows() whenever argv carries
#     options, so the chord destroys any Spectacle window the user had open
#
# If the flag is not recognised the invocation fails outright and the ladder
# falls through to the plain spellings, so leading with it costs nothing.
def screenshot_candidates(path):
    return [
        ["spectacle", "--new-instance", "--background", "--fullscreen",
         "--nonotify", "--output", path],
        ["spectacle", "--background", "--fullscreen", "--nonotify", "--output", path],
        ["spectacle", "--background", "--fullscreen", "--output", path],
        ["spectacle", "-b", "-f", "-n", "-o", path],
    ]


def wait_for_shot(path, deadline_s):
    """Wait for the PNG to appear, rather than judging on an instant stat.

    Spectacle may only have *queued* the capture (see the Unique note above),
    and even in the normal case KWin's ScreenShot2 round trip and the PNG
    encode take a moment. Checking immediately reports failure on a screenshot
    that is about to succeed.
    """
    end = time.monotonic() + deadline_s
    while True:
        try:
            if os.path.getsize(path) > 0:
                return True
        except OSError:
            pass
        if time.monotonic() >= end:
            return False
        time.sleep(0.15)


def take_screenshot_blocking():
    """Run in a thread: the input loop must never wait on Spectacle."""
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
        try:
            import pwd
            pw = pwd.getpwnam(DESKTOP_USER)
            os.chown(SHOT_DIR, pw.pw_uid, pw.pw_gid)
        except (KeyError, OSError):
            pass
    except OSError as e:
        log(f"screenshot: cannot create {SHOT_DIR}: {e}")
        return

    path = os.path.join(SHOT_DIR,
                        time.strftime("Screenshot_%Y%m%d_%H%M%S.png"))

    for cmd in screenshot_candidates(path):
        try:
            r = subprocess.run(desktop_command(cmd), capture_output=True,
                               text=True, timeout=25)
        except subprocess.TimeoutExpired:
            # It may still be writing; check before calling it a failure.
            if wait_for_shot(path, 5):
                log(f"screenshot saved (after timeout): {path}")
            else:
                log("screenshot: spectacle timed out")
            return
        except OSError as e:
            log(f"screenshot: cannot run spectacle: {e}")
            return

        if r.returncode != 0:
            # This spelling was rejected outright, so nothing was captured and
            # nothing was queued - move on immediately and try the next.
            err = (r.stderr or r.stdout or "").strip().splitlines()
            log(f"screenshot: {' '.join(cmd[1:3])} rejected rc={r.returncode} "
                f"{err[0] if err else ''}")
            continue

        # Accepted. Give the file time to appear, then STOP either way: retrying
        # a different spelling now would queue a second capture rather than
        # rescue anything, and could overwrite the first.
        if wait_for_shot(path, 8):
            log(f"screenshot saved: {path}")
        else:
            log(f"screenshot: spectacle accepted '{' '.join(cmd[1:3])}' but no "
                f"file appeared at {path}")
        return

    log(f"screenshot FAILED, every spelling was rejected ({path})")


def do_screenshot(was_asleep):
    global soft_sleep

    log("power + volume down: screenshot")
    # The chord's own keys still reach KWin, which lights the panel. If we went
    # on believing the screen was off, that belief is now stale and the NEXT
    # power press gets swallowed as a wake instead of locking - so the tablet
    # sits unlocked until pressed twice. Reconcile here. This also covers the
    # opposite case, where KWin did not light it and we would otherwise
    # photograph a black screen.
    if was_asleep:
        soft_sleep = False
        set_dpms("on")
    threading.Thread(target=take_screenshot_blocking, daemon=True).start()


def do_short_press(was_asleep):
    global soft_sleep

    if was_asleep:
        log("short press: wake")
        soft_sleep = False
        # KWin has usually lit the panel already, on the key press itself.
        # Asking again is harmless and covers the case where it has not.
        set_dpms("on")
        return

    log("short press: lock + screen off")
    subprocess.run(["loginctl", "lock-sessions"], capture_output=True)
    # Let Plasma actually draw and commit the lock screen before blanking,
    # otherwise the unlock prompt can come up behind a black screen.
    time.sleep(0.8)
    set_dpms("off")
    soft_sleep = True


def do_long_press(was_asleep):
    global soft_sleep

    log("long press: power menu")
    # A menu on a blanked screen is no use to anyone.
    if was_asleep:
        soft_sleep = False
        set_dpms("on")
        time.sleep(0.5)
    prompt_power_menu()


def main():
    devices = {}  # fd -> name
    for name in (PWRKEY_NAME, VOLDOWN_NAME):
        dev = find_key_device(name)
        if not dev:
            if name == PWRKEY_NAME:
                log(f"no input device named {name}; giving up")
                raise SystemExit(1)
            # Without volume down the chord is unavailable, but the power key
            # is the important half - keep going rather than dying.
            log(f"no input device named {name}; screenshot chord disabled")
            continue
        fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
        devices[fd] = (name, dev)

    sel = selectors.DefaultSelector()
    for fd in devices:
        sel.register(fd, selectors.EVENT_READ)

    # BOOTTIME each key went down, or None while it is up.
    down = {KEY_POWER: None, KEY_VOLUMEDOWN: None}
    combo_fired = False  # chord handled; suppress both keys until fully released
    long_fired = False   # long-press already actioned, suppress the release

    # Whether the screen was off when the power key went DOWN.
    #
    # This must be sampled on the press, never on the release. We do not grab
    # the device, so KWin sees the same press and wakes the panel immediately;
    # by the time the key comes up, DPMS reads "On" even for a blank we did not
    # perform, and the release is then treated as "screen is on, so lock" -
    # meaning pressing power to wake a screen that PowerDevil's idle timeout
    # blanked would light it and then lock and blank it again 0.8s later.
    #
    # soft_sleep alone does not cover this: it is only ever set by our own
    # short press, so it is False for any blank we did not cause.
    asleep_at_press = False

    log("watching " + ", ".join(f"{d} ({n})" for n, d in devices.values())
        + f"; long press {LONG_PRESS_S}s, chord window {COMBO_WINDOW_S}s")

    while True:
        timeout = 1.0
        if down[KEY_POWER] is not None and not long_fired and not combo_fired:
            timeout = max(0.05, LONG_PRESS_S - (clock() - down[KEY_POWER]))

        events = sel.select(timeout)
        now = clock()

        if not events:
            # Timed out: the long-press threshold passed with the key still down.
            if down[KEY_POWER] is not None and not long_fired and not combo_fired \
                    and now - down[KEY_POWER] >= LONG_PRESS_S:
                long_fired = True
                do_long_press(asleep_at_press)
            continue

        for key, _mask in events:
            fd = key.fd
            try:
                data = os.read(fd, EVENT_SIZE * 16)
            except BlockingIOError:
                continue
            except OSError as e:
                # The device is gone. epoll reports EPOLLHUP/EPOLLERR
                # unconditionally and selectors turns that into EVENT_READ, so
                # simply logging and continuing spins this loop at full speed
                # forever - forking `logger` every iteration, pegging a core and
                # filling the journal, on a battery-powered tablet.
                name, dev = devices.pop(fd, ("?", "?"))
                log(f"read failed on {dev} ({name}): {e}")
                try:
                    sel.unregister(fd)
                    os.close(fd)
                except OSError:
                    pass
                if name == PWRKEY_NAME or not devices:
                    # Exit and let systemd restart us, which re-runs
                    # find_key_device() and picks up a renumbered eventN.
                    raise SystemExit(1)
                continue  # only the resin died: chord is gone, power key lives

            for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                _sec, _usec, etype, code, value = struct.unpack_from(
                    EVENT_FMT, data, off)
                if etype != EV_KEY or code not in down:
                    continue

                if value == 1:            # down
                    down[code] = now
                    if code == KEY_POWER:
                        long_fired = False
                        # Sample now, while the answer is still true. See the
                        # note where asleep_at_press is declared.
                        asleep_at_press = is_asleep()

                    other = KEY_VOLUMEDOWN if code == KEY_POWER else KEY_POWER
                    if (down[other] is not None and not combo_fired
                            and now - down[other] <= COMBO_WINDOW_S):
                        # Both keys down, pressed close enough together.
                        combo_fired = True
                        # Whatever the power key was going to do, it is not
                        # doing it: this was a chord, not a press.
                        long_fired = True
                        do_screenshot(asleep_at_press)

                elif value == 0:          # up
                    was_down = down[code] is not None
                    down[code] = None

                    if code == KEY_POWER and was_down and not combo_fired \
                            and not long_fired:
                        do_short_press(asleep_at_press)

                    # Only clear the chord once BOTH keys are up, or releasing
                    # the first one would let the second act on its own.
                    if down[KEY_POWER] is None and down[KEY_VOLUMEDOWN] is None:
                        combo_fired = False
                        long_fired = False
                # value == 2 is autorepeat; ignore it


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log(f"fatal: {e}")
        raise
