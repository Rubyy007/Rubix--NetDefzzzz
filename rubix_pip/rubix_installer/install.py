#!/usr/bin/env python3
"""
rubix_installer/install.py
==========================
Production installer for RUBIX Network Defence System.

Handles 50+ edge cases across Windows and Linux with:
- Comprehensive logging to file and console
- Atomic operations with rollback on failure
- Idempotent installation (safe to re-run)
- Automatic dependency resolution
- Graceful degradation at every step
- Detailed diagnostics on failure

Author: Manik
Version: 2.0.0
"""

from __future__ import annotations

import atexit
import ctypes
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

# ── Version & Metadata ───────────────────────────────────────────────────────

__version__ = "2.0.0"
__author__ = "Manik"
__min_python__ = (3, 8)

# ── Package-relative paths ───────────────────────────────────────────────────

_PKG = Path(__file__).parent.resolve()
BIN_WIN = _PKG / "bin" / "windows"
BIN_LINUX = _PKG / "bin" / "linux"
CONFIGS = _PKG / "configs"
NPCAP_EXE = _PKG / "npcap" / "npcap-1.88.exe"

# ── Linux target locations ───────────────────────────────────────────────────

LINUX_BIN = Path("/usr/local/bin")
LINUX_CONF = Path("/etc/rubix")
LINUX_LOGS = Path("/var/log/rubix")
LINUX_UNIT = Path("/etc/systemd/system/rubix.service")
LINUX_UNIT_DIR = Path("/etc/systemd/system")

# ── Windows target locations (resolved at runtime) ───────────────────────────

def _win_programfiles() -> Path:
    """Resolve Windows Program Files with 8 fallback levels."""
    candidates = [
        os.environ.get("PROGRAMFILES", ""),
        os.environ.get("PROGRAMW6432", ""),
        os.environ.get("PROGRAMFILES(X86)", ""),
        r"C:\Program Files",
        r"C:\Program Files (x86)",
        os.path.expanduser("~"),
    ]
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        base = Path(candidate)
        if base.exists() and base.is_dir():
            return base / "Rubix"
    drive = os.path.splitdrive(os.getcwd())[0] or "C:"
    return Path(drive) / "Rubix"

WIN_INSTALL_DIR = _win_programfiles()
WIN_CONFIG_DIR = WIN_INSTALL_DIR / "configs"
WIN_LOG_DIR = WIN_INSTALL_DIR / "logs"

# ── Logging system ───────────────────────────────────────────────────────────

class InstallLogger:
    """Dual logging to file and console with structured output."""
    
    def __init__(self, log_dir: Optional[Path] = None):
        self.entries: List[Dict] = []
        self.console_color = _supports_color()
        
        # Determine log file location
        if log_dir:
            self.log_file = log_dir / "rubix_install.log"
        else:
            temp = Path(tempfile.gettempdir()) / "rubix_install.log"
            self.log_file = temp
        
        self._write_header()
    
    def _write_header(self) -> None:
        header = (
            f"{'='*60}\n"
            f"RUBIX Installer v{__version__}\n"
            f"Timestamp: {datetime.utcnow().isoformat()}Z\n"
            f"Platform: {platform.system()} {platform.release()} {platform.machine()}\n"
            f"Python: {sys.version}\n"
            f"User: {os.environ.get('USER', os.environ.get('USERNAME', 'unknown'))}\n"
            f"Admin: {_is_admin()}\n"
            f"{'='*60}\n"
        )
        self._write_file(header)
    
    def _write_file(self, text: str) -> None:
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception as e:
            # Last resort: stderr
            print(f"[LOG FAIL] {e}: {text[:100]}", file=sys.stderr)
    
    def log(self, level: str, message: str, details: Optional[str] = None) -> None:
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": level,
            "message": message,
            "details": details,
        }
        self.entries.append(entry)
        
        # File log (always)
        detail_str = f"\n  Details: {details}" if details else ""
        self._write_file(f"[{level:8}] {message}{detail_str}")
        
        # Console log (with color)
        color_map = {
            "DEBUG": "\033[96m",    # Cyan
            "INFO": "\033[92m",     # Green
            "WARNING": "\033[93m",  # Yellow
            "ERROR": "\033[91m",    # Red
            "CRITICAL": "\033[91m\033[1m",  # Bold Red
        }
        reset = "\033[0m" if self.console_color else ""
        color = color_map.get(level, "") if self.console_color else ""
        
        prefix = f"  [{level[0]}] "
        if level == "CRITICAL":
            prefix = f"  [FATAL] "
        
        print(f"{color}{prefix}{message}{reset}")
        if details and level in ("ERROR", "CRITICAL", "WARNING"):
            for line in details.splitlines():
                print(f"      {line}")
    
    def debug(self, msg: str, details: Optional[str] = None) -> None:
        self.log("DEBUG", msg, details)
    
    def info(self, msg: str, details: Optional[str] = None) -> None:
        self.log("INFO", msg, details)
    
    def warning(self, msg: str, details: Optional[str] = None) -> None:
        self.log("WARNING", msg, details)
    
    def error(self, msg: str, details: Optional[str] = None) -> None:
        self.log("ERROR", msg, details)
    
    def critical(self, msg: str, details: Optional[str] = None) -> None:
        self.log("CRITICAL", msg, details)
    
    def save_json(self, path: Path) -> None:
        """Save structured log for debugging."""
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "metadata": {
                        "version": __version__,
                        "platform": platform.system(),
                        "success": False,
                    },
                    "entries": self.entries,
                }, f, indent=2)
        except Exception as e:
            self.error(f"Failed to save JSON log: {e}")

# Global logger instance
LOG: Optional[InstallLogger] = None

def get_logger() -> InstallLogger:
    global LOG
    if LOG is None:
        LOG = InstallLogger()
    return LOG

# ── Color support detection ──────────────────────────────────────────────────

def _supports_color() -> bool:
    """Detect if terminal supports ANSI colors."""
    if not sys.stdout.isatty():
        return False
    if os.environ.get("TERM") or os.environ.get("ANSICON"):
        return True
    if os.environ.get("WT_SESSION"):
        return True
    if os.environ.get("PSMODULEPATH"):
        return True
    if platform.system() != "Windows":
        return True
    # Try enable VT100 on Windows CMD
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong(0)
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            return True
    except Exception:
        pass
    return False

# ── Admin detection ──────────────────────────────────────────────────────────

def _is_admin() -> bool:
    """Cross-platform admin check."""
    if platform.system() == "Windows":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    else:
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False

# ── Subprocess runner with exhaustive error handling ─────────────────────────

def _run(
    cmd: List[str],
    *,
    timeout: int = 30,
    capture: bool = True,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    shell: bool = False,
) -> Tuple[int, str, str, Optional[Exception]]:
    """
    Run subprocess with comprehensive error capture.
    
    Returns: (returncode, stdout, stderr, exception_or_None)
    """
    logger = get_logger()
    cmd_str = " ".join(str(c) for c in cmd)
    logger.debug(f"Executing: {cmd_str}", f"cwd={cwd}, timeout={timeout}")
    
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
            shell=shell,
            check=False,  # We handle return codes ourselves
        )
        out = result.stdout.decode("utf-8", errors="replace").strip() if result.stdout else ""
        err = result.stderr.decode("utf-8", errors="replace").strip() if result.stderr else ""
        
        if result.returncode != 0:
            logger.debug(f"Command exited {result.returncode}: {cmd_str}", f"stderr={err[:200]}")
        
        return result.returncode, out, err, None
        
    except FileNotFoundError as e:
        logger.debug(f"Command not found: {cmd[0]}")
        return -1, "", f"Command not found: {cmd[0]}", e
    except subprocess.TimeoutExpired as e:
        logger.debug(f"Command timed out after {timeout}s: {cmd_str}")
        return -2, "", f"Timed out after {timeout} seconds", e
    except PermissionError as e:
        logger.debug(f"Permission denied: {cmd_str}")
        return -3, "", f"Permission denied: {cmd_str}", e
    except OSError as e:
        logger.debug(f"OS error running command: {e}")
        return -4, "", str(e), e
    except Exception as e:
        logger.debug(f"Unexpected error: {e}")
        return -5, "", str(e), e

# ── File operations with atomicity and rollback ──────────────────────────────

class RollbackManager:
    """Track file operations for rollback on failure."""
    
    def __init__(self):
        self.operations: List[Dict] = []
        self.completed = False
    
    def add_file(self, path: Path, original_content: Optional[bytes] = None) -> None:
        self.operations.append({
            "type": "file",
            "path": path,
            "original": original_content,
        })
    
    def add_dir(self, path: Path) -> None:
        self.operations.append({
            "type": "dir",
            "path": path,
        })
    
    def add_registry(self, hive: int, key: str, value_name: str, original_value: Optional[str]) -> None:
        self.operations.append({
            "type": "registry",
            "hive": hive,
            "key": key,
            "value_name": value_name,
            "original": original_value,
        })
    
    def commit(self) -> None:
        """Mark as successful — disable rollback."""
        self.completed = True
    
    def rollback(self) -> None:
        """Undo all tracked operations."""
        if self.completed:
            return
        
        logger = get_logger()
        logger.warning("Rolling back changes due to installation failure...")
        
        for op in reversed(self.operations):
            try:
                if op["type"] == "file":
                    path = op["path"]
                    if path.exists():
                        if op["original"] is not None:
                            path.write_bytes(op["original"])
                            logger.debug(f"Restored file: {path}")
                        else:
                            path.unlink()
                            logger.debug(f"Removed file: {path}")
                
                elif op["type"] == "dir":
                    path = op["path"]
                    if path.exists() and not any(path.iterdir()):
                        path.rmdir()
                        logger.debug(f"Removed empty dir: {path}")
                
                elif op["type"] == "registry":
                    # Windows registry rollback
                    try:
                        import winreg
                        with winreg.OpenKey(op["hive"], op["key"], 0, winreg.KEY_WRITE) as key:
                            if op["original"] is not None:
                                winreg.SetValueEx(key, op["value_name"], 0, winreg.REG_EXPAND_SZ, op["original"])
                            else:
                                winreg.DeleteValue(key, op["value_name"])
                    except Exception as e:
                        logger.debug(f"Registry rollback failed: {e}")
                        
            except Exception as e:
                logger.debug(f"Rollback operation failed: {e}")

# Global rollback manager
ROLLBACK = RollbackManager()

def _mkdir_safe(path: Path, mode: int = 0o755) -> None:
    """
    Create directory with full error diagnostics and rollback tracking.
    
    Handles:
    - Path already exists
    - Permission denied
    - Intermediate directories missing
    - Invalid path characters
    - Disk full
    - Read-only filesystem
    """
    logger = get_logger()
    
    if path.exists():
        if path.is_dir():
            logger.debug(f"Directory already exists: {path}")
            return
        else:
            raise InstallerError(
                f"Cannot create directory: {path}",
                f"A file (not directory) already exists at this path."
            )
    
    # Create with parents, handling each level
    try:
        path.mkdir(parents=True, exist_ok=True, mode=mode)
        ROLLBACK.add_dir(path)
        logger.debug(f"Created directory: {path}")
    except PermissionError as e:
        raise InstallerError(
            f"Permission denied creating: {path}",
            f"Run as Administrator (Windows) or root/sudo (Linux).\n"
            f"Error: {e}"
        )
    except OSError as e:
        # Windows-specific error codes
        if hasattr(e, 'winerror'):
            win_err = e.winerror
            if win_err == 3:  # ERROR_PATH_NOT_FOUND
                raise InstallerError(
                    f"Path not found: {path}",
                    f"An intermediate directory does not exist and could not be created.\n"
                    f"Try setting a different install location."
                )
            elif win_err == 5:  # ERROR_ACCESS_DENIED
                raise InstallerError(
                    f"Access denied: {path}",
                    f"Run as Administrator."
                )
            elif win_err == 28:  # ERROR_NO_MORE_FILES (often disk full)
                raise InstallerError(
                    f"Disk full or resource exhausted: {path}",
                    f"Free up disk space and try again."
                )
            elif win_err == 80:  # ERROR_FILE_EXISTS
                logger.debug(f"Directory race condition: {path}")
                return
            elif win_err == 112:  # ERROR_DISK_FULL
                raise InstallerError(
                    f"Disk full: {path}",
                    f"Free up disk space and try again."
                )
            elif win_err == 183:  # ERROR_ALREADY_EXISTS
                return  # Race condition, already created
            else:
                raise InstallerError(
                    f"Windows error {win_err} creating: {path}",
                    f"Details: {e}"
                )
        else:
            raise InstallerError(
                f"Cannot create directory: {path}",
                f"OS error: {e}"
            )
    except Exception as e:
        raise InstallerError(
            f"Unexpected error creating: {path}",
            f"{type(e).__name__}: {e}"
        )

def _copy_file(
    src: Path,
    dst: Path,
    preserve_existing: bool = True,
    verify_hash: bool = False,
) -> bool:
    """
    Copy file with verification and rollback support.
    
    Returns True if copied, False if skipped (existing preserved).
    """
    logger = get_logger()
    
    if not src.exists():
        raise InstallerError(f"Source file missing: {src}")
    
    if not src.is_file():
        raise InstallerError(f"Source is not a file: {src}")
    
    # Check destination
    if dst.exists():
        if preserve_existing:
            logger.debug(f"Preserving existing file: {dst}")
            return False
        # Backup original for rollback
        try:
            original = dst.read_bytes()
            ROLLBACK.add_file(dst, original)
        except Exception as e:
            logger.warning(f"Could not backup {dst} for rollback: {e}")
    
    # Ensure parent directory exists
    _mkdir_safe(dst.parent)
    
    # Copy with verification
    try:
        shutil.copy2(src, dst)
        
        # Verify copy succeeded
        if not dst.exists():
            raise InstallerError(f"Copy verification failed: {dst} does not exist after copy")
        
        if dst.stat().st_size != src.stat().st_size:
            raise InstallerError(
                f"Copy size mismatch: {dst}",
                f"Source: {src.stat().st_size} bytes, Dest: {dst.stat().st_size} bytes"
            )
        
        if verify_hash:
            src_hash = hashlib.sha256(src.read_bytes()).hexdigest()
            dst_hash = hashlib.sha256(dst.read_bytes()).hexdigest()
            if src_hash != dst_hash:
                raise InstallerError(
                    f"Copy hash mismatch: {dst}",
                    f"Source SHA256: {src_hash}\nDest SHA256: {dst_hash}"
                )
        
        if dst not in [op["path"] for op in ROLLBACK.operations if op["type"] == "file"]:
            ROLLBACK.add_file(dst, None)  # No original, just track for removal
        
        logger.debug(f"Copied: {src} -> {dst}")
        return True
        
    except shutil.SameFileError:
        logger.debug(f"Source and dest are same file: {src}")
        return False
    except PermissionError as e:
        raise InstallerError(
            f"Permission denied copying to: {dst}",
            f"Run as Administrator/root. Error: {e}"
        )
    except OSError as e:
        raise InstallerError(
            f"OS error copying to: {dst}",
            f"{e}"
        )

# ── Custom exception ─────────────────────────────────────────────────────────

class InstallerError(Exception):
    """Installation error with user-friendly messaging."""
    
    def __init__(self, message: str, details: Optional[str] = None, recoverable: bool = False):
        self.message = message
        self.details = details
        self.recoverable = recoverable
        super().__init__(message)
    
    def log_and_exit(self, logger: InstallLogger) -> None:
        logger.critical(self.message, self.details)
        logger.save_json(Path(tempfile.gettempdir()) / "rubix_install_failed.json")
        ROLLBACK.rollback()
        sys.exit(1)

# ── Binary verification ──────────────────────────────────────────────────────

def _verify_binary(path: Path, expected_arch: Optional[str] = None) -> None:
    """
    Verify binary exists, is correct architecture, and is executable.
    """
    logger = get_logger()
    
    if not path.exists():
        raise InstallerError(
            f"Binary missing: {path}",
            f"Package may be corrupted. Reinstall with:\n"
            f"  pip install --force-reinstall rubix-defzz"
        )
    
    if not path.is_file():
        raise InstallerError(f"Binary path is not a file: {path}")
    
    # Check if binary (not text/script)
    try:
        with open(path, "rb") as f:
            header = f.read(4)
            if not header:
                raise InstallerError(f"Binary file is empty: {path}")
            
            # Windows: MZ header
            if platform.system() == "Windows" and header[:2] != b"MZ":
                logger.warning(f"Binary may be corrupted (no MZ header): {path}")
            # Linux: ELF header
            elif platform.system() == "Linux" and header[:4] != b"\x7fELF":
                logger.warning(f"Binary may be corrupted (no ELF header): {path}")
                
    except Exception as e:
        logger.warning(f"Could not verify binary header: {e}")
    
    # Check architecture (Windows)
    if platform.system() == "Windows" and expected_arch:
        try:
            import struct
            with open(path, "rb") as f:
                f.seek(0x3C)  # PE header offset
                pe_offset = struct.unpack("<I", f.read(4))[0]
                f.seek(pe_offset + 4)  # Machine type
                machine = struct.unpack("<H", f.read(2))[0]
                arch_map = {0x14c: "x86", 0x8664: "x64", 0xaa64: "arm64"}
                actual_arch = arch_map.get(machine, "unknown")
                if actual_arch != expected_arch:
                    logger.warning(
                        f"Architecture mismatch: expected {expected_arch}, got {actual_arch}",
                        f"Binary: {path}"
                    )
        except Exception as e:
            logger.debug(f"Could not check architecture: {e}")
    
    # Check executable permissions (Linux)
    if platform.system() == "Linux":
        mode = path.stat().st_mode
        if not (mode & stat.S_IXUSR):
            logger.debug(f"Binary not executable, will fix: {path}")

# ── Windows-specific: NPcap handling ─────────────────────────────────────────

def _npcap_is_installed() -> bool:
    """Check NPcap via registry and file system."""
    logger = get_logger()
    
    # Registry check
    try:
        import winreg
        for hive, key in [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Npcap"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Npcap"),
        ]:
            try:
                with winreg.OpenKey(hive, key):
                    logger.debug(f"NPcap found in registry: {key}")
                    return True
            except FileNotFoundError:
                pass
    except ImportError:
        logger.debug("winreg not available for NPcap check")
    
    # File system check
    sysroot = os.environ.get("SYSTEMROOT", r"C:\Windows")
    dll_paths = [
        Path(sysroot) / "System32" / "Npcap" / "wpcap.dll",
        Path(sysroot) / "SysWOW64" / "Npcap" / "wpcap.dll",
    ]
    for dll in dll_paths:
        if dll.exists():
            logger.debug(f"NPcap DLL found: {dll}")
            return True
    
    logger.debug("NPcap not detected")
    return False

def _install_npcap() -> None:
    """Install NPcap with comprehensive error handling."""
    logger = get_logger()
    logger.info("Checking NPcap packet capture driver...")
    
    if _npcap_is_installed():
        logger.info("NPcap already installed -- skipping")
        return
    
    if not NPCAP_EXE.exists():
        logger.warning(
            "Bundled NPcap installer not found",
            f"Expected: {NPCAP_EXE}\n"
            f"Install NPcap manually: https://npcap.com/#download"
        )
        return
    
    # Verify installer integrity
    try:
        installer_size = NPCAP_EXE.stat().st_size
        if installer_size < 100_000:  # NPcap is ~1MB+
            logger.warning(f"NPcap installer seems too small ({installer_size} bytes)")
    except Exception as e:
        logger.debug(f"Could not check installer size: {e}")
    
    logger.info("Installing NPcap 1.88 (packet capture driver)...")
    logger.info("NOTE: UAC prompt may appear -- click YES to allow")
    
    temp_dir = os.environ.get("TEMP", os.environ.get("TMP", r"C:\Windows\Temp"))
    
    def _run_npcap() -> Tuple[int, Optional[Exception]]:
        try:
            result = subprocess.run(
                [str(NPCAP_EXE), "/S", "/winpcap_mode=no", "/dot11_support=no"],
                timeout=120,
                cwd=temp_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            return result.returncode, None
        except subprocess.TimeoutExpired as e:
            return -2, e
        except Exception as e:
            return -3, e
    
    rc, exc = _run_npcap()
    
    # Exit code 2 = busy (another installer running or UAC cancelled)
    if rc == 2:
        logger.info("NPcap installer busy -- waiting 3 seconds and retrying...")
        time.sleep(3)
        rc, exc = _run_npcap()
    
    # Interpret result
    if rc == 0:
        logger.info("NPcap installed successfully")
    elif rc == 1:
        logger.info("NPcap installed -- reboot required to complete")
        logger.warning("Restart your computer before running rubix")
    elif rc == 2:
        logger.warning(
            "NPcap installation blocked (code 2)",
            "Possible causes:\n"
            "  1. UAC prompt cancelled -- run as Administrator\n"
            "  2. Another installer running -- close it and retry\n"
            "  3. Antivirus blocked the installer -- add exception\n"
            "Manual install: https://npcap.com/#download"
        )
    elif rc == -2:
        logger.warning(
            "NPcap installer timed out",
            "It may still be running in background.\n"
            "If rubix fails to capture, reboot and run: rubix-install"
        )
    elif rc == -3:
        logger.warning(
            "NPcap installer failed to start",
            f"Error: {exc}\n"
            "Manual install: https://npcap.com/#download"
        )
    else:
        logger.warning(
            f"NPcap installer exited with code {rc}",
            "Manual install: https://npcap.com/#download"
        )
    
    # Post-install verification
    if _npcap_is_installed():
        logger.info("NPcap verified: wpcap.dll present")
    else:
        logger.warning(
            "wpcap.dll not found after install attempt",
            "Steps to fix:\n"
            "  1. Reboot your computer\n"
            "  2. Run as Administrator: rubix-install\n"
            "  3. Or install manually: https://npcap.com/#download"
        )

# ── Windows-specific: PATH management ────────────────────────────────────────

def _add_to_path_windows(install_dir: Path) -> None:
    """Add directory to Windows PATH with registry handling."""
    logger = get_logger()
    logger.info("Registering PATH...")
    
    try:
        import winreg
    except ImportError:
        logger.warning("Cannot import winreg -- skipping PATH update")
        return
    
    dir_str = str(install_dir)
    
    def _read_path(hive: int, subkey: str) -> Tuple[Optional[str], Optional[str]]:
        """Read PATH value, returning (value, error_or_None)."""
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                try:
                    value, _ = winreg.QueryValueEx(key, "Path")
                    return str(value), None
                except FileNotFoundError:
                    return "", None
        except PermissionError:
            return None, "Permission denied"
        except OSError as e:
            return None, str(e)
    
    def _write_path(hive: int, subkey: str, new_value: str) -> Tuple[bool, Optional[str]]:
        """Write PATH value, returning (success, error_or_None)."""
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_value)
                return True, None
        except PermissionError:
            return False, "Permission denied"
        except OSError as e:
            return False, str(e)
    
    # Try system PATH first (HKLM), then user PATH (HKCU)
    hives = [
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment", "system"),
        (winreg.HKEY_CURRENT_USER, r"Environment", "user"),
    ]
    
    for hive, subkey, scope in hives:
        current, error = _read_path(hive, subkey)
        
        if current is None:
            logger.debug(f"Cannot read {scope} PATH: {error}")
            continue
        
        # Check if already present
        entries = [p.strip() for p in current.split(";") if p.strip()]
        if dir_str.lower() in [e.lower().rstrip("\\") for e in entries]:
            logger.info(f"Already in {scope} PATH: {install_dir}")
            return
        
        # Add and write
        new_path = current.rstrip(";") + ";" + dir_str
        success, error = _write_path(hive, subkey, new_path)
        
        if success:
            logger.info(f"Added to {scope} PATH: {install_dir}")
            
            # Track for rollback
            ROLLBACK.add_registry(hive, subkey, "Path", current)
            
            # Broadcast environment change
            try:
                HWND_BROADCAST = 0xFFFF
                WM_SETTINGCHANGE = 0x001A
                SMTO_ABORTIFHUNG = 0x0002
                ctypes.windll.user32.SendMessageTimeoutW(
                    HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
                    SMTO_ABORTIFHUNG, 5000, None
                )
                logger.debug("Broadcast WM_SETTINGCHANGE for PATH update")
            except Exception as e:
                logger.debug(f"PATH broadcast failed: {e}")
            
            # Print manual refresh for current terminal
            print(f"\n  To use rubix in THIS terminal, run:")
            print(f"    PowerShell:  $env:PATH += ';{install_dir}'")
            print(f"    CMD:         set PATH=%PATH%;{install_dir}")
            print(f"  Or open a NEW terminal -- it works automatically.\n")
            return
        else:
            logger.debug(f"Cannot write {scope} PATH: {error}")
    
    logger.warning(
        "Could not update PATH automatically",
        f"Add this to PATH manually: {install_dir}"
    )

# ── Linux-specific: package management ───────────────────────────────────────

def _detect_distro() -> Tuple[str, Optional[str]]:
    """Detect Linux distribution and package manager."""
    logger = get_logger()
    
    # Check for package managers
    for pm in ("apt-get", "dnf", "pacman", "zypper", "apk"):
        if shutil.which(pm):
            logger.debug(f"Package manager found: {pm}")
            return pm, None
    
    # Try to identify from /etc/os-release
    try:
        with open("/etc/os-release") as f:
            content = f.read().lower()
            if "debian" in content or "ubuntu" in content:
                return "apt-get", "detected from /etc/os-release"
            elif "fedora" in content or "rhel" in content or "centos" in content:
                return "dnf", "detected from /etc/os-release"
            elif "arch" in content:
                return "pacman", "detected from /etc/os-release"
            elif "alpine" in content:
                return "apk", "detected from /etc/os-release"
            elif "suse" in content:
                return "zypper", "detected from /etc/os-release"
    except Exception:
        pass
    
    logger.debug("No package manager detected")
    return "unknown", None

def _install_linux_deps() -> None:
    """Install Linux dependencies with multiple fallback strategies."""
    logger = get_logger()
    logger.info("Installing system dependencies...")
    
    pm, source = _detect_distro()
    
    if pm == "unknown":
        logger.warning(
            "No supported package manager found",
            "Supported: apt-get (Debian/Ubuntu), dnf (RHEL/Fedora), "
            "pacman (Arch), zypper (SUSE), apk (Alpine)\n"
            "Install manually:\n"
            "  Debian/Ubuntu: sudo apt-get install libpcap-dev nftables\n"
            "  RHEL/Fedora:   sudo dnf install libpcap-devel nftables\n"
            "  Arch:          sudo pacman -S libpcap nftables"
        )
        return
    
    # Package mappings by distro
    PKG_MAP = {
        "apt-get": {
            "libpcap": ["libpcap-dev", "libpcap0.8-dev"],
            "nftables": ["nftables"],
        },
        "dnf": {
            "libpcap": ["libpcap-devel"],
            "nftables": ["nftables"],
        },
        "pacman": {
            "libpcap": ["libpcap"],
            "nftables": ["nftables"],
        },
        "zypper": {
            "libpcap": ["libpcap-devel"],
            "nftables": ["nftables"],
        },
        "apk": {
            "libpcap": ["libpcap-dev"],
            "nftables": ["nftables"],
        },
    }
    
    packages = PKG_MAP.get(pm, {})
    
    for category, variants in packages.items():
        installed = False
        for pkg in variants:
            cmd = {
                "apt-get": ["apt-get", "install", "-y", "--no-install-recommends", pkg],
                "dnf": ["dnf", "install", "-y", pkg],
                "pacman": ["pacman", "-S", "--noconfirm", pkg],
                "zypper": ["zypper", "install", "-y", pkg],
                "apk": ["apk", "add", pkg],
            }[pm]
            
            rc, out, err, exc = _run(cmd, timeout=180)
            
            if rc == 0:
                logger.info(f"Installed: {pkg}")
                installed = True
                break
            else:
                logger.debug(f"Failed to install {pkg}: {err[:200]}")
        
        if not installed:
            logger.warning(
                f"Could not install {category}",
                f"Tried: {', '.join(variants)}\n"
                f"Install manually if rubix fails to start."
            )

def _ensure_nf_tables_module() -> None:
    """Ensure nf_tables kernel module is loaded."""
    logger = get_logger()
    
    rc, out, _, _ = _run(["lsmod"])
    if rc != 0:
        logger.debug("Cannot check loaded modules (lsmod failed)")
        return
    
    if "nf_tables" in out:
        logger.info("Kernel module nf_tables: loaded")
        return
    
    logger.info("Loading nf_tables kernel module...")
    rc, _, err, _ = _run(["modprobe", "nf_tables"], timeout=10)
    
    if rc == 0:
        logger.info("nf_tables loaded successfully")
    elif rc == -1:
        logger.warning(
            "modprobe not found",
            "nftables blocking may not work. Install kmod or modutils."
        )
    else:
        logger.warning(
            f"modprobe nf_tables failed: {err}",
            "nftables blocking may not work. Try: sudo modprobe nf_tables"
        )

def _set_capabilities(binary: Path) -> None:
    """Set Linux capabilities on binary."""
    logger = get_logger()
    
    if not shutil.which("setcap"):
        logger.warning(
            "setcap not found",
            "rubix will need to run as root.\n"
            "Install libcap2-bin (Debian) or libcap (RHEL)."
        )
        return
    
    rc, _, err, _ = _run(
        ["setcap", "cap_net_raw,cap_net_admin=eip", str(binary)],
        timeout=10
    )
    
    if rc == 0:
        logger.info(f"Capabilities set on {binary.name}")
    else:
        logger.warning(
            f"setcap failed: {err}",
            "rubix will need sudo to capture packets."
        )

def _verify_libpcap() -> None:
    """Verify libpcap is available in linker cache."""
    logger = get_logger()
    
    rc, out, _, _ = _run(["ldconfig", "-p"], timeout=10)
    if rc != 0:
        logger.debug("ldconfig failed, skipping libpcap verification")
        return
    
    if any("libpcap" in line for line in out.splitlines()):
        logger.info("libpcap found in linker cache")
    else:
        logger.warning(
            "libpcap not found in linker cache",
            "rubix may fail to start. Fix:\n"
            "  sudo apt-get install libpcap0.8  (Debian/Ubuntu)\n"
            "  sudo dnf install libpcap          (RHEL/Fedora)\n"
            "  Then run: sudo ldconfig"
        )

def _create_systemd_unit(bin_dir: Path) -> None:
    """Create systemd service unit for rubix."""
    logger = get_logger()
    
    if not shutil.which("systemctl"):
        logger.info("systemd not detected -- skipping service unit (container/WSL?)")
        return
    
    # Check if systemd is actually running (not just installed)
    rc, _, _, _ = _run(["systemctl", "is-system-running"], timeout=5)
    if rc not in (0, 1):  # 0=running, 1=degraded (still OK)
        logger.info("systemd not running -- skipping service unit")
        return
    
    unit_content = textwrap.dedent(f"""\
        [Unit]
        Description=RUBIX Network Defence System
        Documentation=https://github.com/yourusername/rubix
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=notify
        ExecStart={bin_dir}/rubix
        WorkingDirectory={bin_dir}
        Restart=on-failure
        RestartSec=5s
        StartLimitInterval=60s
        StartLimitBurst=3
        
        # Security hardening
        NoNewPrivileges=true
        ProtectSystem=strict
        ProtectHome=true
        ReadWritePaths=/var/log/rubix /tmp
        AmbientCapabilities=CAP_NET_RAW CAP_NET_ADMIN
        CapabilityBoundingSet=CAP_NET_RAW CAP_NET_ADMIN
        
        # Logging
        StandardOutput=journal
        StandardError=journal
        SyslogIdentifier=rubix
        
        [Install]
        WantedBy=multi-user.target
    """)
    
    try:
        # Backup existing unit
        if LINUX_UNIT.exists():
            backup = LINUX_UNIT.with_suffix(".service.backup")
            shutil.copy2(LINUX_UNIT, backup)
            logger.debug(f"Backed up existing unit to {backup}")
            ROLLBACK.add_file(LINUX_UNIT, LINUX_UNIT.read_bytes())
        
        LINUX_UNIT.write_text(unit_content, encoding="utf-8")
        LINUX_UNIT.chmod(0o644)
        ROLLBACK.add_file(LINUX_UNIT, None)
        logger.info(f"Created systemd unit: {LINUX_UNIT}")
        
    except OSError as e:
        logger.warning(f"Could not write systemd unit: {e}")
        return
    
    # Reload systemd
    rc, _, err, _ = _run(["systemctl", "daemon-reload"], timeout=10)
    if rc == 0:
        logger.info("systemctl daemon-reload complete")
    else:
        logger.warning(f"daemon-reload failed: {err}")

# ── Main installation functions ──────────────────────────────────────────────

def install_windows() -> None:
    """Windows installation with full error handling."""
    logger = get_logger()
    logger.info("Starting Windows installation...")
    
    is_admin = _is_admin()
    if not is_admin:
        logger.warning(
            "Not running as Administrator",
            "NPcap and system-wide PATH require elevation.\n"
            "For full install: right-click PowerShell -> Run as administrator\n"
            "Continuing with user-level install only..."
        )
    
    install_dir = WIN_INSTALL_DIR
    config_dir = WIN_CONFIG_DIR
    log_dir = WIN_LOG_DIR
    
    logger.info(f"Install directory: {install_dir}")
    
    # Verify binaries exist
    _verify_binary(BIN_WIN / "rubix.exe", expected_arch="x64")
    _verify_binary(BIN_WIN / "rubix-cli.exe", expected_arch="x64")
    
    # Create directories
    try:
        _mkdir_safe(install_dir)
        _mkdir_safe(config_dir)
        _mkdir_safe(log_dir)
    except InstallerError as e:
        e.log_and_exit(logger)
    
    # Verify write access with probe file
    probe = install_dir / ".rubix_write_probe"
    try:
        probe.write_bytes(b"rubix")
        probe.unlink()
    except OSError as e:
        InstallerError(
            f"Cannot write to {install_dir}",
            f"Run as Administrator or check permissions.\nError: {e}"
        ).log_and_exit(logger)
    
    # Initialize logger in install directory now that it exists
    global LOG
    LOG = InstallLogger(log_dir)
    logger = LOG  # Use new logger
    
    # Copy binaries
    logger.info("Installing binaries...")
    for exe in ("rubix.exe", "rubix-cli.exe"):
        src = BIN_WIN / exe
        dst = install_dir / exe
        try:
            copied = _copy_file(src, dst, preserve_existing=False)
            if copied:
                logger.info(f"Installed: {exe}")
            else:
                logger.info(f"Updated: {exe}")
        except InstallerError as e:
            e.log_and_exit(logger)
    
    # Copy configs (never overwrite existing)
    logger.info("Installing default configurations...")
    for cfg in sorted(CONFIGS.iterdir()):
        if not cfg.is_file():
            continue
        dst = config_dir / cfg.name
        try:
            copied = _copy_file(cfg, dst, preserve_existing=True)
            if copied:
                logger.info(f"Installed config: {cfg.name}")
            else:
                logger.info(f"Preserved existing config: {cfg.name}")
        except InstallerError as e:
            e.log_and_exit(logger)
    
    # Install NPcap
    _install_npcap()
    
    # Add to PATH
    _add_to_path_windows(install_dir)
    
    # Success
    ROLLBACK.commit()
    logger.info("Windows installation completed successfully")
    
    print(textwrap.dedent(f"""
    ╔══════════════════════════════════════════════════════════════╗
    ║           RUBIX installed successfully -- Windows            ║
    ╠══════════════════════════════════════════════════════════════╣
    ║  Location : {str(install_dir):<49}║
    ║  Configs  : {str(config_dir):<49}║
    ║  Logs     : {str(log_dir):<49}║
    ╠══════════════════════════════════════════════════════════════╣
    ║  Open a NEW terminal (as Administrator), then run:           ║
    ║                                                              ║
    ║    rubix          -- start the network defence engine        ║
    ║    rubix-cli      -- monitor, logs, and control              ║
    ╚══════════════════════════════════════════════════════════════╝
    """))

def install_linux() -> None:
    """Linux installation with full error handling."""
    logger = get_logger()
    logger.info("Starting Linux installation...")
    
    if not _is_admin():
        InstallerError(
            "Installation requires root privileges",
            "Run: sudo pip install rubix-defzz\n"
            "Or:  sudo python -m rubix_installer.install"
        ).log_and_exit(logger)
    
    # Verify binaries
    _verify_binary(BIN_LINUX / "rubix")
    _verify_binary(BIN_LINUX / "rubix-cli")
    
    # Install dependencies
    _install_linux_deps()
    _ensure_nf_tables_module()
    
    # Create directories
    try:
        _mkdir_safe(LINUX_CONF, mode=0o755)
        _mkdir_safe(LINUX_LOGS, mode=0o755)
    except InstallerError as e:
        e.log_and_exit(logger)
    
    # Initialize logger in log directory
    global LOG
    LOG = InstallLogger(LINUX_LOGS)
    logger = LOG
    
    # Copy binaries
    logger.info("Installing binaries...")
    for name in ("rubix", "rubix-cli"):
        src = BIN_LINUX / name
        dst = LINUX_BIN / name
        
        try:
            copied = _copy_file(src, dst, preserve_existing=False)
            if copied:
                # Make executable
                dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                logger.info(f"Installed: {name}")
            else:
                logger.info(f"Updated: {name}")
        except InstallerError as e:
            e.log_and_exit(logger)
    
    # Set capabilities
    _set_capabilities(LINUX_BIN / "rubix")
    
    # Copy configs
    logger.info("Installing default configurations...")
    for cfg in sorted(CONFIGS.iterdir()):
        if not cfg.is_file():
            continue
        dst = LINUX_CONF / cfg.name
        try:
            copied = _copy_file(cfg, dst, preserve_existing=True)
            if copied:
                logger.info(f"Installed config: {cfg.name}")
            else:
                logger.info(f"Preserved existing config: {cfg.name}")
        except InstallerError as e:
            e.log_and_exit(logger)
    
    # Verify libpcap
    _verify_libpcap()
    
    # Create systemd unit
    _create_systemd_unit(LINUX_BIN)
    
    # Success
    ROLLBACK.commit()
    logger.info("Linux installation completed successfully")
    
    print(textwrap.dedent(f"""
    ╔══════════════════════════════════════════════════════════════╗
    ║            RUBIX installed successfully -- Linux             ║
    ╠══════════════════════════════════════════════════════════════╣
    ║  Binaries : /usr/local/bin/rubix                             ║
    ║             /usr/local/bin/rubix-cli                         ║
    ║  Configs  : /etc/rubix/                                      ║
    ║  Logs     : /var/log/rubix/                                  ║
    ║  Service  : /etc/systemd/system/rubix.service                ║
    ╠══════════════════════════════════════════════════════════════╣
    ║  sudo systemctl start rubix    (background service)          ║
    ║  sudo systemctl enable rubix   (start on boot)               ║
    ║  rubix-cli monitor             (live statistics)             ║
    ║  rubix-cli logs                (live log stream)             ║
    ╚══════════════════════════════════════════════════════════════╝
    """))

# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    """Main entry point with comprehensive error handling."""
    # Initialize basic logger (will be re-initialized after dir creation)
    global LOG
    LOG = InstallLogger()
    logger = LOG
    
    # Python version check
    if sys.version_info < __min_python__:
        logger.critical(
            f"Python {__min_python__[0]}.{__min_python__[1]}+ required",
            f"Current: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        )
        sys.exit(1)
    
    # Platform detection
    system = platform.system()
    logger.info(f"RUBIX Network Defence System v{__version__}")
    logger.info(f"Author: {__author__}")
    logger.info(f"Platform: {system} {platform.release()} ({platform.machine()})")
    logger.debug(f"Python: {sys.version.split()[0]}")
    logger.debug(f"Admin: {_is_admin()}")
    
    # Register rollback on abnormal exit
    def _emergency_rollback():
        if not ROLLBACK.completed:
            logger.critical("Installation interrupted -- rolling back...")
            ROLLBACK.rollback()
    
    atexit.register(_emergency_rollback)
    
    # Platform dispatch
    try:
        if system == "Windows":
            install_windows()
        elif system == "Linux":
            install_linux()
        else:
            InstallerError(
                f"Unsupported platform: {system}",
                "RUBIX supports Windows 10/11 and Linux (kernel >= 4.14).\n"
                f"Your platform: {system} {platform.release()}"
            ).log_and_exit(logger)
            
    except KeyboardInterrupt:
        logger.critical("Installation cancelled by user")
        sys.exit(130)
    except InstallerError as e:
        e.log_and_exit(logger)
    except Exception as e:
        logger.critical(
            f"Unexpected error: {type(e).__name__}",
            f"{str(e)}\n\n{traceback.format_exc()}"
        )
        ROLLBACK.rollback()
        sys.exit(1)
    
    # Normal exit -- disable rollback
    atexit.unregister(_emergency_rollback)

if __name__ == "__main__":
    main()