import QtQuick
import "theme" as Appearance
import QtQuick.Window
import QtQuick.Layouts
import "js/UsageHistory.js" as UsageHistory
import "js/OpenCodeUsage.js" as OpenCodeUsage
import "js/Tone.js" as Tone

// Usage-history chart, ported from the Plasma UsageChart. Self-contained:
// feed it the unified history array plus the window list for the active tab.
Rectangle {
    id: chart

    // Full unified history: [{t, s?, w?, cp?, cw?, kr?, ag?, agg?, age?, or?, mv?, gr?, za?, gh?, ds?}]
    property var shell
    property var usageHistory: []
    property var dailySeries: []
    property real referenceTime: {
        usageHistory;
        return Date.now();
    }
    readonly property bool isTokens: currentWindow !== null && currentWindow.unit === "tokens"
    // Chart ranges for the active tab, straight from the provider contract:
    // [{id, key, label, size, granularity, raw, resets}] (size in ms)
    property var windows: []
    property string chartWindow: ""
    property color accent: "#7dd3fc"
    property string currency: ""
    property real chartTimeOffset: 0
    property string activeId: ""
    property string antigravityFilter: "both"
    // Gate from the host (settings toggle / settings page open) combined with
    // the "have at least one data point" check below.
    property bool extraVisible: true

    signal windowSelected(string id)
    signal antigravityFilterSelected(string filter)

    onChartWindowChanged: chartTimeOffset = 0

    readonly property color googleBlue: "#4285f4"
    readonly property color googleGreen: "#34a853"

    readonly property var currentWindow: {
        for (var i = 0; i < windows.length; i++)
            if (windows[i].id === chartWindow)
                return windows[i];
        return windows.length > 0 ? windows[0] : null;
    }

    // An extra account ("ag@work") and a login told apart from another one in
    // the same folder ("ag#3f9a1c2b") record under series of their own: the part
    // of the window's key from the "@" or "#" on, "" for the folder's first login.
    readonly property string seriesSuffix: {
        var key = currentWindow && currentWindow.key ? String(currentWindow.key) : "";
        var at = key.search(/[@#]/);
        return at < 0 ? "" : key.substring(at);
    }
    readonly property bool isAntigravity: activeId.split("@")[0] === "antigravity" || (currentWindow && currentWindow.key && currentWindow.key.indexOf("ag") === 0)
    readonly property bool hasModelFilter: isAntigravity
    readonly property bool isBoth: isAntigravity && antigravityFilter === "both"

    readonly property string historyKey: {
        if (isAntigravity) {
            if (antigravityFilter === "gemini")
                return "agg" + seriesSuffix;
            if (antigravityFilter === "rest")
                return "age" + seriesSuffix;
            return "ag" + seriesSuffix;
        }
        return currentWindow ? currentWindow.key : "w";
    }
    readonly property real windowSize: currentWindow ? currentWindow.size : 7 * 24 * 3600000
    // Money series (Mistral spend, DeepSeek balance) are absolute amounts the
    // chart auto-scales; rolling plan windows are the ones that drop at a reset.
    readonly property bool isCost: currentWindow ? currentWindow.raw === true : false
    readonly property bool windowHasResets: currentWindow ? currentWindow.resets === true : false

    // Whether a history point carries any of this account's Antigravity series.
    function hasAntigravityPoint(p) {
        var keys = ["ag", "agg", "age"];
        for (var i = 0; i < keys.length; i++) {
            var v = p[keys[i] + chart.seriesSuffix];
            if (v !== undefined && v !== null)
                return true;
        }
        return false;
    }

    function extractSeries(key, fallbackKey) {
        var out = [];
        var now_ms = chart.referenceTime;
        var maxT = now_ms - chart.chartTimeOffset;
        var minT = maxT - chart.windowSize;
        for (var i = 0; i < usageHistory.length; i++) {
            var p = usageHistory[i];
            var v = p[key];
            if ((v === undefined || v === null) && fallbackKey) {
                v = p[fallbackKey];
            }
            if (v === undefined || v === null)
                continue;
            if (p.t >= minT && p.t <= maxT)
                out.push({
                    t: p.t,
                    v: v
                });
        }
        return out;
    }

    readonly property var geminiSeries: isAntigravity ? extractSeries("agg" + seriesSuffix, "ag" + seriesSuffix) : []
    readonly property var restSeries: isAntigravity ? extractSeries("age" + seriesSuffix, null) : []

    // {t, v[, raw]} view of the selected series inside the visible window
    readonly property var series: {
        if (isTokens)
            return OpenCodeUsage.chartSeries(chartWindow, dailySeries, referenceTime, chartTimeOffset);
        var out = [];
        var now_ms = chart.referenceTime;
        var maxT = now_ms - chart.chartTimeOffset;
        var minT = maxT - chart.windowSize;
        for (var i = 0; i < usageHistory.length; i++) {
            var p = usageHistory[i];
            var v = p[chart.historyKey];
            if ((v === undefined || v === null) && chart.isAntigravity && chart.historyKey === "agg" + chart.seriesSuffix) {
                v = p["ag" + chart.seriesSuffix];
            }
            if (v === undefined || v === null)
                continue;
            if (p.t >= minT && p.t <= maxT)
                out.push({
                    t: p.t,
                    v: v
                });
        }
        if (chart.windowHasResets && chart.currentWindow)
            out = UsageHistory.withResets(out, chart.currentWindow.resetAt * 1000, chart.currentWindow.periodMs, minT, maxT);
        if (chart.isCost && out.length > 0) {
            var maxV = 0;
            for (var j = 0; j < out.length; j++)
                if (out[j].v > maxV)
                    maxV = out[j].v;
            if (maxV > 0)
                for (var k = 0; k < out.length; k++)
                    out[k] = {
                        t: out[k].t,
                        v: (out[k].v / maxV) * 100,
                        raw: out[k].v
                    };
        }
        return out;
    }

    // True if the selected series has ANY point in the whole history (not just
    // inside the visible window). Used to keep the chart frame on screen while
    // paging back past the data, instead of vanishing and trapping the user.
    readonly property bool hasAnySeriesData: {
        for (var i = 0; i < usageHistory.length; i++) {
            var p = usageHistory[i];
            if (chart.isAntigravity) {
                if (chart.hasAntigravityPoint(p))
                    return true;
            } else {
                var v = p[chart.historyKey];
                if (v !== undefined && v !== null)
                    return true;
            }
        }
        return false;
    }

    readonly property var resetTimestamps: {
        if (!windowHasResets)
            return [];
        var pts = chart.series;
        var out = [];
        for (var i = 1; i < pts.length; i++)
            if (pts[i - 1].v - pts[i].v > 6)
                out.push(pts[i].t);
        return out;
    }

    readonly property real chartMaxRaw: {
        if (!isCost)
            return 0;
        var pts = chart.series;
        var m = 0;
        for (var i = 0; i < pts.length; i++)
            if (pts[i].raw !== undefined && pts[i].raw > m)
                m = pts[i].raw;
        return m;
    }

    function chartYLabel(fraction) {
        if (isTokens)
            return OpenCodeUsage.compactTokens(chartMaxRaw * fraction);
        if (isCost) {
            var symbol = currency === "CNY" ? "¥" : (currency === "USD" || historyKey.split(/[@#]/)[0] === "mv" ? "$" : "");
            return chartMaxRaw > 0 ? symbol + (chartMaxRaw * fraction).toFixed(2) + (symbol === "" && currency !== "" ? " " + currency : "") : "";
        }
        return Math.round(fraction * 100) + "%";
    }

    function rangeText() {
        var now_ms = chart.referenceTime;
        var maxT = now_ms - chartTimeOffset;
        var minT = maxT - windowSize;
        var minDate = new Date(minT);
        var maxDate = new Date(maxT);
        if (windowSize <= 24 * 3600000) {
            if (minDate.toDateString() === maxDate.toDateString())
                return chart.shell.formatTime(minDate) + " - " + chart.shell.formatTime(maxDate) + " (" + chart.shell.formatDay(maxDate) + ")";
            return chart.shell.formatDayTime(minDate) + " - " + chart.shell.formatDayTime(maxDate);
        }
        return chart.shell.formatDay(minDate) + " - " + chart.shell.formatDay(maxDate);
    }

    // Usage slope in %/hour over the trailing lookback, for the pulse effect —
    // the same fit the Plasma widget uses. See UsageHistory.slopePerHour.
    function usageSlopePerHour(lookbackMs) {
        return UsageHistory.slopePerHour(usageHistory, chart.historyKey, lookbackMs, chart.isAntigravity ? "ag" + chart.seriesSuffix : null);
    }

    visible: extraVisible && hasAnySeriesData && windows.length > 0
    Layout.fillWidth: true
    Layout.preferredHeight: implicitHeight
    implicitHeight: hasModelFilter ? 208 : 184
    radius: 10
    color: shell ? shell.cardColor : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.045))
    border.width: 1
    border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.08))
    clip: true

    // subtle inner top highlight
    Rectangle {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 1
        color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.10))
        radius: 10
    }

    // ── Chart navigation (top-left) ──
    RowLayout {
        id: chartNavRow
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.topMargin: 7
        anchors.leftMargin: 12
        spacing: 6

        Rectangle {
            radius: 4
            implicitHeight: 16
            implicitWidth: 16
            color: leftNavMouse.containsMouse ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.12)) : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.06))
            opacity: enabled ? 1.0 : 0.3
            enabled: {
                var oldestT = 0;
                for (var i = 0; i < chart.usageHistory.length; i++) {
                    var p = chart.usageHistory[i];
                    var hasPt = chart.isAntigravity ? chart.hasAntigravityPoint(p) : (p[chart.historyKey] !== undefined && p[chart.historyKey] !== null);
                    if (hasPt) {
                        oldestT = p.t;
                        break;
                    }
                }
                if (oldestT === 0)
                    return false;
                var now_ms = chart.referenceTime;
                var minT = (now_ms - chart.chartTimeOffset) - chart.windowSize;
                return minT > oldestT;
            }
            Text {
                anchors.centerIn: parent
                text: "<"
                font.pixelSize: 9
                font.bold: true
                color: Tone.c(Appearance.Palette, "#f8fafc")
            }
            MouseArea {
                id: leftNavMouse
                objectName: "chartPrevious"
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: chart.chartTimeOffset += chart.windowSize
            }
        }

        Text {
            objectName: "chartDateRange"
            text: chart.rangeText()
            font.pixelSize: 9
            font.bold: true
            opacity: 0.6
            color: Tone.c(Appearance.Palette, "#f8fafc")
        }

        Rectangle {
            radius: 4
            implicitHeight: 16
            implicitWidth: 16
            color: rightNavMouse.containsMouse ? Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.12)) : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.06))
            opacity: enabled ? 1.0 : 0.3
            enabled: chart.chartTimeOffset > 0
            Text {
                anchors.centerIn: parent
                text: ">"
                font.pixelSize: 9
                font.bold: true
                color: Tone.c(Appearance.Palette, "#f8fafc")
            }
            MouseArea {
                id: rightNavMouse
                objectName: "chartNext"
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: chart.chartTimeOffset = Math.max(0, chart.chartTimeOffset - chart.windowSize)
            }
        }
    }

    // ── Window toggle (5H / 24H / 7D) ──
    RowLayout {
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.topMargin: 7
        anchors.rightMargin: 8
        spacing: 4
        visible: chart.windows.length > 1

        Repeater {
            model: chart.windows

            Rectangle {
                required property var modelData
                radius: 4
                implicitHeight: 16
                implicitWidth: winLabel.implicitWidth + 12
                color: chart.chartWindow === modelData.id ? (Tone.isLight(Appearance.Palette) ? Qt.rgba(chart.accent.r, chart.accent.g, chart.accent.b, 0.18) : chart.accent) : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.06))
                opacity: chart.chartWindow === modelData.id ? 0.9 : 1.0
                Behavior on color {
                    ColorAnimation {
                        duration: 150
                    }
                }
                Text {
                    id: winLabel
                    anchors.centerIn: parent
                    text: modelData.label
                    font.pixelSize: 9
                    font.bold: chart.chartWindow === modelData.id
                    // Near-white accents (Grok, Cursor) would swallow white text.
                    color: chart.chartWindow === modelData.id ? (Tone.isLight(Appearance.Palette) ? Tone.ink(Appearance.Palette, chart.accent) : Tone.onSurface("#ffffff", chart.accent)) : Tone.c(Appearance.Palette, "#f8fafc")
                    opacity: chart.chartWindow === modelData.id ? 1.0 : 0.6
                }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: chart.windowSelected(modelData.id)
                }
            }
        }
    }

    // ── Antigravity model filter (Both / Combined / Gemini / Rest) ──
    RowLayout {
        id: modelFilterRow
        visible: chart.hasModelFilter
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.topMargin: 29
        anchors.leftMargin: 36
        spacing: 4

        readonly property var options: [
            {
                id: "both",
                label: chart.shell.i18n("Both"),
                dotColor: ""
            },
            {
                id: "combined",
                label: chart.shell.i18n("Combined"),
                dotColor: ""
            },
            {
                id: "gemini",
                label: "Gemini",
                dotColor: chart.googleBlue
            },
            {
                id: "rest",
                label: chart.shell.i18nc("quota model", "Rest"),
                dotColor: chart.googleGreen
            }
        ]

        Repeater {
            model: modelFilterRow.options
            Rectangle {
                required property var modelData
                radius: 4
                implicitHeight: 16
                implicitWidth: filterPillContent.implicitWidth + 12
                color: chart.antigravityFilter === modelData.id ? (Tone.isLight(Appearance.Palette) ? Qt.rgba(chart.accent.r, chart.accent.g, chart.accent.b, 0.18) : chart.accent) : Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.06))
                opacity: chart.antigravityFilter === modelData.id ? 0.9 : 1.0
                Behavior on color {
                    ColorAnimation {
                        duration: 150
                    }
                }
                RowLayout {
                    id: filterPillContent
                    anchors.centerIn: parent
                    spacing: 4
                    Rectangle {
                        visible: modelData.dotColor !== ""
                        implicitWidth: 5
                        implicitHeight: 5
                        Layout.preferredWidth: 5
                        Layout.preferredHeight: 5
                        radius: 2.5
                        color: modelData.dotColor
                        Layout.alignment: Qt.AlignVCenter
                    }
                    Text {
                        text: modelData.label
                        font.pixelSize: 9
                        font.bold: chart.antigravityFilter === modelData.id
                        color: chart.antigravityFilter === modelData.id ? (Tone.isLight(Appearance.Palette) ? Tone.ink(Appearance.Palette, chart.accent) : Tone.onSurface("#ffffff", chart.accent)) : Tone.c(Appearance.Palette, "#f8fafc")
                        opacity: chart.antigravityFilter === modelData.id ? 1.0 : 0.65
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        chart.antigravityFilterSelected(modelData.id);
                    }
                }
            }
        }
    }

    // ── Y-axis labels ──
    Text {
        anchors.right: chartCanvas.left
        anchors.rightMargin: 4
        y: chartCanvas.y + 2
        objectName: "chartAxisMax"
        text: chart.chartYLabel(1.0)
        font.pixelSize: 9
        opacity: Tone.isLight(Appearance.Palette) ? 0.65 : 0.35
        color: Tone.c(Appearance.Palette, "#f8fafc")
    }
    Text {
        anchors.right: chartCanvas.left
        anchors.rightMargin: 4
        y: chartCanvas.y + chartCanvas.height / 2 - 6
        text: chart.chartYLabel(0.5)
        font.pixelSize: 9
        opacity: Tone.isLight(Appearance.Palette) ? 0.65 : 0.35
        color: Tone.c(Appearance.Palette, "#f8fafc")
    }
    Text {
        anchors.right: chartCanvas.left
        anchors.rightMargin: 4
        y: chartCanvas.y + chartCanvas.height - 14
        text: chart.chartYLabel(0.0)
        font.pixelSize: 9
        opacity: Tone.isLight(Appearance.Palette) ? 0.65 : 0.35
        color: Tone.c(Appearance.Palette, "#f8fafc")
    }

    Canvas {
        id: chartCanvas
        objectName: "usageChartCanvas"
        anchors.top: parent.top
        anchors.bottom: xAxisRow.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 36
        anchors.rightMargin: 8
        anchors.topMargin: chart.hasModelFilter ? 52 : 28
        anchors.bottomMargin: 2

        property var history: chart.series
        property var geminiHistory: chart.geminiSeries
        property var restHistory: chart.restSeries
        property bool isBoth: chart.isBoth
        property color accentColor: chart.accent
        property real pulse: 0
        property int scrubIndex: -1
        property var scrubGeminiPt: null
        property var scrubRestPt: null
        property real scrubTimestamp: 0

        onHistoryChanged: {
            scrubIndex = -1;
            scrubTimestamp = 0;
            requestPaint();
        }
        onGeminiHistoryChanged: requestPaint()
        onRestHistoryChanged: requestPaint()
        onIsBothChanged: requestPaint()
        onAccentColorChanged: requestPaint()
        onPulseChanged: requestPaint()
        onScrubIndexChanged: requestPaint()
        // Tone.css colours flip with the popup's light or dark (ui/js/Tone.js).
        readonly property bool lightTone: Tone.isLight(Appearance.Palette)
        onLightToneChanged: requestPaint()
        // Both mode keeps scrubIndex at 0 while the selected points move.
        onScrubGeminiPtChanged: requestPaint()
        onScrubRestPtChanged: requestPaint()
        onScrubTimestampChanged: requestPaint()
        onVisibleChanged: if (visible)
            requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()

        readonly property bool climbingFast: {
            var slope = chart.isTokens ? null : chart.usageSlopePerHour(2 * 3600000);
            return slope !== null && slope > 2;
        }
        SequentialAnimation on pulse {
            objectName: "usagePulse"
            running: chartCanvas.climbingFast && chartCanvas.visible && chartCanvas.Window.window !== null && chartCanvas.Window.window.visible
            loops: Animation.Infinite
            NumberAnimation {
                from: 0
                to: 1
                duration: 900
                easing.type: Easing.InOutSine
            }
            NumberAnimation {
                from: 1
                to: 0
                duration: 900
                easing.type: Easing.InOutSine
            }
        }
        onClimbingFastChanged: if (!climbingFast)
            pulse = 0

        // Empty-window hint: shown while paging back (or forward) into a range
        // that has no recorded points, so the chart keeps its frame + nav arrows
        // instead of disappearing.
        Text {
            anchors.centerIn: parent
            visible: (!chartCanvas.isBoth && (!chartCanvas.history || chartCanvas.history.length === 0)) || (chartCanvas.isBoth && (!chartCanvas.geminiHistory || chartCanvas.geminiHistory.length === 0) && (!chartCanvas.restHistory || chartCanvas.restHistory.length === 0))
            text: chart.shell.i18n("No data in this range")
            color: Tone.c(Appearance.Palette, "#94a3b8")
            font.pixelSize: 11
            opacity: 0.7
        }

        onPaint: {
            var ctx = getContext("2d");
            ctx.clearRect(0, 0, width, height);

            var w = width, h = height;
            var now_ms = chart.referenceTime;
            var maxT = now_ms - chart.chartTimeOffset;
            var tRange = chart.windowSize;
            var minT = maxT - tRange;

            // dashed grid lines at 25 / 50 / 75 / 100%
            ctx.save();
            ctx.setLineDash([3, 5]);
            ctx.strokeStyle = Tone.css(Appearance.Palette, Qt.rgba(1, 1, 1, 0.08));
            ctx.lineWidth = 1;
            [25, 50, 75, 100].forEach(function (pct) {
                var y = h - (pct / 100) * h * 0.88 - h * 0.04;
                ctx.beginPath();
                ctx.moveTo(0, y);
                ctx.lineTo(w, y);
                ctx.stroke();
            });
            ctx.restore();

            function drawCurve(seriesPts, strokeColor, showFill) {
                if (!seriesPts || seriesPts.length < 1)
                    return;

                function spx(i) {
                    return ((seriesPts[i].t - minT) / tRange) * w;
                }
                function spy(v) {
                    return h - (v / 100) * h * 0.88 - h * 0.04;
                }

                var rR = Math.round(strokeColor.r * 255);
                var rG = Math.round(strokeColor.g * 255);
                var rB = Math.round(strokeColor.b * 255);
                function sRgba(a) {
                    return "rgba(" + rR + "," + rG + "," + rB + "," + a + ")";
                }

                if (seriesPts.length === 1) {
                    var sx = w / 2, sy = spy(seriesPts[0].v);
                    ctx.beginPath();
                    ctx.arc(sx, sy, 7, 0, Math.PI * 2);
                    ctx.fillStyle = sRgba(0.18);
                    ctx.fill();
                    ctx.beginPath();
                    ctx.arc(sx, sy, 4, 0, Math.PI * 2);
                    ctx.fillStyle = sRgba(1.0);
                    ctx.fill();
                    ctx.beginPath();
                    ctx.arc(sx, sy, 1.8, 0, Math.PI * 2);
                    ctx.fillStyle = Tone.css(Appearance.Palette, Qt.rgba(1, 1, 1, 0.9));
                    ctx.fill();
                    return;
                }

                function sBuildPath() {
                    ctx.moveTo(spx(0), spy(seriesPts[0].v));
                    for (var i = 0; i < seriesPts.length - 1; i++) {
                        var x0 = spx(i), y0 = spy(seriesPts[i].v);
                        var x1 = spx(i + 1), y1 = spy(seriesPts[i + 1].v);
                        var cpx = x0 + (x1 - x0) * 0.5;
                        ctx.bezierCurveTo(cpx, y0, cpx, y1, x1, y1);
                    }
                }

                // glow pass — wide soft strokes behind the crisp line
                ctx.save();
                ctx.lineJoin = "round";
                ctx.lineCap = "round";
                ctx.beginPath();
                sBuildPath();
                ctx.strokeStyle = sRgba(0.08);
                ctx.lineWidth = 14;
                ctx.stroke();
                ctx.beginPath();
                sBuildPath();
                ctx.strokeStyle = sRgba(0.18);
                ctx.lineWidth = 8;
                ctx.stroke();
                ctx.beginPath();
                sBuildPath();
                ctx.strokeStyle = sRgba(0.40);
                ctx.lineWidth = 4;
                ctx.stroke();
                ctx.restore();

                // filled gradient area
                if (showFill) {
                    var grad = ctx.createLinearGradient(0, 0, 0, h);
                    grad.addColorStop(0, sRgba(0.24));
                    grad.addColorStop(0.6, sRgba(0.06));
                    grad.addColorStop(1, sRgba(0.0));
                    ctx.beginPath();
                    sBuildPath();
                    ctx.lineTo(spx(seriesPts.length - 1), h);
                    ctx.lineTo(spx(0), h);
                    ctx.closePath();
                    ctx.fillStyle = grad;
                    ctx.fill();
                }

                // crisp line on top
                ctx.beginPath();
                sBuildPath();
                ctx.strokeStyle = sRgba(1.0);
                ctx.lineWidth = 2;
                ctx.lineJoin = "round";
                ctx.lineCap = "round";
                ctx.stroke();

                // latest point: pulsing halo + dot + white pip
                var lx = spx(seriesPts.length - 1);
                var ly = spy(seriesPts[seriesPts.length - 1].v);
                var haloR = 7 + pulse * 8;
                var haloA = 0.18 + (1 - pulse) * 0.10;
                ctx.beginPath();
                ctx.arc(lx, ly, haloR, 0, Math.PI * 2);
                ctx.fillStyle = sRgba(pulse > 0 ? haloA * (1 - pulse) + 0.06 : 0.18);
                ctx.fill();
                ctx.beginPath();
                ctx.arc(lx, ly, 4, 0, Math.PI * 2);
                ctx.fillStyle = sRgba(1.0);
                ctx.fill();
                ctx.beginPath();
                ctx.arc(lx, ly, 1.8, 0, Math.PI * 2);
                ctx.fillStyle = Tone.css(Appearance.Palette, Qt.rgba(1, 1, 1, 0.9));
                ctx.fill();
            }

            if (chartCanvas.isBoth) {
                drawCurve(chartCanvas.geminiHistory, chart.googleBlue, true);
                drawCurve(chartCanvas.restHistory, chart.googleGreen, false);
            } else {
                drawCurve(chartCanvas.history, accentColor, true);
            }

            // ── Reset-boundary markers ──
            function drawResetLine(resetMs, label, drawLabel) {
                var rx = ((resetMs - minT) / tRange) * w;
                ctx.save();
                ctx.setLineDash([2, 4]);
                ctx.strokeStyle = Tone.css(Appearance.Palette, Qt.rgba(1, 1, 1, 0.22));
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(rx, 12);
                ctx.lineTo(rx, h);
                ctx.stroke();
                ctx.restore();
                if (drawLabel) {
                    ctx.save();
                    ctx.font = "9px sans-serif";
                    ctx.fillStyle = Tone.css(Appearance.Palette, Qt.rgba(1, 1, 1, 0.45));
                    var tw = ctx.measureText(label).width;
                    var tx = Math.min(Math.max(rx + 3, 0), w - tw);
                    ctx.fillText(label, tx, 9);
                    ctx.restore();
                }
                ctx.save();
                ctx.beginPath();
                ctx.arc(rx, h, 2, 0, Math.PI * 2);
                var acR = Math.round(accentColor.r * 255);
                var acG = Math.round(accentColor.g * 255);
                var acB = Math.round(accentColor.b * 255);
                ctx.fillStyle = "rgba(" + acR + "," + acG + "," + acB + ",0.85)";
                ctx.fill();
                ctx.restore();
            }
            var resets = chart.resetTimestamps;
            var resetLabel = chart.chartWindow.indexOf("weekly") >= 0 ? chart.shell.i18nc("chart reset window", "week reset") : chart.shell.i18nc("chart reset window", "5h reset");
            var labelDrawn = false;
            for (var ri = resets.length - 1; ri >= 0; ri--) {
                var rt = resets[ri];
                if (rt > minT && rt < maxT) {
                    drawResetLine(rt, resetLabel, !labelDrawn);
                    labelDrawn = true;
                }
            }

            // hover scrub: vertical guide + highlighted point(s)
            if (scrubIndex >= 0) {
                function drawScrubDot(pt, color) {
                    var hx = ((pt.t - minT) / tRange) * w;
                    var hy = h - (pt.v / 100) * h * 0.88 - h * 0.04;
                    var cR = Math.round(color.r * 255);
                    var cG = Math.round(color.g * 255);
                    var cB = Math.round(color.b * 255);
                    ctx.beginPath();
                    ctx.arc(hx, hy, 5, 0, Math.PI * 2);
                    ctx.fillStyle = "rgba(" + cR + "," + cG + "," + cB + ",1.0)";
                    ctx.fill();
                    ctx.beginPath();
                    ctx.arc(hx, hy, 2, 0, Math.PI * 2);
                    ctx.fillStyle = Tone.css(Appearance.Palette, "#ffffff");
                    ctx.fill();
                }

                var scrubT = chartCanvas.scrubTimestamp;
                if (scrubT > 0) {
                    var hx = ((scrubT - minT) / tRange) * w;
                    ctx.save();
                    ctx.setLineDash([2, 3]);
                    var acR = Math.round(accentColor.r * 255);
                    var acG = Math.round(accentColor.g * 255);
                    var acB = Math.round(accentColor.b * 255);
                    ctx.strokeStyle = chartCanvas.isBoth ? Tone.css(Appearance.Palette, Qt.rgba(1, 1, 1, 0.4)) : "rgba(" + acR + "," + acG + "," + acB + ",0.5)";
                    ctx.lineWidth = 1;
                    ctx.beginPath();
                    ctx.moveTo(hx, 0);
                    ctx.lineTo(hx, h);
                    ctx.stroke();
                    ctx.restore();

                    if (chartCanvas.isBoth) {
                        if (chartCanvas.scrubGeminiPt)
                            drawScrubDot(chartCanvas.scrubGeminiPt, chart.googleBlue);
                        if (chartCanvas.scrubRestPt)
                            drawScrubDot(chartCanvas.scrubRestPt, chart.googleGreen);
                    } else if (chartCanvas.history && scrubIndex < chartCanvas.history.length) {
                        drawScrubDot(chartCanvas.history[scrubIndex], accentColor);
                    }
                }
            }
        }

        MouseArea {
            id: scrubArea
            objectName: "usageChartScrub"
            anchors.fill: parent
            hoverEnabled: true
            onPositionChanged: function (mouse) {
                var now_ms = chart.referenceTime;
                var maxT = now_ms - chart.chartTimeOffset;
                var tRange = chart.windowSize;
                var minT = maxT - tRange;
                var mouseT = minT + (mouse.x / chartCanvas.width) * tRange;

                if (chartCanvas.isBoth) {
                    var gPts = chartCanvas.geminiHistory;
                    var rPts = chartCanvas.restHistory;
                    if ((!gPts || gPts.length === 0) && (!rPts || rPts.length === 0)) {
                        chartCanvas.scrubIndex = -1;
                        return;
                    }
                    function findClosest(pts) {
                        if (!pts || pts.length === 0)
                            return null;
                        var b = 0, bd = Math.abs(pts[0].t - mouseT);
                        for (var i = 1; i < pts.length; i++) {
                            var d = Math.abs(pts[i].t - mouseT);
                            if (d < bd) {
                                bd = d;
                                b = i;
                            }
                        }
                        return pts[b];
                    }
                    chartCanvas.scrubGeminiPt = findClosest(gPts);
                    chartCanvas.scrubRestPt = findClosest(rPts);
                    chartCanvas.scrubTimestamp = chartCanvas.scrubGeminiPt ? chartCanvas.scrubGeminiPt.t : (chartCanvas.scrubRestPt ? chartCanvas.scrubRestPt.t : 0);
                    chartCanvas.scrubIndex = 0;
                } else {
                    var pts = chartCanvas.history;
                    if (!pts || pts.length < 1) {
                        chartCanvas.scrubIndex = -1;
                        return;
                    }
                    var best = 0, bestDist = Math.abs(pts[0].t - mouseT);
                    for (var i = 1; i < pts.length; i++) {
                        var d = Math.abs(pts[i].t - mouseT);
                        if (d < bestDist) {
                            bestDist = d;
                            best = i;
                        }
                    }
                    chartCanvas.scrubIndex = best;
                    chartCanvas.scrubTimestamp = pts[best].t;
                }
            }
            onExited: {
                chartCanvas.scrubIndex = -1;
                chartCanvas.scrubGeminiPt = null;
                chartCanvas.scrubRestPt = null;
                chartCanvas.scrubTimestamp = 0;
            }

            Rectangle {
                id: scrubTooltip
                objectName: "usageChartTooltip"
                visible: chartCanvas.scrubIndex >= 0
                color: Tone.c(Appearance.Palette, Qt.rgba(0, 0, 0, 0.72))
                border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.10))
                border.width: 1
                radius: 6
                width: (chartCanvas.isBoth ? bothTooltipRow.implicitWidth : tooltipRow.implicitWidth) + 14
                height: (chartCanvas.isBoth ? bothTooltipRow.implicitHeight : tooltipRow.implicitHeight) + 8

                property real dotX: {
                    var now_ms = chart.referenceTime;
                    var maxT = now_ms - chart.chartTimeOffset;
                    var tRange = chart.windowSize;
                    var minT = maxT - tRange;
                    var t = chartCanvas.scrubTimestamp;
                    if (!t)
                        return 0;
                    return ((t - minT) / tRange) * chartCanvas.width;
                }
                property real dotY: {
                    if (chartCanvas.isBoth) {
                        var gPt = chartCanvas.scrubGeminiPt;
                        var rPt = chartCanvas.scrubRestPt;
                        var v = gPt ? gPt.v : (rPt ? rPt.v : 50);
                        var h = chartCanvas.height;
                        return h - (v / 100) * h * 0.88 - h * 0.04;
                    }
                    var pts = chartCanvas.history;
                    if (chartCanvas.scrubIndex < 0 || !pts || chartCanvas.scrubIndex >= pts.length)
                        return 0;
                    var v = pts[chartCanvas.scrubIndex].v;
                    var h = chartCanvas.height;
                    return h - (v / 100) * h * 0.88 - h * 0.04;
                }

                x: Math.max(0, Math.min(dotX - width / 2, chartCanvas.width - width - 4))
                y: Math.max(4, dotY - height - 8)

                Row {
                    id: tooltipRow
                    visible: !chartCanvas.isBoth
                    anchors.centerIn: parent
                    spacing: 0

                    Text {
                        objectName: "chartHoverValue"
                        text: {
                            var pts = chartCanvas.history;
                            if (chartCanvas.scrubIndex < 0 || !pts || chartCanvas.scrubIndex >= pts.length)
                                return "";
                            var pt = pts[chartCanvas.scrubIndex];
                            if (chart.isTokens)
                                return chart.shell.i18n("%1 tokens", Number(pt.raw || 0).toLocaleString(Qt.locale(), "f", 0));
                            if (chart.isCost) {
                                var value = (pt.raw !== undefined ? pt.raw : 0).toFixed(4);
                                if (chart.currency === "CNY")
                                    return "¥" + value;
                                if (chart.currency === "USD" || chart.historyKey.split(/[@#]/)[0] === "mv")
                                    return "$" + value;
                                return value + (chart.currency !== "" ? " " + chart.currency : "");
                            }
                            return Math.round(pt.v) + "%";
                        }
                        font.pixelSize: 11
                        font.bold: true
                        color: chart.accent
                    }
                    Text {
                        text: {
                            var pts = chartCanvas.history;
                            if (chartCanvas.scrubIndex < 0 || !pts || chartCanvas.scrubIndex >= pts.length)
                                return "";
                            var point = pts[chartCanvas.scrubIndex];
                            if (chart.isTokens)
                                return "  ·  " + chart.shell.formatDay(point.t) + (point.endDate ? " – " + chart.shell.formatDay(OpenCodeUsage.dayTimestamp(point.endDate)) : "");
                            return "  ·  " + chart.shell.formatDayTime(point.t);
                        }
                        font.pixelSize: 11
                        color: Tone.c(Appearance.Palette, "#f8fafc")
                        opacity: 0.75
                    }
                }

                RowLayout {
                    id: bothTooltipRow
                    visible: chartCanvas.isBoth
                    anchors.centerIn: parent
                    spacing: 4

                    RowLayout {
                        spacing: 3
                        visible: chartCanvas.scrubGeminiPt !== null
                        Rectangle {
                            implicitWidth: 5
                            implicitHeight: 5
                            Layout.preferredWidth: 5
                            Layout.preferredHeight: 5
                            radius: 2.5
                            color: chart.googleBlue
                            Layout.alignment: Qt.AlignVCenter
                        }
                        Text {
                            // xgettext:no-javascript-format — "%1%" is a Qt placeholder and a percent sign, not printf
                            text: chartCanvas.scrubGeminiPt ? chart.shell.i18n("Gemini: %1%", Math.round(chartCanvas.scrubGeminiPt.v)) : ""
                            font.pixelSize: 11
                            font.bold: true
                            color: chart.googleBlue
                        }
                    }

                    Text {
                        visible: chartCanvas.scrubGeminiPt !== null && chartCanvas.scrubRestPt !== null
                        text: "·"
                        font.pixelSize: 11
                        color: Tone.c(Appearance.Palette, "#f8fafc")
                        opacity: 0.4
                    }

                    RowLayout {
                        spacing: 3
                        visible: chartCanvas.scrubRestPt !== null
                        Rectangle {
                            implicitWidth: 5
                            implicitHeight: 5
                            Layout.preferredWidth: 5
                            Layout.preferredHeight: 5
                            radius: 2.5
                            color: chart.googleGreen
                            Layout.alignment: Qt.AlignVCenter
                        }
                        Text {
                            // xgettext:no-javascript-format — "%1%" is a Qt placeholder and a percent sign, not printf
                            text: chartCanvas.scrubRestPt ? chart.shell.i18n("Rest: %1%", Math.round(chartCanvas.scrubRestPt.v)) : ""
                            font.pixelSize: 11
                            font.bold: true
                            color: chart.googleGreen
                        }
                    }

                    Text {
                        text: chartCanvas.scrubTimestamp > 0 ? "  ·  " + chart.shell.formatDayTime(chartCanvas.scrubTimestamp) : ""
                        font.pixelSize: 11
                        color: Tone.c(Appearance.Palette, "#f8fafc")
                        opacity: 0.75
                    }
                }
            }
        }
    }

    // ── X-axis endpoint labels ──
    RowLayout {
        id: xAxisRow
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 36
        anchors.rightMargin: 8
        anchors.bottomMargin: 5
        spacing: 0

        readonly property int tickCount: 2
        readonly property bool hourlyWindow: chart.windowSize <= 24 * 3600000

        function formatLabel(timestamp) {
            var d = new Date(timestamp);
            if (chart.isTokens)
                return chart.shell.formatDay(d);
            if (hourlyWindow)
                return chart.shell.formatTime(d);
            return chart.shell.formatDay(d) + "\n" + chart.shell.formatTime(d);
        }

        function endpointCrowded(endpointFrac) {
            var resets = chart.resetTimestamps;
            var now_ms = chart.referenceTime;
            var minT = (now_ms - chart.chartTimeOffset) - chart.windowSize;
            for (var i = 0; i < resets.length; i++) {
                var f = (resets[i] - minT) / chart.windowSize;
                if (f > 0 && f < 1 && Math.abs(f - endpointFrac) < 0.1)
                    return true;
            }
            return false;
        }

        Repeater {
            model: xAxisRow.tickCount
            delegate: RowLayout {
                readonly property real frac: index / (xAxisRow.tickCount - 1)
                Layout.fillWidth: index > 0
                spacing: 0
                Item {
                    Layout.fillWidth: index > 0
                }
                Text {
                    visible: !xAxisRow.endpointCrowded(parent.frac)
                    text: {
                        var now_ms = chart.referenceTime;
                        var maxT = now_ms - chart.chartTimeOffset;
                        var minT = maxT - chart.windowSize;
                        return xAxisRow.formatLabel(minT + chart.windowSize * parent.frac);
                    }
                    font.pixelSize: 9
                    opacity: 0.40
                    horizontalAlignment: Text.AlignHCenter
                    lineHeight: 0.9
                    color: Tone.c(Appearance.Palette, "#f8fafc")
                }
            }
        }
    }

    // Reset-time labels centered under each reset line
    Item {
        id: resetLabelsRow
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 36
        anchors.rightMargin: 8
        anchors.bottomMargin: 5
        height: xAxisRow.height

        Repeater {
            model: chart.resetTimestamps
            delegate: Text {
                readonly property real frac: {
                    var now_ms = chart.referenceTime;
                    var maxT = now_ms - chart.chartTimeOffset;
                    var minT = maxT - chart.windowSize;
                    return (modelData - minT) / chart.windowSize;
                }
                visible: frac > 0 && frac < 1
                text: chart.chartWindow.indexOf("weekly") >= 0 ? chart.shell.formatDay(modelData) : chart.shell.formatTime(modelData)
                font.pixelSize: 9
                color: chart.accent
                opacity: 0.85
                x: Math.max(0, Math.min(frac * resetLabelsRow.width - implicitWidth / 2, resetLabelsRow.width - implicitWidth))
            }
        }
    }
}
