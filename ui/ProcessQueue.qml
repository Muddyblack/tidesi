import QtQuick

QtObject {
    id: root

    required property var runner
    property var pending: []
    property var current: null
    readonly property bool busy: current !== null || pending.length > 0
    signal idle

    function run(argv, callback) {
        root.pending = root.pending.concat([
            {
                argv: argv,
                callback: callback
            }
        ]);
        root.dispatch();
    }

    function dispatch() {
        if (root.current !== null)
            return;
        if (root.pending.length === 0) {
            root.idle();
            return;
        }
        root.current = root.pending[0];
        root.pending = root.pending.slice(1);
        root.runner.run(root.current.argv, function (out, err, code) {
            var completed = root.current;
            root.current = null;
            try {
                completed.callback(out, err, code);
            } finally {
                root.dispatch();
            }
        });
    }
}
