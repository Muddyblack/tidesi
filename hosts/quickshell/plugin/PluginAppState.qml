import QtQuick
import "./ui" as Upstream

Upstream.AppState {
    id: root

    property bool popupOpen: false
    signal pluginPopupToggled

    property Component pillSource: Component {
        Upstream.PanelPill {
            iconSource: root.pillIcon
            active: root.popupOpen
            slots: root.pillSlots
            stale: root.pillStale
            hasError: root.pillHasError
            groups: root.panelGroups
            onClicked: {
                root.pluginPopupToggled();
            }
        }
    }
}
