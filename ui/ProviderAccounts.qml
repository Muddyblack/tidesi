import QtQuick
import "studio"
import "studio/Theme.js" as Theme
import "js/Tone.js" as Tone
import "js/Accounts.js" as Accounts
import "theme" as Appearance

// A provider's accounts: the login it already reads, and any others the user
// adds. Each extra account becomes a tab of its own next to the provider's, so
// there is nothing to switch — "Claude" and "Claude · Work" are both just there.
//
// The user names the account; the folder it signs in under is suggested, and
// only the folder is saved — the tool keeps the login in it. Hidden for
// providers that identify an account some other way (an API key).
Column {
    id: card

    // An entry from shell.allProviders.
    property var provider: null
    property var shell

    readonly property string providerId: provider ? provider.id : ""
    readonly property var spec: Accounts.HOMES[providerId] || null
    readonly property var accounts: Accounts.list(card.shell.settings, providerId)
    property string draftName: ""
    property string draftHome: ""
    property string problem: ""
    // Why an account's folder edit was not taken, by account key.
    property var rowProblems: ({})
    // A just-added account to show the sign-in step for, whatever its status.
    property string justAdded: ""
    readonly property string folderTakenText: card.shell.i18n("Another login already reads that folder. Pick a folder of its own.")

    // "Signed in as …" for a row whose backend result names its login, plus a
    // note when it is the same account another tab already reads.
    function loginText(snapshot) {
        var login = snapshot && snapshot.login ? snapshot.login : null;
        if (!login || (login.label || "") === "")
            return "";
        var text = card.shell.i18n("Signed in as %1", login.label);
        if ((login.sameAs || "") !== "") {
            var other = card.shell.providerById(login.sameAs);
            text += " · " + card.shell.i18n("same account as %1", other ? other.label : login.sameAs);
        }
        return text;
    }

    visible: spec !== null
    width: parent ? parent.width : implicitWidth
    height: visible ? implicitHeight : 0
    spacing: 14

    function addAccount() {
        var result = Accounts.add(card.shell.settings, card.providerId, card.draftName, card.draftHome);
        if (result.error) {
            card.problem = result.error === "limit" ? card.shell.i18n("That is the most accounts one provider can have.") : result.error === "folder" ? card.folderTakenText : card.shell.i18n("Give the account a name.");
            return;
        }
        card.problem = "";
        card.shell.setSetting2("accounts", result.settings.accounts);
        card.justAdded = result.id;
        card.draftName = "";
        card.draftHome = "";
        card.shell.refresh();
    }

    function removeAccount(key) {
        card.shell.setSetting2("accounts", Accounts.remove(card.shell.settings, card.providerId, key).accounts || {});
        card.shell.refresh();
    }

    function changeAccount(key, changes) {
        var problems = Object.assign({}, card.rowProblems);
        delete problems[key];
        if (changes.home !== undefined && Accounts.folderTaken(card.shell.settings, card.providerId, changes.home, key)) {
            problems[key] = card.folderTakenText;
            card.rowProblems = problems;
            return;
        }
        card.rowProblems = problems;
        card.shell.setSetting2("accounts", Accounts.update(card.shell.settings, card.providerId, key, changes).accounts);
        card.shell.refresh();
    }

    StudioCard {
        title: card.shell.i18n("Accounts")
        caption: card.shell.i18n("Sign in to more than one account. Each one gets its own tab next to this provider's.")

        StudioRow {
            readonly property string signedIn: card.loginText(card.shell.providerById(card.providerId))
            label: card.shell.i18n("Main login")
            desc: signedIn !== "" ? signedIn : card.shell.i18n("The account this provider already uses. Nothing to set up.")
        }

        Repeater {
            model: card.accounts

            StudioRow {
                id: accountRow

                required property var modelData
                // What the backend last reported for this account; null until a refresh.
                readonly property var snapshot: card.shell.providerById(modelData.id)
                readonly property bool signedIn: snapshot !== null && snapshot.ok === true

                label: modelData.label
                desc: {
                    if (!signedIn)
                        return snapshot === null ? card.shell.i18n("Waiting for data") : String(snapshot.error || "") !== "" ? String(snapshot.error) : card.shell.i18n("Not signed in yet");
                    var who = card.loginText(snapshot);
                    return who !== "" ? who : card.shell.i18n("Working");
                }
                full: true

                Column {
                    width: parent.width
                    spacing: 8

                    Row {
                        spacing: 8
                        StudioField {
                            width: Math.floor((parent.parent.width - 16 - 30) * 0.4)
                            value: accountRow.modelData.label
                            placeholder: card.shell.i18n("Name")
                            onCommitted: v => card.changeAccount(accountRow.modelData.key, {
                                    label: v
                                })
                        }
                        StudioField {
                            width: Math.floor((parent.parent.width - 16 - 30) * 0.6)
                            mono: true
                            value: accountRow.modelData.home
                            placeholder: card.shell.i18n("Folder")
                            onCommitted: v => card.changeAccount(accountRow.modelData.key, {
                                    home: v
                                })
                        }
                        StudioIconButton {
                            icon: "trash"
                            danger: true
                            tip: card.shell.i18n("Remove account")
                            onClicked: card.removeAccount(accountRow.modelData.key)
                        }
                    }

                    Text {
                        readonly property string message: card.rowProblems[accountRow.modelData.key] || ""
                        visible: message !== ""
                        width: parent.width
                        text: message
                        wrapMode: Text.WordWrap
                        color: Tone.ink(Appearance.Palette, Theme.tone("bad"))
                        font.pixelSize: 10
                    }

                    // The one step the widget cannot do for the user: signing in.
                    SignInStep {
                        visible: !accountRow.signedIn || card.justAdded === accountRow.modelData.id
                        width: parent.width
                        command: Accounts.loginCommand(card.providerId, accountRow.modelData.home, Qt.platform.os)
                        shell: card.shell
                    }
                }
            }
        }

        StudioRow {
            label: card.shell.i18n("Add an account")
            desc: card.shell.i18n("Name it, for example Work. The folder is optional.")
            full: true

            Column {
                width: parent.width
                spacing: 8

                Row {
                    spacing: 8
                    StudioField {
                        width: Math.round((parent.parent.width - 8) * 0.4)
                        live: true
                        value: card.draftName
                        placeholder: card.shell.i18n("Name")
                        onCommitted: v => {
                            card.draftName = v;
                            card.problem = "";
                        }
                    }
                    StudioField {
                        width: Math.round((parent.parent.width - 8) * 0.6)
                        live: true
                        mono: true
                        value: card.draftHome
                        placeholder: Accounts.slug(card.draftName) !== "" ? Accounts.defaultHome(card.providerId, card.draftName) : card.spec ? card.spec.dir : ""
                        onCommitted: v => card.draftHome = v
                    }
                }
                Row {
                    spacing: 10
                    StudioButton {
                        primary: true
                        text: "+  " + card.shell.i18n("Add account")
                        onClicked: card.addAccount()
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: card.problem !== ""
                        text: card.problem
                        color: Tone.ink(Appearance.Palette, Theme.tone("bad"))
                        font.pixelSize: 10
                    }
                }
            }
        }
    }
}
