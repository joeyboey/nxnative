// The in-game menu, opened by the gamepad's menu button at the top centre (DmGamepad.m): the controller layout, then the settings-screen settings the running game takes at once
// (front_ios.m dm_setting_live), grouped as on the settings screen but without its headings, one row each, so the menu stays short over the game (its text size is fixed: front_ios.m
// startGame). A pick is saved as Play saves it (LauncherModel.choose), so the settings screen shows it at the next start; a setting an env file fixes is shown, not changed. Settings
// that need the next start (language, pipelines, the frame rate and so on), and the sliders, stay on the settings screen, which the menu's title points to.
import UIKit

var gameMenuModel: LauncherModel?   // the settings screen's model (Launcher.swift); nil in an app without one: the menu has the layout only

// The menu's own words in the player's language (LNPANDY/NX2Apple#1129): DM_GAME_MENU_TEXT, one per line in GameMenuText's order, passed by the
// launcher as DM_HOME_QUIT_TEXT is for the quit prompt: the shell sets it at launch, and a game's own app gets it in its bundled dm/env.txt (the build's
// --env-file), where the lines are joined by a backslash and n. A line that is missing or empty stays English.
enum GameMenuText: Int, CaseIterable {
    case moreSettings, controllerLayout
    var english: String {
        switch self {
        case .moreSettings: return "More settings on the start screen"
        case .controllerLayout: return "Controller layout"
        }
    }
    static func line(_ t: GameMenuText, in text: String?) -> String {
        let lines = (text ?? "").replacingOccurrences(of: "\\n", with: "\n").components(separatedBy: "\n")
        let line = t.rawValue < lines.count ? lines[t.rawValue].trimmingCharacters(in: .whitespaces) : ""
        return line.isEmpty ? t.english : line
    }
    var text: String { Self.line(self, in: getenv("DM_GAME_MENU_TEXT").map { String(cString: $0) }) }
}

private let shellMenuAllowlist: Set<String> = ["DM_OPMODE", "DM_PICTURE_FIT", "DM_SHOW_FPS", "DM_AUDIO_MIX", "DM_METAL_HUD"]

private struct ShellMenuPayload: Decodable {
    let suiteName: String
    let items: [ShellMenuItem]
}

private struct ShellMenuItem: Decodable {
    let env, kind, label, effectiveDefault: String
    let onValue, offValue: String?
    let options: [[String]]?

    var resolvedOnValue: String { onValue ?? "1" }
    var resolvedOffValue: String { offValue ?? "0" }

    func accepts(_ v: String) -> Bool {
        switch kind {
        case "choice": return options?.contains { $0.count >= 2 && $0[0] == v } ?? false
        case "toggle": return v == resolvedOnValue || v == resolvedOffValue
        default: return false
        }
    }
}

private func currentShellMenu() -> ShellMenuPayload? {
    guard let raw = getenv("DM_SHELL_MENU").map({ String(cString: $0) }),
          let data = raw.data(using: .utf8) else { return nil }
    return try? JSONDecoder().decode(ShellMenuPayload.self, from: data)
}

@_cdecl("dm_game_menu")
public func dmGameMenu() -> UnsafeMutableRawPointer {
    let title = (gameMenuModel != nil || currentShellMenu() != nil) ? GameMenuText.moreSettings.text : ""
    let menu = UIMenu(title: title, children: [UIDeferredMenuElement.uncached { done in done(gameMenuItems()) }])   // built each time it opens, so it shows the current choices
    return Unmanaged.passRetained(menu).toOpaque()
}

private func gameMenuItems() -> [UIMenuElement] {
    let names = ["2D", "3D", "Tap"]
    let layouts = (0..<3).filter { dm_pad_layout_offered(Int32($0)) }.map { l in
        UIAction(title: names[l], state: dm_pad_layout() == l ? .on : .off) { _ in dm_pad_layout_set(Int32(l)) }
    }
    let layout = UIMenu(title: GameMenuText.controllerLayout.text, options: .singleSelection, children: layouts)   // a choice's value shows in its submenu: a subtitle would make each row two lines
    var items: [UIMenuElement] = []
    var placed = false
    if let m = gameMenuModel {
        for section in m.sections {
            var rows: [UIMenuElement] = section == "Controls" ? [layout] : []
            if section == "Controls" { placed = true }
            for s in m.rows(in: section) where (s.kind == "choice" || s.kind == "toggle") && dm_setting_live(s.env, nil) { rows.append(gameMenuRow(m, s)) }
            if !rows.isEmpty { items.append(UIMenu(options: .displayInline, children: rows)) }
        }
    } else if let shell = currentShellMenu() {
        let fixed = Set((getenv("DM_ENV_FILE_KEYS").map { String(cString: $0) } ?? "").split(separator: " ").map(String.init))
        let rows = shell.items
            .filter { shellMenuAllowlist.contains($0.env) && ($0.kind == "choice" || $0.kind == "toggle") && dm_setting_live($0.env, nil) }
            .map { shellMenuRow(shell, $0, fixed: fixed) }
        items.append(UIMenu(options: .displayInline, children: [layout]))
        placed = true
        if !rows.isEmpty { items.append(UIMenu(options: .displayInline, children: rows)) }
    }
    if !placed { items.insert(UIMenu(options: .displayInline, children: [layout]), at: 0) }
    items.append(Unmanaged<UIMenu>.fromOpaque(dm_quit_menu()).takeRetainedValue())   // Quit <game>, last and on its own: it opens the quit prompt (front_ios.m)
    return items
}

private func gameMenuRow(_ m: LauncherModel, _ s: Setting) -> UIMenuElement {
    let now = m.values[s.env] ?? s.def, fixed = m.fixed.contains(s.env)
    let attributes: UIMenuElement.Attributes = fixed ? .disabled : []
    func pick(_ v: String) { m.choose(s, v); _ = dm_setting_live(s.env, v) }
    if s.kind == "toggle" {
        let a = UIAction(title: s.label, attributes: attributes, state: now == s.onValue ? .on : .off) { _ in pick(now == s.onValue ? s.offValue : s.onValue) }
        if fixed { a.subtitle = "Set in env.txt" }
        return a
    }
    let options = offeredOptions(s, current: now)
    let current = options.first { $0[0] == now }?[1] ?? now
    let actions = options.map { o in UIAction(title: o[1], attributes: attributes, state: o[0] == now ? .on : .off) { _ in pick(o[0]) } }
    return UIMenu(title: s.label, subtitle: fixed ? "\(current), set in env.txt" : nil, options: .singleSelection, children: actions)
}

private func shellMenuRow(_ shell: ShellMenuPayload, _ item: ShellMenuItem, fixed: Set<String>) -> UIMenuElement {
    let envValue = getenv(item.env).map { String(cString: $0) }
    let initialNow = envValue.flatMap { item.accepts($0) ? $0 : nil } ?? item.effectiveDefault
    let tvLocked = item.env == "DM_OPMODE" && dm_tv_connected()
    let now = tvLocked ? "1" : initialNow
    let isFixed = fixed.contains(item.env)
    let attributes: UIMenuElement.Attributes = (isFixed || tvLocked) ? .disabled : []
    func pick(_ v: String) {
        setenv(item.env, v, 1)
        _ = dm_setting_live(item.env, v)
        if !shell.suiteName.isEmpty, let defaults = UserDefaults(suiteName: shell.suiteName) {
            let key = "launcher." + item.env
            if v == item.effectiveDefault { defaults.removeObject(forKey: key) } else { defaults.set(v, forKey: key) }
        }
        fputs("launcher: \(item.env)=\(v) (in-game menu)\n", stderr)
    }
    if item.kind == "toggle" {
        let on = item.resolvedOnValue, off = item.resolvedOffValue
        let a = UIAction(title: item.label, attributes: attributes, state: now == on ? .on : .off) { _ in pick(now == on ? off : on) }
        if isFixed { a.subtitle = "Set in env.txt" }
        return a
    }
    let options = (item.options ?? []).filter { $0.count >= 2 }
    let current = options.first { $0[0] == now }?[1] ?? now
    let actions = options.map { o in UIAction(title: o[1], attributes: attributes, state: o[0] == now ? .on : .off) { _ in pick(o[0]) } }
    return UIMenu(title: item.label, subtitle: isFixed ? "\(current), set in env.txt" : nil, options: .singleSelection, children: actions)
}
