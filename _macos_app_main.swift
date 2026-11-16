
import Cocoa
import WebKit

class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var backendProcess: Process?
    let serverURL = URL(string: "http://127.0.0.1:5050")!
    let projectDir = "/Users/snivk/Documents/google/baby_track"

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupMainMenu()

        let screenRect = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let winW: CGFloat = min(1480, screenRect.width * 0.92)
        let winH: CGFloat = min(900, screenRect.height * 0.90)
        let winRect = NSRect(
            x: screenRect.origin.x + (screenRect.width - winW) / 2,
            y: screenRect.origin.y + (screenRect.height - winH) / 2,
            width: winW,
            height: winH
        )

        window = NSWindow(
            contentRect: winRect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "BabyTrack Studio"
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = NSColor(calibratedRed: 0.035, green: 0.035, blue: 0.045, alpha: 1.0)
        window.titlebarAppearsTransparent = true
        window.isMovableByWindowBackground = false
        window.minSize = NSSize(width: 1024, height: 680)

        let config = WKWebViewConfiguration()
        config.mediaTypesRequiringUserActionForPlayback = []
        webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.uiDelegate = self
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")

        window.contentView?.addSubview(webView)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        ensureBackendAndLoad()
    }

    func setupMainMenu() {
        let mainMenu = NSMenu()

        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Reload Studio", action: #selector(reloadPage), keyEquivalent: "r")
        appMenu.addItem(withTitle: "Open Outputs Folder in Finder", action: #selector(openOutputsFolder), keyEquivalent: "o")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Quit BabyTrack Studio", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appMenuItem.submenu = appMenu

        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu

        NSApp.mainMenu = mainMenu
    }

    @objc func reloadPage() {
        webView.reload()
    }

    @objc func openOutputsFolder() {
        let outURL = URL(fileURLWithPath: projectDir + "/outputs")
        NSWorkspace.shared.open(outURL)
    }

    func ensureBackendAndLoad() {
        checkServerReady { [weak self] ready in
            guard let self = self else { return }
            if ready {
                DispatchQueue.main.async {
                    self.webView.load(URLRequest(url: self.serverURL))
                }
            } else {
                self.startBackendProcess()
                self.pollUntilReady(attemptsLeft: 40)
            }
        }
    }

    func checkServerReady(completion: @escaping (Bool) -> Void) {
        var req = URLRequest(url: serverURL)
        req.timeoutInterval = 0.6
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            if let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                completion(true)
            } else {
                completion(false)
            }
        }.resume()
    }

    func startBackendProcess() {
        let proc = Process()
        let pyPath = FileManager.default.fileExists(atPath: "/opt/anaconda3/bin/python3")
            ? "/opt/anaconda3/bin/python3"
            : "/usr/bin/python3"
        proc.executableURL = URL(fileURLWithPath: pyPath)
        proc.arguments = [projectDir + "/app.py"]
        proc.currentDirectoryURL = URL(fileURLWithPath: projectDir)
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        do {
            try proc.run()
            self.backendProcess = proc
        } catch {
            print("Failed to launch backend:", error)
        }
    }

    func pollUntilReady(attemptsLeft: Int) {
        if attemptsLeft <= 0 {
            DispatchQueue.main.async {
                self.webView.load(URLRequest(url: self.serverURL))
            }
            return
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.2) { [weak self] in
            guard let self = self else { return }
            self.checkServerReady { ready in
                if ready {
                    DispatchQueue.main.async {
                        self.webView.load(URLRequest(url: self.serverURL))
                    }
                } else {
                    self.pollUntilReady(attemptsLeft: attemptsLeft - 1)
                }
            }
        }
    }

    // 支持 HTML <input type="file"> 弹出 macOS 原生文件选择窗口
    func webView(
        _ webView: WKWebView,
        runOpenPanelWith parameters: WKOpenPanelParameters,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping ([URL]?) -> Void
    ) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.message = "选择要导入 BabyTrack Studio 的视频文件"
        panel.beginSheetModal(for: window) { result in
            if result == .OK {
                completionHandler(panel.urls)
            } else {
                completionHandler(nil)
            }
        }
    }

    // 拦截 /download/<filename> 链接，直接在 Finder 的 ~/Downloads 或 outputs 中高亮显示文件
    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        if let url = navigationAction.request.url, url.path.hasPrefix("/download/") {
            let filename = url.lastPathComponent
            let dlPath = (NSHomeDirectory() as NSString).appendingPathComponent("Downloads/" + filename)
            let outPath = projectDir + "/outputs/" + filename
            if FileManager.default.fileExists(atPath: dlPath) {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: dlPath)])
            } else if FileManager.default.fileExists(atPath: outPath) {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: outPath)])
            }
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let proc = backendProcess, proc.isRunning {
            proc.terminate()
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
