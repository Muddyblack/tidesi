.pragma library

// The settings studio palette, after the Glassy System Monitor and Audio
// Visualizer studios: quiet translucent cards on the popup's own glass, one
// bright brand gradient for the things that are on. Text and hairlines are
// white at fixed opacities so the page reads on any popup tint.
var text = "#f1f5f9";
var muted = "#a3adbb";
var dim = "#7b8594";
var sectionTitle = "#b4bdca";
var line = "#14ffffff";
var line2 = "#26ffffff";
var card = "#0affffff";
var sunk = "#33000000";
// A row held above the others while it is dragged: near opaque so what is under it does not show through.
var lifted = "#f2222a33";
var hover = "#14ffffff";
var navActive = "#1fffffff";
var brand = "#4f9dde";
var brandTop = "#7cc0f5";
var brandBottom = "#3b7fc4";
var brandInk = "#06121f";
var switchOff = "#3a4048";
var knob = "#f4f6f8";
var ok = "#34d399";
var warn = "#f5a623";
var bad = "#f87171";
var noteBg = "#1a4f9dde";
var noteBorder = "#334f9dde";
var noteText = "#c9ddf0";

// A colour by the tone name the shared logic uses (ProviderSources.stateTone).
function tone(name) {
    return name === "ok" ? ok : name === "bad" ? bad : name === "warn" ? warn : name === "dim" ? dim : muted;
}
