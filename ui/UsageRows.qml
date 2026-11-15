import QtQuick
import QtQuick.Layouts

// The production quota rows shared by Hyprland and Windows. Kept separate
// from the popup chrome so fixture tests instantiate the actual delegates.
ColumnLayout {
    id: rows

    property var provider: null
    property string activeId: ""
    property color accent: "#10a37f"
    property var countdown: function (epoch) {
        return "";
    }
    // How a backend-worded field is shown; the popup passes shell.tr, which
    // translates it. The default shows the English text.
    property var translate: function (obj, key) {
        return obj[key] || "";
    }
    // The hover text of a row, from its window; the popup supplies it.
    property var tooltip: function (window) {
        return "";
    }
    // The row's burn-rate ETA ("~3h to 100%"), from its window; the popup
    // supplies it.
    property var eta: function (window) {
        return "";
    }
    // The row's comparison with the period before ("+12% vs last week"), from
    // its window; the popup supplies it.
    property var delta: function (window) {
        return "";
    }
    readonly property int rowCount: repeater.count

    spacing: 12

    Repeater {
        id: repeater
        model: ((rows.provider && rows.provider.quotaWindows) || []).filter(function (window) {
            return window.available === true;
        })

        UsageRow {
            required property var modelData
            objectName: "quota-" + modelData.key
            label: rows.translate(modelData, "label")
            value: modelData.pct || 0
            resetText: modelData.resetText || ""
            countdownText: rows.countdown(modelData.resetAt || 0)
            detail: rows.translate(modelData, "detail")
            note: rows.translate(modelData, "note")
            barColor: modelData.color || (rows.activeId === "antigravity" && (modelData.key === "external" || modelData.key === "rest" || (modelData.label && modelData.label.indexOf("Claude") !== -1)) ? "#34a853" : rows.accent)
            showMeter: modelData.showMeter !== false
            tooltipText: rows.tooltip(modelData)
            etaText: rows.eta(modelData)
            deltaText: rows.delta(modelData)
        }
    }
}
