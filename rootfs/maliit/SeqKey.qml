/*
 * SeqKey - sends a real key event named by a QKeySequence string.
 *
 * The plugin's "keysequence" action runs the label through
 * QKeySequence::fromString(s, PortableText) and then emits an actual key press
 * and release, so "Esc", "Tab" or "PgUp" arrive as keystrokes rather than as
 * inserted text. That distinction matters: a shell needs a Tab *keypress* to
 * complete a filename, and inserting a tab character does nothing.
 */
import QtQuick 2.4

import MaliitKeyboard 2.0

ActionKey {
    id: seqKey

    // A QKeySequence name: "Esc", "Tab", "PgUp", "Del", ...
    property string sequence: "Esc"

    shifted: label
    noMagnifier: true
    skipAutoCaps: true
    overridePressArea: true
    padding: 0
    action: "sequence"

    onPressed: {
        Feedback.keyPressed();
    }

    onReleased: {
        panel.sendWithMods(sequence);
    }
}
