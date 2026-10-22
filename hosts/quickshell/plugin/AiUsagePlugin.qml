import QtQuick
import QtQuick.Controls.Basic as QC
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Common
import qs.Widgets
import qs.Modules.Plugins
import "./ui" as Upstream
import "./ui/js/I18n.js" as I18n

PluginComponent {
    id: root

    property point popupAnchor: Qt.point(0, 0)

    readonly property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "").replace(/\/$/, "")
    readonly property string configPath: {
        const xdgConfig = Quickshell.env("XDG_CONFIG_HOME");
        const configHome = xdgConfig && xdgConfig !== "" ? xdgConfig : Quickshell.env("HOME") + "/.config";
        return configHome + "/tidesi/quickshell-settings.json";
    }

    QtObject {
        id: processRunner

        property Component job: Component {
            Process {
                id: proc

                property var callback: null
                property string out: ""
                property string err: ""
                property int outDone: 0
                property int code: -1

                function finish(): void {
                    if (proc.outDone < 2 || proc.code < 0)
                        return;
                    const finishedCallback = proc.callback;
                    proc.callback = null;
                    if (typeof finishedCallback === "function")
                        finishedCallback(proc.out, proc.err, proc.code);
                    proc.destroy();
                }

                stdout: StdioCollector {
                    onStreamFinished: {
                        proc.out = this.text;
                        proc.outDone += 1;
                        proc.finish();
                    }
                }
                stderr: StdioCollector {
                    onStreamFinished: {
                        proc.err = this.text;
                        proc.outDone += 1;
                        proc.finish();
                    }
                }
                onExited: function (exitCode) {
                    proc.code = exitCode;
                    proc.finish();
                }
            }
        }

        function run(argv, callback): void {
            const process = processRunner.job.createObject(processRunner, {
                callback: callback
            });
            process.exec({
                command: argv
            });
        }
    }

    Upstream.CommandBackend {
        id: commandBackend
        runner: processRunner
        toolsDir: root.pluginDir + "/backend/sh"
        translateDir: root.pluginDir + "/translate"
        assetsDir: root.pluginDir + "/assets"
        configPath: root.configPath
        pythonPath: appState.settings.pythonPath || ""
        systemLanguages: {
            const languages = (Quickshell.env("LANGUAGE") || "").split(":");
            const uiLanguages = Qt.locale().uiLanguages;
            for (let i = 0; i < uiLanguages.length; i++)
                languages.push(uiLanguages[i]);
            return I18n.languageCandidates(languages);
        }
    }

    PluginAppState {
        id: appState
        backend: commandBackend
        popupVisible: appState.popupOpen
        interpreterControls: true
        cliPath: commandBackend.toolsDir + "/tidesi-cli"
        systemLight: Qt.styleHints.colorScheme === Qt.ColorScheme.Light
    }

    Connections {
        target: appState
        function onPluginPopupToggled() {
            root.triggerPopout();
        }
    }

    horizontalBarPill: appState.pillSource
    pillClickAction: () => {
        root.popupAnchor = root.mapToItem(null, root.width / 2, root.height / 2);
        appState.popupOpen = !appState.popupOpen;
    }

    // The surface stays screen-sized: resizing a grabbing xdg_popup on Labwc
    // stretches its last buffer instead of repainting the new settings view.
    // Only upstream's popup body draws; the transparent outside area dismisses it.
    PanelWindow {
        id: popup
        screen: root.parentScreen
        anchors {
            top: true
            bottom: true
            left: true
            right: true
        }
        visible: appState.popupOpen
        color: "transparent"
        exclusiveZone: 0
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.namespace: "tidesi-plugin"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: visible ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
        onVisibleChanged: appState.popupOpen = visible

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            onPressed: appState.popupOpen = false
        }

        Item {
            id: popupBody
            readonly property string edge: root.axis?.edge || "bottom"
            width: Math.min(appState.popupWidth, parent.width - 24)
            height: Math.min(parent.height - 90, popupHeader.implicitHeight + popupContent.implicitHeight + 40)
            x: edge === "left" ? root.barThickness + 8 : edge === "right" ? parent.width - width - root.barThickness - 8 : Math.max(12, Math.min(parent.width - width - 12, root.popupAnchor.x - width / 2))
            y: edge === "top" ? root.barThickness + 8 : edge === "bottom" ? parent.height - height - root.barThickness - 8 : Math.max(12, Math.min(parent.height - height - 12, root.popupAnchor.y - height / 2))
            palette.window: appState.windowColor
            focus: true

            Shortcut {
                sequence: "Escape"
                context: Qt.WindowShortcut
                enabled: popup.visible
                onActivated: appState.popupOpen = false
            }

            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.AllButtons
            }

            Upstream.PopupBackground {
                anchors.fill: parent
                shell: appState
            }

            Upstream.PopupHeader {
                id: popupHeader
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: 14
                z: 2
                shell: appState
                snapshotTarget: popupBody
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
                contentHeight: popupContent.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                interactive: Math.round(contentHeight) > Math.round(height) + 1

                QC.ScrollBar.vertical: Upstream.PopupScrollBar {
                    policy: contentFlick.interactive ? QC.ScrollBar.AsNeeded : QC.ScrollBar.AlwaysOff
                }

                Upstream.PopupContent {
                    id: popupContent
                    width: contentFlick.width
                    shell: appState
                }
            }

            Upstream.ScrollFade {
                anchors.left: contentFlick.left
                anchors.right: contentFlick.right
                anchors.bottom: contentFlick.bottom
                flick: contentFlick
            }
        }
    }
}
