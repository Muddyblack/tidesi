import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as QC
import "../../../ui"

// The popup window of the Windows and macOS tray apps (app.py places, shows
// and hides it, and draws the tray icon). Everything inside — and all state
// behind it — is the shared ui/ (AppState.qml, PopupContent.qml), fed by the
// in-process `backend` object app.py exposes. This file is only the window
// around it and the optional floating pill.
Window {
    id: root

    // Wider while the settings studio is open: it has a sidebar.
    width: app.popupWidth
    // --screenshot sets this so every capture is the same size.
    property int fixedHeight: 0
    // The free height of the screen the popup opens on, set by app.py as it
    // places it. Screen.desktopAvailableHeight is every screen together, which
    // let the popup outgrow a laptop's screen beside a taller monitor.
    property int screenAvailableHeight: 0
    readonly property int maxHeight: screenAvailableHeight > 0 ? screenAvailableHeight - 60 : (Screen.desktopAvailableHeight > 0 ? Screen.desktopAvailableHeight - 60 : 720)
    // As tall as the content, up to the screen's free height.
    height: fixedHeight > 0 ? fixedHeight : Math.min(maxHeight, popupHeader.implicitHeight + 12 + popupContent.height + 28)
    // Set by app.py when the desktop has no tray to put icons in (GNOME
    // without an AppIndicator extension): the floating pill is then the only
    // way back to the popup, so it shows whatever the setting says.
    property bool forcePill: false
    visible: false
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    // Matched by the KWin script in app.py (POPUP_TITLE) on Plasma Wayland.
    title: "Tidesi"

    // Set by app.py when the macOS or Windows system backdrop succeeds.
    property bool nativeBlur: false
    // app.py matches the macOS blur to the shared appearance.
    readonly property bool light: app.light
    onLightChanged: backend.setPopupLight(light)

    // Read by app.py (the tray menu's "Settings" and the self-test).
    property alias showSettings: app.showSettings
    // Writable so --screenshot can stage pins and the pill without saving them.
    property alias settings: app.settings
    property alias activeSubTab: app.activeSubTab
    // Driven by --screenshot DIR, which shoots every tab and settings section.
    property alias activeId: app.activeId
    property alias settingsSection: app.settingsSection
    readonly property var popupTabIds: app.popupTabs.map(function (tab) {
        return tab.id;
    })

    // app.py's Backend, a context property. Named apart from AppState's own
    // `backend` property, which would otherwise shadow it in there.
    readonly property var hostBackend: backend

    AppState {
        id: app
        backend: root.hostBackend
        // Settings → Appearance → Theme "auto": the system's light or dark.
        systemLight: Qt.styleHints.colorScheme === Qt.ColorScheme.Light
        // Settings → Appearance → "use the theme's accent": the accent colour
        // set in Windows / macOS (Qt 6.6+).
        // Where the platform has none (offscreen, older Qt) the switch is hidden.
        themeAccentAvailable: Qt.styleHints.accentColor !== undefined && Qt.styleHints.accentColor.valid
        themeAccentColor: themeAccentAvailable ? Qt.styleHints.accentColor : "#3daee9"
        popupVisible: root.visible
        trayOptions: true
        systemBlur: root.nativeBlur
    }

    function flushHistory() {
        app.flushHistory();
    }

    // ── Floating pill ────────────────────────────────────────────────────────
    // The panel pill (ui/PanelPill.qml) in a small always-on-top window of
    // its own, for anyone who wants the panel's look rather than tray icons.
    // Drag it anywhere; app.py puts it back where it was left (pillPosition) or,
    // the first time, just above the taskbar. A click opens the popup by it.
    // It never takes focus, so clicking it does not close an open popup first.
    Window {
        id: pillWindow

        objectName: "floatingPill"
        readonly property string placementMode: app.settings.desktopPillPlacement || "free"
        property bool taskbarDocked: false
        property bool taskbarLight: false
        // Declared inside the popup, a Window would be its transient child, and
        // Qt shows a transient child only while its parent is shown — the popup
        // is hidden most of the time. Without a parent it stands on its own.
        transientParent: null
        visible: app.settings.floatingPill === true || root.forcePill
        width: pill.implicitWidth + 8
        height: pill.implicitHeight + 8
        color: "transparent"
        flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
        // Matched by the KWin script in app.py (PILL_TITLE) on Plasma Wayland.
        title: "Tidesi pill"

        onXChanged: pillSave.restart()
        onYChanged: pillSave.restart()

        Timer {
            id: pillSave
            interval: 800
            onTriggered: {
                if (pillWindow.visible && !pillWindow.taskbarDocked && pillWindow.placementMode === "free")
                    app.setSetting2("pillPosition", {
                        x: pillWindow.x,
                        y: pillWindow.y
                    });
            }
        }

        PanelPill {
            id: pill

            anchors.centerIn: parent
            iconSource: app.pillIcon
            // The pill's own hover sits under the drag area below, so the tint
            // is driven from there.
            active: pillMouse.containsMouse || root.visible
            slots: app.pillSlots
            stale: app.pillStale
            hasError: app.pillHasError
            groups: app.panelGroups
            framed: !pillWindow.taskbarDocked
            textColor: (pillWindow.taskbarDocked ? pillWindow.taskbarLight : app.light) ? "#172033" : "#f8fafc"
        }

        // A press that moves drags the window; one that does not is a click.
        MouseArea {
            id: pillMouse

            property point pressedAt
            property bool dragged: false

            anchors.fill: pill
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onPressed: mouse => {
                pressedAt = Qt.point(mouse.x, mouse.y);
                dragged = false;
            }
            onPositionChanged: mouse => {
                if (pressed && !dragged && Math.abs(mouse.x - pressedAt.x) + Math.abs(mouse.y - pressedAt.y) > 4) {
                    dragged = true;
                    if (pillWindow.taskbarDocked) {
                        pillWindow.taskbarDocked = false;
                        app.setSetting2("windowsTaskbar", false);
                    }
                    app.setSetting2("desktopPillPlacement", "free");
                    pillWindow.startSystemMove();
                }
            }
            onClicked: {
                if (!dragged)
                    backend.togglePopupFromPill();
            }
        }

        QC.ToolTip {
            // A window of its own: the pill's window is only as big as the pill.
            popupType: QC.Popup.Window
            visible: pillMouse.containsMouse && !pillMouse.pressed && !root.visible && pill.tooltipText !== ""
            delay: 500
            text: pill.tooltipText
        }
    }

    // ── Window ───────────────────────────────────────────────────────────────
    Shortcut {
        sequence: "Escape"
        onActivated: root.hide()
    }

    // Only the title row moves the popup. A handler on the whole window can
    // steal slider and scrolling gestures from the settings below it.
    Item {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 44
        DragHandler {
            target: null
            acceptedButtons: Qt.LeftButton
            grabPermissions: PointerHandler.TakeOverForbidden
            onActiveChanged: {
                if (active)
                    root.startSystemMove();
            }
        }
    }

    // ── Popup ──
    // The shared glass (ui/PopupBackground.qml), with its tint and decoration.
    PopupBackground {
        anchors.fill: parent
        shell: app
        blurred: root.nativeBlur
    }

    // Capped height, so the settings page scrolls rather than running off the
    // screen — see the same Flickable in TidesiShell.qml.
    // Title bar and tabs stay put while the body below scrolls.
    PopupHeader {
        id: popupHeader
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 14
        z: 2
        shell: app
        snapshotTarget: root.contentItem
    }

    Flickable {
        id: contentFlick
        anchors.top: popupHeader.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 14
        anchors.topMargin: 12
        clip: true
        contentWidth: width
        contentHeight: popupContent.height
        boundsBehavior: Flickable.StopAtBounds
        interactive: Math.round(contentHeight) > Math.round(height) + 1

        QC.ScrollBar.vertical: PopupScrollBar {
            policy: contentFlick.interactive ? QC.ScrollBar.AsNeeded : QC.ScrollBar.AlwaysOff
        }

        Item {
            id: popupContent

            width: contentFlick.width
            height: mainColumn.implicitHeight

            PopupContent {
                id: mainColumn
                width: parent.width
                shell: app
            }
        }
    }

    // Fades the bottom edge while more is below (ui/ScrollFade.qml).
    ScrollFade {
        anchors.left: contentFlick.left
        anchors.right: contentFlick.right
        anchors.bottom: contentFlick.bottom
        flick: contentFlick
    }
}
