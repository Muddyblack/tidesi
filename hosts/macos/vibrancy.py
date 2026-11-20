"""The popup's real blur on macOS: an NSVisualEffectView behind the Qt window.

A Qt window cannot see what is behind it, so the shared QML glass
(ui/PopupBackground.qml) can only paint a blur-like gradient. A menu bar
item's popover on macOS blurs the desktop for real, through the system's
NSVisualEffectView, and follows the light or dark appearance by itself. This
puts one under the popup's Qt view, in the same window, so the QML draws on
top of the system blur; the QML is told (Main.qml's nativeBlur) to thin its
own glass out so the blur reads through.

Everything runs on the main thread, in the NSApplication Qt drives.
"""

import objc
from AppKit import (
    NSAppearance,
    NSAppearanceNameAqua,
    NSAppearanceNameDarkAqua,
    NSColor,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialPopover,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowBelow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
)

# Matches ui/PopupBackground.qml's radius, so the blur and the drawn edge agree.
CORNER_RADIUS = 12


def apply(window):
    """Puts the system blur behind `window` (a QWindow that has been shown
    once). True when it is there, now or from an earlier call."""
    view = objc.objc_object(c_void_p=int(window.winId()))
    ns_window = view.window()
    container = view.superview()
    if ns_window is None or container is None:
        return False
    for sibling in container.subviews():
        if isinstance(sibling, NSVisualEffectView):
            return True
    effect = NSVisualEffectView.alloc().initWithFrame_(view.frame())
    effect.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    effect.setMaterial_(NSVisualEffectMaterialPopover)
    effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
    # Active even while the app is not frontmost: an accessory app's popup is
    # often shown before the activation lands.
    effect.setState_(NSVisualEffectStateActive)
    effect.setWantsLayer_(True)
    effect.layer().setCornerRadius_(CORNER_RADIUS)
    effect.layer().setMasksToBounds_(True)
    container.addSubview_positioned_relativeTo_(effect, NSWindowBelow, view)
    ns_window.setOpaque_(False)
    ns_window.setBackgroundColor_(NSColor.clearColor())
    ns_window.setHasShadow_(True)
    ns_window.invalidateShadow()
    return True


def set_light(window, light):
    """The blur's own light or dark, matched to the popup's Theme setting
    rather than the system's (they differ when the Theme is not Auto)."""
    view = objc.objc_object(c_void_p=int(window.winId()))
    ns_window = view.window()
    if ns_window is not None:
        ns_window.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameAqua if light else NSAppearanceNameDarkAqua))


def join_all_spaces(window):
    """Let `window` (a QWindow that has been shown once) open on whichever Space
    is current, a full-screen app's included, as a menu bar item's popover does.

    Without this the popup belongs to the desktop it was first shown on:
    clicking the menu bar item over a full-screen app shows nothing there, or
    switches away from the app to show it. True when the behaviour was set."""
    view = objc.objc_object(c_void_p=int(window.winId()))
    ns_window = view.window()
    if ns_window is None:
        return False
    wanted = NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
    if ns_window.collectionBehavior() & wanted != wanted:
        ns_window.setCollectionBehavior_(ns_window.collectionBehavior() | wanted)
    return True
