"""
token_flow._service — cross-platform service installer/uninstaller.

Platforms:
  Linux   → systemd user service  (~/.config/systemd/user/token-flow.service)
  macOS   → launchd user agent    (~/Library/LaunchAgents/com.freightdawg.token-flow.plist)
  Windows → Task Scheduler task   (TokenFlow — runs at log-on, restarts on failure)

Called from:
  tf-server install-service
  tf-server uninstall-service
  pip install (via setup.py post_install hook)
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import textwrap
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────
SERVICE_NAME    = "token-flow"
LAUNCHD_LABEL   = "com.freightdawg.token-flow"
WIN_TASK_NAME   = "TokenFlow"
WIN_TASK_DESC   = "Token Flow local API — smart memory and token tracking for OpenClaw"

HOME = Path.home()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _os() -> str:
    s = platform.system()
    if s == "Linux":   return "linux"
    if s == "Darwin":  return "macos"
    if s == "Windows": return "windows"
    return s.lower()


def _python() -> str:
    """Absolute path to the current Python interpreter."""
    return sys.executable


def _tf_server() -> str:
    """Absolute path to the tf-server script (same env as current Python)."""
    import shutil

    # 1. Already on PATH?
    found = shutil.which("tf-server")
    if found:
        return found

    # 2. Scripts dir next to the interpreter (venv / system)
    for scripts in (
        Path(sys.executable).parent,                          # venv: bin/
        Path(sys.executable).parent.parent / "bin",          # venv alt
        Path.home() / ".local" / "bin",                      # pip install --user
        Path("/usr/local/bin"),
        Path("/usr/bin"),
    ):
        for name in ("tf-server", "tf-server.exe"):
            p = scripts / name
            if p.exists():
                return str(p)

    # 3. Fallback: run as module (always works if package is installed)
    return f"{sys.executable} -m token_flow._cli_runner"


def _env_file() -> Path | None:
    """Return the .env path to embed in the service, if it exists."""
    explicit = os.environ.get("TOKEN_FLOW_ENV_FILE")
    if explicit and Path(explicit).exists():
        return Path(explicit)
    # Check well-known repo location (set by manage.sh or developer env)
    repo = os.environ.get("TOKEN_FLOW_REPO", "")
    if repo and (Path(repo) / ".env").exists():
        return Path(repo) / ".env"
    return None


def _load_env() -> dict[str, str]:
    """Load .env into a dict (without polluting os.environ)."""
    env: dict[str, str] = {}
    ef = _env_file()
    if ef:
        for line in ef.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _merged_env() -> dict[str, str]:
    """os.environ overlaid with .env values (os.environ wins)."""
    merged = _load_env()
    merged.update(os.environ)
    return merged


# ── Linux — systemd user service ─────────────────────────────────────────────

def _systemd_unit_path() -> Path:
    return HOME / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def install_linux() -> None:
    env = _merged_env()
    tf  = _tf_server()
    unit_dir = _systemd_unit_path().parent
    unit_dir.mkdir(parents=True, exist_ok=True)

    env_file_line = ""
    ef = _env_file()
    if ef:
        env_file_line = f"EnvironmentFile={ef}"

    # tf may be "python3 -m token_flow._cli_runner" (fallback) — systemd needs
    # an absolute binary path so split into ExecStart= args correctly.
    if tf.startswith(sys.executable) and " " in tf:
        exec_start = tf  # e.g. /usr/bin/python3 -m token_flow._cli_runner start
    else:
        exec_start = f"{tf} start"

    unit = textwrap.dedent(f"""\
        [Unit]
        Description=Token Flow API
        After=network.target

        [Service]
        Type=simple
        {env_file_line}
        Environment=TOKEN_FLOW_PORT={env.get('TOKEN_FLOW_PORT', '8001')}
        Environment=TOKEN_FLOW_DB={env.get('TOKEN_FLOW_DB', str(HOME / '.openclaw/data/token_flow.db'))}
        Environment=WORKSPACE={env.get('WORKSPACE', str(HOME / '.openclaw/workspace'))}
        Environment=MEMORY_DIR={env.get('MEMORY_DIR', str(HOME / '.openclaw/workspace/memory'))}
        Environment=SESSIONS_DIR={env.get('SESSIONS_DIR', str(HOME / '.openclaw/agents/main/sessions'))}
        Environment=S3_BUCKET={env.get('S3_BUCKET', 'smart-memory')}
        Environment=OWNER_EMAIL={env.get('OWNER_EMAIL', '')}
        Environment=PYTHONUNBUFFERED=1
        ExecStart={exec_start}
        Restart=on-failure
        RestartSec=5

        [Install]
        WantedBy=default.target
    """)

    unit_path = _systemd_unit_path()
    unit_path.write_text(unit)
    print(f"   Wrote unit : {unit_path}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", SERVICE_NAME], check=True)
    subprocess.run(["systemctl", "--user", "start",  SERVICE_NAME], check=True)
    print(f"✅ systemd user service installed, enabled, and started.")
    print(f"   Manage: tf-server start|stop|restart|status")


def uninstall_linux() -> None:
    subprocess.run(["systemctl", "--user", "stop",    SERVICE_NAME], check=False)
    subprocess.run(["systemctl", "--user", "disable", SERVICE_NAME], check=False)
    unit_path = _systemd_unit_path()
    if unit_path.exists():
        unit_path.unlink()
        print(f"   Removed: {unit_path}")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print("✅ systemd user service removed.")


# ── macOS — launchd user agent ────────────────────────────────────────────────

def _launchd_plist_path() -> Path:
    return HOME / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _plist_env_dict(env: dict[str, str]) -> str:
    keys = [
        "TOKEN_FLOW_PORT", "TOKEN_FLOW_DB", "WORKSPACE", "MEMORY_DIR",
        "SESSIONS_DIR", "S3_BUCKET", "AUTH0_DOMAIN", "AUTH0_CLIENT_ID",
        "SECRET_KEY", "TOKEN_FLOW_UI_URL", "ANTHROPIC_API_KEY",
        "OWNER_EMAIL", "PYTHONUNBUFFERED",
    ]
    lines = []
    for k in keys:
        v = env.get(k, "1" if k == "PYTHONUNBUFFERED" else "")
        lines.append(f"    <key>{k}</key><string>{v}</string>")
    return "\n".join(lines)


def install_macos() -> None:
    env  = _merged_env()
    tf   = _tf_server()
    pdir = _launchd_plist_path().parent
    pdir.mkdir(parents=True, exist_ok=True)

    log_out = str(HOME / "Library" / "Logs" / "token-flow.log")
    log_err = str(HOME / "Library" / "Logs" / "token-flow-err.log")

    plist = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
          <key>Label</key>
          <string>{LAUNCHD_LABEL}</string>

          <key>ProgramArguments</key>
          <array>
            <string>{tf}</string>
            <string>start</string>
          </array>

          <key>EnvironmentVariables</key>
          <dict>
        {_plist_env_dict(env)}
          </dict>

          <key>RunAtLoad</key>        <true/>
          <key>KeepAlive</key>        <true/>
          <key>ThrottleInterval</key> <integer>5</integer>

          <key>StandardOutPath</key>  <string>{log_out}</string>
          <key>StandardErrorPath</key><string>{log_err}</string>
        </dict>
        </plist>
    """)

    plist_path = _launchd_plist_path()
    plist_path.write_text(plist)
    print(f"   Wrote plist: {plist_path}")

    subprocess.run(["launchctl", "load", "-w", str(plist_path)], check=True)
    print("✅ launchd user agent installed and loaded.")
    print(f"   Logs: {log_out}")
    print(f"   Manage: tf-server start|stop|restart|status")


def uninstall_macos() -> None:
    plist_path = _launchd_plist_path()
    if plist_path.exists():
        subprocess.run(["launchctl", "unload", "-w", str(plist_path)], check=False)
        plist_path.unlink()
        print(f"   Removed: {plist_path}")
    print("✅ launchd user agent removed.")


# ── Windows — Task Scheduler ──────────────────────────────────────────────────

def _win_xml(tf: str, env: dict[str, str]) -> str:
    """Build a Task Scheduler XML definition."""
    import getpass
    user = getpass.getuser()

    # Build env-setter chain: each var is set before tf-server runs.
    # We embed them as arguments to cmd /c "set VAR=VAL && ... && tf-server start"
    env_keys = [
        "TOKEN_FLOW_PORT", "TOKEN_FLOW_DB", "WORKSPACE", "MEMORY_DIR",
        "SESSIONS_DIR", "S3_BUCKET", "AUTH0_DOMAIN", "AUTH0_CLIENT_ID",
        "SECRET_KEY", "TOKEN_FLOW_UI_URL", "ANTHROPIC_API_KEY",
        "OWNER_EMAIL", "PYTHONUNBUFFERED",
    ]
    set_cmds = " && ".join(
        f'set "{k}={env.get(k, "1" if k == "PYTHONUNBUFFERED" else "")}"'
        for k in env_keys
    )
    cmd_args = f'/c "{set_cmds} && \\"{tf}\\" start"'

    log_dir = HOME / "AppData" / "Local" / "TokenFlow" / "Logs"

    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-16"?>
        <Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
          <RegistrationInfo>
            <Description>{WIN_TASK_DESC}</Description>
          </RegistrationInfo>
          <Triggers>
            <LogonTrigger>
              <Enabled>true</Enabled>
              <UserId>{user}</UserId>
            </LogonTrigger>
          </Triggers>
          <Principals>
            <Principal id="Author">
              <LogonType>InteractiveToken</LogonType>
              <RunLevel>LeastPrivilege</RunLevel>
            </Principal>
          </Principals>
          <Settings>
            <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
            <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
            <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
            <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
            <RestartOnFailure>
              <Interval>PT30S</Interval>
              <Count>999</Count>
            </RestartOnFailure>
            <Enabled>true</Enabled>
          </Settings>
          <Actions Context="Author">
            <Exec>
              <Command>cmd.exe</Command>
              <Arguments>{cmd_args}</Arguments>
              <WorkingDirectory>{HOME}</WorkingDirectory>
            </Exec>
          </Actions>
        </Task>
    """)


def install_windows() -> None:
    import tempfile
    env = _merged_env()
    tf  = _tf_server()

    log_dir = HOME / "AppData" / "Local" / "TokenFlow" / "Logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    xml_content = _win_xml(tf, env)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".xml", delete=False, encoding="utf-16"
    ) as f:
        f.write(xml_content)
        xml_path = f.name

    try:
        # Delete existing task (ignore error if not present)
        subprocess.run(
            ["schtasks", "/Delete", "/TN", WIN_TASK_NAME, "/F"],
            check=False, capture_output=True,
        )
        # Register new task
        subprocess.run(
            ["schtasks", "/Create", "/TN", WIN_TASK_NAME, "/XML", xml_path],
            check=True,
        )
        # Start it immediately
        subprocess.run(
            ["schtasks", "/Run", "/TN", WIN_TASK_NAME],
            check=True,
        )
        print(f"✅ Windows Task Scheduler task '{WIN_TASK_NAME}' installed and started.")
        print(f"   Logs: {log_dir}")
        print(f"   Manage: tf-server start|stop|restart|status")
    finally:
        Path(xml_path).unlink(missing_ok=True)


def uninstall_windows() -> None:
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", WIN_TASK_NAME, "/F"],
        capture_output=True,
    )
    if result.returncode == 0:
        print(f"✅ Windows Task Scheduler task '{WIN_TASK_NAME}' removed.")
    else:
        print(f"ℹ️  Task '{WIN_TASK_NAME}' not found or already removed.")


# ── Windows — start/stop/restart/status via schtasks ─────────────────────────

def win_task_exists() -> bool:
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", WIN_TASK_NAME],
        capture_output=True,
    )
    return r.returncode == 0


def win_service_start() -> None:
    subprocess.run(["schtasks", "/Run", "/TN", WIN_TASK_NAME], check=True)
    print(f"✅ Task '{WIN_TASK_NAME}' triggered.")


def win_service_stop() -> None:
    subprocess.run(["schtasks", "/End", "/TN", WIN_TASK_NAME], check=False)
    print(f"✅ Task '{WIN_TASK_NAME}' stopped.")


def win_service_status() -> None:
    subprocess.run(["schtasks", "/Query", "/TN", WIN_TASK_NAME, "/FO", "LIST"], check=False)


# ── Public API ────────────────────────────────────────────────────────────────

def install_service(verbose: bool = True) -> None:
    """Install and start the appropriate OS service. Called by CLI + post-install hook."""
    os_name = _os()
    print(f"📦 Installing token-flow as a {os_name} service…")
    if os_name == "linux":
        install_linux()
    elif os_name == "macos":
        install_macos()
    elif os_name == "windows":
        install_windows()
    else:
        print(f"⚠️  Unsupported platform '{os_name}'. Use tf-server start manually.", file=sys.stderr)


def uninstall_service() -> None:
    """Stop and remove the OS service."""
    os_name = _os()
    print(f"🗑️  Removing token-flow {os_name} service…")
    if os_name == "linux":
        uninstall_linux()
    elif os_name == "macos":
        uninstall_macos()
    elif os_name == "windows":
        uninstall_windows()
    else:
        print(f"⚠️  Unsupported platform '{os_name}'.", file=sys.stderr)
