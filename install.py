"""
RUBIX installer — called automatically by pip after package install.

Handles:
  Windows : copies binaries to Program Files\Rubix, installs NPcap silently,
            registers PATH via the Windows registry (HKLM or HKCU).
  Linux   : copies binaries to /usr/local/bin, configs to /etc/rubix.
            Requires sudo / root.
"""

import os
import sys
import shutil
import platform
import subprocess
import textwrap
from pathlib import Path


# ── Paths inside the installed package ───────────────────────────────────────

PKG_DIR   = Path(__file__).parent
BIN_WIN   = PKG_DIR / "bin" / "windows"
BIN_LINUX = PKG_DIR / "bin" / "linux"
CONFIGS   = PKG_DIR / "configs"
NPCAP_EXE = PKG_DIR / "npcap" / "npcap-1.88.exe"

# ── Target install locations ──────────────────────────────────────────────────

def windows_install_dir() -> Path:
    base = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    return Path(base) / "Rubix"

LINUX_BIN_DIR     = Path("/usr/local/bin")
LINUX_CONFIG_DIR  = Path("/etc/rubix")
LINUX_LOG_DIR     = Path("/var/log/rubix")


# ── Helpers ───────────────────────────────────────────────────────────────────

def banner(msg: str) -> None:
    print(f"\n  [RUBIX] {msg}")

def ok(msg: str) -> None:
    print(f"  [RUBIX]   OK  {msg}")

def warn(msg: str) -> None:
    print(f"  [RUBIX]  WARN {msg}", file=sys.stderr)

def fail(msg: str) -> None:
    print(f"  [RUBIX] FAIL  {msg}", file=sys.stderr)
    sys.exit(1)


# ── Windows ───────────────────────────────────────────────────────────────────

def _npcap_installed() -> bool:
    """Return True if NPcap is already present on the system."""
    try:
        import winreg
        key_paths = [
            r"SOFTWARE\Npcap",
            r"SOFTWARE\WOW6432Node\Npcap",
        ]
        for kp in key_paths:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, kp):
                    return True
            except FileNotFoundError:
                continue
    except ImportError:
        pass
    # Fallback: check for the DLL
    npcap_dll = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "Npcap" / "wpcap.dll"
    return npcap_dll.exists()


def _install_npcap() -> None:
    """Run the bundled NPcap installer silently."""
    if _npcap_installed():
        ok("NPcap already installed — skipping")
        return

    if not NPCAP_EXE.exists():
        warn(f"NPcap installer not found at {NPCAP_EXE} — skipping")
        warn("Install NPcap manually from https://npcap.com/#download")
        return

    banner("Installing NPcap (required for packet capture)...")
    try:
        # /S = silent, /winpcap_mode=no, /dot11_support=no
        result = subprocess.run(
            [str(NPCAP_EXE), "/S", "/winpcap_mode=no", "/dot11_support=no"],
            check=True,
            timeout=120,
        )
        ok("NPcap installed successfully")
    except subprocess.CalledProcessError as e:
        warn(f"NPcap installer exited with code {e.returncode}")
        warn("Packet capture may not work. Install NPcap manually: https://npcap.com/#download")
    except subprocess.TimeoutExpired:
        warn("NPcap installer timed out — it may still be running in the background")
    except PermissionError:
        warn("NPcap installation requires Administrator privileges")
        warn("Run this install command as Administrator, or install NPcap manually: https://npcap.com/#download")


def _add_to_path_windows(install_dir: Path) -> None:
    """
    Add install_dir to the system PATH in the Windows registry.

    Tries HKLM (system-wide, requires admin) first.
    Falls back to HKCU (current user only) if that fails.
    """
    import winreg

    dir_str = str(install_dir)

    def _update_reg_path(hive, subkey: str) -> bool:
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
                current, _ = winreg.QueryValueEx(key, "Path")
                paths = [p for p in current.split(";") if p]
                if dir_str.lower() not in [p.lower() for p in paths]:
                    paths.append(dir_str)
                    winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(paths))
            return True
        except (PermissionError, OSError):
            return False

    # Try system PATH first (needs admin)
    system_env = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
    if _update_reg_path(winreg.HKEY_LOCAL_MACHINE, system_env):
        ok(f"Added to system PATH: {install_dir}")
    else:
        # Fall back to user PATH
        user_env = r"Environment"
        if _update_reg_path(winreg.HKEY_CURRENT_USER, user_env):
            ok(f"Added to user PATH: {install_dir}")
            warn("Added to user PATH only (not system-wide). Run as Administrator for system-wide PATH.")
        else:
            warn(f"Could not add to PATH automatically.")
            warn(f"Add this directory to PATH manually: {install_dir}")

    # Broadcast WM_SETTINGCHANGE so open terminals pick up the new PATH
    # without requiring a reboot
    try:
        import ctypes
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            0x0002,  # SMTO_ABORTIFHUNG
            5000, None
        )
    except Exception:
        pass  # Non-fatal — PATH will be active in new terminals regardless


def install_windows() -> None:
    install_dir = windows_install_dir()
    config_dir  = install_dir / "configs"
    log_dir     = install_dir / "logs"

    banner(f"Installing RUBIX to {install_dir} ...")

    # ── Create directories ────────────────────────────────────────────────
    try:
        install_dir.mkdir(parents=True, exist_ok=True)
        config_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        fail(
            f"Cannot create {install_dir}\n"
            "  Run: pip install rubix    (as Administrator)\n"
            "  Or open an elevated PowerShell and retry."
        )

    # ── Copy binaries ─────────────────────────────────────────────────────
    for exe in ["rubix.exe", "rubix-cli.exe"]:
        src = BIN_WIN / exe
        dst = install_dir / exe
        if not src.exists():
            fail(f"Binary missing from package: {src}\nPlease reinstall: pip install --force-reinstall rubix")
        shutil.copy2(src, dst)
        ok(f"Installed {exe}")

    # ── Copy configs (skip if user already has them) ──────────────────────
    for cfg in CONFIGS.iterdir():
        dst = config_dir / cfg.name
        if dst.exists():
            ok(f"Config already exists, skipping: {cfg.name}")
        else:
            shutil.copy2(cfg, dst)
            ok(f"Installed config: {cfg.name}")

    # ── NPcap ─────────────────────────────────────────────────────────────
    _install_npcap()

    # ── PATH ──────────────────────────────────────────────────────────────
    _add_to_path_windows(install_dir)

    # ── Done ──────────────────────────────────────────────────────────────
    print(textwrap.dedent(f"""
  ╔══════════════════════════════════════════════════════╗
  ║         RUBIX installed successfully                 ║
  ╠══════════════════════════════════════════════════════╣
  ║  Location : {str(install_dir):<41}║
  ║  Configs  : {str(config_dir):<41}║
  ║  Logs     : {str(log_dir):<41}║
  ╠══════════════════════════════════════════════════════╣
  ║  Open a NEW terminal (or restart this one), then:   ║
  ║                                                      ║
  ║    rubix          (run as Administrator)             ║
  ║    rubix-cli      (monitor / logs / control)         ║
  ╚══════════════════════════════════════════════════════╝
    """))


# ── Linux ─────────────────────────────────────────────────────────────────────

def install_linux() -> None:
    if os.geteuid() != 0:
        fail(
            "RUBIX installation on Linux requires root privileges.\n"
            "  Run: sudo pip install rubix"
        )

    banner("Installing RUBIX on Linux ...")

    # ── Binaries ──────────────────────────────────────────────────────────
    for binary in ["rubix", "rubix-cli"]:
        src = BIN_LINUX / binary
        dst = LINUX_BIN_DIR / binary
        if not src.exists():
            fail(f"Binary missing from package: {src}\nPlease reinstall: pip install --force-reinstall rubix")
        shutil.copy2(src, dst)
        dst.chmod(0o755)
        ok(f"Installed {binary} -> {dst}")

    # ── Configs ───────────────────────────────────────────────────────────
    LINUX_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for cfg in CONFIGS.iterdir():
        dst = LINUX_CONFIG_DIR / cfg.name
        if dst.exists():
            ok(f"Config already exists, skipping: {cfg.name}")
        else:
            shutil.copy2(cfg, dst)
            ok(f"Installed config: {cfg.name}")

    # ── Log directory ─────────────────────────────────────────────────────
    LINUX_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ok(f"Log directory: {LINUX_LOG_DIR}")

    # ── libpcap check ─────────────────────────────────────────────────────
    _check_libpcap()

    print(textwrap.dedent(f"""
  ╔══════════════════════════════════════════════════════╗
  ║         RUBIX installed successfully                 ║
  ╠══════════════════════════════════════════════════════╣
  ║  Binaries : /usr/local/bin/rubix                     ║
  ║             /usr/local/bin/rubix-cli                 ║
  ║  Configs  : /etc/rubix/                              ║
  ║  Logs     : /var/log/rubix/                          ║
  ╠══════════════════════════════════════════════════════╣
  ║    sudo rubix          (start engine)                ║
  ║    rubix-cli monitor   (live stats)                  ║
  ║    rubix-cli logs      (live log stream)             ║
  ╚══════════════════════════════════════════════════════╝
    """))


def _check_libpcap() -> None:
    """Warn if libpcap is not installed — we can't install it ourselves."""
    found = (
        Path("/usr/lib/libpcap.so").exists()
        or Path("/usr/lib/x86_64-linux-gnu/libpcap.so.0.8").exists()
        or shutil.which("libpcap") is not None
        or _ldconfig_has("libpcap")
    )
    if found:
        ok("libpcap found")
    else:
        warn("libpcap not detected. Install it with:")
        warn("  Debian/Ubuntu : sudo apt install libpcap-dev")
        warn("  RHEL/CentOS   : sudo yum install libpcap-devel")
        warn("  Arch          : sudo pacman -S libpcap")


def _ldconfig_has(lib: str) -> bool:
    try:
        out = subprocess.check_output(["ldconfig", "-p"], stderr=subprocess.DEVNULL, text=True)
        return lib in out
    except Exception:
        return False


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    system = platform.system()
    if system == "Windows":
        install_windows()
    elif system == "Linux":
        install_linux()
    else:
        fail(f"Unsupported platform: {system}. RUBIX supports Windows and Linux only.")


if __name__ == "__main__":
    main()
