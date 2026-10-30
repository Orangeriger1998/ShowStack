# ShowStack engine (Linux).
#   Inventory (slow, cached): which developer tools are installed (tool-catalog.json) and which localhost servers the
#   code in your project folders can start (Claude launch.json, package.json scripts, Python, Django, .NET, compose).
#   Status (fast, live): Docker, which of those servers are running, and any other dev server listening on a port.
# Plain Python 3 standard library; the GTK window in showstack.py and showstack-report.py both import this.

import datetime
import fnmatch
import glob
import json
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(os.path.dirname(APP_DIR), 'assets')
HOME = os.path.expanduser('~')
CATALOG_PATH = os.path.join(APP_DIR, 'tool-catalog.json')
CONFIG_DIR = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.join(HOME, '.config'), 'showstack')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'showstack.json')
STATE_DIR = os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.join(HOME, '.local', 'state'), 'showstack')
INVENTORY_PATH = os.path.join(STATE_DIR, 'inventory.json')
STARTED_PATH = os.path.join(STATE_DIR, 'started.json')
HIDDEN_PATH = os.path.join(STATE_DIR, 'hidden.json')
LOG_PATH = os.path.join(STATE_DIR, 'widget.log')

# Processes that count as a dev server when one listens on a port no known server claims
# (developer runtimes, plus local LLM servers: Ollama, LM Studio, llama.cpp, KoboldCpp).
DEV_RUNTIMES = {'python', 'node', 'bun', 'deno', 'dotnet', 'java', 'ruby', 'php', 'uvicorn', 'gunicorn', 'hugo', 'caddy',
                'ollama', 'lm-studio', 'lms', 'llama-server', 'koboldcpp'}
# Folders never worth descending into when looking for project code.
SKIP_FOLDERS = {'node_modules', 'venv', 'env', '__pycache__', 'dist', 'build', 'bin', 'obj', 'target', 'out',
                'coverage', 'site-packages', 'vendor', 'packages', 'tests', 'test', '__tests__', 'logs', 'tmp', 'temp',
                'snap', 'lost+found'}
MAX_SCAN_FOLDERS = 20000
# Places tools install themselves that a desktop launcher's PATH often lacks (the shell profile adds them).
KNOWN_BIN_DIRS = ['~/.local/bin', '~/.cargo/bin', '~/.bun/bin', '~/.deno/bin', '~/go/bin', '~/.fly/bin', '~/.volta/bin',
                  '~/.lmstudio/bin', '~/.juliaup/bin', '~/.dotnet', '~/.dotnet/tools', '~/.local/share/pnpm', '~/.yarn/bin',
                  '~/.npm-global/bin', '/usr/local/bin', '/usr/local/go/bin', '/snap/bin', '/var/lib/flatpak/exports/bin',
                  '~/.local/share/flatpak/exports/bin', '/home/linuxbrew/.linuxbrew/bin', '/opt/homebrew/bin']
TERMINALS = ['x-terminal-emulator', 'gnome-terminal', 'xfce4-terminal', 'mate-terminal', 'konsole', 'tilix', 'alacritty',
             'kitty', 'xterm']
UNKNOWN_PROCESS = {'pid': 0, 'ppid': 0, 'name': 'another program (not yours)', 'cmdline': ''}

os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

# ---------------------------------------------------------------------------------------------------------------- config


@dataclass
class Config:
    required_tools: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    project_roots: list = field(default_factory=list)
    include_claude_projects: bool = True
    scan_depth: int = 5
    exclude_folders: list = field(default_factory=list)
    shell_folder: str = HOME
    titles: dict = field(default_factory=dict)
    ports: dict = field(default_factory=dict)


def default_config():
    return {
        'requiredTools': ['Git', 'Node.js', 'Python'],
        'tools': [],
        'projectRoots': ['~/source/repos', '~/repos', '~/dev', '~/.dev', '~/code', '~/projects', '~/Projects', '~/src',
                         '~/git', '~/workspace', '~/Documents/GitHub'],
        'includeClaudeProjects': True,
        'scanDepth': 5,
        'excludeFolders': ['*backup*', '*archive*', '*.bak', 'old', 'TO BE DELETED'],
        'shellFolder': '~',
        'titles': {},
        'ports': {},
    }


def expand(path):
    """~ and $VARIABLES in a setting, without a trailing slash."""
    value = os.path.expandvars(os.path.expanduser(str(path)))
    return value if value == '/' else value.rstrip('/')


def path_key(path):
    """One spelling for a folder, for comparisons (Linux paths are case-sensitive, so case is kept)."""
    return os.path.normpath(str(path)) if path else ''


def get_config():
    defaults = default_config()
    if not os.path.isfile(CONFIG_PATH):
        write_state(CONFIG_PATH, defaults)
    with open(CONFIG_PATH, encoding='utf-8') as handle:
        raw = json.load(handle)

    def value(name):
        return raw[name] if raw.get(name) is not None else defaults[name]

    titles = {path_key(expand(k)): str(v) for k, v in (raw.get('titles') or {}).items()}
    ports = {str(k).lower(): int(v) for k, v in (raw.get('ports') or {}).items()}
    return Config(
        required_tools=[str(t).lower() for t in value('requiredTools')],
        tools=list(value('tools')),
        project_roots=[expand(p) for p in value('projectRoots')],
        include_claude_projects=bool(value('includeClaudeProjects')),
        scan_depth=int(value('scanDepth')),
        exclude_folders=list(value('excludeFolders')),
        shell_folder=expand(value('shellFolder')),
        titles=titles,
        ports=ports,
    )


def read_state(path, default):
    if os.path.isfile(path):
        try:
            with open(path, encoding='utf-8') as handle:
                return json.load(handle)
        except (OSError, ValueError):
            pass
    return default


def write_state(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')


def log(text):
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as handle:
            handle.write('{:%Y-%m-%d %H:%M:%S}  {}\n'.format(datetime.datetime.now(), text))
    except OSError:
        pass

# --------------------------------------------------------------------------------------------------------------- helpers


_shell_path = None


def probe_shell_path():
    """PATH as the user's interactive shell sets it (nvm, cargo, pyenv...); a desktop launcher does not see that."""
    global _shell_path
    shells = [os.environ.get('SHELL') or '/bin/bash', '/bin/bash']
    for shell in dict.fromkeys(shells):
        if not os.path.isfile(shell):
            continue
        try:
            result = subprocess.run([shell, '-lic', 'printf "__SHOWSTACK_PATH__=%s\\n" "$PATH"'], capture_output=True,
                                    text=True, timeout=8, stdin=subprocess.DEVNULL, env={**os.environ, 'TERM': 'dumb'})
        except (OSError, subprocess.SubprocessError):
            continue
        for line in result.stdout.splitlines():
            if line.startswith('__SHOWSTACK_PATH__='):
                _shell_path = line.split('=', 1)[1]
                return _shell_path
    _shell_path = ''
    return _shell_path


def refresh_path(probe_shell=False):
    """The widget runs for days and is started from a launcher; merge in the shell's PATH and the usual tool folders
    so tools installed since it opened are found, and so the servers it starts find them too."""
    if probe_shell or _shell_path is None:
        probe_shell_path()
    entries = (_shell_path or '').split(':') + os.environ.get('PATH', '').split(':') + \
        [os.path.expanduser(d) for d in KNOWN_BIN_DIRS]
    # nvm keeps each Node version in its own folder; take the newest if the shell did not pick one.
    nvm = sorted(glob.glob(os.path.expanduser('~/.nvm/versions/node/*/bin')), key=version_key, reverse=True)
    entries += nvm[:1]
    merged = []
    seen = set()
    for entry in entries:
        entry = entry.strip()
        if entry and entry not in seen and os.path.isdir(entry):
            seen.add(entry)
            merged.append(entry)
    os.environ['PATH'] = ':'.join(merged)
    return os.environ['PATH']


def version_key(path):
    """Sort key that puts v20.10.0 after v9.1.0 (used on nvm's version folders)."""
    name = os.path.basename(os.path.dirname(path)) if path.endswith('/bin') else os.path.basename(path)
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', name)]


def which(name):
    return shutil.which(name, path=os.environ.get('PATH'))


@dataclass
class Captured:
    exit_code: int
    output: list
    timed_out: bool


def start_captured(command, cwd=None, env=None):
    """Starts a program with no window and captured output. Returns a handle for wait_captured."""
    return subprocess.Popen(command, cwd=cwd or None, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)


def wait_captured(handle, timeout=10):
    try:
        out, err = handle.communicate(timeout=max(0.01, timeout))
    except subprocess.TimeoutExpired:
        kill_tree(handle.pid)
        try:
            handle.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        return Captured(None, [], True)
    text = (out or b'').decode('utf-8', 'replace') + '\n' + (err or b'').decode('utf-8', 'replace')
    lines = [line.strip() for line in text.replace('\0', '').splitlines() if line.strip()]
    return Captured(handle.returncode, lines, False)


def run_captured(command, cwd=None, timeout=10, env=None):
    try:
        return wait_captured(start_captured(command, cwd=cwd, env=env), timeout)
    except OSError as error:
        return Captured(127, [str(error)], False)


def kill_tree(pid):
    """Ends a process and everything it started: its process group (servers get their own) and its descendants."""
    if not pid or pid == os.getpid():
        return
    processes = read_processes()
    victims = [pid] + descendant_ids(pid, processes)
    try:
        pgid = os.getpgid(pid)
        if pgid == pid:   # a group we created with start_new_session; never the widget's own group
            os.killpg(pgid, signal.SIGTERM)
    except OSError:
        pass
    for victim in victims:
        try:
            os.kill(victim, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(process_alive(v) for v in victims):
        time.sleep(0.1)
    for victim in victims:
        if process_alive(victim):
            try:
                os.kill(victim, signal.SIGKILL)
            except OSError:
                pass
    reap_children()


def process_alive(pid):
    try:
        with open('/proc/{}/stat'.format(pid)) as handle:
            return handle.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except OSError:
        return False


def descendant_ids(pid, processes):
    children = {}
    for process in processes.values():
        children.setdefault(process['ppid'], []).append(process['pid'])
    found = []
    stack = [pid]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child not in found:
                found.append(child)
                stack.append(child)
    return found


def read_small_text(path, max_bytes=262144):
    if not path:
        return None
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > max_bytes:
            return None
        with open(path, encoding='utf-8', errors='replace') as handle:
            return handle.read()
    except OSError:
        return None


def find_upward(folder, names, stop):
    """The nearest file of these names in folder or its parents, without going above stop."""
    current = folder
    stop_key = path_key(stop)
    while current:
        for name in names:
            candidate = os.path.join(current, name)
            if os.path.isfile(candidate):
                return candidate
        if path_key(current) == stop_key or current == '/':
            break
        current = os.path.dirname(current)
    return None


def port_from_text(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text or '', re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None

# ----------------------------------------------------------------------------------------------------------------- tools


def path_index():
    """Every program on PATH, by name, first one wins (the one the shell would run)."""
    index = {}
    for directory in os.environ.get('PATH', '').split(':'):
        if not directory or not os.path.isdir(directory):
            continue
        try:
            entries = os.scandir(directory)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if not entry.is_file() or not os.access(entry.path, os.X_OK):
                        continue
                except OSError:
                    continue
                index.setdefault(entry.name, entry.path)
    return index


def tool_catalog(config):
    with open(CATALOG_PATH, encoding='utf-8') as handle:
        tools = json.load(handle).get('tools', [])
    merged = {}
    for tool in list(tools) + list(config.tools):
        if tool and tool.get('name'):
            merged[str(tool['name']).lower()] = tool   # config entries override the catalog
    return list(merged.values())


def resolve_catalog_tool(tool, index):
    for command in tool.get('commands') or []:
        if command and command in index:
            return index[command]
    for pattern in tool.get('paths') or []:
        if not pattern:
            continue
        expanded = expand(pattern)
        if any(c in expanded for c in '*?['):
            hits = sorted(glob.glob(expanded), reverse=True)
            hits = [h for h in hits if os.path.isfile(h)]
            if hits:
                return hits[0]
        elif os.path.isfile(expanded):
            return expanded
    return None


def flatpak_apps():
    """Installed Flatpak apps, id -> version; Mint's Software Manager installs many editors this way."""
    flatpak = which('flatpak')
    if not flatpak:
        return {}
    result = run_captured([flatpak, 'list', '--app', '--columns=application,version'], timeout=8)
    apps = {}
    if result.exit_code == 0:
        for line in result.output:
            parts = line.split('\t') if '\t' in line else line.split(None, 1)
            if parts and '.' in parts[0]:
                apps[parts[0].strip()] = parts[1].strip() if len(parts) > 1 else ''
    return apps


def tool_install_command(tool):
    if tool.get('install'):
        return str(tool['install'])
    if tool.get('apt'):
        return 'sudo apt install -y {}'.format(tool['apt'])
    return None


def tool_inventory(config):
    """Every catalog tool that is installed (green, or yellow if it is there but does not report a version),
    plus required tools that are missing (red). Version commands run in parallel."""
    refresh_path(probe_shell=True)
    index = path_index()
    flatpaks = flatpak_apps()
    found = []
    for tool in tool_catalog(config):
        path = resolve_catalog_tool(tool, index)
        flatpak_version = None
        if not path and tool.get('flatpak') and tool['flatpak'] in flatpaks:
            path = 'flatpak run {}'.format(tool['flatpak'])
            flatpak_version = flatpaks[tool['flatpak']]
        required = str(tool['name']).lower() in config.required_tools
        if path or required:
            found.append({'tool': tool, 'path': path, 'required': required, 'handle': None, 'error': None,
                          'flatpak_version': flatpak_version})

    for entry in found:
        if entry['path'] and entry['tool'].get('versionArgs') and entry['flatpak_version'] is None:
            try:
                entry['handle'] = start_captured([entry['path']] + [str(a) for a in entry['tool']['versionArgs']],
                                                 env={**os.environ, 'LC_ALL': 'C.UTF-8', 'NO_COLOR': '1'})
            except OSError as error:
                entry['error'] = str(error)

    deadline = time.monotonic() + 15
    rows = []
    for entry in found:
        tool = entry['tool']
        state, version, detail = 'Green', '', ''
        if not entry['path']:
            state, detail = 'Red', 'Required, but not installed'
        elif entry['flatpak_version'] is not None:
            version = entry['flatpak_version']
            detail = 'Version {} (Flatpak)'.format(version) if version else 'Installed (Flatpak)'
        elif not tool.get('versionArgs'):
            detail = 'Installed'
        elif entry['handle']:
            result = wait_captured(entry['handle'], deadline - time.monotonic())
            first = next((line for line in result.output if re.search(r'[A-Za-z0-9]', line) and not re.match(r'^(picked up|warning)', line, re.I)), '')
            match = re.search(r'\d+(\.\d+)+', first)
            if result.timed_out:
                state, detail = 'Yellow', 'Installed, but did not report a version in time'
            elif result.exit_code == 0 and first:
                detail = first
            elif match:
                detail = first
            else:
                state, detail = 'Yellow', first or 'Installed, but did not report a version'
            if match:
                version = match.group(0)
        else:
            state, detail = 'Yellow', 'Installed, but could not run: {}'.format(entry['error'])

        rows.append({
            'Name': str(tool['name']),
            'Category': str(tool.get('category', '')),
            'State': state,
            'Version': version,
            'Detail': detail,
            'Path': entry['path'],
            'Open': str(tool.get('open', '')),
            'Install': tool_install_command(tool) if state != 'Green' else None,
            'Required': entry['required'],
        })
    return rows

# --------------------------------------------------------------------------------------------------- server discovery


BLOCKED_ROOTS = ['/', '/bin', '/boot', '/dev', '/etc', '/lib', '/lib64', '/proc', '/run', '/sbin', '/snap', '/sys', '/usr',
                 '/var', HOME, os.path.join(HOME, '.cache'), os.path.join(HOME, '.config'), os.path.join(HOME, '.local')]


def scan_groups(config):
    """Folders to search, each a 'group' that labels the servers found under it."""
    groups = {}
    blocked = [path_key(b) for b in BLOCKED_ROOTS]

    def add(path, is_root):
        if not path or not os.path.isdir(path):
            return
        full = os.path.abspath(path)
        key = path_key(full)
        # Never sweep a whole home folder, the root or a system folder.
        if key in blocked or key == '/tmp/claude' or key.startswith('/tmp/claude/'):
            return
        if key not in groups:
            groups[key] = {'Path': full, 'IsRoot': is_root}

    for root in config.project_roots:
        add(root, True)
    if config.include_claude_projects:
        claude_json = os.path.join(HOME, '.claude.json')
        if os.path.isfile(claude_json):
            try:
                with open(claude_json, encoding='utf-8') as handle:
                    for project in (json.load(handle).get('projects') or {}):
                        add(project, False)
            except (OSError, ValueError):
                pass
    return list(groups.values())


def scan_folders(config, groups):
    visited = set()
    folders = []
    stack = [(group['Path'], 0) for group in groups]
    while stack and len(folders) < MAX_SCAN_FOLDERS:
        path, depth = stack.pop()
        key = path_key(path)
        if key in visited:
            continue
        visited.add(key)
        folders.append(path)
        if depth >= config.scan_depth:
            continue
        try:
            children = list(os.scandir(path))
        except OSError:
            continue
        for child in children:
            name = child.name
            try:
                if not child.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if name.startswith('.') or name in SKIP_FOLDERS:
                continue
            if any(fnmatch.fnmatch(name, pattern) for pattern in config.exclude_folders):
                continue
            stack.append((child.path, depth + 1))
    return folders


def new_server(**fields):
    values = {'Kind': 'process', 'Executable': '', 'Arguments': [], 'Port': 0, 'NeedsPort': False, 'Signature': '',
              'Runtime': '', 'Service': '', 'Registered': False, 'Framework': '', 'Source': '', 'Dir': '', 'Bin': '',
              'InstallDir': '', 'InstallCommand': ''}
    values.update(fields)
    return values


NODE_FRAMEWORKS = [
    (r'\bvite\s+preview\b', 'Vite preview', 4173, 'vite'),
    (r'\bvite\b(?!\s+build)', 'Vite', 5173, 'vite'),
    (r'\bnext\s+(dev|start)\b', 'Next.js', 3000, None),
    (r'\b(nuxt|nuxi)\s+dev\b', 'Nuxt', 3000, None),
    (r'\bastro\s+dev\b', 'Astro', 4321, None),
    (r'react-scripts\s+start', 'Create React App', 3000, None),
    (r'\bng\s+serve\b', 'Angular', 4200, None),
    (r'webpack(-dev-server|\s+serve)', 'webpack', 8080, None),
    (r'\bgatsby\s+develop\b', 'Gatsby', 8000, None),
    (r'\bremix\s+dev\b', 'Remix', 3000, None),
    (r'\bdocusaurus\s+start\b', 'Docusaurus', 3000, None),
    (r'\bstorybook\s+dev\b|start-storybook', 'Storybook', 6006, None),
    (r'\bwrangler\s+dev\b', 'Wrangler', 8787, None),
    (r'\bexpo\s+start\b', 'Expo', 8081, None),
    (r'\beleventy\b.*--serve', 'Eleventy', 8080, None),
    (r'\bparcel\b(?!\s+build)', 'Parcel', 1234, None),
    (r'\bhttp-server\b', 'http-server', 8080, None),
    (r'^\s*serve\b', 'serve', 3000, None),
]


def find_node_server(folder, group_path, files):
    if 'package.json' not in files:
        return None
    text = read_small_text(files['package.json'])
    if not text:
        return None
    try:
        package = json.loads(text)
    except ValueError:
        return None
    scripts = package.get('scripts') if isinstance(package, dict) else None
    if not isinstance(scripts, dict):
        return None

    for script_name in ('dev', 'start', 'serve', 'develop', 'storybook'):
        command = str(scripts.get(script_name) or '')
        if not command:
            continue
        # Scripts written for the Windows shell (set X=... &&) do not run from Linux npm.
        if re.search(r'(^|&&|;)\s*set\s+\w+=', command, re.I):
            continue

        framework = next((f for f in NODE_FRAMEWORKS if re.search(f[0], command, re.I)), None)
        port = port_from_text(command, [r'(?:--port|-p)[=\s]+(\d{2,5})', r'\bPORT=(\d{2,5})'])
        needs_port = False

        if framework:
            name = framework[1]
            if not port and framework[3] == 'vite':
                config_file = next((k for k in files if fnmatch.fnmatch(k, 'vite.config.*')), None)
                config_text = read_small_text(files[config_file]) if config_file else ''
                port = port_from_text(config_text, [r'\bport\s*:\s*(\d{2,5})', r'rawPort\s*\?\s*Number\(rawPort\)\s*:\s*(\d{2,5})',
                                                    r'process\.env\.PORT\s*(?:\|\||\?\?)\s*["\']?(\d{2,5})'])
                if not port and re.search(r'process\.env\.PORT', config_text or ''):
                    needs_port = True
            if not port and not needs_port:
                port = framework[2]
        else:
            match = re.match(r'^(?:cross-env\s+(?:\S+=\S+\s+)*)?(?:tsx|node|ts-node|nodemon|bun)\s+(?:watch\s+|--watch\s+)?(?:--\S+\s+)*([^\s]+\.[cm]?[jt]s)\b', command)
            if not match:
                continue
            name = 'Node server'
            server_text = read_small_text(os.path.join(folder, match.group(1)))
            if not port:
                port = port_from_text(server_text, [
                    r'(?:const|let|var)\s+PORT\s*=\s*(?:Number\(|parseInt\()?\s*(?:process\.env\.PORT\s*(?:\|\||\?\?)\s*)?["\']?(\d{2,5})',
                    r'\.listen\(\s*(\d{2,5})'])
            if not port:
                needs_port = True

        lock = find_upward(folder, ['pnpm-lock.yaml', 'yarn.lock', 'bun.lockb', 'bun.lock', 'package-lock.json'], group_path)
        lock_name = os.path.basename(lock or '')
        manager = 'pnpm' if lock_name == 'pnpm-lock.yaml' else 'yarn' if lock_name == 'yarn.lock' \
            else 'bun' if lock_name.startswith('bun.lock') else 'npm'
        # The program the script runs (vite, tsx, next...) must exist in node_modules/.bin.
        bin_match = re.match(r'^(?:cross-env\s+(?:\S+=\S+\s+)*)?([\w@./-]+)', command)
        bin_name = bin_match.group(1) if bin_match else ''
        if bin_name in ('node', 'npm', 'pnpm', 'yarn', 'bun', 'npx'):
            bin_name = ''
        return new_server(
            Name='{} ({} run {})'.format(name, manager, script_name), Framework=name, Dir=folder, Executable=manager,
            Arguments=['run', script_name], Port=int(port or 0), NeedsPort=needs_port, Runtime='node',
            Source='package.json "{}"'.format(script_name), Bin=bin_name,
            InstallDir=os.path.dirname(lock) if lock else folder, InstallCommand='{} install'.format(manager))
    return None


def find_python_servers(folder, files):
    py_files = sorted(k for k in files if k.endswith('.py') and not k.startswith('test_'))[:40]
    if not py_files:
        return []
    venv = find_upward(folder, ['.venv/bin/python', 'venv/bin/python'], os.path.dirname(folder))
    python = venv or 'python3'

    if 'manage.py' in files:
        return [new_server(Name='Django (manage.py runserver)', Framework='Django', Dir=folder, Executable=python,
                           Arguments=['manage.py', 'runserver', '127.0.0.1:8000'], Port=8000, Signature='manage.py',
                           Runtime='python', Source='manage.py')]

    found = []
    for file_name in py_files:
        path = files[file_name]
        text = read_small_text(path)
        if not text or not re.search(r'__name__\s*==\s*["\']__main__["\']', text, re.I):
            continue
        if re.search(r'uvicorn\.run\(|import uvicorn', text, re.I):
            kind = ('uvicorn', 8000)
        elif re.search(r'\bflask\b', text, re.I) and re.search(r'\bapp\.run\(', text, re.I):   # GTK apps call app.run() too
            kind = ('Flask', 5000)
        elif re.search(r'web\.run_app\(', text, re.I):
            kind = ('aiohttp', 8080)
        elif re.search(r'serve_forever\(|HTTPServer\(|make_server\(', text, re.I):
            kind = ('Python HTTP', 8000)
        else:
            continue
        port = port_from_text(text, [
            r'["\']--port["\'][^)\r\n]*?default\s*=\s*["\']?(\d{2,5})', r'--port\s+\[?(\d{2,5})', r'else\s+["\'](\d{4,5})["\']',
            r'\bport\s*=\s*(?:int\()?\s*(?:os\.environ\.get\(\s*["\']PORT["\']\s*,\s*)?["\']?(\d{2,5})',
            r'\bPORT\s*=\s*(?:int\()?\s*["\']?(\d{2,5})', r'\(\s*["\'][\w.]*["\']\s*,\s*(\d{2,5})\s*\)']) or kind[1]
        base = os.path.basename(path)
        found.append(new_server(Name='{} ({})'.format(kind[0], base), Framework=kind[0], Dir=folder, Executable=python,
                                Arguments=[base], Port=port, Signature=base, Runtime='python', Source=base))
    return found


def find_compose_servers(folder, files):
    for file_name in ('compose.yaml', 'compose.yml', 'docker-compose.yml', 'docker-compose.yaml'):
        if file_name not in files:
            continue
        text = read_small_text(files[file_name])
        if not text:
            continue
        found = []
        service, in_services, in_ports, service_indent = None, False, False, -1
        for line in text.splitlines():
            if re.match(r'^\s*(#|$)', line):
                continue
            indent = len(line) - len(line.lstrip())
            if indent == 0:
                in_services = bool(re.match(r'^services\s*:', line, re.I))
                service, service_indent = None, -1
                continue
            if not in_services:
                continue
            if service_indent < 0 or indent <= service_indent:
                match = re.match(r'^\s*([A-Za-z0-9_.-]+)\s*:\s*$', line)
                if match:
                    service, service_indent, in_ports = match.group(1), indent, False
                continue
            if re.match(r'^\s*ports\s*:', line, re.I):
                in_ports = True
                continue
            if in_ports and re.match(r'^\s*[A-Za-z_]+\s*:', line) and 'published' not in line:
                in_ports = False
            if not in_ports or not service:
                continue
            port = port_from_text(line, [r'^\s*-\s*["\']?(?:[\d.]+:)?(\d{2,5}):\d{2,5}', r'published\s*:\s*["\']?(\d{2,5})'])
            if port:
                found.append(new_server(Kind='compose', Name='{} (docker compose)'.format(service), Framework='Docker {}'.format(service),
                                        Dir=folder, Service=service, Port=port, Runtime='docker', Source=file_name))
        return found
    return []


def find_dotnet_servers(folder):
    path = os.path.join(folder, 'Properties', 'launchSettings.json')
    text = read_small_text(path) if os.path.isfile(path) else None
    if not text:
        return []
    try:
        settings = json.loads(text)
    except ValueError:
        return []
    for name, profile in (settings.get('profiles') or {}).items():
        if not isinstance(profile, dict) or profile.get('commandName') != 'Project' or not profile.get('applicationUrl'):
            continue
        port = port_from_text(str(profile['applicationUrl']), [r'http://[^:;]+:(\d{2,5})', r'https://[^:;]+:(\d{2,5})'])
        if not port:
            continue
        return [new_server(Name='.NET ({})'.format(name), Framework='.NET', Dir=folder, Executable='dotnet',
                           Arguments=['run', '--launch-profile', name], Port=port, Signature=os.path.basename(folder),
                           Runtime='dotnet', Source='launchSettings.json')]
    return []


def find_launch_json_servers(folder):
    path = os.path.join(folder, '.claude', 'launch.json')
    text = read_small_text(path) if os.path.isfile(path) else None
    if not text:
        return []
    try:
        launch = json.loads(text)
    except ValueError:
        return []
    found = []
    for config in launch.get('configurations') or []:
        arguments = [str(a) for a in (config.get('runtimeArgs') or [])]
        executable = str(config.get('runtimeExecutable') or '')
        # Servers run from a Claude scratchpad are throwaway test copies, not apps.
        if not config.get('port') or any('/tmp/claude/' in a or '/scratch-workspaces/' in a for a in arguments + [executable]):
            continue
        found.append(new_server(Name=str(config.get('name') or 'Claude server'), Framework='Claude Code', Dir=folder,
                                Executable=executable, Arguments=arguments, Port=int(config['port']), Registered=True,
                                Signature=next((a for a in arguments if not a.startswith('-')), ''), Source='.claude/launch.json'))
    return found


def is_project_folder(path):
    for marker in ('.git', '.claude'):
        if os.path.isdir(os.path.join(path, marker)):
            return True
    for marker in ('package.json', 'pyproject.toml', 'requirements.txt', 'CLAUDE.md', 'README.md', 'docker-compose.yml',
                   'compose.yaml', 'go.mod', 'Cargo.toml'):
        if os.path.isfile(os.path.join(path, marker)):
            return True
    return False


def group_path_for(folder, group_keys, cache):
    """Which project a folder belongs to: the deepest Claude project containing it, or, under a root, the first
    folder on the way down that looks like a project (has .git, a README, package.json, ...). Whichever is deeper."""
    folder_key = path_key(folder)
    owners = [g for g in group_keys if folder_key == g['Key'] or folder_key.startswith(g['Key'] + '/')]
    owner = max(owners, key=lambda g: len(g['Key']))
    if not owner['IsRoot'] or folder_key == owner['Key']:
        return owner['Path']

    parts = os.path.relpath(folder, owner['Path']).split('/')
    path = owner['Path']
    for part in parts:
        path = os.path.join(path, part)
        key = path_key(path)
        if key not in cache:
            cache[key] = is_project_folder(path)
        if cache[key]:
            return path
    return os.path.join(owner['Path'], parts[0])


def stable_port(key, taken):
    value = 0
    for char in key:
        value = (value * 31 + ord(char)) % 1000003
    port = 5200 + (value % 700)
    while port in taken:
        port += 1
    return port


def find_dev_servers(config):
    groups = scan_groups(config)
    if not groups:
        return []
    folders = scan_folders(config, groups)
    group_keys = [{'Key': path_key(g['Path']), 'Path': g['Path'], 'IsRoot': g['IsRoot']} for g in groups]

    project_root_cache = {}
    candidates = []
    for folder in folders:
        files = {}
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_file():
                            files[entry.name] = entry.path
                    except OSError:
                        pass
        except OSError:
            continue
        found = find_launch_json_servers(folder) + find_python_servers(folder, files) + \
            find_compose_servers(folder, files) + find_dotnet_servers(folder)
        folder_key = path_key(folder)
        group_path = None
        if found or 'package.json' in files:
            group_path = group_path_for(folder, group_keys, project_root_cache)
            node = find_node_server(folder, group_path, files)
            if node:
                found.append(node)
        for candidate in found:
            relative = '' if folder_key == path_key(group_path) else os.path.relpath(folder, group_path)
            title = config.titles.get(path_key(group_path))
            if not title:
                # Nested projects read better with their parent: "EPiC/source", not "source".
                root = next((g for g in group_keys if g['IsRoot'] and path_key(group_path).startswith(g['Key'] + '/')), None)
                parts = os.path.relpath(group_path, root['Path']).split('/') if root else []
                title = '{}/{}'.format(parts[-2], parts[-1]) if len(parts) >= 3 else os.path.basename(group_path)
            if candidate['Registered']:
                label = candidate['Name']
            elif relative:
                label = '{} - {}'.format(relative, candidate['Framework'])
            else:
                label = candidate['Framework']
            candidate['Project'] = title
            candidate['GroupPath'] = group_path
            candidate['Label'] = label
            candidate['Key'] = '{}|{}'.format(folder_key, candidate['Name']).lower()
            candidates.append(candidate)

    # A Claude launch.json entry is the authority for its port: drop code guesses on that port in the same project,
    # or in the folder its command runs from (launch.json often lives apart from the code it starts).
    registered = []
    for c in candidates:
        if c['Registered']:
            script_dir = path_key(os.path.dirname(c['Signature'])) if c['Signature'].startswith('/') else ''
            registered.append({'Port': int(c['Port']), 'Group': path_key(c['GroupPath']), 'ScriptDir': script_dir})

    def shadowed(candidate):
        if candidate['Registered']:
            return False
        dir_key = path_key(candidate['Dir'])
        return any(r['Port'] == candidate['Port'] and (r['Group'] == path_key(candidate['GroupPath']) or
                   (r['ScriptDir'] and (r['ScriptDir'] == dir_key or r['ScriptDir'].startswith(dir_key + '/'))))
                   for r in registered)
    candidates = [c for c in candidates if not shadowed(c)]

    # Same folder, same port: keep one.
    seen = set()
    unique = []
    for c in candidates:
        if c['NeedsPort']:
            unique.append(c)
            continue
        key = '{}|{}|{}'.format(path_key(c['Dir']), c['Port'], c['Kind'])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    candidates = unique

    # Ports: an explicit setting in showstack.json, else what the code says, else a stable port of its own.
    taken = {int(c['Port']) for c in candidates if c['Port']}
    for c in candidates:
        if c['Key'] in config.ports:
            c['Port'] = config.ports[c['Key']]
            c['NeedsPort'] = True
        elif c['NeedsPort']:
            c['Port'] = stable_port(c['Key'], taken)
            taken.add(c['Port'])

    return sorted(candidates, key=lambda c: (c['Project'].lower(), c['Label'].lower()))

# ------------------------------------------------------------------------------------------------------------ inventory


def update_inventory(servers_only=False):
    """Full scan of tools and servers, saved so the widget can show it instantly next time."""
    config = get_config()
    previous = get_inventory()
    if servers_only and previous:
        refresh_path(probe_shell=True)
        tools = list(previous['Tools'])
    else:
        tools = tool_inventory(config)
    inventory = {'Tools': tools, 'Servers': find_dev_servers(config), 'ScannedAt': datetime.datetime.now().isoformat()}
    write_state(INVENTORY_PATH, inventory)
    return inventory


def get_inventory():
    inventory = read_state(INVENTORY_PATH, None)
    if inventory:
        inventory['Tools'] = list(inventory.get('Tools') or [])
        inventory['Servers'] = [s for s in (inventory.get('Servers') or []) if s]
        for server in inventory['Servers']:
            server['Arguments'] = list(server.get('Arguments') or [])
    return inventory

# --------------------------------------------------------------------------------------------------------- live status


def status_item(section, name, state, detail='', group='', action='', url='', server=None):
    return {'Section': section, 'Group': group, 'Name': name, 'State': state, 'Detail': detail, 'Action': action,
            'Url': url, 'Server': server}


_children = []


def reap_children():
    for child in list(_children):
        if child.poll() is not None:
            _children.remove(child)


def read_processes():
    processes = {}
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open('/proc/{}/stat'.format(pid)) as handle:
                stat = handle.read()
            name = stat[stat.index('(') + 1:stat.rindex(')')]
            fields = stat[stat.rindex(')') + 2:].split()
            with open('/proc/{}/cmdline'.format(pid), 'rb') as handle:
                cmdline = handle.read().replace(b'\0', b' ').decode('utf-8', 'replace').strip()
        except (OSError, ValueError, IndexError):
            continue
        processes[pid] = {'pid': pid, 'ppid': int(fields[1]), 'name': name, 'cmdline': cmdline, 'state': fields[0]}
    return processes


def runtime_name(process):
    """'python' for python3.12, 'node' for /home/me/.nvm/.../node: the program behind a listening socket."""
    argv0 = process['cmdline'].split(' ', 1)[0] if process['cmdline'] else ''
    name = os.path.basename(argv0) or process['name']
    return re.sub(r'[\d.]+$', '', name.lower())


def _local_address(hex_address, ipv6):
    if ipv6:
        raw = bytes.fromhex(hex_address)
        # The kernel writes each 32-bit word in host order.
        words = [raw[i:i + 4][::-1] for i in range(0, 16, 4)]
        address = b''.join(words)
        if address == b'\0' * 16 or address == b'\0' * 15 + b'\1':
            return True
        if address[:12] == b'\0' * 10 + b'\xff\xff':
            return address[12] in (0, 127)
        return False
    raw = bytes.fromhex(hex_address)[::-1]
    return raw[0] in (0, 127)


def listening_ports():
    """Port -> listening socket inode, for sockets reachable at localhost."""
    ports = {}
    for path, ipv6 in (('/proc/net/tcp', False), ('/proc/net/tcp6', True)):
        try:
            with open(path) as handle:
                lines = handle.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != '0A':
                continue
            address, port_hex = fields[1].rsplit(':', 1)
            try:
                if not _local_address(address, ipv6):
                    continue
            except ValueError:
                continue
            ports.setdefault(int(port_hex, 16), set()).add(fields[9])
    return ports


def port_listening(port):
    return port in listening_ports()


def process_snapshot():
    reap_children()
    processes = read_processes()
    wanted = listening_ports()
    inodes = {}
    for port, port_inodes in wanted.items():
        for inode in port_inodes:
            inodes[inode] = port
    ports = {}
    for pid in processes:
        try:
            fds = os.listdir('/proc/{}/fd'.format(pid))
        except OSError:
            continue   # another user's process: its sockets stay "another program"
        for fd in fds:
            try:
                target = os.readlink('/proc/{}/fd/{}'.format(pid, fd))
            except OSError:
                continue
            if target.startswith('socket:[') and target[8:-1] in inodes:
                ports.setdefault(inodes[target[8:-1]], processes[pid])
    for port in wanted:
        ports.setdefault(port, UNKNOWN_PROCESS)
    return {'Processes': processes, 'Ports': ports}


def ancestor_ids(process, processes):
    ids = set()
    current = process
    for _ in range(15):
        if not current or current['pid'] in ids:
            break
        ids.add(current['pid'])
        current = processes.get(current['ppid'])
    return ids


def docker_state():
    docker = which('docker')
    if not docker:
        return {'Path': None, 'Running': False, 'Version': '', 'Containers': {}, 'Error': ''}
    version = run_captured([docker, 'version', '--format', '{{.Server.Version}}'], timeout=8)
    containers = {}
    if version.exit_code == 0:
        for line in run_captured([docker, 'ps', '--format', '{{.Labels}}'], timeout=8).output:
            directory = re.search(r'com\.docker\.compose\.project\.working_dir=([^,]+)', line)
            service = re.search(r'com\.docker\.compose\.service=([^,]+)', line)
            if directory and service:
                containers['{}|{}'.format(path_key(directory.group(1)), service.group(1).lower())] = True
    return {'Path': docker, 'Running': version.exit_code == 0, 'Version': version.output[0] if version.output else '',
            'Containers': containers, 'Error': ' '.join(version.output) if version.exit_code != 0 else ''}


def systemd_unit(unit, user=False):
    """('loaded'|'not-found'|None, 'active'|'inactive'|'activating'|...) for a systemd unit."""
    systemctl = which('systemctl')
    if not systemctl:
        return None, None
    command = [systemctl] + (['--user'] if user else []) + ['show', '-p', 'LoadState', '-p', 'ActiveState', unit]
    result = run_captured(command, timeout=5)
    values = dict(line.split('=', 1) for line in result.output if '=' in line)
    return values.get('LoadState'), values.get('ActiveState')


def docker_start_method():
    """How Docker can be started on this machine: ('desktop'|'rootless'|'system'|None, active state)."""
    for method, unit, user in (('desktop', 'docker-desktop', True), ('rootless', 'docker', True), ('system', 'docker', False)):
        load, active = systemd_unit(unit, user)
        if load == 'loaded':
            return method, active
    return None, None


def docker_status_item(docker):
    if not docker['Path']:
        return None
    if docker['Running']:
        return status_item('Services', 'Docker', 'Green', 'Engine {} running'.format(docker['Version']))
    if 'permission denied' in docker['Error'].lower():
        return status_item('Services', 'Docker', 'Red',
                           'Docker is running, but your user may not use it. Run: sudo usermod -aG docker $USER, then log out and in.')
    method, active = docker_start_method()
    if method is None:
        return status_item('Services', 'Docker', 'Yellow', 'Docker CLI only; no Docker service found on this machine')
    if active == 'activating' or (active == 'active' and method == 'desktop'):
        return status_item('Services', 'Docker', 'Yellow', 'Docker is starting')
    if active == 'active':
        return status_item('Services', 'Docker', 'Yellow', 'The Docker service is running, but the CLI cannot reach it: {}'.format(docker['Error']))
    how = {'desktop': 'Docker Desktop', 'rootless': 'rootless Docker', 'system': 'the Docker service (asks for your password)'}[method]
    return status_item('Services', 'Docker', 'Yellow', 'Stopped; Start runs {}'.format(how), action='Start')


def started_record():
    record = read_state(STARTED_PATH, None) or {}
    return {str(k): int(v) for k, v in record.items()}


def server_ownership(server, owner, snapshot, started, claims):
    """Is the process listening on this server's port this server? 'Yes', 'Likely', or 'No'."""
    ancestors = ancestor_ids(owner, snapshot['Processes'])
    launcher = started.get(server['Key'])
    if launcher and launcher in ancestors:
        return 'Yes'
    command_line = owner['cmdline']
    if server.get('Signature') and server['Signature'] in command_line:
        return 'Yes'
    if server.get('Dir') and server['Dir'] in command_line:
        return 'Yes'
    runtime = runtime_name(owner)
    if server.get('Runtime') and runtime.startswith(server['Runtime']) and claims.get(int(server['Port'])) == 1:
        return 'Likely'
    return 'No'


def server_dependencies_ok(server):
    """False when a Node project's packages are missing."""
    if not server.get('Bin'):
        return True
    return bool(find_upward(server['Dir'], ['node_modules/.bin/{}'.format(server['Bin'])], server['GroupPath']))


def server_status_items(servers, snapshot, docker, hidden):
    started = started_record()
    visible = [s for s in servers if s and s['Key'] not in hidden]
    claims = {}
    for server in visible:
        claims[int(server['Port'])] = 1 + claims.get(int(server['Port']), 0)

    rows = []
    owned = set()
    for server in visible:
        port = int(server['Port'])
        common = {'section': 'Servers', 'group': server['Project'], 'name': server['Label'],
                  'url': 'http://localhost:{}'.format(port), 'server': server}
        where = 'port {} - {}'.format(port, server['Source'])
        owner = snapshot['Ports'].get(port)

        if server['Kind'] == 'compose':
            if not docker['Running']:
                rows.append(status_item(state='Yellow', detail='Needs Docker - {}'.format(where), **common))
            elif '{}|{}'.format(path_key(server['Dir']), server['Service'].lower()) in docker['Containers']:
                owned.add(port)
                rows.append(status_item(state='Green', detail='Running - {}'.format(where), action='Restart', **common))
            elif owner:
                rows.append(status_item(state='Yellow', detail='Port {} is in use by {} - {}'.format(port, owner['name'], server['Source']), **common))
            else:
                rows.append(status_item(state='Yellow', detail='Stopped - {}'.format(where), action='Start', **common))
            continue

        if not owner:
            if not server_dependencies_ok(server):
                rows.append(status_item(state='Yellow', action='Install', **common,
                                        detail="Packages not installed; Install runs '{}' - {}".format(server['InstallCommand'], where)))
                continue
            note = ' (port picked by widget)' if server['NeedsPort'] else ''
            rows.append(status_item(state='Yellow', detail='Stopped - {}{}'.format(where, note), action='Start', **common))
            continue
        ownership = server_ownership(server, owner, snapshot, started, claims)
        if ownership == 'Yes':
            owned.add(port)
            rows.append(status_item(state='Green', detail='Running (PID {}) - {}'.format(owner['pid'], where), action='Restart', **common))
        elif ownership == 'Likely':
            owned.add(port)
            rows.append(status_item(state='Green', detail='Running (PID {}, probably this one) - {}'.format(owner['pid'], where), action='Restart', **common))
        else:
            state = 'Red' if server['Registered'] else 'Yellow'
            who = '{} (PID {})'.format(owner['name'], owner['pid']) if owner['pid'] else owner['name']
            rows.append(status_item(state=state, detail='Port {} is in use by {} - {}'.format(port, who, server['Source']), **common))

    for port in sorted(snapshot['Ports']):
        if port in owned:
            continue
        owner = snapshot['Ports'][port]
        if not owner['pid']:
            continue
        runtime = runtime_name(owner)
        if runtime not in DEV_RUNTIMES:
            continue
        command_line = re.sub(r'\s+', ' ', owner['cmdline'])
        what = 'Claude test copy' if '/tmp/claude/' in command_line else runtime
        rows.append(status_item('Other local servers', 'Port {}'.format(port), 'Green',
                                '{} (PID {}): {}'.format(what, owner['pid'], command_line), url='http://localhost:{}'.format(port)))
    return rows


def hidden_servers():
    return {str(key) for key in (read_state(HIDDEN_PATH, []) or []) if key}


def get_status(inventory=None):
    """Live rows for the widget, using the saved inventory."""
    refresh_path()
    if not inventory:
        inventory = get_inventory()
    if not inventory:
        inventory = {'Tools': [], 'Servers': [], 'ScannedAt': None}
    hidden = hidden_servers()
    snapshot = process_snapshot()
    docker = docker_state()
    rows = []
    docker_row = docker_status_item(docker)
    if docker_row:
        rows.append(docker_row)
    rows += server_status_items(inventory['Servers'], snapshot, docker, hidden)
    return {
        'Tools': list(inventory['Tools']),
        'Rows': rows,
        'ScannedAt': inventory.get('ScannedAt'),
        'Hidden': sum(1 for s in inventory['Servers'] if s and s['Key'] in hidden),
    }

# ------------------------------------------------------------------------------------------------------------- actions


def wait_for_port(port, timeout, process=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_listening(port):
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def resolve_executable(executable):
    if os.path.isfile(executable) and os.access(executable, os.X_OK):
        return executable
    return which(executable)


def start_server(server, timeout=60):
    """Starts a server, or restarts it if it is already running. Raises with the reason when it cannot."""
    refresh_path()
    label = '{}: {}'.format(server['Project'], server['Label'])
    port = int(server['Port'])

    if server['Kind'] == 'compose':
        docker = docker_state()
        if not docker['Running']:
            raise RuntimeError('{} needs Docker, which is not running. Start Docker first.'.format(label))
        running = '{}|{}'.format(path_key(server['Dir']), server['Service'].lower()) in docker['Containers']
        arguments = ['compose', 'restart', server['Service']] if running else ['compose', 'up', '-d', server['Service']]
        result = run_captured([docker['Path']] + arguments, cwd=server['Dir'], timeout=180)
        if result.exit_code != 0:
            raise RuntimeError('{} did not start: {}'.format(label, ' '.join(result.output[-3:])))
        return

    snapshot = process_snapshot()
    owner = snapshot['Ports'].get(port)
    started = started_record()
    if owner:
        if server_ownership(server, owner, snapshot, started, {port: 1}) == 'No':
            who = '{} (PID {})'.format(owner['name'], owner['pid']) if owner['pid'] else owner['name']
            raise RuntimeError('Port {} is in use by {}, which is not {}. Not touching it.'.format(port, who, label))
        kill_tree(owner['pid'])
    if started.get(server['Key']):
        kill_tree(started[server['Key']])
    deadline = time.monotonic() + 10
    while port_listening(port) and time.monotonic() < deadline:
        time.sleep(0.3)

    executable = resolve_executable(server['Executable'])
    if not executable:
        raise RuntimeError('{} is not installed or not on PATH, so {} cannot start.'.format(server['Executable'], label))

    log_base = os.path.join(STATE_DIR, re.sub(r'[^\w.-]', '_', '{}-{}'.format(server['Project'], server['Label'])))
    env = dict(os.environ)
    if server['NeedsPort']:
        env['PORT'] = str(port)
    # Its own session: closing the widget does not take the server with it, and Restart can end the whole tree.
    with open(log_base + '.out.log', 'wb') as out, open(log_base + '.err.log', 'wb') as err:
        try:
            process = subprocess.Popen([executable] + list(server['Arguments']), cwd=server['Dir'], env=env,
                                       stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
        except OSError as error:
            raise RuntimeError('{} could not start: {}'.format(label, error))
    _children.append(process)

    started[server['Key']] = process.pid
    write_state(STARTED_PATH, started)

    if not wait_for_port(port, timeout, process):
        tail = tail_lines(log_base + '.err.log') or tail_lines(log_base + '.out.log')
        raise RuntimeError('{} did not start on port {}. {} (log: {}.err.log)'.format(label, port, ' '.join(tail), log_base))


def tail_lines(path, count=3):
    text = read_small_text(path, 4 * 1024 * 1024) or ''
    return [line for line in text.splitlines() if line.strip()][-count:]


def start_docker_engine(timeout=120):
    status = docker_status_item(docker_state())
    if not status or status['Action'] != 'Start':
        return
    method, _ = docker_start_method()
    systemctl = which('systemctl')
    if method == 'desktop':
        command = [systemctl, '--user', 'start', 'docker-desktop']
    elif method == 'rootless':
        command = [systemctl, '--user', 'start', 'docker']
    elif which('pkexec'):
        command = [which('pkexec'), systemctl, 'start', 'docker']
    else:
        run_in_terminal('Docker', 'sudo systemctl start docker')
        command = None
    if command:
        result = run_captured(command, timeout=60)
        if result.exit_code not in (0, None):
            raise RuntimeError('Docker did not start: {}'.format(' '.join(result.output[-3:]) or 'cancelled'))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(2)
        if docker_state()['Running']:
            return


def restart_all():
    """Docker if stopped; every Claude-registered server (started or restarted); every other server that is running."""
    problems = []
    start_docker_engine()
    for row in get_status()['Rows']:
        server = row.get('Server')
        if server and (server['Registered'] or row['Action'] == 'Restart'):
            try:
                start_server(server)
            except Exception as error:   # noqa: BLE001 - every failure is reported together
                problems.append(str(error))
    if problems:
        raise RuntimeError('\n'.join(problems))


def hide_server(key):
    hidden = [k for k in (read_state(HIDDEN_PATH, []) or []) if k]
    if key not in hidden:
        hidden.append(key)
    write_state(HIDDEN_PATH, hidden)


def clear_hidden_servers():
    write_state(HIDDEN_PATH, [])


def terminal_command():
    """The user's terminal emulator and how to hand it a script to run."""
    for name in TERMINALS:
        path = which(name)
        if path:
            return path, name
    return None, None


def run_in_terminal(name, command, working_directory=None):
    """Runs a command in a terminal window the user can watch and answer (sudo, installer prompts), and returns
    when that window is closed. Terminals like gnome-terminal return at once, so the script leaves a marker file."""
    terminal, terminal_name = terminal_command()
    if not terminal:
        raise RuntimeError('No terminal emulator found (looked for {}).'.format(', '.join(TERMINALS)))
    work = tempfile.mkdtemp(prefix='showstack-')
    done = os.path.join(work, 'done')
    script = os.path.join(work, 'run.sh')
    location = 'cd {} || exit 1\n'.format(shlex.quote(working_directory)) if working_directory else ''
    with open(script, 'w', encoding='utf-8') as handle:
        handle.write(
            '#!/usr/bin/env bash\n'
            'DONE={done}\n'
            'trap \'touch "$DONE"\' EXIT\n'
            'trap \'touch "$DONE"; exit 130\' HUP INT TERM\n'
            '{location}'
            'printf \'\\033[36mInstalling %s\\033[0m\\n\' {name}\n'
            'printf \'\\033[90m> %s   (in %s)\\033[0m\\n\\n\' {shown} "$PWD"\n'
            'bash -lic {command}\n'
            'status=$?\n'
            'echo\n'
            'if [ $status -ne 0 ]; then printf \'\\033[31mThe command ended with exit code %s\\033[0m\\n\' "$status"; fi\n'
            'read -rp \'Finished. Press Enter to close this window; ShowStack then checks again. \'\n'
            .format(done=shlex.quote(done), location=location, name=shlex.quote(name), shown=shlex.quote(command),
                    command=shlex.quote(command)))
    os.chmod(script, 0o700)
    if terminal_name == 'kitty':
        argv = [terminal, script]
    elif terminal_name == 'gnome-terminal':
        argv = [terminal, '--', script]
    else:
        argv = [terminal, '-e', script]
    try:
        process = subprocess.Popen(argv, cwd=working_directory or HOME, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as error:
        raise RuntimeError('Could not open a terminal: {}'.format(error))
    started = time.monotonic()
    while not os.path.exists(done) and time.monotonic() - started < 2 * 3600:
        if process.poll() not in (None, 0) and time.monotonic() - started > 3:
            shutil.rmtree(work, ignore_errors=True)
            raise RuntimeError('The terminal ({}) closed with exit code {} before the command ran.'.format(terminal_name, process.returncode))
        time.sleep(0.5)
    shutil.rmtree(work, ignore_errors=True)


def install_tool(name):
    """Runs the tool's install command in a terminal window, so the user sees progress and answers any prompt
    (sudo password, installer questions), and returns when it is closed."""
    tool = next((t for t in tool_catalog(get_config()) if t.get('name') == name), None)
    if not tool:
        raise RuntimeError("No tool named '{}' in the catalog.".format(name))
    command = tool_install_command(tool)
    if not command:
        raise RuntimeError("The catalog has no install command for '{}'.".format(name))
    run_in_terminal(name, command)


def install_server_dependencies(server):
    if not server.get('InstallCommand'):
        raise RuntimeError('No install command known for {}: {}.'.format(server['Project'], server['Label']))
    run_in_terminal('{} packages'.format(server['Project']), server['InstallCommand'], server['InstallDir'])


def launch(argv, cwd=None):
    """Starts a desktop app or terminal detached from the widget."""
    subprocess.Popen(argv, cwd=cwd or None, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def open_tool(tool):
    path = tool.get('Path') or ''
    if path.startswith('flatpak run '):
        launch(path.split())
    elif tool.get('Open') == 'shell':
        terminal, _ = terminal_command()
        if not terminal:
            raise RuntimeError('No terminal emulator found.')
        launch([terminal], cwd=get_config().shell_folder)
    else:
        launch([path])


_os_description = None


def os_description():
    global _os_description
    if _os_description is None:
        pretty = ''
        try:
            with open('/etc/os-release', encoding='utf-8') as handle:
                for line in handle:
                    if line.startswith('PRETTY_NAME='):
                        pretty = line.split('=', 1)[1].strip().strip('"')
        except OSError:
            pass
        _os_description = ' ({}, kernel {})'.format(pretty or 'Linux', platform.release())
    return _os_description


def export_markdown(status):
    """The whole stack as Markdown: readable as plain text, and formatted when pasted into a chat, GitHub or a
    document. Always complete, whatever is folded or filtered in the window."""
    def cell(value):
        return re.sub(r'\r?\n', ' ', str(value if value is not None else '')).replace('|', r'\|').strip()

    state_word = {'Green': 'OK', 'Yellow': 'Needs a look', 'Red': 'Missing'}
    tools = list(status['Tools'])
    rows = [r for r in status['Rows'] if r]
    servers = [r for r in rows if r['Section'] == 'Servers']
    running = sum(1 for r in servers if r['State'] == 'Green')
    problems = sum(1 for r in rows + tools if r['State'] == 'Red')
    installed = sum(1 for t in tools if t['State'] != 'Red')

    lines = ['# ShowStack export', '',
             'Generated {:%Y-%m-%d %H:%M} on {}{}.'.format(datetime.datetime.now(), socket.gethostname(), os_description())]
    summary = '{} tools, {} of {} servers running'.format(installed, running, len(servers))
    if problems:
        summary += ', {} need attention'.format(problems)
    lines.append('Summary: {}.'.format(summary))

    lines += ['', '## Tools ({})'.format(installed), '', '| Tool | Version | Category | Status | Location |', '|---|---|---|---|---|']
    for tool in tools:
        if tool['State'] == 'Green':
            tool_status = 'OK'
        elif tool['State'] == 'Red':
            tool_status = 'Missing (required)'
        else:
            tool_status = 'Needs a look: {}'.format(tool['Detail'])
        lines.append('| {} | {} | {} | {} | {} |'.format(cell(tool['Name']), cell(tool['Version']), cell(tool['Category']),
                                                       cell(tool_status), cell(tool['Path'])))

    services = [r for r in rows if r['Section'] == 'Services']
    if services:
        lines += ['', '## Services', '', '| Service | Status | Detail |', '|---|---|---|']
        for row in services:
            lines.append('| {} | {} | {} |'.format(cell(row['Name']), state_word[row['State']], cell(row['Detail'])))

    lines += ['', '## Servers ({} of {} running)'.format(running, len(servers))]
    groups = {}
    for row in servers:
        groups.setdefault(row['Group'], []).append(row)
    for group, group_rows in groups.items():
        lines += ['', '### {}'.format(group), '', '| Server | Status | Port | URL | Start command | Folder | Found in |',
                  '|---|---|---|---|---|---|---|']
        for row in group_rows:
            server = row['Server']
            if server['Kind'] == 'compose':
                command = 'docker compose up -d {}'.format(server['Service'])
            else:
                command = ' '.join(shlex.quote(str(a)) for a in [server['Executable']] + list(server['Arguments']) if a)
            if server['NeedsPort']:
                command = 'PORT={} {}'.format(server['Port'], command)
            state = str(row['Detail']).split(' - ')[0]
            lines.append('| {} | {} | {} | {} | `{}` | {} | {} |'.format(cell(row['Name']), cell(state), server['Port'], cell(row['Url']),
                                                                      cell(command), cell(server['Dir']), cell(server['Source'])))
    if status['Hidden']:
        lines += ['', '_{} hidden server(s) not listed._'.format(status['Hidden'])]

    others = [r for r in rows if r['Section'] == 'Other local servers']
    if others:
        lines += ['', '## Other local servers ({})'.format(len(others)), '', '| Port | URL | Process and command line |', '|---|---|---|']
        for row in others:
            lines.append('| {} | {} | {} |'.format(cell(re.sub(r'^Port ', '', row['Name'])), cell(row['Url']), cell(row['Detail'])))

    lines += ['', '_Exported by ShowStack (https://github.com/Orangeriger1998/ShowStack)._']
    return '\n'.join(lines)
