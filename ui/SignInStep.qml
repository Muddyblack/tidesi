import QtQuick
import "studio"
import "studio/Theme.js" as Theme
import "js/Tone.js" as Tone
import "theme" as Appearance

// "Run this once": the command that signs an account's folder in, ready to copy.
// The widget never signs in for the user; that stays with the tool, so its
// login is never touched or refreshed from here.
Column {
    id: step

    property string command: ""
    property var shell

    spacing: 6

    Text {
        width: parent.width
        text: step.shell.i18n("Sign in once: run this in a terminal, finish the login, then refresh.")
        color: Tone.c(Appearance.Palette, Theme.muted)
        font.pixelSize: 10
        wrapMode: Text.WordWrap
    }
    Row {
        spacing: 8
        StudioField {
            id: field
            width: step.width - copyButton.width - 8
            mono: true
            readOnly: true
            value: step.command
        }
        StudioButton {
            id: copyButton
            text: step.shell.i18n("Copy")
            onClicked: field.copyAll()
        }
    }
}
