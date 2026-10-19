"""Εκκίνηση της desktop έκδοσης (Windows) — το entry point του PyInstaller.

Τρέχει το ίδιο web UI τοπικά (127.0.0.1, τυχαία θύρα), ανοίγει τον browser
και βάζει εικονίδιο στο system tray (κάτω δεξιά): δεξί κλικ -> Open / Exit,
διπλό κλικ -> Open. Καθαρό Win32 μέσω ctypes — χωρίς tkinter/pystray/Pillow,
για μικρότερο πακέτο.

Δεδομένα: %LOCALAPPDATA%\\misevent (εκτός φακέλου εγκατάστασης, ώστε να
επιβιώνουν σε αναβάθμιση/απεγκατάσταση).
"""

import ctypes
import os
import sys
import threading
import urllib.request
import webbrowser
from ctypes import wintypes as wt
from pathlib import Path

APP_TITLE = "misevent"
MUTEX_NAME = "Local\\misevent-desktop-5E2B7C41"
TRAY_CLASS = "misevent-tray"
MB_ICONERROR = 0x10

# Win32 σταθερές
WM_DESTROY, WM_COMMAND, WM_NULL = 0x0002, 0x0111, 0x0000
WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0203, 0x0205
WM_TRAY = 0x8000 + 1  # WM_APP + 1
NIM_ADD, NIM_DELETE = 0, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
IMAGE_ICON, LR_LOADFROMFILE, SM_CXSMICON = 1, 0x10, 49
CMD_OPEN, CMD_EXIT = 1, 2

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON),
    ]


def _win32():
    user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [
        wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
    ]
    user32.LoadImageW.restype = wt.HANDLE
    user32.LoadImageW.argtypes = [
        wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT,
    ]
    user32.CreatePopupMenu.restype = wt.HMENU
    user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
    user32.TrackPopupMenu.argtypes = [
        wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID,
    ]
    user32.DestroyMenu.argtypes = [wt.HMENU]
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    return user32, shell32


def run_tray(url: str, icon_path: Path) -> None:
    """Εικονίδιο στο tray — μπλοκάρει μέχρι το «Exit»."""
    user32, shell32 = _win32()
    hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
    nid = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), uID=1,
                          uFlags=NIF_MESSAGE | NIF_ICON | NIF_TIP, uCallbackMessage=WM_TRAY)

    def show_menu(hwnd):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, CMD_OPEN, "Open")
        user32.AppendMenuW(menu, 0, CMD_EXIT, "Exit")
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)  # αλλιώς το μενού δεν κλείνει με κλικ έξω
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                    pt.x, pt.y, 0, hwnd, None)
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if cmd:
            user32.PostMessageW(hwnd, WM_COMMAND, cmd, 0)

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam == WM_RBUTTONUP:
                show_menu(hwnd)
            elif lparam == WM_LBUTTONDBLCLK:
                webbrowser.open(url)
            return 0
        if msg == WM_COMMAND:
            if wparam == CMD_OPEN:
                webbrowser.open(url)
            elif wparam == CMD_EXIT:
                user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wndproc)  # κρατάμε αναφορά: αλλιώς ο GC σβήνει το callback
    wc = WNDCLASSW(lpfnWndProc=proc, hInstance=hinst, lpszClassName=TRAY_CLASS)
    user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(0, TRAY_CLASS, APP_TITLE, 0, 0, 0, 0, 0,
                                  None, None, hinst, None)
    size = user32.GetSystemMetrics(SM_CXSMICON)
    nid.hWnd = hwnd
    nid.hIcon = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, size, size,
                                  LR_LOADFROMFILE)
    nid.szTip = f"{APP_TITLE} — {url}"
    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        raise OSError("Δεν μπήκε το εικονίδιο στο system tray")

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def main() -> int:
    data = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "misevent"
    data.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MISEVENT_DATA_DIR", str(data))
    runtime = data / "runtime.url"

    # Μία εκτέλεση τη φορά: η δεύτερη απλώς ανοίγει την ήδη ανοιχτή.
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        if runtime.exists():
            webbrowser.open(runtime.read_text(encoding="utf-8"))
        return 0

    try:
        from werkzeug.serving import make_server

        import webapp

        server = make_server("127.0.0.1", 0, webapp.create_app(), threaded=True)
        url = f"http://127.0.0.1:{server.server_port}/"
        threading.Thread(target=server.serve_forever, daemon=True).start()

        if "--smoke-test" in sys.argv:  # build-windows.ps1: λείπει κάτι από το πακέτο;
            from webapp.pdf_reports import ReportPDF

            pdf = ReportPDF()
            pdf.add_page()
            pdf.font(10)
            pdf.cell(text="Μισθοδοσία ΑΒΓ")
            with urllib.request.urlopen(url + "login", timeout=10) as r:
                return 0 if r.status == 200 and pdf.output()[:4] == b"%PDF" else 1

        runtime.write_text(url, encoding="utf-8")
        webbrowser.open(url)
        run_tray(url, Path(webapp.__file__).parent / "static" / "app.ico")
        server.shutdown()
        return 0
    except Exception as exc:  # noqa: BLE001 - χωρίς κονσόλα: δείξε το σφάλμα
        ctypes.windll.user32.MessageBoxW(
            None, f"Η εφαρμογή δεν μπόρεσε να ξεκινήσει.\n\n{exc}", APP_TITLE, MB_ICONERROR
        )
        return 1
    finally:
        runtime.unlink(missing_ok=True)
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
