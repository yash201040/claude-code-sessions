#!/usr/bin/python3 -I
# <xbar.title>Claude Code sessions</xbar.title>
# <xbar.desc>Recent Claude Code sessions grouped by folder; click one to resume it in Supacode or Terminal.</xbar.desc>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
"""Menu-bar list of Claude Code sessions.

Reads the transcripts Claude Code keeps in ~/.claude/projects/<folder-slug>/<id>.jsonl.
Each transcript is parsed once and cached by mtime and size, so a refresh only
re-reads sessions that changed. Titles follow the picker's order: /rename title,
then the generated title, then the first prompt. Read-only; no network.

Invoked as `claude-sessions.1m.py open <session-id>` when a row is clicked. With
Supacode installed, a session that is already running is refocused in its Supacode tab
(found through the running process's SUPACODE_TAB_ID); otherwise its folder is opened
in Supacode and `claude --resume <id>` runs in a new tab. Without Supacode, it resumes
in a new Terminal window. On failure it notifies and copies the command.
`claude-sessions.1m.py index` prints the session list as JSON for the search panel
(~/.local/share/claude-sessions-menu/session-search).

The cache and index hold words from every conversation, so they are readable by the
owner only (folder 0700, files 0600).
"""
from __future__ import annotations

import json, os, re, shlex, shutil, subprocess, sys, time
from urllib.parse import unquote

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
PROJECTS = os.path.join(CLAUDE_DIR, "projects")
CACHE_DIR = os.path.expanduser("~/.cache/claude-sessions-menu")
CACHE = os.path.join(CACHE_DIR, "cache.json")
INDEX = os.path.join(CACHE_DIR, "index.json")  # read by the search panel
# SwiftBar runs plugins with a minimal PATH, so look in the app bundle first.
SUPACODE = next((p for p in ("/Applications/supacode.app/Contents/Resources/bin/supacode",
                             os.path.expanduser("~/Applications/supacode.app/Contents/Resources/bin/supacode"))
                 if os.access(p, os.X_OK)), None) or shutil.which("supacode")
SEARCH = os.path.expanduser("~/.local/share/claude-sessions-menu/session-search")
SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

MAX_DAYS = 365         # hide sessions idle longer than this
PER_FOLDER = 200       # cap per folder submenu (menus scroll)
TITLE_CHARS = 60
MAX_WORDS = 20000      # distinct words kept per session for full-text search
WORD = re.compile(r"[a-z0-9][a-z0-9_.+-]*[a-z0-9]|[a-z0-9]")
HEAD = "size=11 color=#8a8a8a"
# User records that hold harness output rather than something the person typed.
HARNESS = ("<local-command", "<system-reminder", "<task-notification", "<bash-", "Caveat:",
           "[Request interrupted", "This session is being continued")


def first_prompt(record):
    if record.get("isMeta") or record.get("isSidechain"):
        return None
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content
                           if isinstance(b, dict) and b.get("type") == "text") or None
    if not isinstance(content, str):
        return None
    text = content.strip()
    if text.startswith("<command-name>") or text.startswith(HARNESS):
        return None
    # Keep pasted text: dictation apps paste what the person said.
    text = re.sub(r"<pasted_content[^>]*>(.*?)</pasted_content[^>]*>", r" \1 ", text, flags=re.S)
    text = re.sub(r"\[Image #\d+\]", "", text)
    return " ".join(text.split()) or None


def summarize(path):
    info = {"cwd": None, "custom": None, "ai": None, "agent": None, "prompt": None}
    words = {}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"type":"' not in line or '"type":"attachment"' in line[:200]:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            kind = record.get("type")
            if not info["cwd"] and record.get("cwd"):
                info["cwd"] = record["cwd"]
            if kind == "custom-title":
                info["custom"] = record.get("customTitle") or info["custom"]
            elif kind == "ai-title":
                info["ai"] = record.get("aiTitle") or info["ai"]
            elif kind == "agent-name":
                info["agent"] = record.get("agentName") or info["agent"]
            elif kind == "user":
                prompt = first_prompt(record)
                if prompt:
                    info["prompt"] = info["prompt"] or prompt
                    words.update(dict.fromkeys(WORD.findall(prompt.lower())))
            elif kind == "assistant" and not record.get("isSidechain"):
                for block in (record.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        words.update(dict.fromkeys(WORD.findall(block.get("text", "").lower())))
    info["words"] = " ".join(list(words)[:MAX_WORDS])
    return info


def running():
    """Session id -> pid for live Claude Code processes (from ~/.claude/sessions/<pid>.json)."""
    live, folder = {}, os.path.join(CLAUDE_DIR, "sessions")
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name)) as f:
                data = json.load(f)
            os.kill(int(data["pid"]), 0)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if data.get("sessionId"):
            live[data["sessionId"]] = int(data["pid"])
    return live


def load_sessions():
    try:
        with open(CACHE) as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    fresh, sessions, cutoff = {}, [], time.time() - MAX_DAYS * 86400
    live = running()
    for slug in os.listdir(PROJECTS) if os.path.isdir(PROJECTS) else []:
        folder = os.path.join(PROJECTS, slug)
        for name in os.listdir(folder) if os.path.isdir(folder) else []:
            sid = name[:-6]
            if not name.endswith(".jsonl") or not SESSION_ID.match(sid):
                continue
            path = os.path.join(folder, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            key = f"v2:{st.st_mtime_ns}:{st.st_size}"  # bump v2 when summarize() changes
            hit = cache.get(path)
            info = hit["info"] if hit and hit.get("key") == key and "words" in hit["info"] else summarize(path)
            fresh[path] = {"key": key, "info": info}
            if not info["prompt"]:
                continue  # opened and closed without a prompt
            try:
                with open(os.path.join(folder, sid, "custom-title.json")) as f:
                    custom = json.load(f).get("customTitle")
            except Exception:
                custom = None
            title = custom or info["custom"] or info["agent"] or info["ai"] or info["prompt"]
            sessions.append({"id": sid, "mtime": st.st_mtime, "title": " ".join(title.split()),
                             "named": title != info["prompt"], "open": sid in live,
                             "cwd": info["cwd"] or "?", "words": info["words"]})
    try:
        os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
        os.chmod(CACHE_DIR, 0o700)  # also tightens a folder made before this rule
        write_atomic(CACHE, fresh)
        write_atomic(INDEX, sorted(sessions, key=lambda s: s["mtime"], reverse=True))
    except OSError:
        pass
    return sorted(sessions, key=lambda s: s["mtime"], reverse=True)


def write_atomic(path, data):
    tmp = f"{path}.{os.getpid()}"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)  # readers never see a half-written file


def ago(ts):
    s = time.time() - ts
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{int(s // size)}{unit}"
    return "now"


def clean(text):
    text = " ".join(str(text).replace("|", "/").split())
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS - 1] + "…"


def row(s):
    style = "" if s["named"] else " color=#8a8a8a,#a0a0a0"
    mark = "● " if s["open"] else ""
    return (f'--{mark}{clean(s["title"])}  ·  {ago(s["mtime"])} | bash="{sys.argv[0]}" '
            f'param1=open param2={s["id"]} terminal=false refresh=false{style}')


def render():
    sessions = load_sessions()
    print("| sfimage=text.bubble")
    print("---")
    print(f'Search sessions… | bash="{SEARCH}" param1="{sys.argv[0]}" terminal=false '
          'refresh=false sfimage=magnifyingglass')
    print("---")
    if not sessions:
        print(f"No sessions in the last {MAX_DAYS} days | {HEAD}")
    groups = {}
    for s in sessions:  # already newest first, so folders come out by latest activity
        groups.setdefault(s["cwd"], []).append(s)
    for cwd, items in groups.items():
        live = sum(s["open"] for s in items)
        badge = f" · {live} open" if live else ""
        print(f'{clean(os.path.basename(cwd.rstrip("/")) or cwd)} ({len(items)}{badge}) | sfimage=folder '
              'bash=/usr/bin/true terminal=false refresh=false')  # SwiftBar greys rows without an action
        # The full path goes inside the submenu; a tooltip would cover the first rows.
        home = os.path.expanduser("~")
        path = cwd.replace(home, "~", 1) if cwd.startswith(home) else cwd
        print(f'--{path.replace("|", "/")} | {HEAD}')
        print("-----")
        for s in items[:PER_FOLDER]:
            print(row(s))
    print("---")
    print("● open in a tab · grey = untitled | " + HEAD)
    print("Refresh | refresh=true")


def find_worktree(cwd):
    """The Supacode worktree whose folder is cwd or the closest parent of it."""
    out = subprocess.run([SUPACODE, "worktree", "list"], capture_output=True, text=True,
                         timeout=20).stdout
    best, cwd = None, cwd.rstrip("/") + "/"
    for line in out.splitlines():
        wt = line.split("\t")[0].strip()
        root = unquote(wt).rstrip("/") + "/"
        if cwd.startswith(root) and (best is None or len(root) > len(best[1])):
            best = (wt, root)
    return best[0] if best else None


def supacode(*args, timeout=30):
    return subprocess.run([SUPACODE, *args], capture_output=True, text=True, timeout=timeout)


def notify(message, command=None):
    if command:
        subprocess.run(["pbcopy"], input=command, text=True)
        message += " Command copied: " + command
    subprocess.run(["osascript", "-e",
                    f"display notification {applescript_str(message)} with title \"Claude sessions\""])


def applescript_str(text):
    # JSON string escaping (\" \\ \n) is valid AppleScript; keep non-ASCII as is.
    return json.dumps(text, ensure_ascii=False)


def open_in_terminal(command):
    """Run command in a new Terminal window. macOS asks once for permission to control Terminal."""
    script = f'tell application "Terminal"\nactivate\ndo script {applescript_str(command)}\nend tell'
    return subprocess.run(["osascript", "-e", script], capture_output=True, timeout=30).returncode == 0


def focus_running(pid):
    """Focus the Supacode tab hosting this Claude process. False if it isn't in Supacode."""
    env = subprocess.run(["ps", "-E", "-ww", "-p", str(pid), "-o", "command="],
                         capture_output=True, text=True).stdout
    tab = re.search(r"\bSUPACODE_TAB_ID=(\S+)", env)
    wt = re.search(r"\bSUPACODE_WORKTREE_ID=(\S+)", env)
    if not (tab and wt) or tab.group(1) not in supacode("tab", "list", "-w", wt.group(1)).stdout:
        return False
    supacode("worktree", "focus", "-w", wt.group(1))
    supacode("tab", "focus", "-w", wt.group(1), "-t", tab.group(1))
    supacode("open")
    return True


def open_session(sid):
    if not SESSION_ID.match(sid):
        return
    try:
        with open(INDEX) as f:
            match = next((s for s in json.load(f) if s["id"] == sid), None)
    except (OSError, ValueError):
        match = None
    match = match or next((s for s in load_sessions() if s["id"] == sid), None)
    command = f"claude --resume {sid}"
    cwd = match["cwd"] if match else ""
    # claude --resume only finds a session from the folder it was started in.
    full = f"cd {shlex.quote(cwd)} && {command}" if cwd else command
    pid = running().get(sid)
    try:
        if pid:
            if not (SUPACODE and focus_running(pid)):
                notify("This session is already open in another window.")
            return
        if SUPACODE:
            wt = find_worktree(cwd) if cwd else None
            if not wt and os.path.isdir(cwd):
                supacode("repo", "open", cwd)
                wt = find_worktree(cwd)
            if wt:
                supacode("worktree", "focus", "-w", wt)
                supacode("tab", "new", "-w", wt, "-i", full,
                         "--title", clean(match["title"] if match else sid)).check_returncode()
                supacode("open")
                return
        if open_in_terminal(full):
            return
    except (OSError, subprocess.SubprocessError):
        pass
    notify("Couldn't open the session.", full)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "open":
        open_session(sys.argv[2])
    elif len(sys.argv) >= 2 and sys.argv[1] == "index":
        load_sessions()  # rewrites INDEX
    else:
        render()
