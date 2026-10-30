import QtQuick
import QtQuick.Layouts
import "theme" as Appearance
import "js/Tone.js" as Tone
import "js/ZedUsage.js" as ZedUsage

ColumnLayout {
    id: chart
    property var shell
    property var usage: ({})
    property real nowSeconds: Date.now() / 1000
    readonly property var chartData: ZedUsage.series(usage, nowSeconds)
    readonly property real total: usage && typeof usage.usedUSD === "number" && isFinite(usage.usedUSD) ? usage.usedUSD : (chartData.points && chartData.points.length ? chartData.points[chartData.points.length - 1].usd : 0)
    readonly property color textColor: Tone.c(Appearance.Palette, "#f8fafc")
    visible: true
    spacing: 6

    Timer {
        interval: 60000
        running: chart.visible
        repeat: true
        onTriggered: chart.nowSeconds = Date.now() / 1000
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: body.implicitHeight + 20
        radius: 6
        color: shell.cardColor
        border.width: 1
        border.color: Tone.c(Appearance.Palette, Qt.rgba(1, 1, 1, 0.10))

        ColumnLayout {
            id: body
            anchors.fill: parent
            anchors.margins: 10
            spacing: 6

            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: shell.i18n("Account-reported usage")
                    color: chart.textColor
                    opacity: 0.65
                    font.pixelSize: 10
                }
                Text {
                    text: chart.chartData.available ? "$" + Number(chart.total).toFixed(2) : "—"
                    color: chart.textColor
                    font.pixelSize: 12
                    font.bold: true
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: chart.chartData.threshold === null ? shell.i18n("Included allowance unavailable") : shell.i18n("Included allowance · $%1", Number(chart.chartData.threshold).toFixed(2))
                    color: chart.textColor
                    opacity: 0.62
                    font.pixelSize: 10
                }
                Text {
                    visible: chart.chartData.available && chart.chartData.threshold !== null && chart.total > chart.chartData.threshold
                    text: shell.i18n("Above included allowance")
                    color: shell.dangerColor
                    font.pixelSize: 9
                }
            }

            Item {
                id: plotArea
                Layout.fillWidth: true
                Layout.preferredHeight: 74
                visible: chart.chartData.threshold !== null || chart.chartData.points !== null

                Canvas {
                    id: plot
                    anchors.fill: parent
                    property var points: chart.chartData.points || []
                    property real threshold: chart.chartData.threshold === null ? -1 : chart.chartData.threshold
                    property real maxValue: ZedUsage.maximum(points, chart.chartData.threshold)
                    property var split: ZedUsage.split(points, chart.chartData.threshold)
                    property color usageColor: shell.activeAccent
                    property color dangerColor: shell.dangerColor
                    readonly property real plotLeft: 38
                    readonly property real plotRight: width - 2
                    readonly property real plotTop: 5
                    readonly property real plotBottom: height - 5
                    function mapX(value) {
                        return plotLeft + value * (plotRight - plotLeft);
                    }
                    function mapY(value) {
                        return plotBottom - value / maxValue * (plotBottom - plotTop);
                    }
                    function stroke(ctx, series, color) {
                        if (!series || series.length < 2)
                            return;
                        ctx.beginPath();
                        ctx.moveTo(mapX(series[0].x), mapY(series[0].usd));
                        for (var i = 1; i < series.length; i++)
                            ctx.lineTo(mapX(series[i].x), mapY(series[i].usd));
                        ctx.strokeStyle = color;
                        ctx.lineWidth = 2;
                        ctx.lineJoin = "round";
                        ctx.lineCap = "round";
                        ctx.stroke();
                    }
                    onPaint: {
                        var ctx = getContext("2d");
                        ctx.clearRect(0, 0, width, height);
                        if (!points.length && threshold < 0)
                            return;
                        ctx.fillStyle = Tone.css(Appearance.Palette, Qt.rgba(1, 1, 1, 0.55));
                        ctx.font = "9px sans-serif";
                        ctx.fillText("$" + maxValue.toFixed(2), 0, plotTop + 7);
                        ctx.fillText("$0", 0, plotBottom);
                        if (threshold !== 0) {
                            ctx.beginPath();
                            ctx.strokeStyle = Tone.css(Appearance.Palette, Qt.rgba(1, 1, 1, 0.10));
                            ctx.lineWidth = 1;
                            ctx.moveTo(plotLeft, plotBottom);
                            ctx.lineTo(plotRight, plotBottom);
                            ctx.stroke();
                        }
                        if (threshold >= 0) {
                            ctx.save();
                            ctx.setLineDash([4, 4]);
                            ctx.beginPath();
                            ctx.strokeStyle = threshold === 0 ? Tone.css(Appearance.Palette, usageColor) : Tone.css(Appearance.Palette, Qt.rgba(1, 1, 1, 0.38));
                            ctx.lineWidth = threshold === 0 ? 1.5 : 1;
                            ctx.moveTo(plotLeft, mapY(threshold));
                            ctx.lineTo(plotRight, mapY(threshold));
                            ctx.stroke();
                            if (threshold === 0) {
                                ctx.textAlign = "right";
                                ctx.fillStyle = Tone.css(Appearance.Palette, Qt.rgba(1, 1, 1, 0.7));
                                ctx.fillText(shell.i18n("Included $0"), plotRight, plotBottom - 5);
                            }
                            ctx.restore();
                        }
                        if (!points.length)
                            return;
                        stroke(ctx, split.included, Tone.css(Appearance.Palette, usageColor));
                        stroke(ctx, split.overage, Tone.css(Appearance.Palette, dangerColor));
                        if (points.length === 1) {
                            ctx.beginPath();
                            ctx.arc(mapX(points[0].x), mapY(points[0].usd), 2.5, 0, Math.PI * 2);
                            ctx.fillStyle = Tone.css(Appearance.Palette, usageColor);
                            ctx.fill();
                        }
                    }
                    onPointsChanged: requestPaint()
                    onWidthChanged: requestPaint()
                    onHeightChanged: requestPaint()
                    onThresholdChanged: requestPaint()
                    onMaxValueChanged: requestPaint()
                    onSplitChanged: requestPaint()
                    onUsageColorChanged: requestPaint()
                    onDangerColorChanged: requestPaint()
                }
            }

            Text {
                Layout.fillWidth: true
                visible: chart.chartData.points === null
                text: shell.i18n("Billed usage unavailable from the Zed account API")
                color: chart.textColor
                opacity: 0.62
                font.pixelSize: 10
            }
            Text {
                Layout.fillWidth: true
                visible: chart.chartData.points !== null && usage.costStatus === "partial"
                text: shell.i18n("Partial account-reported usage.")
                color: shell.dangerColor
                wrapMode: Text.WordWrap
                font.pixelSize: 9
            }
            RowLayout {
                Layout.fillWidth: true
                visible: chart.chartData.available
                Text {
                    Layout.fillWidth: true
                    text: usage.periodStart ? Qt.formatDateTime(new Date(usage.periodStart * 1000), "MMM d") : ""
                    color: chart.textColor
                    opacity: 0.55
                    font.pixelSize: 9
                }
                Text {
                    text: usage.periodEnd ? shell.i18n("Resets %1", Qt.formatDateTime(new Date(usage.periodEnd * 1000), "MMM d")) : ""
                    color: chart.textColor
                    opacity: 0.55
                    font.pixelSize: 9
                }
            }
            Text {
                Layout.fillWidth: true
                visible: usage && usage.available !== true
                text: shell.i18n("Local Zed tokens are not priced because its internal billing model is unknown.")
                color: chart.textColor
                opacity: 0.55
                wrapMode: Text.WordWrap
                font.pixelSize: 9
            }
        }
    }
}
