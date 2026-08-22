"""Network diagnostic for Chrome profile."""
import json
from pathlib import Path

print("=" * 60)
print("CHROME PROFILE NETWORK CONFIGURATION")
print("=" * 60)
print()

# 1. Check Local State
print("=== LOCAL STATE ===")
local_state_path = Path("browser_profile/Local State")
if local_state_path.exists():
    with open(local_state_path, "r", encoding="utf-8") as f:
        local_state = json.load(f)
    
    # Check for proxy settings
    if "proxy" in str(local_state).lower():
        print("Proxy-related settings found in Local State")
    else:
        print("No proxy settings in Local State")
else:
    print("Local State file not found")
print()

# 2. Check Default/Preferences
print("=== DEFAULT PREFERENCES ===")
prefs_path = Path("browser_profile/Default/Preferences")
if prefs_path.exists():
    with open(prefs_path, "r", encoding="utf-8") as f:
        prefs = json.load(f)
    
    # Check for proxy settings
    proxy_settings = prefs.get("proxy", {})
    if proxy_settings:
        print(f"Proxy settings: {proxy_settings}")
    else:
        print("No proxy settings in Preferences")
    
    # Check for network prediction
    net_settings = prefs.get("net", {})
    if net_settings:
        print(f"Net settings: {net_settings}")
    
    # Check for DNS settings
    dns_over_https = prefs.get("dns_over_https", {})
    if dns_over_https:
        print(f"DNS over HTTPS: {dns_over_https}")
    
    # Check for extensions
    extensions = prefs.get("extensions", {}).get("settings", {})
    if extensions:
        print(f"Number of extensions: {len(extensions)}")
        for ext_id, ext_data in list(extensions.items())[:5]:
            name = ext_data.get("manifest", {}).get("name", "Unknown")
            print(f"  - {name} ({ext_id[:8]}...)")
else:
    print("Preferences file not found")
print()

# 3. Check Windows proxy settings
print("=== WINDOWS PROXY SETTINGS ===")
import subprocess
try:
    result = subprocess.run(
        ["netsh", "winhttp", "show", "proxy"],
        capture_output=True,
        text=True
    )
    print(result.stdout)
except Exception as e:
    print(f"Could not check WinHTTP proxy: {e}")

# Check Internet Options proxy
try:
    import winreg
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    )
    proxy_enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
    print(f"IE Proxy Enabled: {proxy_enable}")
    try:
        proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
        print(f"IE Proxy Server: {proxy_server}")
    except:
        print("IE Proxy Server: Not set")
    winreg.CloseKey(key)
except Exception as e:
    print(f"Could not check IE proxy: {e}")
print()

# 4. Chrome launch arguments
print("=== CHROME LAUNCH ARGUMENTS ===")
from app.flow.browser import get_chrome_launch_command
cmd = get_chrome_launch_command()
print("Command:")
for arg in cmd:
    print(f"  {arg}")
print()

# Check for proxy-related arguments
proxy_args = ["proxy", "host-resolver", "dns"]
print("Proxy-related arguments:")
found_proxy_args = False
for arg in cmd:
    for proxy_term in proxy_args:
        if proxy_term in arg.lower():
            print(f"  FOUND: {arg}")
            found_proxy_args = True
if not found_proxy_args:
    print("  None found")
print()

print("=== DIAGNOSTIC COMPLETE ===")
