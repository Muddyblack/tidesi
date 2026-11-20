import QtQuick
import QtQuick.Layouts
import "js/PanelColor.js" as PanelColor
import "js/Tone.js" as Tone

// Compact panel readout, mirroring the Plasma PanelSlot: layered tinted icon +
// animated % (or cost text) that rolls up/down when the value changes.
RowLayout {
    id: slot

    property real pct: 0
    property color iconColor: "#cc785c"
    property bool stale: false
    property string costText: ""    // when non-empty, shown instead of the %
    // The provider's brand logo, colorized to `iconColor` — as in the Plasma
    // compact representation, the panel reads severity from the icon's colour,
    // so the logo contributes its shape and the backend its colour.
    property string iconSource: ""
    // An extra account's initial, on the logo's corner.
    property string badge: ""
    // The normal-level text colour; a host on a light panel passes its theme's.
    property color textColor: "#f8fafc"
    property color surfaceColor: "#10141c"
    readonly property color readableIcon: Tone.onSurface(iconColor, surfaceColor)
    readonly property bool lightSurface: surfaceColor.hslLightness > 0.5

    readonly property color dangerColor: "#ff4d4d"
    readonly property color warningColor: "#ffa64d"

    property real displayPct: 0
    onPctChanged: displayPct = pct
    Component.onCompleted: displayPct = pct
    Behavior on displayPct {
        NumberAnimation {
            duration: 600
            easing.type: Easing.OutCubic
        }
    }

    spacing: 5
    opacity: stale ? 0.55 : 1.0
    Behavior on opacity {
        NumberAnimation {
            duration: 300
        }
    }

    Item {
        Layout.preferredWidth: 16
        Layout.preferredHeight: 16
        Layout.alignment: Qt.AlignVCenter

        // Canvas also works with Qt Quick's software renderer, where
        // MultiEffect cannot render the otherwise hidden source images.
        Canvas {
            id: logo
            anchors.fill: parent
            property url loadedSource
            readonly property real rasterScale: Math.max(1, Screen.devicePixelRatio)
            canvasSize: Qt.size(width * rasterScale, height * rasterScale)

            function loadSource() {
                if (loadedSource && loadedSource.toString() !== "")
                    unloadImage(loadedSource);
                loadedSource = Tone.panelLogo(slot.iconSource);
                if (loadedSource.toString() !== "")
                    loadImage(loadedSource);
                requestPaint();
            }
            Component.onCompleted: loadSource()
            onImageLoaded: requestPaint()
            onCanvasSizeChanged: requestPaint()
            Connections {
                target: slot
                function onIconSourceChanged() {
                    logo.loadSource();
                }
                function onReadableIconChanged() {
                    logo.requestPaint();
                }
            }
            onPaint: {
                const ctx = getContext("2d");
                ctx.reset();
                if (!loadedSource || !isImageLoaded(loadedSource))
                    return;
                ctx.scale(rasterScale, rasterScale);
                // A logo that is a solid tile (Kiro, ...) would turn into a
                // featureless square once colorized, so it keeps its own
                // colours; the % beside it still carries the severity.
                ctx.drawImage(loadedSource, 2, 2, 12, 12);
                const corner = ctx.getImageData(Math.round(3.5 * rasterScale), Math.round(3.5 * rasterScale), 1, 1).data;
                if (corner[3] > 200)
                    return;
                ctx.clearRect(0, 0, 16, 16);
                ctx.globalAlpha = 0.22;
                ctx.drawImage(loadedSource, 0, 0, 16, 16);
                ctx.globalAlpha = 1;
                ctx.drawImage(loadedSource, 2, 2, 12, 12);
                ctx.globalCompositeOperation = "source-in";
                ctx.fillStyle = slot.readableIcon;
                ctx.fillRect(0, 0, 16, 16);
            }
        }

        // Which account, when the provider has more than one on the panel.
        Rectangle {
            visible: slot.badge !== ""
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.rightMargin: -3
            anchors.bottomMargin: -3
            width: 10
            height: 10
            radius: 5
            color: slot.surfaceColor
            border.width: 1
            border.color: slot.readableIcon

            Text {
                anchors.centerIn: parent
                text: slot.badge
                font.pixelSize: 7
                font.bold: true
                color: slot.readableIcon
            }
        }
    }

    Text {
        text: slot.costText !== "" ? slot.costText : Math.round(slot.displayPct) + "%"
        font.pixelSize: 12
        font.bold: true
        color: {
            if (slot.lightSurface && slot.costText === "") {
                var level = PanelColor.level(slot.pct);
                if (level === "danger")
                    return "#c62828";
                if (level === "warning")
                    return "#c2410c";
            }
            return Tone.onSurface(slot.costText !== "" ? slot.iconColor : PanelColor.colorFor(slot.pct, slot.textColor), slot.surfaceColor);
        }
        Layout.alignment: Qt.AlignVCenter
    }
}
