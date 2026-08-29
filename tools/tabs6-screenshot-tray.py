#!/usr/bin/env python3
"""A screenshot button in the Plasma system tray, on the RIGHT of the panel.

Why this speaks D-Bus directly instead of using a toolkit:

  * Plasma 6 on Fedora ships no standalone Quick Launch applet. The only
    launcher-capable plasmoid present is icontasks, which lives with the task
    manager on the LEFT of the panel. A .desktop launcher can be added there,
    but it cannot be put on the right.
  * Qt's QSystemTrayIcon looked like the answer - PySide6 is installed, and
    QSystemTrayIcon.isSystemTrayAvailable() returns True and isVisible() returns
    True - but it never registers a StatusNotifierItem on the bus. Verified with
    QT_QPA_PLATFORMTHEME set to kde, generic, gnome and unset, on the wayland
    platform: no org.kde.StatusNotifierItem-* name ever appears. So it silently
    does nothing here.

The tray protocol itself is only D-Bus, so this implements StatusNotifierItem
directly with GLib/Gio and registers with org.kde.StatusNotifierWatcher. No Qt
involved, nothing to silently fail.

Tap it: full-screen shot, copied to the clipboard ready to paste, and saved to
~/Pictures/Screenshots. The capture is delegated to
/usr/local/bin/tabs6-screenshot so there is exactly one implementation.
"""

import os
import signal
import subprocess
import sys

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

SHOOTER = "/usr/local/bin/tabs6-screenshot"

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"

ITEM_PATH = "/StatusNotifierItem"
ITEM_IFACE = "org.kde.StatusNotifierItem"

# Only what Plasma actually reads. ItemIsMenu=false is the important one: with
# it true, a tap opens a menu instead of calling Activate, and the whole point
# here is that one tap takes the shot.
INTROSPECTION = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
    <signal name="NewToolTip"/>
  </interface>
</node>
"""

TITLE = "Screenshot"
TOOLTIP_TEXT = "Tap to capture the screen — copied to the clipboard"
ICON = "spectacle"


def log(msg):
    print(msg, flush=True)


class TrayItem:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.service_name = "org.kde.StatusNotifierItem-%d-1" % os.getpid()
        self.reg_id = None
        self.registered_with_watcher = False

        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
        self.iface = node.interfaces[0]

        self.reg_id = self.bus.register_object(
            ITEM_PATH, self.iface, self.on_method, self.on_get, None)

        Gio.bus_own_name_on_connection(
            self.bus, self.service_name, Gio.BusNameOwnerFlags.NONE,
            self.on_name_acquired, self.on_name_lost)

        # plasmashell restarting takes the watcher with it; re-register when it
        # comes back, otherwise the icon silently disappears for good.
        Gio.bus_watch_name_on_connection(
            self.bus, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE,
            lambda *_: self.register_with_watcher(),
            lambda *_: self.on_watcher_vanished())

    # ---------------------------------------------------------------- naming
    def on_name_acquired(self, _conn, name):
        log("owned %s" % name)
        self.register_with_watcher()

    def on_name_lost(self, _conn, name):
        log("lost bus name %s" % name)

    def on_watcher_vanished(self):
        log("StatusNotifierWatcher went away (plasmashell restart?)")
        self.registered_with_watcher = False

    def register_with_watcher(self):
        if self.registered_with_watcher:
            return
        try:
            self.bus.call_sync(
                WATCHER_NAME, WATCHER_PATH, WATCHER_IFACE,
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (self.service_name,)),
                None, Gio.DBusCallFlags.NONE, 5000, None)
            self.registered_with_watcher = True
            log("registered with the tray")
        except GLib.Error as e:
            log("could not register with the tray: %s" % e.message)

    # ------------------------------------------------------------ properties
    #
    # PyGObject calls the property getter with exactly five arguments -
    # (connection, sender, object_path, interface_name, property_name) - and no
    # GError and no user_data, unlike the method-call closure which does get
    # seven. Getting this wrong throws on every property read, and the only
    # visible symptom is that the icon never appears: Plasma cannot read Id,
    # IconName or Status, so it has nothing to draw.
    def on_get(self, _conn, _sender, _path, _iface, prop):
        if prop == "Category":
            return GLib.Variant("s", "ApplicationStatus")
        if prop == "Id":
            return GLib.Variant("s", "tabs6-screenshot")
        if prop == "Title":
            return GLib.Variant("s", TITLE)
        if prop == "Status":
            return GLib.Variant("s", "Active")
        if prop == "IconName":
            return GLib.Variant("s", ICON)
        if prop in ("AttentionIconName", "OverlayIconName"):
            return GLib.Variant("s", "")
        if prop == "ToolTip":
            return GLib.Variant("(sa(iiay)ss)", (ICON, [], TITLE, TOOLTIP_TEXT))
        if prop == "ItemIsMenu":
            return GLib.Variant("b", False)
        return None

    # --------------------------------------------------------------- methods
    def on_method(self, _conn, _sender, _path, _iface, method, _params,
                  invocation, _data=None):
        if method in ("Activate", "SecondaryActivate"):
            self.shoot()
            invocation.return_value(None)
        elif method in ("ContextMenu", "Scroll"):
            # Nothing useful to offer; returning cleanly stops Plasma retrying.
            invocation.return_value(None)
        else:
            invocation.return_value(None)

    def shoot(self):
        log("tray tapped: taking screenshot")
        try:
            subprocess.Popen([SHOOTER], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            log("could not run %s: %s" % (SHOOTER, e))


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    TrayItem()
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
