import QtQuick
import QtQuick.Window
import "../../ui" as Widgets
import "../../ui/theme" as Appearance

Window {
    id: window
    width: 400
    height: 300
    visible: true
    property bool light: false
    property real referenceTime: 1_792_281_600
    color: light ? "#f4f6fa" : "#141820"
    onColorChanged: Appearance.Palette.window = color
    Component.onCompleted: Appearance.Palette.window = color
    property var usage: ({})
    readonly property var rendered: chart.chartData
    QtObject {
        id: qaShell
        property color cardColor: window.light ? "#ffffff" : "#202632"
        property color activeAccent: "#34d399"
        property color dangerColor: "#f59e0b"
        function i18n(text) {
            for (var i = 1; i < arguments.length; i++)
                text = text.replace("%" + i, arguments[i]);
            return text;
        }
    }
    Widgets.ZedUsageChart {
        id: chart
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 12
        shell: qaShell
        usage: window.usage
        nowSeconds: window.referenceTime
    }
}
