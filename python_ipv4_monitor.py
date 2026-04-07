#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import objc  # type: ignore
    from AppKit import (  # type: ignore
        NSApp,
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSBackingStoreBuffered,
        NSColor,
        NSEvent,
        NSEventMaskRightMouseDown,
        NSFloatingWindowLevel,
        NSFont,
        NSMenu,
        NSMenuItem,
        NSMakeRect,
        NSPanel,
        NSScreen,
        NSTextField,
        NSView,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowStyleMaskBorderless,
        NSWindowStyleMaskNonactivatingPanel,
    )
    from Foundation import NSObject, NSMakePoint, NSTimer  # type: ignore
    from PyObjCTools import AppHelper  # type: ignore
except Exception as exc:  # pragma: no cover - environment-dependent
    objc = None
    NSApp = None
    NSApplication = None
    AppHelper = None
    _PYOBJC_IMPORT_ERROR = exc
else:
    _PYOBJC_IMPORT_ERROR = None


ENDPOINTS = [
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://ipv4.icanhazip.com",
    "https://ipinfo.io/ip",
]

VPN_PREFIXES = ("utun", "ppp", "ipsec", "wg", "tun", "tap")
PHYSICAL_PREFIXES = ("en", "bridge")
IPV4_RE = re.compile(
    r"\b(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}\b"
)

STATE_DIR = Path.home() / ".mac_ipv4_monitor"
STATE_FILE = STATE_DIR / "state.json"


@dataclass
class Snapshot:
    vpn_ipv4: Optional[str]
    direct_ipv4: Optional[str]
    vpn_active: bool
    updated_at: float
    note: str


class AppStateStore:
    def __init__(self, path: Path = STATE_FILE) -> None:
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, payload: dict) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_command(args: list[str], timeout: float = 2.5) -> str:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(str(exc)) from exc

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(stderr or f"command failed: {' '.join(args)}")
    return proc.stdout


def extract_ipv4(text: str) -> Optional[str]:
    match = IPV4_RE.search(text)
    return match.group(0) if match else None


def fetch_public_ipv4(endpoints: list[str], interface: Optional[str], timeout: float) -> tuple[str, str]:
    errors: list[str] = []

    for endpoint in endpoints:
        try:
            if interface:
                output = run_command(
                    [
                        "/usr/bin/curl",
                        "-4",
                        "-sS",
                        "--max-time",
                        str(max(1, int(round(timeout)))),
                        "--interface",
                        interface,
                        endpoint,
                    ],
                    timeout=timeout + 1,
                )
            else:
                req = Request(endpoint, headers={"User-Agent": "mac-ipv4-monitor/3.0"})
                with urlopen(req, timeout=timeout) as resp:
                    output = resp.read().decode("utf-8", errors="ignore")

            value = extract_ipv4(output)
            if value:
                return value, endpoint
            errors.append(f"{endpoint}: no ipv4 in response")
        except (URLError, HTTPError, TimeoutError, RuntimeError) as exc:
            errors.append(f"{endpoint}: {exc}")

    raise RuntimeError("; ".join(errors) if errors else "all endpoints failed")


def parse_active_interfaces(scutil_output: str) -> list[str]:
    result: list[str] = []
    for raw_line in scutil_output.splitlines():
        if ": flags" not in raw_line:
            continue
        left = raw_line.split(":", 1)[0].strip()
        if left == "REACH" or not left:
            continue
        if left not in result:
            result.append(left)
    return result


def get_active_interfaces() -> list[str]:
    try:
        output = run_command(["/usr/sbin/scutil", "--nwi"], timeout=1.8)
    except RuntimeError:
        return []
    return parse_active_interfaces(output)


def get_default_route_interface() -> Optional[str]:
    try:
        output = run_command(["/sbin/route", "-n", "get", "default"], timeout=1.8)
    except RuntimeError:
        return None

    for line in output.splitlines():
        line = line.strip()
        if line.startswith("interface:"):
            return line.split(":", 1)[1].strip()
    return None


def is_vpn_interface(name: str) -> bool:
    low = name.lower()
    return any(low.startswith(prefix) for prefix in VPN_PREFIXES)


def is_physical_interface(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in PHYSICAL_PREFIXES)


class NetworkMonitor:
    def __init__(self, store: AppStateStore, endpoints: list[str], timeout: float = 2.5) -> None:
        self.store = store
        self.endpoints = endpoints
        self.timeout = timeout

    def refresh(self) -> Snapshot:
        state = self.store.load()
        now = time.time()

        active_interfaces = get_active_interfaces()
        default_interface = get_default_route_interface()
        physical = [name for name in active_interfaces if is_physical_interface(name)]

        vpn_active = False
        if default_interface and is_vpn_interface(default_interface):
            vpn_active = True
        if any(is_vpn_interface(name) for name in active_interfaces):
            vpn_active = True

        notes: list[str] = []

        vpn_ipv4: Optional[str] = None
        try:
            vpn_ipv4, _ = fetch_public_ipv4(self.endpoints, interface=None, timeout=self.timeout)
        except RuntimeError as exc:
            notes.append(f"vpn probe: {exc}")

        direct_ipv4: Optional[str] = None

        candidate_interfaces = list(physical)
        if default_interface and is_physical_interface(default_interface) and default_interface not in candidate_interfaces:
            candidate_interfaces.insert(0, default_interface)

        for iface in candidate_interfaces:
            try:
                direct_ipv4, _ = fetch_public_ipv4(self.endpoints, interface=iface, timeout=self.timeout)
                break
            except RuntimeError as exc:
                notes.append(f"[{iface}] {exc}")

        if direct_ipv4 and not vpn_active:
            state["cached_direct_ipv4"] = direct_ipv4
            state["cached_direct_at"] = now

        if not direct_ipv4:
            cached_ip = state.get("cached_direct_ipv4")

            if not vpn_active and vpn_ipv4:
                direct_ipv4 = vpn_ipv4
                state["cached_direct_ipv4"] = vpn_ipv4
                state["cached_direct_at"] = now
            elif vpn_active and cached_ip:
                direct_ipv4 = str(cached_ip)

        self.store.save(state)

        return Snapshot(
            vpn_ipv4=vpn_ipv4,
            direct_ipv4=direct_ipv4,
            vpn_active=vpn_active,
            note=" | ".join(notes),
            updated_at=now,
        )


if objc is not None:

    class OverlayContentView(NSView):
        controller = objc.ivar()

        def initWithFrame_controller_(self, frame, controller):
            self = objc.super(OverlayContentView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.controller = controller
            return self

        def acceptsFirstMouse_(self, _event):
            return True

        def mouseDown_(self, event):
            if self.controller is None:
                return
            if event.clickCount() >= 2:
                self.controller.toggle_lock()
                return
            if not self.controller.locked:
                win = self.window()
                if win is not None:
                    win.performWindowDragWithEvent_(event)

        def rightMouseDown_(self, event):
            if self.controller is None:
                return
            self.controller.show_context_menu(event, self)

        def menuForEvent_(self, _event):
            if self.controller is None:
                return None
            return self.controller.build_context_menu()


    class OverlayController(NSObject):
        def init(self):
            self = objc.super(OverlayController, self).init()
            if self is None:
                return None

            self.store = AppStateStore()
            self.monitor = NetworkMonitor(self.store, ENDPOINTS)
            self.state = self.store.load()
            self.locked = bool(self.state.get("locked", False))
            self.passthrough = False
            self.refreshing = False
            self.timer = None
            self.global_right_click_monitor = None

            self.main_window = None
            self.content_view = None
            self.vpn_label = None
            self.sep1_label = None
            self.direct_label = None
            self.sep2_label = None
            self.time_label = None

            self.color_base = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.93, 0.97, 1.0, 0.95)
            self.color_dim = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.7, 0.78, 0.86, 0.75)
            self.color_diff = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.46, 0.96, 0.74, 0.98)
            self.color_same = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.98, 0.8, 0.3, 0.98)
            self.color_missing = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.47, 0.47, 0.98)
            self.color_passthrough = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.58, 0.9, 1.0, 0.98)
            return self

        def applicationDidFinishLaunching_(self, _notification):
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

            self._setup_main_window()
            self._apply_interaction_mode()
            self.main_window.orderFrontRegardless()

            self.refresh_async()
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                3.0,
                self,
                b"onTimer:",
                None,
                True,
            )
            self.global_right_click_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSEventMaskRightMouseDown, self._on_global_right_click
            )

        def _default_origin(self, width: float, height: float) -> tuple[float, float]:
            screen = NSScreen.mainScreen()
            if screen is None:
                return 80.0, 80.0
            visible = screen.visibleFrame()
            x = float(visible.origin.x + max(20.0, (visible.size.width - width) / 2.0))
            y = float(visible.origin.y + visible.size.height - height - 44.0)
            return x, y

        def _make_label(self, text: str, color) -> NSTextField:
            label = NSTextField.labelWithString_(text)
            label.setFont_(NSFont.monospacedSystemFontOfSize_weight_(10.0, 0.56))
            label.setTextColor_(color)
            label.setBackgroundColor_(NSColor.clearColor())
            label.setDrawsBackground_(False)
            return label

        def _setup_main_window(self):
            width = 320.0
            height = 24.0
            if "window_x" in self.state and "window_y" in self.state:
                x = float(self.state["window_x"])
                y = float(self.state["window_y"])
            else:
                x, y = self._default_origin(width, height)

            style_mask = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
            frame = NSMakeRect(x, y, width, height)
            self.main_window = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                frame,
                style_mask,
                NSBackingStoreBuffered,
                False,
            )
            self.main_window.setOpaque_(False)
            self.main_window.setBackgroundColor_(NSColor.clearColor())
            self.main_window.setHasShadow_(False)
            self.main_window.setLevel_(NSFloatingWindowLevel)
            self.main_window.setMovableByWindowBackground_(False)
            self.main_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            self.main_window.setDelegate_(self)

            self.content_view = OverlayContentView.alloc().initWithFrame_controller_(NSMakeRect(0, 0, width, height), self)
            self.content_view.setWantsLayer_(True)

            self.vpn_label = self._make_label("VPN --", self.color_base)
            self.sep1_label = self._make_label(" | ", self.color_dim)
            self.direct_label = self._make_label("D --", self.color_base)
            self.sep2_label = self._make_label(" · ", self.color_dim)
            self.time_label = self._make_label("--:--:--", self.color_dim)

            self.content_view.addSubview_(self.vpn_label)
            self.content_view.addSubview_(self.sep1_label)
            self.content_view.addSubview_(self.direct_label)
            self.content_view.addSubview_(self.sep2_label)
            self.content_view.addSubview_(self.time_label)
            self.main_window.setContentView_(self.content_view)
            self._layout_labels()

        def _save_window_position(self):
            if self.main_window is None:
                return
            frame = self.main_window.frame()
            self.state["window_x"] = float(frame.origin.x)
            self.state["window_y"] = float(frame.origin.y)
            self.state["locked"] = self.locked
            self.store.save(self.state)

        def _layout_labels(self):
            if self.main_window is None or self.vpn_label is None:
                return

            labels = [self.vpn_label, self.sep1_label, self.direct_label, self.sep2_label, self.time_label]
            x = 6.0
            y = 4.0
            for label in labels:
                label.sizeToFit()
                width = float(label.frame().size.width)
                label.setFrame_(NSMakeRect(x, y, width, 14.0))
                x += width

            width = max(190.0, min(760.0, x + 6.0))
            frame = self.main_window.frame()
            if abs(frame.size.width - width) > 1.0:
                self.main_window.setFrame_display_(
                    NSMakeRect(frame.origin.x, frame.origin.y, width, frame.size.height),
                    True,
                )
                if self.content_view is not None:
                    self.content_view.setFrame_(NSMakeRect(0, 0, width, frame.size.height))

        def _apply_interaction_mode(self):
            if self.main_window is None:
                return

            self.main_window.setIgnoresMouseEvents_(self.passthrough)
            if self.time_label is not None:
                if self.passthrough:
                    self.time_label.setTextColor_(self.color_passthrough)
                elif self.locked:
                    self.time_label.setTextColor_(self.color_same)
                else:
                    self.time_label.setTextColor_(self.color_dim)
            self._save_window_position()

        def toggle_lock(self):
            if self.passthrough:
                return
            self.locked = not self.locked
            self._apply_interaction_mode()

        def toggle_passthrough(self):
            if self.passthrough:
                self.passthrough = False
            else:
                self.passthrough = True
                self.locked = True
            self._apply_interaction_mode()

        def build_context_menu(self):
            menu = NSMenu.alloc().initWithTitle_("IPv4 Overlay")

            lock_title = "解锁拖动（双击）" if self.locked else "锁定位置（双击）"
            lock_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(lock_title, b"toggleLockFromMenu:", "")
            lock_item.setTarget_(self)
            menu.addItem_(lock_item)

            passthrough_title = "关闭穿透模式" if self.passthrough else "开启穿透模式"
            passthrough_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                passthrough_title, b"togglePassthroughFromMenu:", ""
            )
            passthrough_item.setTarget_(self)
            menu.addItem_(passthrough_item)

            refresh_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("立即刷新", b"refreshFromMenu:", "")
            refresh_item.setTarget_(self)
            menu.addItem_(refresh_item)

            reset_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("重置到顶部居中", b"resetPosition:", "")
            reset_item.setTarget_(self)
            menu.addItem_(reset_item)

            menu.addItem_(NSMenuItem.separatorItem())

            quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("退出", b"closeApp:", "")
            quit_item.setTarget_(self)
            menu.addItem_(quit_item)
            return menu

        def show_context_menu(self, event, view):
            menu = self.build_context_menu()
            NSMenu.popUpContextMenu_withEvent_forView_(menu, event, view)

        def refresh_async(self):
            if self.refreshing:
                return
            self.refreshing = True
            threading.Thread(target=self._refresh_worker, daemon=True).start()

        def _refresh_worker(self):
            try:
                snapshot = self.monitor.refresh()
            except Exception as exc:  # noqa: BLE001
                snapshot = Snapshot(
                    vpn_ipv4=None,
                    direct_ipv4=None,
                    vpn_active=False,
                    note=str(exc),
                    updated_at=time.time(),
                )
            AppHelper.callAfter(self._apply_snapshot, snapshot)

        def _apply_snapshot(self, snapshot: Snapshot):
            self.refreshing = False
            if self.vpn_label is None or self.direct_label is None:
                return

            vpn_ip = snapshot.vpn_ipv4 or "--"
            direct_ip = snapshot.direct_ipv4 or "--"
            self.vpn_label.setStringValue_(f"VPN {vpn_ip}")

            if snapshot.vpn_ipv4 is None or snapshot.direct_ipv4 is None:
                direct_prefix = "D ? "
                status_color = self.color_missing
            elif snapshot.vpn_ipv4 == snapshot.direct_ipv4:
                direct_prefix = "D = "
                status_color = self.color_same
            else:
                direct_prefix = "D ≠ "
                status_color = self.color_diff

            self.direct_label.setStringValue_(f"{direct_prefix}{direct_ip}")
            self.direct_label.setTextColor_(status_color)
            self.vpn_label.setTextColor_(self.color_missing if snapshot.vpn_ipv4 is None else self.color_base)

            if self.sep1_label is not None:
                self.sep1_label.setTextColor_(self.color_dim)
            if self.sep2_label is not None:
                self.sep2_label.setTextColor_(self.color_dim)

            if self.time_label is not None:
                ts = datetime.fromtimestamp(snapshot.updated_at).strftime("%H:%M:%S")
                self.time_label.setStringValue_(ts)
                if self.passthrough:
                    self.time_label.setTextColor_(self.color_passthrough)
                elif self.locked:
                    self.time_label.setTextColor_(self.color_same)
                else:
                    self.time_label.setTextColor_(self.color_dim)

            self._layout_labels()

        def onTimer_(self, _timer):
            self.refresh_async()

        def toggleLockFromMenu_(self, _sender):
            self.toggle_lock()

        def togglePassthroughFromMenu_(self, _sender):
            self.toggle_passthrough()

        def refreshFromMenu_(self, _sender):
            self.refresh_async()

        def resetPosition_(self, _sender):
            if self.main_window is None:
                return
            frame = self.main_window.frame()
            x, y = self._default_origin(frame.size.width, frame.size.height)
            self.main_window.setFrameOrigin_(NSMakePoint(x, y))
            self._save_window_position()

        def closeApp_(self, _sender):
            self.close_all_windows()
            NSApp.terminate_(None)

        def windowDidMove_(self, notification):
            if self.main_window is not None and notification.object() == self.main_window:
                self._save_window_position()

        def close_all_windows(self):
            self._save_window_position()
            if self.timer is not None:
                self.timer.invalidate()
                self.timer = None
            if self.global_right_click_monitor is not None:
                NSEvent.removeMonitor_(self.global_right_click_monitor)
                self.global_right_click_monitor = None
            if self.main_window is not None:
                self.main_window.orderOut_(None)
                self.main_window.close()

        def _on_global_right_click(self, event):
            if not self.passthrough or self.main_window is None:
                return

            point = event.locationInWindow()
            frame = self.main_window.frame()
            within_x = frame.origin.x <= point.x <= frame.origin.x + frame.size.width
            within_y = frame.origin.y <= point.y <= frame.origin.y + frame.size.height
            if within_x and within_y:
                AppHelper.callAfter(self._disable_passthrough_from_global_click)

        def _disable_passthrough_from_global_click(self):
            if self.passthrough:
                self.passthrough = False
                self._apply_interaction_mode()


    class AppDelegate(NSObject):
        def init(self):
            self = objc.super(AppDelegate, self).init()
            if self is None:
                return None
            self.controller = OverlayController.alloc().init()
            return self

        def applicationDidFinishLaunching_(self, notification):
            self.controller.applicationDidFinishLaunching_(notification)

        def applicationWillTerminate_(self, _notification):
            self.controller.close_all_windows()


def main() -> None:
    if _PYOBJC_IMPORT_ERROR is not None or NSApplication is None or AppHelper is None:
        raise RuntimeError(
            "PyObjC is required for the native macOS overlay UI. "
            "Run: ./scripts/setup_venv.sh"
        )

    app = NSApplication.sharedApplication()
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)

    def _handle_exit_signal(_signum, _frame):
        AppHelper.callAfter(app.terminate_, None)

    signal.signal(signal.SIGINT, _handle_exit_signal)
    signal.signal(signal.SIGTERM, _handle_exit_signal)

    try:
        AppHelper.runEventLoop()
    except KeyboardInterrupt:
        app.terminate_(None)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(exc)
