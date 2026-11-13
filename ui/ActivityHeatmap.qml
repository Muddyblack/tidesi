import QtQuick
import "theme" as Appearance
import QtQuick.Layouts
import "js/Tone.js" as Tone

// Activity heatmap for the Stats view: a calendar of the last weeks (one square
// per day, as many weeks as the width holds, today in the last column) and a
// strip of the 24 hours of the day. Shading is relative to the busiest day or
// hour shown, on a square-root scale so quiet days still register next to a
// very busy one.
ColumnLayout {
    id: heat

    // [{ date: "YYYY-MM-DD", total }] — the provider's per-day series.
    property var series: []
    // 24 numbers, hour 0 first (stats.hourCounts); empty or all zero hides the strip.
    property var hours: []
    property color accent: "#38bdf8"
    property var shell
    // Turns a day's or hour's value into tooltip text.
    property var describe: function (v) {
        return String(Math.round(v));
    }

    readonly property int cell: 10
    readonly property int gap: 2
    readonly property int labelW: 24
    readonly property int weeks: Math.max(4, Math.min(53, Math.floor((width - labelW + gap) / (cell + gap))))

    // date string -> total
    readonly property var byDate: {
        var m = {};
        for (var i = 0; i < series.length; i++) {
            var d = series[i] && series[i].date ? String(series[i].date).slice(0, 10) : "";
            if (d !== "")
                m[d] = (m[d] || 0) + Number(series[i].total || 0);
        }
        return m;
    }

    function pad(n) {
        return n < 10 ? "0" + n : String(n);
    }
    function key(d) {
        return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
    }

    // The days on screen, column by column (Monday on top), as
    // { date, key, value, future }.
    readonly property var days: {
        var today = new Date();
        today.setHours(12, 0, 0, 0);
        // Monday of the current week, then back to the first column.
        var start = new Date(today);
        start.setDate(today.getDate() - ((today.getDay() + 6) % 7) - (weeks - 1) * 7);
        var out = [];
        for (var i = 0; i < weeks * 7; i++) {
            var d = new Date(start);
            d.setDate(start.getDate() + i);
            var k = key(d);
            out.push({
                date: d,
                key: k,
                value: byDate[k] || 0,
                future: d > today
            });
        }
        return out;
    }
    readonly property real dayMax: {
        var mx = 0;
        for (var i = 0; i < days.length; i++)
            mx = Math.max(mx, days[i].value);
        return mx;
    }
    readonly property int activeShown: {
        var n = 0;
        for (var i = 0; i < days.length; i++)
            if (days[i].value > 0)
                n++;
        return n;
    }
    readonly property real hourMax: {
        var mx = 0;
        for (var i = 0; i < (hours || []).length; i++)
            mx = Math.max(mx, Number(hours[i] || 0));
        return mx;
    }

    // 0 (nothing) to 4 (the busiest) for a value against `max`.
    function level(v, max) {
        if (!(v > 0) || !(max > 0))
            return 0;
        return Math.max(1, Math.min(4, Math.ceil(4 * Math.sqrt(v / max))));
    }
    function shade(lv) {
        if (lv === 0)
            return Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.06));
        return Qt.rgba(accent.r, accent.g, accent.b, [0, 0.28, 0.5, 0.75, 1][lv]);
    }

    Layout.fillWidth: true
    spacing: 6
    visible: series.length > 0 || hourMax > 0

    // ── Calendar ─────────────────────────────────────────────────────────
    RowLayout {
        Layout.fillWidth: true
        Text {
            text: heat.shell.i18n("Activity")
            font.pixelSize: 9
            color: Tone.c(Appearance.Palette, "#94a3b8")
            opacity: 0.8
        }
        Item {
            Layout.fillWidth: true
        }
        Text {
            text: heat.shell.i18np("%1 active day in the last %2 weeks", "%1 active days in the last %2 weeks", heat.activeShown, heat.weeks)
            font.pixelSize: 9
            color: Tone.c(Appearance.Palette, "#94a3b8")
            opacity: 0.6
        }
    }

    Item {
        id: calendar
        Layout.fillWidth: true
        Layout.preferredHeight: monthRow.height + 3 + 7 * (heat.cell + heat.gap)
        visible: heat.series.length > 0

        // Month names over the first column of each month.
        Item {
            id: monthRow
            x: heat.labelW
            width: parent.width - heat.labelW
            height: 11
            Repeater {
                model: heat.weeks
                Text {
                    required property int index
                    readonly property var first: heat.days[index * 7]
                    readonly property bool starts: !!first && (index === 0 ? first.date.getDate() <= 7 : first.date.getMonth() !== heat.days[(index - 1) * 7].date.getMonth())
                    visible: starts && index < heat.weeks - 2
                    x: index * (heat.cell + heat.gap)
                    text: first ? Qt.locale().monthName(first.date.getMonth(), Locale.ShortFormat) : ""
                    font.pixelSize: 8
                    color: Tone.c(Appearance.Palette, "#94a3b8")
                    opacity: 0.7
                }
            }
        }

        // Mon, Wed and Fri, in the user's language.
        Repeater {
            model: [0, 2, 4]
            Text {
                required property int modelData
                y: monthRow.height + 3 + modelData * (heat.cell + heat.gap) - 1
                width: heat.labelW - 4
                // Locale day 1 is Monday.
                text: Qt.locale().dayName((modelData + 1) % 7, Locale.ShortFormat)
                font.pixelSize: 8
                color: Tone.c(Appearance.Palette, "#94a3b8")
                opacity: 0.7
                elide: Text.ElideRight
            }
        }

        Item {
            id: grid
            x: heat.labelW
            y: monthRow.height + 3
            width: heat.weeks * (heat.cell + heat.gap)
            height: 7 * (heat.cell + heat.gap)

            Repeater {
                model: heat.days.length
                Rectangle {
                    required property int index
                    readonly property var day: heat.days[index]
                    x: Math.floor(index / 7) * (heat.cell + heat.gap)
                    y: (index % 7) * (heat.cell + heat.gap)
                    width: heat.cell
                    height: heat.cell
                    radius: 2
                    visible: !day.future
                    color: heat.shade(heat.level(day.value, heat.dayMax))
                    border.width: gridHover.index === index ? 1 : 0
                    border.color: Tone.c(Appearance.Palette, "#f8fafc")
                }
            }

            MouseArea {
                id: gridHover
                property int index: -1
                anchors.fill: parent
                hoverEnabled: true
                onPositionChanged: mouse => {
                    var col = Math.floor(mouse.x / (heat.cell + heat.gap));
                    var row = Math.floor(mouse.y / (heat.cell + heat.gap));
                    var i = col * 7 + row;
                    index = col >= 0 && row >= 0 && row < 7 && i < heat.days.length && !heat.days[i].future ? i : -1;
                }
                onExited: index = -1
            }

            TipBubble {
                visible: gridHover.index >= 0
                x: gridHover.index >= 0 ? Math.min(grid.width - width, Math.floor(gridHover.index / 7) * (heat.cell + heat.gap)) : 0
                y: gridHover.index >= 0 ? (gridHover.index % 7) * (heat.cell + heat.gap) - height - 4 : 0
                text: {
                    if (gridHover.index < 0)
                        return "";
                    var d = heat.days[gridHover.index];
                    return Qt.locale().toString(d.date, Locale.ShortFormat).split(" ")[0] + " · " + (d.value > 0 ? heat.describe(d.value) : heat.shell.i18n("no activity"));
                }
            }
        }
    }

    // ── Hour of day ──────────────────────────────────────────────────────
    ColumnLayout {
        Layout.fillWidth: true
        Layout.topMargin: 4
        spacing: 3
        visible: heat.hourMax > 0

        Text {
            text: heat.shell.i18n("Hour of day")
            font.pixelSize: 9
            color: Tone.c(Appearance.Palette, "#94a3b8")
            opacity: 0.8
        }

        Item {
            id: strip
            Layout.fillWidth: true
            Layout.preferredHeight: 14
            readonly property real cellW: (width - 23 * 2) / 24

            Repeater {
                model: 24
                Rectangle {
                    required property int index
                    readonly property real v: Number((heat.hours || [])[index] || 0)
                    x: index * (strip.cellW + 2)
                    width: strip.cellW
                    height: strip.height
                    radius: 2
                    color: heat.shade(heat.level(v, heat.hourMax))
                    // The busiest hour, outlined.
                    border.width: v > 0 && v === heat.hourMax || hourHover.index === index ? 1 : 0
                    border.color: Tone.c(Appearance.Palette, "#f8fafc")
                }
            }

            MouseArea {
                id: hourHover
                property int index: -1
                anchors.fill: parent
                hoverEnabled: true
                onPositionChanged: mouse => {
                    var i = Math.floor(mouse.x / (strip.cellW + 2));
                    index = i >= 0 && i < 24 ? i : -1;
                }
                onExited: index = -1
            }

            TipBubble {
                visible: hourHover.index >= 0
                x: hourHover.index >= 0 ? Math.min(strip.width - width, hourHover.index * (strip.cellW + 2)) : 0
                y: -height - 4
                // Hours count sessions or requests started, not the day unit.
                text: hourHover.index < 0 ? "" : heat.pad(hourHover.index) + ":00 · " + heat.shell.i18np("%1 start", "%1 starts", Math.round(Number((heat.hours || [])[hourHover.index] || 0)))
            }
        }

        // 0, 6, 12, 18 under their hours.
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 10
            Repeater {
                model: [0, 6, 12, 18]
                Text {
                    required property int modelData
                    x: modelData * (strip.cellW + 2)
                    text: heat.pad(modelData) + ":00"
                    font.pixelSize: 8
                    color: Tone.c(Appearance.Palette, "#94a3b8")
                    opacity: 0.7
                }
            }
        }
    }
}
