#!/usr/bin/env python3
"""Re-apply this port's desktop patches, idempotently, at every boot.

Some of what makes this tablet usable lives in files owned by RPM packages -
Plasma's lock screen QML, and the maliit on-screen keyboard. Those are not
marked %config, so rpm silently overwrites them on upgrade and the changes
vanish with no warning and no .rpmsave. That is exactly what happened across the
Plasma 6.6.4 -> 6.7.4 upgrade: the lock screen reverted to stock and went back to
being unusable by touch, while the keyboard survived only because maliit happened
not to be in that transaction.

So instead of patching by hand and hoping, this runs at boot and puts things back
if they are missing. It is safe to run repeatedly: every change is checked before
it is made, and a file already carrying our marker is left alone.

Nothing here is clever. It is deliberately boring, because it runs unattended
before the session starts and a mistake here means no lock screen.
"""

import io
import os
import shutil
import subprocess
import sys

MARKER = "TABS6-PATCHED"

LOCKSCREEN = ("/usr/share/plasma/shells/org.kde.plasma.desktop"
              "/contents/lockscreen/LockScreenUi.qml")

# Pristine copies of our maliit files, stashed at install time. If an upgrade
# replaces the packaged ones, they get restored from here.
MALIIT_STASH = "/usr/local/share/tabs6/maliit"
MALIIT_TARGETS = {
    "KeyboardContainer.qml": "/usr/lib64/maliit/keyboard2/qml/KeyboardContainer.qml",
    "Keyboard.qml": "/usr/lib64/maliit/keyboard2/qml/Keyboard.qml",
    "CharKey.qml": "/usr/lib64/maliit/keyboard2/qml/keys/CharKey.qml",
    "ModKey.qml": "/usr/lib64/maliit/keyboard2/qml/keys/ModKey.qml",
    "NavKey.qml": "/usr/lib64/maliit/keyboard2/qml/keys/NavKey.qml",
    "SeqKey.qml": "/usr/lib64/maliit/keyboard2/qml/keys/SeqKey.qml",
    "Keyboard_en.qml": "/usr/lib64/maliit/keyboard2/languages/en/Keyboard_en.qml",
    # Without qmldir the new key types are not registered as importable at all,
    # so the keyboard fails to load entirely rather than merely losing the extra
    # row. It is the easiest one to forget and the most damaging to lose.
    "qmldir": "/usr/lib64/maliit/keyboard2/qml/keys/qmldir",
}


# Audio needs an ALSA UCM profile or the desktop gets nothing. Without one
# WirePlumber falls back to the ACP profile path, finds nothing it recognises on
# a q6 card with 1130 controls, and leaves you with a "Dummy Output" and no sink
# at all - the card is there, the amps are there, and nothing can play.
#
# These files are ours, but they live inside a directory alsa-ucm-conf owns, so
# an update to that package can take them with it exactly the way the Plasma
# upgrade took the lock screen.
UCM_STASH = "/usr/local/share/tabs6/ucm2"
UCM_CARD = "Samsung-TabS6WIFI-gts6lwifi-MTP.conf"
UCM_DIR = "/usr/share/alsa/ucm2/Qualcomm/sm8150"
# conf.d is keyed by the DRIVER name, and ours is the sm8250 machine driver with
# a Secondary TDM case added, so the link lives under sm8250 on an sm8150 SoC.
UCM_LINK = "/usr/share/alsa/ucm2/conf.d/sm8250/" + UCM_CARD
UCM_LINK_TARGET = "../../Qualcomm/sm8150/" + UCM_CARD
UCM_FILES = ("HiFi.conf", UCM_CARD)


def log(msg):
    print(msg, flush=True)
    subprocess.run(["logger", "-t", "tabs6-desktop-patches", "--", msg],
                   check=False)


def read(path):
    return io.open(path, encoding="utf-8").read()


def write(path, text):
    tmp = path + ".tabs6.tmp"
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(text)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)


def patch_lockscreen():
    """Make the lock screen usable with a finger.

    Stock Plasma assumes a mouse. Three things break on a touchscreen:

      * onPositionChanged sets `uiVisible = seenPositionChange`, so the FIRST
        interaction only arms a flag and reveals nothing. With a mouse you keep
        moving and it appears; with a finger you tap once and nothing happens.
      * onExited hides the UI again - and lifting a finger counts as exiting, so
        the password box disappears the moment you stop touching.
      * the UI starts hidden, so a locked tablet looks dead until you guess that
        it wants a swipe.

    Also guards Window.window. Setting uiVisible during Component.onCompleted
    runs onUiVisibleChanged before the window exists, and the resulting TypeError
    aborts the handler *before* authenticator.startAuthenticating(), which leaves
    the lock screen unable to accept a password at all. That one cost an evening.
    """
    if not os.path.exists(LOCKSCREEN):
        log(f"lockscreen: {LOCKSCREEN} not found, skipping")
        return False

    src = read(LOCKSCREEN)
    if MARKER in src:
        return False

    original = src

    # 1. Reveal on any movement, never re-hide from a position change.
    old_pos = ("        onPositionChanged: {\n"
               "            uiVisible = seenPositionChange;\n"
               "            seenPositionChange = true;\n"
               "        }\n")
    new_pos = ("        // " + MARKER + ": a finger produces one position change and then\n"
               "        // stops. Arming a flag and revealing nothing leaves a tap doing\n"
               "        // visibly nothing at all, so reveal on the first movement.\n"
               "        onPositionChanged: {\n"
               "            uiVisible = true;\n"
               "            seenPositionChange = true;\n"
               "        }\n")
    if old_pos in src:
        src = src.replace(old_pos, new_pos, 1)
    else:
        log("lockscreen: onPositionChanged did not match, skipping that hunk")

    # 2. Lifting a finger must not hide the password box.
    old_exit = ("        onExited: {\n"
                "            uiVisible = false;\n"
                "        }\n")
    new_exit = ("        // " + MARKER + ": lifting a finger counts as exiting, and hiding\n"
                "        // here made the password box vanish the instant you stopped\n"
                "        // touching the screen. Leave it up; the fadeout timer still runs.\n"
                "        onExited: {\n"
                "        }\n")
    if old_exit in src:
        src = src.replace(old_exit, new_exit, 1)
    else:
        log("lockscreen: onExited did not match, skipping that hunk")

    # 3. Window.window is null this early; unguarded it throws before
    #    startAuthenticating() and the lock screen cannot authenticate.
    old_win = ("            if (uiVisible) {\n"
               "                Window.window.requestActivate();\n"
               "            }\n")
    new_win = ("            if (uiVisible && Window.window) {\n"
               "                Window.window.requestActivate();\n"
               "            }\n")
    if old_win in src:
        src = src.replace(old_win, new_win, 1)
    else:
        log("lockscreen: Window.window guard did not match, skipping that hunk")

    # 4. Show the prompt immediately rather than waiting to be discovered.
    #
    #    This MUST merge into the MouseArea's EXISTING Component.onCompleted
    #    rather than adding one. QML allows exactly one binding per property per
    #    element, and a second is:
    #
    #        LockScreenUi.qml:205:9: Property value set multiple times
    #        Failed to load lockscreen QML, falling back to built-in locker
    #
    #    Plasma does not surface that anywhere the user can see - the lock
    #    screen just quietly becomes the plain built-in one. It is easy to miss
    #    because the anchor you are inserting after is nowhere near the existing
    #    binding (line ~160 vs line ~205), so "is my anchor present?" is the
    #    wrong question. The right question is whether the ENCLOSING element
    #    already binds the property. Checked the wrong one once and shipped a
    #    broken lock screen for it.
    old_completed = "        Component.onCompleted: launchAnimation.start();\n"
    new_completed = ("        Component.onCompleted: {\n"
                     "            launchAnimation.start();\n"
                     "            // " + MARKER + ": start revealed. A locked tablet showing\n"
                     "            // nothing looks broken rather than locked.\n"
                     "            uiVisible = true;\n"
                     "        }\n")
    if old_completed in src:
        src = src.replace(old_completed, new_completed, 1)
    else:
        log("lockscreen: Component.onCompleted did not match, skipping that hunk")

    if src == original:
        log("lockscreen: nothing matched, file left untouched")
        return False

    backup = LOCKSCREEN + ".stock"
    if not os.path.exists(backup):
        shutil.copy2(LOCKSCREEN, backup)
    write(LOCKSCREEN, src)
    log(f"lockscreen: patched (stock copy kept at {backup})")
    return True


def restore_maliit():
    """Put our keyboard back if a maliit upgrade replaced it."""
    restored = []
    for name, target in MALIIT_TARGETS.items():
        stash = os.path.join(MALIIT_STASH, name)
        if not os.path.exists(stash):
            continue
        if not os.path.exists(target):
            log(f"maliit: {target} missing entirely, restoring")
        else:
            try:
                if read(stash) == read(target):
                    continue
            except OSError as e:
                log(f"maliit: cannot compare {target}: {e}")
                continue
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(stash, target)
            os.chmod(target, 0o644)
            restored.append(name)
        except OSError as e:
            log(f"maliit: cannot restore {target}: {e}")
    if restored:
        log("maliit: restored " + ", ".join(restored))
    return bool(restored)


def restore_audio_ucm():
    """Put the UCM profile back if a package update removed it."""
    restored = []

    for name in UCM_FILES:
        stash = os.path.join(UCM_STASH, name)
        target = os.path.join(UCM_DIR, name)
        if not os.path.exists(stash):
            log(f"ucm: stash missing for {name}")
            continue
        try:
            if os.path.exists(target) and read(stash) == read(target):
                continue
            os.makedirs(UCM_DIR, exist_ok=True)
            shutil.copy2(stash, target)
            os.chmod(target, 0o644)
            restored.append(name)
        except OSError as e:
            log(f"ucm: cannot restore {target}: {e}")

    # The symlink is the part an alsa-ucm-conf update is most likely to take,
    # because conf.d/sm8250 is entirely package-owned.
    try:
        current = os.readlink(UCM_LINK)
    except OSError:
        current = None
    if current != UCM_LINK_TARGET:
        try:
            os.makedirs(os.path.dirname(UCM_LINK), exist_ok=True)
            if os.path.lexists(UCM_LINK):
                os.unlink(UCM_LINK)
            os.symlink(UCM_LINK_TARGET, UCM_LINK)
            restored.append("conf.d symlink")
        except OSError as e:
            log(f"ucm: cannot create {UCM_LINK}: {e}")

    if restored:
        log("ucm: restored " + ", ".join(restored))
    return bool(restored)


def main():
    changed = False
    try:
        changed |= patch_lockscreen()
    except Exception as e:
        log(f"lockscreen: FAILED: {e}")
    try:
        changed |= restore_maliit()
    except Exception as e:
        log(f"maliit: FAILED: {e}")
    try:
        changed |= restore_audio_ucm()
    except Exception as e:
        log(f"ucm: FAILED: {e}")

    if not changed:
        log("everything already in place")
    return 0


if __name__ == "__main__":
    sys.exit(main())
