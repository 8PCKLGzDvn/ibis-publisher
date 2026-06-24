"""
Ibis Publisher · notifications.py
Cross-platform desktop notification sender.
Uses plyer if available, falls back to platform-native methods.
"""

import sys
import subprocess


APP_NAME = "Ibis Publisher"
ICON_PATH = None  # set to bundled .icns / .ico path if available


def send(title: str, message: str):
    """Send a desktop notification. Fails silently on unsupported platforms."""
    try:
        _send_native(title, message)
    except Exception:
        try:
            _send_plyer(title, message)
        except Exception:
            pass  # Silent fail — notifications are nice-to-have


def _send_plyer(title: str, message: str):
    from plyer import notification
    notification.notify(
        title=title,
        message=message,
        app_name=APP_NAME,
        app_icon=ICON_PATH or '',
        timeout=8,
    )


def _send_native(title: str, message: str):
    if sys.platform == 'darwin':
        # macOS: use osascript
        script = (
            f'display notification "{_esc(message)}" '
            f'with title "{_esc(title)}" '
            f'subtitle "{_esc(APP_NAME)}"'
        )
        subprocess.run(['osascript', '-e', script],
                       capture_output=True, timeout=5)

    elif sys.platform == 'win32':
        # Windows 10+: use PowerShell toast
        ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null
$template = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{_esc_ps(title)}</text>
      <text>{_esc_ps(message)}</text>
    </binding>
  </visual>
</toast>
"@
$xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_NAME}').Show($toast)
"""
        subprocess.run(
            ['powershell', '-Command', ps_script],
            capture_output=True, timeout=10
        )

    else:
        # Linux: notify-send
        subprocess.run(
            ['notify-send', '-a', APP_NAME, title, message],
            capture_output=True, timeout=5
        )


def _esc(s: str) -> str:
    return s.replace('"', '\\"').replace('\n', ' ')


def _esc_ps(s: str) -> str:
    return s.replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace('\n', ' ')
