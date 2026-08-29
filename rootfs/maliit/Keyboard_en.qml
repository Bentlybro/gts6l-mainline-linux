/*
 * English layout, Tab S6 port.
 *
 * Upstream layout plus a modifier/navigation row: escape, latching ctrl and
 * alt, real cursor keys, and a dismiss key.
 *
 * maliit only offers cursor movement as a swipe gesture, which is unusable in
 * a terminal or an editor, there is no way to put the keyboard away without
 * defocusing the field, and there is no modifier at all - so no Ctrl+C, no
 * Ctrl+D, and no escape for vim.
 *
 * The row is eleven keys, and it is ONE row on purpose. KeyPad.calculateKeyWidth()
 * sizes every key by the widest row, so this row sets the width for the whole
 * keyboard - eleven across 2560 px is about 232 px each, still comfortably large.
 * And calculateKeyHeight() divides the fixed keyboard height by the row count, so
 * a second modifier row would make every key on the board shorter.
 */
import QtQuick 2.4

import MaliitKeyboard 2.0

import keys 1.0

KeyPad {
    anchors.fill: parent

    content: c1
    symbols: "languages/Keyboard_symbols.qml"

    Column {
        id: c1
        anchors.fill: parent
        spacing: 0

        // Modifiers and navigation. ctrl and alt latch: tap ctrl, then c, to
        // send one Ctrl+C. They stay drawn as pressed while latched.
        Row {
            anchors.horizontalCenter: parent.horizontalCenter;
            spacing: 0

            SeqKey { label: "esc";  sequence: "Esc"; leftSide: true; }
            ModKey { label: "ctrl"; modifier: "ctrl"; }
            ModKey { label: "alt";  modifier: "alt"; }
            /* Meta earns its place on a tablet: dragging a window to a screen
             * edge to tile it needs the pointer to REACH the edge, and a finger
             * runs out of screen first. meta then left-arrow tiles left, meta
             * then right-arrow tiles right, with no dragging at all. */
            ModKey { label: "meta"; modifier: "meta"; }
            NavKey { label: "⇱"; navAction: "home"; }
            NavKey { label: "←"; navAction: "left"; }
            NavKey { label: "↑"; navAction: "up"; }
            NavKey { label: "↓"; navAction: "down"; }
            NavKey { label: "→"; navAction: "right"; }
            NavKey { label: "⇲"; navAction: "end"; }
            NavKey { label: "⌄"; navAction: "hide"; rightSide: true; }
        }

        Row {
            anchors.horizontalCenter: parent.horizontalCenter;
            spacing: 0

            CharKey { label: "q"; shifted: "Q"; extended: ["1"]; extendedShifted: ["1"]; leftSide: true; }
            CharKey { label: "w"; shifted: "W"; extended: ["2"]; extendedShifted: ["2"] }
            CharKey { label: "e"; shifted: "E"; extended: ["3", "è", "é", "ê", "ë", "€"]; extendedShifted: ["3", "È","É", "Ê", "Ë", "€"] }
            CharKey { label: "r"; shifted: "R"; extended: ["4"]; extendedShifted: ["4"] }
            CharKey { label: "t"; shifted: "T"; extended: ["5", "þ"]; extendedShifted: ["5", "Þ"] }
            CharKey { label: "y"; shifted: "Y"; extended: ["6", "ý", "¥"]; extendedShifted: ["6", "Ý", "¥"] }
            CharKey { label: "u"; shifted: "U"; extended: ["7", "û","ù","ú","ü"]; extendedShifted: ["7", "Û","Ù","Ú","Ü"] }
            CharKey { label: "i"; shifted: "I"; extended: ["8", "î","ï","ì","í"]; extendedShifted: ["8", "Î","Ï","Ì","Í"] }
            CharKey { label: "o"; shifted: "O"; extended: ["9", "ö","ô","ò","ó"]; extendedShifted: ["9", "Ö","Ô","Ò","Ó"] }
            CharKey { label: "p"; shifted: "P"; extended: ["0"]; extendedShifted: ["0"]; rightSide: true; }
        }

        Row {
            anchors.horizontalCenter: parent.horizontalCenter;
            spacing: 0

            CharKey { label: "a"; shifted: "A"; extended: ["ä","à","â","ª","á","å", "æ"]; extendedShifted: ["Ä","À","Â","ª","Á","Å","Æ"]; leftSide: true; }
            CharKey { label: "s"; shifted: "S"; extended: ["ß","$"]; extendedShifted: ["$"] }
            CharKey { label: "d"; shifted: "D"; extended: ["ð"]; extendedShifted: ["Ð"] }
            CharKey { label: "f"; shifted: "F"; }
            CharKey { label: "g"; shifted: "G"; }
            CharKey { label: "h"; shifted: "H"; }
            CharKey { label: "j"; shifted: "J"; }
            CharKey { label: "k"; shifted: "K"; }
            CharKey { label: "l"; shifted: "L"; rightSide: true; }
        }

        Row {
            anchors.horizontalCenter: parent.horizontalCenter;
            spacing: 0

            ShiftKey {}
            CharKey { label: "z"; shifted: "Z"; }
            CharKey { label: "x"; shifted: "X"; }
            CharKey { label: "c"; shifted: "C"; extended: ["ç"]; extendedShifted: ["Ç"] }
            CharKey { label: "v"; shifted: "V"; }
            CharKey { label: "b"; shifted: "B"; }
            CharKey { label: "n"; shifted: "N"; extended: ["ñ"]; extendedShifted: ["Ñ"] }
            CharKey { label: "m"; shifted: "M"; }
            BackspaceKey {}
        }

        Item {
            anchors.left: parent.left
            anchors.right: parent.right

            height: panel.keyHeight + Device.row_margin;

            // Tab is a SeqKey, not a CharKey: a CharKey submits its own label as
            // text, so the old tab key inserted a literal glyph. A shell needs
            // an actual Tab keypress to complete a filename.
            SymbolShiftKey { id: symShiftKey;                            anchors.left: parent.left; height: parent.height; }
            SeqKey         { id: tabKey;      label: "⇥"; sequence: "Tab"; anchors.left: symShiftKey.right; height: parent.height; }
            CharKey        { id: commaKey;    label: ","; shifted: ","; extended: ["'", "\"", ";", ":", "@", "&", "(", ")"]; extendedShifted: ["'", "\"", ";", ":", "@", "&", "(", ")"]; anchors.left: tabKey.right; height: parent.height; }
            SpaceKey       { id: spaceKey;                               anchors.left: commaKey.right; anchors.right: dotKey.left; noMagnifier: true; height: parent.height; }
            CharKey        { id: dotKey;      label: "."; shifted: "."; extended: ["?", "-", "_", "!", "+", "%","#","/"];  extendedShifted: ["?", "-", "_", "!", "+", "%","#","/"]; anchors.right: enterKey.left; height: parent.height; }
            ReturnKey      { id: enterKey;                               anchors.right: parent.right; height: parent.height; }
        }
    } // column
}
