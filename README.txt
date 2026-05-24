RUBIX Network Defense Engine v1.0.0
=====================================
Real-time packet capture, threat detection and network blocking.


WINDOWS — QUICK START
─────────────────────
1. Install Npcap (required — do this FIRST):
     Go to: https://npcap.com/#download
     Download and run the installer as Administrator
     IMPORTANT: tick "Install Npcap in WinPcap API-compatible Mode"

2. Right-click install.bat → Run as Administrator
   (RUBIX installs itself as a Windows service and starts automatically)

3. Open any terminal and run:
     rubix-cli monitor         ← live dashboard
     rubix-cli status          ← check it is running

4. Open the web dashboard:
     http://127.0.0.1:7878
     (copy the token printed in the service terminal on first start)


COMMANDS
────────
rubix-cli monitor              Live terminal dashboard (Ctrl+C to exit)
rubix-cli status               Daemon status + uptime + rule counts
rubix-cli logs                 Stream all security events
rubix-cli logs blocks          Stream Block events only
rubix-cli logs alerts          Stream Alert events only
rubix-cli logs threats         Stream Threat detections only
rubix-cli logs normal          Stream allowed traffic (if enabled)
rubix-cli block 1.2.3.4        Block an IP permanently
rubix-cli block 1.2.3.4 --duration 3600   Block for 1 hour
rubix-cli unblock 1.2.3.4      Remove an IP block
rubix-cli list                 List all active IP blocks
rubix-cli block-pid 1234       Block a process by PID
rubix-cli block-exe notepad.exe  Block all instances of a program
rubix-cli list-processes       List all process blocks
rubix-cli rules                List all loaded policy rules
rubix-cli reload               Reload rules.yaml without restarting


SERVICE CONTROL (as Administrator)
───────────────────────────────────
sc start RUBIX                 Start the service
sc stop  RUBIX                 Stop the service
sc query RUBIX                 Check service status


CONFIG FILES
────────────
C:\Program Files\RUBIX\configs\rubix.windows.yaml   Main config
C:\Program Files\RUBIX\configs\rules.yaml            Block/Alert rules

Edit with:
  notepad "C:\Program Files\RUBIX\configs\rules.yaml"
Then reload:
  rubix-cli reload


LOG FILES
─────────
C:\ProgramData\rubix\logs\rubix.log      Daemon log
C:\ProgramData\rubix\logs\alerts.log     Security alerts and blocks


UNINSTALL
─────────
Right-click uninstall.bat → Run as Administrator


SUPPORT
───────
If rubix-cli cannot connect: make sure the service is running
  sc query RUBIX
  sc start RUBIX

If Npcap error on start: reinstall Npcap from https://npcap.com
