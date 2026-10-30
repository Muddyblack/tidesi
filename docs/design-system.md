# Existing QML chart and card tokens

This note records the existing shared-popup styling used by the Zed usage chart; it does not establish a new visual design.

- **Surface:** `shell.cardColor` for a translucent chart card. The value is derived from popup settings and tone-adjusted in `ui/AppState.qml`.
- **Accent:** `shell.activeAccent` for the cumulative usage line.
- **Over-allowance state:** `shell.dangerColor`, with wording kept informational (“Above included allowance”), never a billing assertion.
- **Neutral colors:** `Tone.c(Appearance.Palette, ...)` for text, baseline, and borders so light and dark popups remain readable.
- **Typography:** existing popup sizes: 12px key amount, 10–11px labels, 9px footnotes.
- **Spacing and shape:** compact chart card uses 10px internal padding, 6px component spacing, and 6px corner radius, matching surrounding popup cards.
- **Rendering:** native Qt Quick `Canvas`, no external chart dependency; a zero-based USD axis includes allowance and usage with headroom.
- **States:** known zero usage draws a flat line; unavailable usage shows a dash without a curve; partial pricing shows a warning; unknown allowance has no threshold; zero allowance colors all positive usage as above allowance. Below/above-threshold paths meet at the interpolated crossing. Billing-period dates and a distinct dashed allowance line explain the scale without relying on color alone.

`ZedUsageChart.qml` receives the backend `details.agentUsage` object unchanged. Its pure series/threshold geometry lives in `ui/js/ZedUsage.js` and is covered by `tests/zed-usage.test.js`. It renders usage only when authoritative account-reported USD data is present; local Zed thread estimates are not treated as spend because the internal pricing model is unknown.

For Free's zero allowance, the baseline itself becomes a distinct accent-colored dashed threshold labeled “Included $0”; a second solid baseline is not drawn over it. Its position remains mathematically zero.

When the account API provides an allowance but no billed usage (the current Zed response), the plot still shows the allowance threshold and axes, with no usage line; the text labels billed usage unavailable. This keeps the graph frame visible without suggesting zero consumption.
