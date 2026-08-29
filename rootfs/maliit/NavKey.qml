/*
 * NavKey - cursor/navigation keys for the Tab S6 port.
 *
 * maliit ships cursor movement only as a swipe gesture, which is unusable in a
 * terminal. Keyboard.qml already exposes the plumbing (event_handler with
 * "left"/"right"/"up"/"down"/"home"/"end"), it simply has no key bound to it.
 * This binds real keys to it, plus a key that dismisses the keyboard.
 *
 * When a modifier is latched the key is sent as a key sequence instead, so
 * ctrl then -> is Ctrl+Right (move by word) rather than a plain arrow.
 */
import QtQuick 2.4

import MaliitKeyboard 2.0

ActionKey {
    id: navKey

    // one of: left right up down home end hide
    property string navAction: "left"

    shifted: label
    noMagnifier: true
    skipAutoCaps: true
    overridePressArea: true
    padding: 0
    action: "nav"

    // QKeySequence name for this key, used only on the modifier path.
    function sequenceName() {
        switch (navAction) {
        case "left":  return "Left";
        case "right": return "Right";
        case "up":    return "Up";
        case "down":  return "Down";
        case "home":  return "Home";
        case "end":   return "End";
        }
        return "";
    }

    onPressed: {
        Feedback.keyPressed();
    }

    onReleased: {
        if (navAction === "hide") {
            // This build exposes no single documented hide binding, so try the
            // candidates in order and stop at the first that works. Keyboard
            // (the MaliitKeyboard singleton) is the one that actually fires
            // here; the rest are kept as fallbacks for other maliit builds.
            panel.clearMods();
            try { if (typeof Keyboard !== "undefined" && Keyboard && Keyboard.hide) { Keyboard.hide(); return; } } catch (e1) {}
            try { if (typeof fullScreenItem.hide === "function") { fullScreenItem.hide(); return; } } catch (e2) {}
            try { if (typeof maliit_input_method !== "undefined" && maliit_input_method) { maliit_input_method.hide(); return; } } catch (e3) {}
            try { Qt.inputMethod.hide(); } catch (e4) {}
            return;
        }

        // Modifier held: send as a sequence so Ctrl/Alt actually reach the app.
        if (panel.modsActive()) {
            panel.sendWithMods(sequenceName());
            return;
        }

        switch (navAction) {
        case "left":  event_handler.onKeyReleased("", "left");  break;
        case "right": event_handler.onKeyReleased("", "right"); break;
        case "up":    event_handler.onKeyReleased("", "up");    break;
        case "down":  event_handler.onKeyReleased("", "down");  break;
        case "home":  event_handler.onKeyReleased("", "home");  break;
        case "end":   event_handler.onKeyReleased("", "end");   break;
        }
    }
}
