.pragma library

function finite(value) {
    return typeof value === "number" && isFinite(value);
}

function series(usage, nowSeconds) {
    var threshold = usage && finite(usage.includedUSD) && usage.includedUSD >= 0 ? usage.includedUSD : null;
    if (!usage || usage.available !== true || !finite(usage.periodStart) || !finite(usage.periodEnd) || usage.periodEnd <= usage.periodStart || !Array.isArray(usage.dailyUSD))
        return { available: false, points: null, threshold: threshold };
    var end = Math.min(nowSeconds, usage.periodEnd);
    if (!finite(end) || end < usage.periodStart)
        return { available: false, points: null, threshold: threshold };
    var rows = usage.dailyUSD.slice().filter(function (row) {
        return row && typeof row.date === "string" && finite(row.usd) && row.usd >= 0;
    }).sort(function (a, b) { return a.date.localeCompare(b.date); });
    var points = [{ x: 0, usd: 0 }];
    var total = 0;
    rows.forEach(function (row) {
        var epoch = finite(row.at) ? row.at : Date.parse(row.date + "T00:00:00Z") / 1000;
        if (!isFinite(epoch) || epoch < usage.periodStart || epoch > end)
            return;
        total += row.usd;
        var x = Math.max(0, Math.min(1, (epoch - usage.periodStart) / (usage.periodEnd - usage.periodStart)));
        var last = points[points.length - 1];
        if (last.x === x && !(x === 0 && points.length === 1))
            last.usd = total;
        else
            points.push({ x: x, usd: total });
    });
    var endX = (end - usage.periodStart) / (usage.periodEnd - usage.periodStart);
    if (points[points.length - 1].x < endX)
        points.push({ x: endX, usd: total });
    return { available: true, points: points, threshold: threshold };
}

function split(points, threshold) {
    var included = [], overage = [];
    if (!Array.isArray(points) || !points.length)
        return { included: included, overage: overage };
    if (threshold === null || !finite(threshold))
        return { included: points.slice(), overage: [] };
    for (var i = 0; i < points.length; i++) {
        var point = points[i];
        if (point.usd <= threshold) {
            if (!overage.length)
                included.push(point);
            else
                overage.push(point);
        } else if (i === 0) {
            overage.push(point);
        } else {
            var prev = points[i - 1];
            if (prev.usd <= threshold) {
                var ratio = (threshold - prev.usd) / (point.usd - prev.usd);
                var crossing = { x: prev.x + (point.x - prev.x) * ratio, usd: threshold };
                if (crossing.x !== prev.x || crossing.usd !== prev.usd)
                    included.push(crossing);
                overage.push(crossing);
            }
            overage.push(point);
        }
    }
    return { included: included, overage: overage };
}

function maximum(points, threshold) {
    var max = Math.max(0, threshold === null ? 0 : threshold);
    if (Array.isArray(points))
        points.forEach(function (point) { max = Math.max(max, point.usd); });
    return max > 0 ? max * 1.15 : 1;
}
