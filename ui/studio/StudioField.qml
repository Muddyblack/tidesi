import QtQuick
import "../theme" as Appearance
import "Theme.js" as Theme
import "../js/Tone.js" as Tone

// Sunk single-line field. Commits on Enter and when focus leaves, so a
// half-typed value never reaches the backend; `live` commits per keystroke
// for fields the owner debounces itself (API keys).
Rectangle {
    id: control

    property string value: ""
    property string placeholder: ""
    property bool secret: false
    property bool live: false
    property bool readOnly: false
    property bool mono: false
    // Only #rrggbb, for colour fields.
    property bool hex: false
    // Reveal button for secrets.
    property bool revealed: false
    signal committed(string value)
    readonly property alias text: input.text

    function copyAll() {
        input.selectAll();
        input.copy();
        input.deselect();
    }

    implicitWidth: 220
    implicitHeight: 30
    width: implicitWidth
    height: implicitHeight
    radius: 8
    color: Tone.c(Appearance.Palette, Theme.sunk)
    border.width: 1
    border.color: input.activeFocus ? Qt.rgba(0.31, 0.62, 0.87, 0.8) : area.containsMouse ? Tone.c(Appearance.Palette, "#40ffffff") : Tone.c(Appearance.Palette, Theme.line2)

    function commit() {
        var t = input.text.trim();
        if (hex && !/^#[0-9A-Fa-f]{6}$/.test(t)) {
            input.text = control.value;
            return;
        }
        if (hex)
            t = t.toLowerCase();
        if (t !== control.value)
            control.committed(t);
    }

    // A field nobody is typing in shows the start of its text, the part that
    // tells two names or folders apart, not wherever the cursor was left.
    function showStart() {
        if (!input.activeFocus)
            input.cursorPosition = 0;
    }

    onValueChanged: if (!input.activeFocus) {
        input.text = value;
        showStart();
    }
    Component.onCompleted: {
        input.text = value;
        showStart();
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.IBeamCursor
        onClicked: input.forceActiveFocus()
    }
    TextInput {
        id: input
        x: 10
        width: parent.width - 20 - (control.secret ? 22 : 0)
        anchors.verticalCenter: parent.verticalCenter
        color: Tone.c(Appearance.Palette, Theme.text)
        selectionColor: Qt.rgba(0.31, 0.62, 0.87, 0.45)
        font.pixelSize: 11
        font.family: control.mono ? "monospace" : defaultFont.font.family
        clip: true
        selectByMouse: true
        readOnly: control.readOnly
        echoMode: control.secret && !control.revealed ? TextInput.Password : TextInput.Normal
        validator: control.hex ? hexValidator : null
        onTextEdited: if (control.live)
            control.commit()
        onAccepted: control.commit()
        onActiveFocusChanged: if (!activeFocus) {
            if (!control.readOnly)
                control.commit();
            control.showStart();
        }
        Keys.onEscapePressed: {
            text = control.value;
            focus = false;
        }
    }
    // Only to read the platform's default family back.
    Text {
        id: defaultFont
        visible: false
    }
    RegularExpressionValidator {
        id: hexValidator
        regularExpression: /#[0-9A-Fa-f]{0,6}/
    }
    Text {
        anchors.fill: input
        verticalAlignment: Text.AlignVCenter
        visible: input.text === "" && !input.activeFocus
        text: control.placeholder
        color: Tone.c(Appearance.Palette, Theme.dim)
        font: input.font
        elide: Text.ElideRight
    }
    Text {
        visible: control.secret
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        text: control.revealed ? "🙈" : "👁"
        font.pixelSize: 12
        opacity: revealArea.containsMouse ? 1 : 0.55
        MouseArea {
            id: revealArea
            anchors.fill: parent
            anchors.margins: -5
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: control.revealed = !control.revealed
        }
    }
}
