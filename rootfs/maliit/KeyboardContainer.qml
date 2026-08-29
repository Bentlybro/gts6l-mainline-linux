/*
 * Copyright 2013 Canonical Ltd.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU Lesser General Public License as published by
 * the Free Software Foundation; version 3.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

import QtQuick 2.4
import QtQuick.Window 2.0
import "languages/"
import "keys/"
import MaliitKeyboard 2.0

Item {
    id: panel

    property int keyWidth: 0
    property int keyHeight: 0

    property bool autoCapsTriggered: false
    property bool delayedAutoCaps: false

    property string activeKeypadState: "NORMAL"
    property alias popoverEnabled: extendedKeysSelector.enabled
    property bool hideKeyLabels: false // Hide key labels when in cursor movement mode

    property Item lastKeyPressed // Used for determining double click validity in PressArea

    // Latching modifiers for the modifier/navigation row. A touch screen cannot
    // hold a key down while tapping another, so ctrl and alt latch: they apply
    // to the next key and then clear.
    property bool ctrlLatched: false
    property bool altLatched: false
    property bool metaLatched: false

    function modsActive() {
        return ctrlLatched || altLatched || metaLatched;
    }

    function clearMods() {
        ctrlLatched = false;
        altLatched = false;
        metaLatched = false;
    }

    // Send `base` (a QKeySequence name such as "C", "Left" or "Tab") with any
    // latched modifiers applied, then drop the latch. The plugin parses this
    // with QKeySequence::fromString(s, PortableText) and emits a real key
    // press/release, which is what terminals and shortcuts need.
    function sendWithMods(base) {
        if (base.length === 0) {
            clearMods();
            return;
        }
        /* Qt's canonical order for a key sequence is Ctrl, Alt, Shift, Meta. */
        var prefix = (ctrlLatched ? "Ctrl+" : "")
                   + (altLatched ? "Alt+" : "")
                   + (metaLatched ? "Meta+" : "");
        event_handler.onKeyReleased(prefix + base, "keysequence");
        clearMods();
    }

    state: "CHARACTERS"

    function closeExtendedKeys()
    {
        extendedKeysSelector.closePopover();
    }

    Loader {
        id: characterKeypadLoader
        objectName: "characterKeyPadLoader"
        anchors.fill: parent
        asynchronous: false
        source: panel.state === "CHARACTERS" ? internal.characterKeypadSource : internal.symbolKeypadSource
        onLoaded: {
            if (delayedAutoCaps) {
                activeKeypadState = "SHIFTED";
                delayedAutoCaps = false;
            } else {
                activeKeypadState = "NORMAL";
            }
        }
    }

    Loader {
        id: emojiKeypadLoader
        objectName: "emojiKeypadLoader"
        anchors.fill: parent
        asynchronous: true
        active: panel.state === "EMOJI"
        source: "languages/Keyboard_emoji.qml"
    }

    ExtendedKeysSelector {
        id: extendedKeysSelector
        objectName: "extendedKeysSelector"
        anchors.fill: parent
    }

    Magnifier {
        id: magnifier
        shown: false
    }

    states: [
        State {
            name: "CHARACTERS"
            PropertyChanges {
                target: characterKeypadLoader
                visible: true
            }
        },
        State {
            name: "SYMBOLS"
            PropertyChanges {
                target: characterKeypadLoader
                visible: true
            }
        },
        State {
            name: "EMOJI"
            PropertyChanges {
                target: characterKeypadLoader
                visible: false
            }
        }
    ]

    onStateChanged: {
        Keyboard.keyboardState = state
    }

    QtObject {
        id: internal

        property Item activeKeypad: characterKeypadLoader.item
        property string characterKeypadSource: loadLayout(Keyboard.contentType,
                                                          Keyboard.activeLanguage)
        property string symbolKeypadSource: activeKeypad ? activeKeypad.symbols : ""

        onCharacterKeypadSourceChanged: {
            panel.state = "CHARACTERS";
        }

        function loadLayout(contentType, activeLanguage)
        {
            var language = activeLanguage.toLowerCase();
            if (!Keyboard.languageIsSupported(language)) {
                // If we don't have a layout for this specific locale 
                // check more generic locale
                language = language.slice(0,2);
            }

            if (!Keyboard.languageIsSupported(language)) {
                console.log("Language '" + language + "' not supported - using 'en' instead");
                Keyboard.activeLanguage = "en";
                language = "en";
            }

            // NumberContentType
            if (contentType === 1) {
                canvas.layoutId = "number";
                return "languages/Keyboard_numbers.qml";
            }

            // PhoneNumberContentType
            if (contentType === 2) {
                canvas.layoutId = "telephone";
                return "languages/Keyboard_telephone.qml";
            }

            // EmailContentType
            if (contentType === 3) {
                canvas.layoutId = "email";
                return Keyboard.currentPluginPath + "/Keyboard_" + language + "_email.qml";
            }

            // UrlContentType
            if (contentType === 4) {
                canvas.layoutId = "url";
                return Keyboard.currentPluginPath + "/Keyboard_" + language + "_url_search.qml";
            }

            // FreeTextContentType used as fallback
            canvas.layoutId = "freetext";
            return Keyboard.currentPluginPath + "/Keyboard_" + language + ".qml";
        }
    }
}
