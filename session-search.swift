// Spotlight-style fuzzy search over Claude Code sessions, opened from the SwiftBar menu.
// Usage: session-search <path to claude-sessions.1m.py>
// Reads the index the plugin writes (~/.cache/claude-code-sessions/index.json) so the window
// opens instantly, refreshes it in the background with `<plugin> index`, and hands the
// choice to `<plugin> open <id>`. Titles match fuzzily; conversation text matches by word.
// Local only: no network. Build: swiftc -O -o session-search session-search.swift
import AppKit

struct Session: Decodable {
    let id: String, title: String, cwd: String, mtime: Double, named: Bool, open: Bool
    let words: String?
}

let indexURL = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".cache/claude-code-sessions/index.json")

func readIndex() -> [Session] {
    guard let data = try? Data(contentsOf: indexURL) else { return [] }
    return (try? JSONDecoder().decode([Session].self, from: data)) ?? []
}

let plugin = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ""

func run(_ args: [String], wait: Bool = true) -> Data {
    let process = Process(), pipe = Pipe()
    process.executableURL = URL(fileURLWithPath: plugin)
    process.arguments = args
    process.standardOutput = pipe
    guard (try? process.run()) != nil else { return Data() }
    let data = wait ? pipe.fileHandleForReading.readDataToEndOfFile() : Data()
    if wait { process.waitUntilExit() }
    return data
}

/// Subsequence match; consecutive letters and word starts score higher. nil = no match.
func fuzzy(_ query: [Character], _ text: String) -> Int? {
    if query.isEmpty { return 0 }
    let chars = Array(text.lowercased())
    var qi = 0, score = 0, last = -2
    for (i, c) in chars.enumerated() where qi < query.count && c == query[qi] {
        score += 1
        if i == last + 1 { score += 5 }
        if i == 0 || " /-_.".contains(chars[i - 1]) { score += 8 }
        last = i
        qi += 1
    }
    guard qi == query.count else { return nil }
    // A literal substring beats letters scattered across the title.
    return text.lowercased().contains(String(query)) ? score + 50 : score
}

/// An accessory app has no menu bar, so ⌘A/⌘C/⌘V/⌘X/⌘Z only work if an Edit menu exists.
func installEditMenu() {
    let main = NSMenu(), appItem = NSMenuItem(), editItem = NSMenuItem()
    main.addItem(appItem)
    main.addItem(editItem)
    let appMenu = NSMenu()
    appMenu.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
    appMenu.addItem(withTitle: "Close", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "w")
    appItem.submenu = appMenu
    let edit = NSMenu(title: "Edit")
    edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
    edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
    edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
    edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
    edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
    edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
    editItem.submenu = edit
    NSApp.mainMenu = main
}

func ago(_ t: Double) -> String {
    let s = Date().timeIntervalSince1970 - t
    for (unit, size) in [("d", 86400.0), ("h", 3600.0), ("m", 60.0)] where s >= size {
        return "\(Int(s / size))\(unit)"
    }
    return "now"
}

final class Controller: NSObject, NSApplicationDelegate, NSWindowDelegate, NSSearchFieldDelegate,
    NSTableViewDataSource, NSTableViewDelegate {
    var all = readIndex()
    var shown: [Session] = []
    var inText: Set<String> = []  // ids matched only by conversation text
    let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 640, height: 420),
                        styleMask: [.titled, .fullSizeContentView], backing: .buffered, defer: false)
    let field = NSSearchField()
    let table = NSTableView()

    func applicationDidFinishLaunching(_ note: Notification) {
        installEditMenu()
        panel.titlebarAppearsTransparent = true
        panel.titleVisibility = .hidden
        panel.isMovableByWindowBackground = true
        panel.level = .floating
        panel.delegate = self

        let background = NSVisualEffectView()
        background.material = .popover
        panel.contentView = background

        field.placeholderString = "Search Claude sessions"
        field.font = .systemFont(ofSize: 18)
        field.focusRingType = .none
        field.delegate = self

        let column = NSTableColumn(identifier: .init("s"))
        table.addTableColumn(column)
        table.headerView = nil
        table.rowHeight = 40
        table.backgroundColor = .clear
        table.style = .plain
        table.dataSource = self
        table.delegate = self
        table.target = self
        table.doubleAction = #selector(choose)
        let scroll = NSScrollView()
        scroll.documentView = table
        scroll.hasVerticalScroller = true
        scroll.drawsBackground = false

        for view in [field, scroll] as [NSView] {
            view.translatesAutoresizingMaskIntoConstraints = false
            background.addSubview(view)
        }
        NSLayoutConstraint.activate([
            field.topAnchor.constraint(equalTo: background.topAnchor, constant: 32),
            field.leadingAnchor.constraint(equalTo: background.leadingAnchor, constant: 16),
            field.trailingAnchor.constraint(equalTo: background.trailingAnchor, constant: -16),
            scroll.topAnchor.constraint(equalTo: field.bottomAnchor, constant: 10),
            scroll.leadingAnchor.constraint(equalTo: background.leadingAnchor, constant: 8),
            scroll.trailingAnchor.constraint(equalTo: background.trailingAnchor, constant: -8),
            scroll.bottomAnchor.constraint(equalTo: background.bottomAnchor, constant: -8),
        ])

        filter()
        // Refresh (new sessions, open/closed state) without delaying the window.
        DispatchQueue.global().async {
            _ = run(["index"])
            let fresh = readIndex()
            DispatchQueue.main.async {
                guard !fresh.isEmpty else { return }
                self.all = fresh
                self.filter()
            }
        }
        // Open on the screen under the pointer, a little above centre like Spotlight.
        let mouse = NSEvent.mouseLocation
        if let screen = NSScreen.screens.first(where: { $0.frame.contains(mouse) }) ?? NSScreen.main {
            let area = screen.visibleFrame, size = panel.frame.size
            panel.setFrameOrigin(NSPoint(x: area.midX - size.width / 2,
                                         y: area.midY - size.height / 2 + area.height / 8))
        }
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        panel.makeFirstResponder(field)
    }

    func filter() {
        // Keep the highlighted session across the background refresh.
        let previous = shown.indices.contains(table.selectedRow) ? shown[table.selectedRow].id : nil
        let raw = field.stringValue.lowercased()
        let query = Array(raw.filter { !$0.isWhitespace })
        let terms = raw.split(separator: " ").map(String.init)
        inText = []
        shown = all
            .compactMap { s -> (Session, Int)? in
                let folder = (s.cwd as NSString).lastPathComponent
                if let score = fuzzy(query, "\(s.title) \(folder)") { return (s, score + 1000) }
                // Every typed word must occur in what was said in the conversation.
                guard !terms.isEmpty, let words = s.words,
                      terms.allSatisfy({ words.contains($0) }) else { return nil }
                inText.insert(s.id)
                return (s, 0)
            }
            .sorted { $0.1 != $1.1 ? $0.1 > $1.1 : $0.0.mtime > $1.0.mtime }
            .map(\.0)
        table.reloadData()
        let row = shown.firstIndex { $0.id == previous } ?? 0
        if !shown.isEmpty { table.selectRowIndexes([row], byExtendingSelection: false) }
    }

    func controlTextDidChange(_ note: Notification) { filter() }

    func control(_ control: NSControl, textView: NSTextView, doCommandBy selector: Selector) -> Bool {
        switch selector {
        case #selector(NSResponder.moveDown(_:)), #selector(NSResponder.moveUp(_:)):
            guard !shown.isEmpty else { return true }
            let step = selector == #selector(NSResponder.moveDown(_:)) ? 1 : -1
            let row = min(max(table.selectedRow + step, 0), shown.count - 1)
            table.selectRowIndexes([row], byExtendingSelection: false)
            table.scrollRowToVisible(row)
        case #selector(NSResponder.insertNewline(_:)): choose()
        case #selector(NSResponder.cancelOperation(_:)): NSApp.terminate(nil)
        default: return false
        }
        return true
    }

    @objc func choose() {
        let row = table.clickedRow >= 0 ? table.clickedRow : table.selectedRow
        guard shown.indices.contains(row) else { return }
        panel.orderOut(nil)
        _ = run(["open", shown[row].id])
        NSApp.terminate(nil)
    }

    func windowDidResignKey(_ note: Notification) { NSApp.terminate(nil) }

    func numberOfRows(in tableView: NSTableView) -> Int { shown.count }

    func tableView(_ tableView: NSTableView, viewFor column: NSTableColumn?, row: Int) -> NSView? {
        let s = shown[row]
        let text = NSMutableAttributedString(
            string: (s.open ? "● " : "") + s.title + "\n",
            attributes: [.font: NSFont.systemFont(ofSize: 14),
                         .foregroundColor: s.named ? NSColor.labelColor : NSColor.secondaryLabelColor])
        text.append(NSAttributedString(
            string: "\((s.cwd as NSString).lastPathComponent)  ·  \(ago(s.mtime))"
                + (inText.contains(s.id) ? "  ·  found in conversation" : ""),
            attributes: [.font: NSFont.systemFont(ofSize: 11), .foregroundColor: NSColor.secondaryLabelColor]))
        let label = NSTextField(labelWithAttributedString: text)
        label.lineBreakMode = .byTruncatingTail
        label.maximumNumberOfLines = 2
        return label
    }
}

let app = NSApplication.shared
let controller = Controller()
app.delegate = controller
app.setActivationPolicy(.accessory)
app.run()
