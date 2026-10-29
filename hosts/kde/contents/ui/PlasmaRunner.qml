pragma ComponentBehavior: Bound
import QtQuick
import QtCore
import org.kde.plasma.plasma5support as Plasma5Support

// The `runner` ui/CommandBackend.qml starts commands through, on Plasma: one
// disposable executable DataSource per call. A DataSource keeps every command
// it has seen in a map for its whole life, so each job gets its own and drops
// it once answered. The command text carries a job number, so two identical
// commands in flight are still two jobs.
//
// The executable engine only answers when a command has exited, so a caller that
// wants output as it arrives (`onLine`) gets it from a file: the command writes
// its stdout there, a timer re-reads the file while it runs and hands over each
// finished line. The command still returns its whole stdout on exit, so a caller
// that ignores `onLine` sees no difference.
Item {
    id: root

    property int jobCount: 0
    property int pollCount: 0
    // Per-user and not world-writable, so the stream file cannot be a planted
    // symlink. Empty when the session has none, which turns streaming off.
    readonly property string streamDir: StandardPaths.writableLocation(StandardPaths.RuntimeLocation).toString().replace("file://", "").replace(/\/$/, "")

    // A widget closed mid-refresh kills the shell before it removes its stream
    // file, so leftovers older than an hour are cleared once at start.
    Component.onCompleted: {
        if (root.streamDir !== "")
            root.run(["find", root.streamDir, "-maxdepth", "1", "-name", "tidesi-stream-*", "-mmin", "+60", "-delete"], function () {});
    }

    // One argument, safe for /bin/sh: single-quoted, with any single quote
    // closed, escaped and reopened.
    function quote(arg) {
        return "'" + String(arg).replace(/'/g, "'\\''") + "'";
    }

    function run(argv, callback, onLine) {
        root.jobCount += 1;
        var stream = null;
        if (typeof onLine === "function" && root.streamDir !== "") {
            var path = root.streamDir + "/tidesi-stream-" + Date.now() + "-" + root.jobCount;
            stream = streamComponent.createObject(root, {
                path: path,
                lineCallback: onLine
            });
            // The file goes with the command, replayed to stdout once it ends.
            argv = ["sh", "-c", "umask 077; f=\"$1\"; shift; trap 'rm -f \"$f\"' EXIT; \"$@\" > \"$f\"; c=$?; cat \"$f\"; exit $c", "tidesi", path].concat(argv);
        }
        var command = argv.map(root.quote).join(" ") + " # job " + root.jobCount;
        const job = jobComponent.createObject(root, {
            callback: function (out, err, code) {
                // Stop polling first: the answer below holds every line anyway.
                if (stream)
                    stream.stop();
                callback(out, err, code);
            }
        }) as Plasma5Support.DataSource;
        job.connectSource(command);
    }

    Component {
        id: jobComponent

        Plasma5Support.DataSource {
            id: job

            property var callback: null

            engine: "executable"
            connectedSources: []
            onNewData: function (source, data) {
                job.disconnectSource(source);
                var cb = job.callback;
                job.callback = null;
                if (typeof cb === "function")
                    cb(data["stdout"] || "", data["stderr"] || "", data["exit code"] || 0);
                job.destroy();
            }
        }
    }

    // Re-reads a growing stdout file and hands over the lines that have ended.
    Component {
        id: streamComponent

        Item {
            id: stream

            property string path: ""
            property var lineCallback: null
            property int offset: 0
            property bool reading: false
            property bool stopped: false

            function stop() {
                stream.stopped = true;
                poll.stop();
                stream.destroy();
            }

            function deliver(text) {
                var end = text.indexOf("\n", stream.offset);
                while (end !== -1 && !stream.stopped) {
                    var line = text.slice(stream.offset, end);
                    stream.offset = end + 1;
                    stream.lineCallback(line);
                    end = text.indexOf("\n", stream.offset);
                }
            }

            Timer {
                id: poll

                interval: 400
                repeat: true
                running: true
                onTriggered: {
                    if (stream.reading || stream.stopped)
                        return;
                    stream.reading = true;
                    root.pollCount += 1;
                    reader.connectSource("cat " + root.quote(stream.path) + " 2>/dev/null # poll " + root.pollCount);
                }
            }

            Plasma5Support.DataSource {
                id: reader

                engine: "executable"
                connectedSources: []
                onNewData: function (source, data) {
                    reader.disconnectSource(source);
                    stream.reading = false;
                    if (!stream.stopped)
                        stream.deliver(data["stdout"] || "");
                }
            }
        }
    }
}
