/*
 * ModKey - latching Ctrl/Alt for the Tab S6 port.
 *
 * A touch keyboard cannot hold a modifier down while another key is tapped, so
 * these latch instead: tap ctrl, then c, and the app receives one Ctrl+C. The
 * latch is applied by panel.sendWithMods() and cleared after the next key, or
 * by tapping the modifier again. The key stays drawn as pressed while latched
 * so the state is always visible.
 */
import QtQuick 2.4

import MaliitKeyboard 2.0

ActionKey {
    id: modKey

    // one of: ctrl alt meta
    property string modifier: "ctrl"

    shifted: label
    noMagnifier: true
    skipAutoCaps: true
    overridePressArea: true
    padding: 0
    action: "modifier"

    forceDown: modifier === "alt"  ? panel.altLatched
             : modifier === "meta" ? panel.metaLatched
             : panel.ctrlLatched

    onPressed: {
        Feedback.keyPressed();
    }

    onReleased: {
        if (modifier === "alt")
            panel.altLatched = !panel.altLatched;
        else if (modifier === "meta")
            panel.metaLatched = !panel.metaLatched;
        else
            panel.ctrlLatched = !panel.ctrlLatched;
    }
}
