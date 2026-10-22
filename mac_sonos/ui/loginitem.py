"""Open-at-login for the .app bundle, through ServiceManagement (macOS 13+)."""
import logging

log = logging.getLogger("mac_sonos")

NOT_REGISTERED, ENABLED, REQUIRES_APPROVAL, NOT_FOUND = 0, 1, 2, 3


def _service():
    from Foundation import NSBundle
    from ServiceManagement import SMAppService
    if not str(NSBundle.mainBundle().bundlePath()).endswith(".app"):
        return None  # running from source, not from Mac Sonos.app
    # python.app of Homebrew is also an .app: only our own bundle can be a login item
    if NSBundle.mainBundle().bundleIdentifier() != "io.github.lagefreitas.mac-sonos":
        return None
    return SMAppService.mainAppService()


def status():
    """One of the constants above, or None when not running from Mac Sonos.app."""
    service = _service()
    return None if service is None else int(service.status())


def set_enabled(enabled):
    """Register or unregister the login item; returns the new status."""
    service = _service()
    if service is None:
        return None
    ok, err = (service.registerAndReturnError_(None) if enabled
               else service.unregisterAndReturnError_(None))
    if not ok:
        log.error("login item %s failed: %s", "register" if enabled else "unregister", err)
    return int(service.status())
