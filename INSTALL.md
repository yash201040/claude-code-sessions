# Claude Code sessions menu: install instructions

These are instructions for Claude Code. Give it this folder or the repo URL and say "install this". From a URL, clone the repo into a temporary folder first and install from there.

## What it is

A SwiftBar menu that lists your Claude Code sessions grouped by folder, with a Spotlight-style search over titles and conversation text. Clicking a session resumes it with `claude --resume <id>` in a new [Herdr](https://github.com/herdrdev/herdr) tab inside [Ghostty](https://ghostty.org). A session that's already running in Herdr or [Supacode](https://supacode.sh) is focused there instead.

## Files

- `claude-code-sessions.1m.py`: the SwiftBar plugin. It refreshes every minute.
- `session-search.swift`: source for the search panel, compiled once during install.

## Requirements

- macOS, Apple Silicon or Intel.
- The Xcode Command Line Tools, which provide `/usr/bin/python3` and `swiftc`. If `swiftc` is missing, ask the user before running `xcode-select --install`.
- [SwiftBar](https://github.com/swiftbar/SwiftBar). If it's missing, ask before running `brew install --cask swiftbar`. The user opens it once and picks a plugin folder.
- Claude Code.
- [Herdr](https://github.com/herdrdev/herdr). If it's missing, ask before running `brew install herdr`. The plugin looks for it in `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin` and `PATH`.
- [Ghostty](https://ghostty.org) 1.4 or newer. If it's missing, ask before running `brew install --cask ghostty`. The plugin uses Ghostty's AppleScript support to raise the window showing Herdr, or to open one when none is attached.
- Supacode is optional. Don't install it. If it's there, sessions running in it are focused there. New tabs open in Herdr, and if Herdr is missing, in Supacode or then Terminal.

## Install steps (for Claude)

1. Find SwiftBar's plugin folder: `defaults read com.ameba.SwiftBar PluginDirectory`.
2. Copy `claude-code-sessions.1m.py` into that folder as a single file and `chmod +x` it. If a file with that name is already there, show it to the user and ask before replacing it.
3. Compile the search panel:
   ```sh
   mkdir -p ~/.local/share/claude-code-sessions
   cp session-search.swift ~/.local/share/claude-code-sessions/
   cd ~/.local/share/claude-code-sessions && swiftc -O -o session-search session-search.swift
   ```
   Keep the compiled `session-search` out of the plugin folder, because SwiftBar would run it as a plugin.
4. Refresh SwiftBar with `open -g "swiftbar://refreshallplugins"`. A speech-bubble icon appears in the menu bar.
5. Check the install:
   - `<plugin path> index` writes `~/.cache/claude-code-sessions/index.json`.
   - Running the plugin with no arguments prints the menu.
6. Tell the user that the first click asks for permission to control Ghostty, and that they should allow it.

## Notes

- Titles come in this order: the `/rename` title, then Claude's generated title, then the first prompt (shown in grey). ● marks a session that is running now.
- In Herdr, a resumed session opens as a tab in the workspace that has a pane in the same folder or git checkout, or else in a new workspace named after the folder. If no Herdr server is running, the Ghostty window the plugin opens starts one. A session that's already running is found through Herdr's agent list, including in named Herdr sessions.
- Only Claude Code sessions in `~/.claude` (or `$CLAUDE_CONFIG_DIR`) are listed. claude.ai chats live on Anthropic's servers and don't appear.
- Search reads an index in `~/.cache/claude-code-sessions/`. It holds words from every conversation, so it's readable by its owner only. Nothing is sent anywhere.
- Renamed folders: the plugin remembers each session folder's identity (device and inode, which a rename keeps) in `~/.cache/claude-code-sessions/folders.json`. When a folder or one of its parents is renamed on the same disk, sessions show under the new name and resume there. A folder renamed before the menu first saw it, moved to another disk or deleted shows as "folder missing".
- "Move sessions to the new folder name" refiles a renamed folder's sessions and project memory inside `~/.claude/projects/` so Claude Code's resume picker, `--continue` and memory find them again. It asks first, refuses while one of those sessions is running, never overwrites a differing file (it leaves it in place and reports it), and logs to `~/.local/state/claude-code-sessions/moves.log`.
- Settings are constants at the top of the plugin: `MAX_DAYS`, `PER_FOLDER` and `TITLE_CHARS`.
- SwiftBar runs each click through `bash -l`. A slow `~/.bash_profile` (for example, conda init) delays every click.
- To uninstall, delete the plugin file, `~/.local/share/claude-code-sessions/`, `~/.cache/claude-code-sessions/` and `~/.local/state/claude-code-sessions/`.
