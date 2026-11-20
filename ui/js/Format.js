// Formatting shared by both frontends.
//
// The Plasma popup and the Quickshell panel render different widgets, but a
// countdown has to read identically in both — so the arithmetic lives here
// rather than being reimplemented per shell.

// "2d 4h 13m" until `targetMs`, "" when there is no target, "resetting..." once
// the deadline has passed. `nowMs` is passed in so callers can drive it from
// their own clock tick instead of re-reading the system clock per binding.
function countdown(targetMs, nowMs) {
    if (!targetMs || targetMs <= 0)
        return "";

    var diffMs = targetMs - nowMs;
    if (diffMs <= 0)
        return "resetting...";

    var totalMins = Math.floor(diffMs / 60000);
    var d = Math.floor(totalMins / 1440);
    var h = Math.floor((totalMins % 1440) / 60);
    var m = totalMins % 60;
    var parts = [];
    if (d > 0)
        parts.push(d + "d");

    if (h > 0 || d > 0)
        parts.push(h + "h");

    parts.push(m + "m");
    return parts.join(" ");
}

// Same, for the epoch-seconds timestamps the provider contract uses.
function countdownFromEpoch(resetAt, nowMs) {
    return countdown(resetAt ? resetAt * 1000 : 0, nowMs);
}

// Dates in the user's language. The chart, the rows and the reset lines used
// fixed "MMM d" and "hh:mm" patterns, which Qt writes with English month names
// and a 24-hour clock whatever the language. These turn a locale's own short
// formats (Locale.dateFormat / timeFormat, ShortFormat) into the patterns the
// views need, for Date.toLocaleString(locale, pattern):

// "MMM d" or "d MMM": the order the locale's short date puts day and month in.
function dayPattern(shortDateFormat) {
    var f = String(shortDateFormat || "");
    var d = f.indexOf("d");
    var m = f.indexOf("M");
    return d >= 0 && m >= 0 && d < m ? "d MMM" : "MMM d";
}

// The locale's short time without seconds or a time zone: "HH:mm", "h:mm AP".
function timePattern(shortTimeFormat) {
    var f = String(shortTimeFormat || "")
        .replace(/[:.]s{1,2}(\.z+)?/g, "")
        .replace(/\s*t+/g, "")
        .trim();
    return f === "" ? "HH:mm" : f;
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        countdown: countdown,
        countdownFromEpoch: countdownFromEpoch,
        dayPattern: dayPattern,
        timePattern: timePattern
    };
}
