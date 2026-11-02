import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Services.Pam

AuthSession {
    id: root

    property bool probePamService: true
    property string pamConfig: "login"

    readonly property string pamConfigDirectory: "/etc/pam.d"
    backend: nativePam
    successResult: PamResult.Success
    failedResult: PamResult.Failed
    maxTriesResult: PamResult.MaxTries

    Component.onCompleted: {
        if (probePamService)
            detectPamServiceProc.running = true;
    }

    // Prefer the requested service (the wrapper requests the dedicated
    // pond-lock service), but only once it exists: before the system
    // configuration providing it is active, fall back to login.
    Process {
        id: detectPamServiceProc
        command: ["sh", "-c", "for s in \"$1\" login system-auth common-auth; do if [ -n \"$s\" ] && [ -f \"/etc/pam.d/$s\" ]; then printf '%s\\n' \"$s\"; exit 0; fi; done; exit 1", "sh", Quickshell.env("POND_PAM_SERVICE") || ""]

        stdout: StdioCollector {
            onStreamFinished: {
                const service = String(text || "").trim();
                if (service.length > 0) {
                    root.pamConfig = service;
                    root.pamReady = true;
                }
            }
        }

        onExited: (exitCode, exitStatus) => {
            if (exitCode !== 0)
                root.authenticationError("No supported PAM service is available");
        }
    }

    PamContext {
        id: nativePam
        configDirectory: root.pamConfigDirectory
        config: root.pamConfig
        // An empty user makes PamContext resolve getuid() itself. Avoid trusting
        // a mutable environment variable for the authentication identity.
        user: ""
    }
}
