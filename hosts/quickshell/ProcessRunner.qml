import QtQuick
import Quickshell.Io

// The `runner` ui/CommandBackend.qml starts commands through: one Quickshell
// Process per call, destroyed once it has exited and both streams are read
// (those arrive in either order).
QtObject {
    id: root

    property string workingDirectory: ""

    property Component job: Component {
        Process {
            id: proc

            property var callback: null
            property var lineCallback: null
            property int stdoutOffset: 0
            property string out: ""
            property string err: ""
            property int outDone: 0
            property int code: -1

            function deliverLines(text) {
                if (typeof proc.lineCallback !== "function")
                    return;
                var end = text.indexOf("\n", proc.stdoutOffset);
                while (end !== -1) {
                    var line = text.slice(proc.stdoutOffset, end);
                    proc.stdoutOffset = end + 1;
                    proc.lineCallback(line);
                    end = text.indexOf("\n", proc.stdoutOffset);
                }
            }

            function finish() {
                if (proc.outDone < 2 || proc.code < 0)
                    return;
                var cb = proc.callback;
                proc.callback = null;
                if (typeof cb === "function")
                    cb(proc.out, proc.err, proc.code);
                proc.destroy();
            }

            stdout: StdioCollector {
                waitForEnd: typeof proc.lineCallback !== "function"
                onTextChanged: proc.deliverLines(this.text)
                onStreamFinished: {
                    proc.deliverLines(this.text);
                    proc.out = this.text;
                    proc.outDone += 1;
                    proc.finish();
                }
            }
            stderr: StdioCollector {
                onStreamFinished: {
                    proc.err = this.text;
                    proc.outDone += 1;
                    proc.finish();
                }
            }
            onExited: function (exitCode) {
                proc.code = exitCode;
                proc.finish();
            }
        }
    }

    function run(argv, callback, onLine) {
        var proc = root.job.createObject(root, {
            callback: callback,
            lineCallback: onLine || null
        });
        var options = {
            command: argv
        };
        if (root.workingDirectory !== "")
            options.workingDirectory = root.workingDirectory;
        proc.exec(options);
    }
}
