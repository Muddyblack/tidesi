import QtQuick
import "../theme" as Appearance
import "Theme.js" as Theme
import "../js/Tone.js" as Tone

// A grip that lets the user drag one row of a fixed-height list up or down.
// The row follows the pointer through `offset` (bind a Translate to it); when
// it is let go, `moved` reports where it landed and the page reorders its
// model. Rows are laid out by their parent, so nothing here moves them for good.
//
//     StudioReorderHandle { id: grip; index: row.index; count: n; rowHeight: 56
//                           onMoved: (from, to) => ... }
//     transform: Translate { y: grip.offset }
Item {
    id: handle

    // The row's place in the list, the list's length and each row's height.
    property int index: 0
    property int count: 1
    property real rowHeight: 56
    // How far the row has been pulled from its place, and whether it is held.
    readonly property real offset: area.lifted ? area.dragOffset : 0
    readonly property bool dragging: area.lifted
    // The place the row would take if let go now.
    readonly property int target: Math.max(0, Math.min(handle.count - 1, handle.index + Math.round(area.dragOffset / handle.rowHeight)))

    signal moved(int from, int to)

    implicitWidth: 22
    implicitHeight: 30

    // Six dots, the usual grip.
    Grid {
        anchors.centerIn: parent
        columns: 2
        spacing: 3
        Repeater {
            model: 6
            Rectangle {
                width: 3
                height: 3
                radius: 1.5
                color: Tone.c(Appearance.Palette, area.containsMouse || handle.dragging ? Theme.text : Theme.dim)
            }
        }
    }

    MouseArea {
        id: area

        property real pressY: 0
        property real dragOffset: 0
        // A press that has not travelled yet is a click, not a drag.
        readonly property bool lifted: pressed && Math.abs(dragOffset) > 3

        anchors.fill: parent
        hoverEnabled: true
        cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
        // Scene coordinates, so the row moving under the pointer does not skew them.
        function sceneY(mouse) {
            return mapToItem(null, mouse.x, mouse.y).y;
        }
        onPressed: mouse => {
            pressY = sceneY(mouse);
            dragOffset = 0;
        }
        onPositionChanged: mouse => {
            if (!pressed)
                return;
            var lowest = -handle.index * handle.rowHeight;
            var highest = (handle.count - 1 - handle.index) * handle.rowHeight;
            dragOffset = Math.max(lowest, Math.min(highest, sceneY(mouse) - pressY));
        }
        onReleased: {
            var to = handle.target;
            dragOffset = 0;
            if (to !== handle.index)
                handle.moved(handle.index, to);
        }
        onCanceled: dragOffset = 0
    }
}
