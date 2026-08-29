# Making it an actual daily-driver tablet

Getting mainline Linux to boot is one problem. Making a 10.5" tablet with no keyboard
into something you can do development work on is a different one, and most of it is
not kernel work. This is what it took.

Everything here applies to any Qualcomm tablet running Fedora + KDE Plasma on Wayland;
none of it is Tab S6 specific.

---

## 1. The on-screen keyboard has no modifiers

`maliit-keyboard` is what KDE uses as the Wayland input method. Out of the box it is
built for texting: no Ctrl, no Alt, no Escape, no Tab, and no arrow keys. That rules out
essentially all terminal work — no `Ctrl+C` to kill a process, no `Ctrl+D` to exit, no
`Ctrl+R` to search history, and no way out of insert mode in vim.

### Can maliit even send a modifier?

QML gets exactly four invokable methods on `event_handler` — `onKeyPressed`,
`onKeyReleased`, `onKeyEntered`, `onKeyExited` — so `onKeyReleased(key, action)` is the
only lever, and the `action` string decides what the plugin does with the key.

The stock layouts call it with `action: "keysequence"` and Qt *standard key* names such
as `SelectPreviousChar`, which strongly suggests a fixed enum lookup with no way to
express an arbitrary chord. That reading is wrong, and it is worth ten minutes to find
out, because believing it leads you to build a row of hardcoded `Ctrl+C` / `Ctrl+D` keys
instead of a real modifier.

Disassembling the plugin settles it:

```
$ objdump -dC --no-show-raw-insn /usr/lib64/maliit/plugins/libmaliit-keyboard-plugin.so
  ...
  bl  <QKeySequence::fromString(QString const&, QKeySequence::SequenceFormat)@plt>
  ...
  bl  <MaliitKeyboard::AbstractTextEditor::sendKeySequence(...)>
```

The `keysequence` path is `QKeySequence::fromString(s, PortableText)` handed to
`sendKeySequence()`, which walks the sequence emitting **real press/release events with
the modifier flags attached**. `PortableText` parses `"Ctrl+C"`, `"Alt+F4"`, `"Esc"`,
`"Ctrl+Shift+T"`. Arbitrary chords work.

### Latching, because fingers

A touch screen cannot hold Ctrl down while tapping C, so the modifiers latch: tap `ctrl`
and it stays visibly pressed, the next key is sent as `Ctrl+<key>`, and the latch clears.
Tapping `ctrl` again cancels it.

State lives on the object every key already refers to as `panel`
(`qml/KeyboardContainer.qml`), which avoids a QML singleton:

```qml
property bool ctrlLatched: false
property bool altLatched: false

function modsActive() { return ctrlLatched || altLatched; }
function clearMods()  { ctrlLatched = false; altLatched = false; }

function sendWithMods(base) {
    if (base.length === 0) { clearMods(); return; }
    var prefix = (ctrlLatched ? "Ctrl+" : "") + (altLatched ? "Alt+" : "");
    event_handler.onKeyReleased(prefix + base, "keysequence");
    clearMods();
}
```

`CharKey.qml` gets a `forceDown` property so a latched modifier draws as pressed
(`down: keyMouseArea.pressed || key.forceDown`) and an interception at its send site that
redirects to `sendWithMods()`. **`ActionKey` inherits `CharKey`, so that single hook
covers every key type on the board.**

Then two new components: `ModKey.qml` (latching ctrl/alt) and `SeqKey.qml` (sends any
QKeySequence name). `NavKey` honours the latch too, so `ctrl` + `→` is `Ctrl+Right`.

### The row is exactly ten keys, deliberately

Both halves of the reason are in `KeyPad.qml`:

- `calculateKeyWidth()` sizes every key by the **widest row**, so a row of eleven would
  shrink every key on the keyboard. Ten matches the qwerty row.
- `calculateKeyHeight()` divides the **fixed** keyboard height by the row count, so
  adding a sixth row would make every key shorter. Modifiers and navigation therefore
  share one row: `esc ctrl alt ⇱ ← ↑ ↓ → ⇲ ⌄`.

### Gotcha: a key labelled "⇥" does not send Tab

`CharKey` defines `valueToSubmit: keyLabel.text` — a key submits **its own label**. So a
tab key added as a `CharKey` labelled `⇥` inserts that literal glyph.

Even a genuine tab *character* would be wrong. A shell needs a Tab **keypress** to
complete a filename; inserted text does nothing. Tab must be a `SeqKey` sending `"Tab"`.

### Other fixes

- Auto-capitalisation is on by default and makes the keyboard open in caps every time:
  `gsettings set org.maliit.keyboard.maliit auto-capitalization false`.
- There is no key to dismiss the keyboard. `NavKey` calls the `Keyboard.hide()` singleton
  (with `maliit_input_method.hide()` and `Qt.inputMethod.hide()` as fallbacks).
- The emoji panel is reachable from the bottom-row smiley (`LanguageKey.qml` jumps to
  `keypad.state = "EMOJI"` when only one language is enabled); removing that jump
  removes the panel.

### Verifying without touching the device

KWin will open the keyboard on request, which forces the QML to compile and instantiate —
so a typo appears in the journal immediately:

```bash
dbus-send --session --print-reply --dest=org.kde.KWin /VirtualKeyboard org.kde.kwin.VirtualKeyboard.forceActivate
```

Then check `journalctl -b | grep -iE '\.qml|is not a type|Unable to assign'`.

### Re-applying, and why "keep .orig copies" was not enough

These files live under `/usr/lib64/maliit/keyboard2/`, so a package update overwrites
them. Changed: `qml/KeyboardContainer.qml`, `qml/keys/CharKey.qml`, `qml/keys/NavKey.qml`,
the new `qml/keys/ModKey.qml` and `qml/keys/SeqKey.qml`, `qml/keys/qmldir`, and
`languages/en/Keyboard_en.qml`.

None of them are marked `%config`, so RPM replaces them **silently** — no warning, no
`.rpmsave`, no `.rpmnew`. The advice used to be "keep `.orig` copies", which only helps
if you happen to remember to check.

A Plasma **6.6.4 → 6.7.4** upgrade proved the point. The keyboard survived only because
maliit was not in that transaction; the lock screen did not, and reverted to stock:

```
$ stat -c %y .../lockscreen/LockScreenUi.qml
2026-08-04 01:00:00        <- package build date, i.e. rpm replaced it
$ grep -A2 onExited .../LockScreenUi.qml
onExited: { uiVisible = false; }     <- our fix gone
```

Nothing announced it. The tablet would simply have gone back to a lock screen that
cannot be dismissed by touch, at the next lock.

So it is automated now. `tools/tabs6-desktop-patches.py`, run by
`tabs6-desktop-patches.service` before `graphical.target` on every boot:

- re-applies the lock screen touch fixes if the marker `TABS6-PATCHED` is absent,
  keeping a `.stock` copy of whatever it replaced
- restores the maliit files from a pristine stash in `/usr/local/share/tabs6/maliit/`
  if the live ones differ

Every change is checked before it is made, so it is safe to run repeatedly — a second
run reports `everything already in place`. Verified by clobbering `ModKey.qml` with a
placeholder and re-running: restored byte-identically.

`qmldir` is the one to not forget. Without it the new key types are not registered as
importable and the keyboard fails to load **entirely**, rather than just losing the
extra row.

### The lock screen fixes themselves

Stock Plasma assumes a mouse. Three separate things break under a finger:

| Stock behaviour | Why it fails on touch |
|---|---|
| `onPositionChanged: uiVisible = seenPositionChange` | the first interaction only arms a flag and reveals nothing; with a mouse you keep moving and it appears, with a finger you tap once and nothing happens |
| `onExited: uiVisible = false` | lifting a finger counts as exiting, so the password box vanishes the moment you stop touching |
| UI starts hidden | a locked tablet looks dead rather than locked |

The fourth change is not cosmetic. Setting `uiVisible` from `Component.onCompleted` runs
`onUiVisibleChanged` before the window exists, and `Window.window.requestActivate()` then
throws — aborting the handler *before* `authenticator.startAuthenticating()`, which
leaves the lock screen unable to accept a password at all. It is guarded with
`if (uiVisible && Window.window)`.

Related trap: adding a second `Component.onCompleted` to an element that already has one
is a "Property value set multiple times" error, and Plasma's response is to silently fall
back to the basic locker — which looks exactly like the patch having done nothing.

---

## 2. Memory on a 6 GB device

### Fedora's PIM stack is not free

KDE's Akonadi — a full `mysqld` plus seventeen agents for mail, calendar, contacts,
birthdays, indexing and so on — was resident at **880 MB** on a machine that will never
see an email. Disabling it via `~/.config/autostart` overrides (reversible, no packages
removed), along with Baloo indexing, the Discover notifier, geoclue's demo agent and the
ABRT applet, moved 3.4 GiB used / 1.4 available to **2.7 / 2.1**.

### zram, and the backend trap

There was no swap at all, because the kernel had no `CONFIG_ZRAM` — even though Fedora's
`zram-generator` was installed and trying. With `CONFIG_ZRAM=m`, `ZSMALLOC=y`, `ZSWAP=y`:

```ini
# /etc/systemd/zram-generator.conf
[zram0]
zram-size = 6144
compression-algorithm = zstd
swap-priority = 100
fs-type = swap
```

**The trap:** Kconfig force-selects LZO (`CONFIG_ZRAM_BACKEND_FORCE_LZO=y`) when no
backend is chosen explicitly. `compression-algorithm = zstd` then silently falls back to
lzo-rle. On developer workloads zstd runs about 3:1 against roughly 2:1 for lzo, which on
a 6 GiB device is gigabytes of difference. Enable `CONFIG_ZRAM_BACKEND_ZSTD` and confirm
with `cat /sys/block/zram0/comp_algorithm` — the active one is in `[brackets]`.

Tune the VM for it, because the defaults assume swap is a slow disk:

```ini
# /etc/sysctl.d/91-tabs6-zram.conf
vm.swappiness = 100   # see below — 150 was too aggressive
vm.page-cluster = 0   # random-access; readahead is pure waste
```

> **Learn from this one.** `swappiness` was originally set to **150**, on the reasoning that
> zram is RAM-speed so there's no reason to be shy about using it. That's right for throughput
> and **wrong for interactivity**. It produced visible desktop stutter, and the counters showed
> why: `pswpout 353043`, `pswpin 45576` — hundreds of thousands of pages evicted and tens of
> thousands dragged back, with 1.3 GiB still available. Every page pulled back is a
> decompression stall in the middle of whatever you were doing. 100 is what the zram
> documentation actually suggests.

Result: 4.8 GiB of RAM plus 6 GiB of RAM-speed swap, costing ~2 GiB of real memory when
full.

---

## 3. Firefox on a touch screen

### Android-style text selection

Firefox already contains the Android selection UI — the draggable handles either side of
a selection that appear on long press or double tap. It is compiled into the desktop
build and switched **off**, because a desktop is assumed to have a mouse.

```js
// /etc/firefox/pref/tabs6-touch.js
pref("layout.accessiblecaret.enabled", true);
// touch on Wayland is often reported as mouse input, which would otherwise
// dismiss the handles the instant they appear
pref("layout.accessiblecaret.hide_carets_for_mouse_input", false);
pref("layout.accessiblecaret.script_change_update_mode", 1);
pref("layout.accessiblecaret.width", 8.0);
pref("layout.accessiblecaret.height", 8.0);
pref("apz.allow_zooming", true);
pref("browser.ui.zoom.force-user-scalable", true);
pref("dom.w3c_touch_events.enabled", 1);
```

Scope, honestly: this fixes Firefox. Qt *widget* applications (Konsole, Kate, Dolphin)
have no equivalent switch. GTK 4 applications already have touch handles of their own.

### The keyboard vs. the window: a trade-off with no free option

Opening the on-screen keyboard **resizes** the focused window instead of covering it. That's
KWin reporting the input panel geometry to the window, so a maximised window shrinks to stay
clear. There's a setting to stop it:

```bash
kwriteconfig6 --file kwinrc --group Windows --key OverlayVirtualKeyboardOnWindows true
dbus-send --session --dest=org.kde.KWin --type=method_call /KWin org.kde.KWin.reconfigure
```

**But understand what you're buying.** That setting stops the resize by no longer telling the
window anything about the keyboard — which is *also* exactly what stops the window moving your
text field clear of it. Turn it on and the keyboard will sit on top of the field you're typing
into. It trades one annoyance for a worse one.

We ended up back on `false` (windows resize, field stays visible) and softened the cost
instead, by making the keyboard shorter:

```qml
/* qml/Keyboard.qml, on the canvas Item */
readonly property real heightScale: 0.78
height: fullScreenItem.height * (fullScreenItem.landscape ? Device.keyboardHeightLandscape
                                                          : Device.keyboardHeightPortrait)
        * heightScale
```

`Device.keyboardHeightLandscape` is a C++ getter with no gsettings knob, so scaling it in QML
is the way. Stock takes close to half a 1600 px screen in landscape — and this port adds a
sixth row (esc/ctrl/alt/arrows) upstream never had, so it starts taller than intended. A
shorter keyboard covers less *and* makes the resize less severe, since height changes call
`reportKeyboardVisibleRect()` and the compositor sizes windows to what it's told.

### Desktop effects cost more here than elsewhere

There's no real display controller — KWin renders on the GPU and the result is **copied** into
the bootloader framebuffer that simpledrm scans out. No page flip. Full-screen effects mean
full-screen damage, which means copying ~16 MB every frame, where damage-tracked partial
updates would be nearly free. Blur is the worst offender because it dirties everything beneath
it.

```bash
kwriteconfig6 --file kwinrc --group Plugins --key blurEnabled false
kwriteconfig6 --file kwinrc --group Plugins --key contrastEnabled false
kwriteconfig6 --file kdeglobals --group KDE --key AnimationDurationFactor 0.25
```

The copy is architectural, though: until the real DSI/DPU pipe works, there will always be one
between what the GPU draws and what the panel shows.

---

## 4. Electron / VS Code on Wayland

VS Code (and every Electron app) defaults to the X11 ozone backend and fails from a
non-session shell with *"Missing X server or $DISPLAY"*. Native Wayland is the better
target on a touch device anyway — correct scaling and real touch input rather than
XWayland's emulation:

```bash
ELECTRON_OZONE_PLATFORM_HINT=auto
```

Set in `/etc/environment` and `~/.config/plasma-workspace/env/`.

---

## 5. Routing: Wi-Fi and the USB lifeline together

With both the USB gadget and Wi-Fi up, `dnf` could not reach anything. Not a Wi-Fi
fault — the gadget's default route had metric 400 against Wi-Fi's 600, so it won; and
when the host-side RNDIS adapter went away the route stayed, merely marked `linkdown`.
**Linux uses linkdown routes by default.** Everything blackholed into a dead gateway
while Wi-Fi sat there working.

```ini
# /etc/sysctl.d/90-tabs6-linkdown.conf
net.ipv4.conf.all.ignore_routes_with_linkdown = 1
net.ipv6.conf.all.ignore_routes_with_linkdown = 1
```

and move the gadget's default route to metric 1000, so Wi-Fi is preferred whenever it is
up and USB remains a fallback. That is the right shape anyway: Wi-Fi for traffic, USB as
the recovery lifeline.

---

## 6. The hardware buttons

The PMIC PON block gives three keys, and `resin` is not self-describing, so
decode the evdev bitmaps rather than guessing:

| Device | Name | Bit | Key |
|---|---|---|---|
| `event0` | `pm8941_pwrkey` | 116 | `KEY_POWER` |
| `event1` | `pm8941_resin` | 114 | `KEY_VOLUMEDOWN` |
| `event2` | `gpio-keys` | 115 | `KEY_VOLUMEUP` |

Volume **down** sits on the PON block next to the power key; volume **up** is a
separate GPIO. Both PON keys are armed wake sources — see [SLEEP.md](SLEEP.md).

`tools/tabs6-powerkeyd.py` owns them:

| Input | Action |
|---|---|
| short press | lock the session, screen off |
| next short press | screen on, at the lock screen |
| long press (1.5 s) | Plasma's power menu |
| **power + volume down** | screenshot to `~/Pictures/Screenshots/` |

logind is `HandlePowerKey=ignore` and PowerDevil's button actions are off, so
this daemon is the only thing acting on the key. If two things handle it, Plasma
queues a logout prompt *behind* the lock screen and the tablet looks wedged.

### The chord

Both keys down within 0.6 s of each other, either order. `combo_fired` suppresses
the lock and the power menu for that press, and clears only once **both** keys
are up — otherwise releasing volume down first lets the still-held power key act
alone and lock the screen right after the shot.

The keys are read, never grabbed (`EVIOCGRAB`), so KWin still sees them and normal
handling is untouched. The cost is that the volume-down half also registers as a
volume press. There is no audio on this port yet so it currently does nothing;
the fix, if it ever matters, is to grab `resin` and re-emit lone presses through
uinput — a lot of machinery for a small annoyance.

### Four bugs worth knowing about, none of which a button-press would reveal

**Sample the screen state on the key DOWN, never the release.** Because the
device is not grabbed, KWin sees the same press and lights the panel immediately.
By the time the key comes up, DPMS reads `On` even for a blank we did not perform
— so pressing power to wake a screen that PowerDevil's idle timeout blanked would
light it, then lock and re-blank it 0.8 s later. `soft_sleep` does not cover this:
it is only ever set by our own short press, so it is `False` for any blank we did
not cause. Latch `is_asleep()` on the press and use that at release.

**Spectacle is `KDBusService::Unique`.** If an instance is already running, the
process you spawn hands its argv to that instance and exits **0 immediately**,
while the capture happens asynchronously over there. So an instantaneous
`os.path.exists()` check reports failure on a screenshot that is about to
succeed — and a retry ladder then fires *another* capture, and another. Worse,
`SpectacleCore::activate()` calls `deleteWindows()` whenever argv carries
options, so the chord destroys any Spectacle window you had open. Lead with
`--new-instance`, wait for the file with a deadline rather than a single stat,
and stop the ladder as soon as an invocation is *accepted* (exit 0) rather than
when a file appears.

For the record, KWin authorisation is **not** a problem here and the launch path
does not matter: `fetchRestrictedDBusInterfacesFromPid()` resolves
`/proc/<pid>/exe` and matches it against desktop-file `Exec` entries, and the
D-Bus caller is the exec'd `/usr/bin/spectacle` itself. Going through
`runuser -u fedora -- env ... spectacle` from a root daemon is authorised exactly
as a launcher-started Spectacle is.

**A dead input device busy-loops the daemon.** epoll reports `EPOLLHUP`/`EPOLLERR`
unconditionally — they are not maskable — and `selectors` turns those into
`EVENT_READ`. So once an evdev node goes away, `select()` returns that fd ready on
*every* iteration and `os.read()` raises `ENODEV` forever. Logging and continuing
pegs a core and forks `logger` in a tight loop, on a battery-powered tablet.
Unregister and close the fd; if it was the power key, exit and let systemd restart
and re-resolve the node (which needs `Restart=always` with `StartLimitBurst=0`).

**Chording while the screen is blanked leaves `soft_sleep` stale.** The chord
suppresses `do_short_press()`, which is the only place the flag is cleared — but
KWin still lights the panel. The flag then says "off" while the panel is on, and
`is_asleep()` short-circuits on it, so the next deliberate power press is
swallowed as a wake instead of locking. The tablet sits unlocked and needs two
presses. Reconcile the flag in the chord handler.

Those came out of an adversarial review pass: 28 candidate findings, 4 survived
refutation. All four are in the "would look like flaky hardware" category rather
than the "obvious on first use" category, which is exactly the kind worth writing
down.

---

## 7. A screenshot button on the right of the panel

The power+volume-down chord works, but a button is easier — and on a tablet a
one-tap target beats a two-key chord. `tools/tabs6-screenshot` takes the shot;
`tools/tabs6-screenshot-tray.py` puts a button in the system tray.

    tap the tray icon -> full-screen PNG
                      -> copied to the clipboard, ready to paste
                      -> saved to ~/Pictures/Screenshots/

### Getting a button on the RIGHT is harder than it sounds

Plasma 6 on Fedora ships **no standalone Quick Launch applet**. The only
launcher-capable plasmoid installed is `icontasks`, which lives with the task
manager on the **left** of the panel:

```
AppletOrder=3;4;5;6;7;22;23
             │ │ │ └── 6 = marginsseparator: everything after it is right-aligned
             │ │ └──── 5 = icontasks (launchers live here, so: left)
             │ └────── 4 = pager
             └──────── 3 = kickoff
```

A `.desktop` file added to `icontasks`'s `launchers=` list works fine and is the
simplest option, but it can only ever sit on the left.

### Qt's QSystemTrayIcon does not work here

PySide6 is installed, so `QSystemTrayIcon` looked like the answer. It is not:

    isSystemTrayAvailable() -> True
    tray.isVisible()        -> True
    SNI names on the bus    -> NONE

No `org.kde.StatusNotifierItem-*` name ever appears. Tested with
`QT_QPA_PLATFORMTHEME` set to `kde`, `generic`, `gnome` and unset, on the
`wayland` platform, with the icon resolving (`QIcon.fromTheme("spectacle")` is
not null). It silently does nothing — no warning, no error.

### So speak the tray protocol directly

The tray is only D-Bus, and `python3-gobject` is present, so the item is
implemented directly with GLib/Gio: own `org.kde.StatusNotifierItem-<pid>-1`,
export `/StatusNotifierItem`, and call `RegisterStatusNotifierItem` on
`org.kde.StatusNotifierWatcher`. No toolkit involved, nothing to fail quietly.

Two things to get right:

**The property getter takes five arguments, not seven.** PyGObject calls it as
`(connection, sender, object_path, interface_name, property_name)` — no `GError`
and no `user_data`, unlike the method-call closure which does get seven. Getting
it wrong throws on *every* property read:

    TypeError: TrayItem.on_get() missing 1 required positional argument: '_err'

and the only visible symptom is that the icon never appears, because Plasma
cannot read `Id`, `IconName` or `Status` and so has nothing to draw. The bus name
still registers, so it looks like it worked.

**`ItemIsMenu` must be false**, or a tap opens a context menu instead of calling
`Activate`, and one-tap capture is the entire point.

It also re-registers when `org.kde.StatusNotifierWatcher` reappears, so the icon
survives a plasmashell restart instead of vanishing permanently.

### Clipboard: `--copy-image` does not work either

`spectacle --copy-image` leaves the clipboard offering only
`application/x-kde-onlyReplaceEmpty`; `wl-paste --type image/png` returns
nothing, and klipper's history shows a broken image entry (`-1x-1`). The Wayland
clipboard offer dies with the process that made it and klipper is not persisting
it. So the file is copied afterwards with `wl-copy`, which forks a helper that
keeps serving the data until something replaces it. Verified by round-trip:

    wl-paste --list-types            -> image/png
    wl-paste --type image/png > f    -> valid PNG, 2560x1600, 2.7 MB

### Judge the file, not the exit status

Same trap as the chord daemon: Spectacle is `KDBusService::Unique`, so when an
instance is already running your process forwards its argv and exits 0
immediately while the capture happens elsewhere. Both the script and the tray
lead with `--new-instance`, wait for the file with a deadline, and stop as soon
as a spelling is *accepted* rather than retrying and queueing duplicate captures.
