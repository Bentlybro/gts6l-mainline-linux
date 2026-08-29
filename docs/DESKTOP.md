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

### Re-applying

These files live under `/usr/lib64/maliit/keyboard2/`, so a package update overwrites
them. Keep `.orig` copies. Changed: `qml/KeyboardContainer.qml`, `qml/keys/CharKey.qml`,
`qml/keys/NavKey.qml`, the new `qml/keys/ModKey.qml` and `qml/keys/SeqKey.qml`,
`qml/keys/qmldir`, and `languages/en/Keyboard_en.qml`.

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
vm.swappiness = 150   # this is RAM, swap eagerly
vm.page-cluster = 0   # random-access; readahead is pure waste
```

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

### Stop the keyboard resizing windows

Opening the on-screen keyboard **resizes** the focused window instead of covering it.
That is KWin: it reports the input panel geometry to the window, and a maximised window
shrinks to stay clear. There is a setting, and it defaults to off:

```bash
kwriteconfig6 --file kwinrc --group Windows --key OverlayVirtualKeyboardOnWindows true
dbus-send --session --dest=org.kde.KWin --type=method_call /KWin org.kde.KWin.reconfigure
```

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
