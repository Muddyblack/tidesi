import QtQuick
import "theme" as Appearance
import "studio"
import "studio/Theme.js" as Theme
import "js/Tone.js" as Tone

// One provider's page: how it is read and whether that works, then its key and
// extras. Opened from the provider list or right after adding one.
Column {
    id: detail

    // An entry from shell.allProviders.
    property var provider: null
    property var shell

    signal back

    readonly property string providerId: provider ? provider.id : ""
    // What the backend last reported for it; null until the first refresh.
    readonly property var snapshot: detail.shell.providerById(detail.providerId)

    width: parent ? parent.width : implicitWidth
    spacing: 14

    Item {
        width: parent.width
        height: 96

        StudioIconButton {
            anchors.right: parent.right
            icon: "refresh"
            enabled: !detail.shell.busy
            onClicked: detail.shell.refresh()
        }
        Column {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 6
            ProviderBadge {
                anchors.horizontalCenter: parent.horizontalCenter
                provider: detail.provider
                shell: detail.shell
                size: 52
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: detail.provider ? detail.provider.label : ""
                color: Tone.c(Appearance.Palette, Theme.text)
                font.pixelSize: 13
                font.weight: Font.DemiBold
            }
        }
    }

    // What is wrong, in the words the provider's own tab uses.
    StudioNote {
        visible: detail.snapshot !== null && detail.snapshot.ok !== true && String(detail.snapshot.error || "") !== ""
        text: detail.snapshot ? String(detail.snapshot.error || "") : ""
    }

    SourceChoice {
        shell: detail.shell
        snapshot: detail.snapshot
        providerId: detail.providerId
    }

    ProviderAccounts {
        provider: detail.provider
        shell: detail.shell
    }

    ProviderOptions {
        provider: detail.provider
        shell: detail.shell
    }

    StudioButton {
        text: detail.shell.i18n("Remove provider")
        onClicked: {
            detail.shell.setSetting("providers", detail.providerId, false);
            detail.shell.refresh();
            detail.back();
        }
    }
}
