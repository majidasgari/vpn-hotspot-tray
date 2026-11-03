#!/usr/bin/env python3
"""VPN Hotspot Tray — a tray switch for sharing this machine's VPN tunnel.

The privileged work (AP vif, hostapd, dnsmasq, NAT) stays in `vpn-hotspot`, a
small root script that ships with this project; this app only asks for it
through pkexec and reports what is true right now.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

from PySide6.QtCore import QPointF, QRectF, Qt, QProcess, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

import vpn_hotspot_state as hs

ON, OFF, WARN, ERR = "#1b8a3a", "#9aa0a6", "#d98b00", "#c62828"
PERSIAN = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
TONE = {"on": ON, "off": OFF, "partial": WARN}
STATE_TEXT = {"on": "روشن", "off": "خاموش", "partial": "نیمه‌کاره"}


def orient(lbl: QLabel, text: str) -> None:
    """One line, one direction: any Persian letter means right-aligned RTL.

    Qt mirrors AlignLeft/AlignRight inside an RTL widget, so AlignLeft is what
    puts Persian text against the *right* edge; the direction does the mirroring.
    """
    lbl.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft
        if PERSIAN.search(text)
        else Qt.LayoutDirection.LeftToRight
    )
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


def fa(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    orient(lbl, text)
    return lbl


class ValueLabel(QLabel):
    """Re-decides its direction on every setText — values arrive after construction."""

    def __init__(self, text: str):
        super().__init__(text)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        orient(self, text)

    def setText(self, text: str) -> None:
        super().setText(text)
        orient(self, text)


def value_label(text: str) -> QLabel:
    return ValueLabel(text)


def make_icon(color: str) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidth(6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for r in (13, 23, 33):
        p.drawArc(QRectF(32 - r, 47 - r, 2 * r, 2 * r), 45 * 16, 90 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QPointF(32, 47), 4.5, 4.5)
    p.end()
    return QIcon(pm)


PSK_RULE = re.compile(r"[A-Za-z0-9._-]{8,63}")


class PassDialog(QDialog):
    """Ask twice, validate where WPA2 cares: 8-63 chars, no shell metacharacters."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("تغییر رمز هات‌اسپات")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.value: str | None = None
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.a, self.b = QLineEdit(), QLineEdit()
        for f in (self.a, self.b):
            f.setEchoMode(QLineEdit.EchoMode.Password)
            f.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        form.addRow(fa("رمز جدید"), self.a)
        form.addRow(fa("تکرار رمز"), self.b)
        form.addRow(
            fa(
                "۸ تا ۶۳ نویسه؛ فقط حروف انگلیسی، رقم، نقطه، خط تیره و زیرخط. "
                "اگر هات‌اسپات روشن باشد یک بار پایین و بالا می‌آید و گوشی‌ها باید دوباره وصل شوند."
            )
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("ذخیره")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("انصراف")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self):
        value = self.a.text()
        if value != self.b.text():
            QMessageBox.warning(self, "رمز یکسان نیست", "دو رمز وارد شده یکی نیستند.")
            return
        if not PSK_RULE.fullmatch(value):
            QMessageBox.warning(
                self,
                "رمز نامعتبر",
                "رمز باید ۸ تا ۶۳ نویسه باشد و فقط شامل حروف انگلیسی، رقم، نقطه، "
                "خط تیره و زیرخط.",
            )
            return
        self.value = value
        self.accept()


class HotspotApp(QWidget):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.proc: QProcess | None = None
        self._psk_file: str | None = None
        self.status = hs.snapshot()
        self.setWindowTitle("هات‌اسپات وی‌پی‌ان")
        self.setWindowIcon(make_icon(ON))
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setFixedWidth(460)
        self._build()
        self._tray()
        self._paint()
        # wayland leaves an unsized window at the compositor's default, which
        # spread the rows over a full screen height
        self.resize(460, self.sizeHint().height())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(3000)

    # ---------- widgets ----------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        self.lbl_state = QLabel()
        self.lbl_state.setObjectName("state")
        self.lbl_state.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.lbl_state.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_state.setContentsMargins(0, 0, 0, 6)
        big = self.lbl_state.font()
        big.setPointSizeF(big.pointSizeF() * 1.35)
        big.setBold(True)
        self.lbl_state.setFont(big)
        root.addWidget(self.lbl_state)

        form = QFormLayout()
        # AlignLeft here means "towards the start of an RTL row", i.e. the right
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.v_ssid, self.v_psk = value_label("—"), value_label("—")
        self.v_chan, self.v_route = value_label("—"), value_label("—")
        form.addRow(fa("نام شبکه (SSID)"), self.v_ssid)
        form.addRow(fa("رمز"), self.v_psk)
        form.addRow(fa("کانال"), self.v_chan)
        form.addRow(fa("تونل ترافیک"), self.v_route)
        root.addLayout(form)

        self.lbl_warn = value_label("")
        self.lbl_warn.setObjectName("warn")
        root.addWidget(self.lbl_warn)

        self.lbl_hint = fa(
            "گوشی را به همین SSID با همین رمز وصل کن و در مرورگرش آدرس "
            "api.ipify.org را باز کن؛ باید IP وی‌پی‌ان را ببینی، نه IP عادی اینترنت."
        )
        self.lbl_hint.setObjectName("hint")
        root.addWidget(self.lbl_hint)

        self.btn_toggle = QPushButton()
        self.btn_toggle.clicked.connect(self.on_toggle)
        self.btn_pass = QPushButton("تغییر رمز")
        self.btn_pass.clicked.connect(self.on_change_pass)
        btn_refresh = QPushButton("به‌روزرسانی")
        btn_refresh.clicked.connect(self.refresh)
        btn_help = QPushButton("راهنما")
        btn_help.clicked.connect(self.on_help)
        row = QHBoxLayout()
        for w in (self.btn_pass, btn_refresh, btn_help):
            row.addWidget(w, 1)
        root.addWidget(self.btn_toggle)
        root.addLayout(row)

        self.lbl_action = value_label("")
        self.lbl_action.setObjectName("action")
        root.addWidget(self.lbl_action)

    def _tray(self):
        self.tray = QSystemTrayIcon(make_icon(OFF), self)
        menu = QMenu()
        self.act_state = QAction("هات‌اسپات: —", menu)
        self.act_state.setEnabled(False)
        self.act_toggle = QAction("روشن کردن هات‌اسپات", menu)
        self.act_toggle.triggered.connect(self.on_toggle)
        act_show = QAction("نمایش پنجره", menu)
        act_show.triggered.connect(self.show_window)
        act_quit = QAction("خروج", menu)
        act_quit.triggered.connect(self.app.quit)
        for a in (self.act_state, self.act_toggle):
            menu.addAction(a)
        menu.addSeparator()
        for a in (act_show, act_quit):
            menu.addAction(a)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda why: self.show_window()
            if why == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        self.tray.show()

    # ---------- state painting ----------
    def refresh(self):
        if self.proc is not None:
            return
        try:
            # shortcut: five short subprocess calls on the GUI thread every 3 s
            # (~80 ms); move to a worker if it ever stutters.
            self.status = hs.snapshot()
        except Exception as exc:  # never leave the UI claiming stale truth
            self.lbl_action.setText(f"خطا در خواندن وضعیت: {exc}")
            return
        self._paint()

    def _paint(self):
        s = self.status
        tone = TONE[s.state]
        self.lbl_state.setText(f"هات‌اسپات: {STATE_TEXT[s.state]}")
        self.lbl_state.setProperty("tone", s.state)
        for w in (self.lbl_state,):
            w.style().unpolish(w)
            w.style().polish(w)
        self.v_ssid.setText(s.ap_ssid or s.conf_ssid or "—")
        self.v_psk.setText(s.psk or "—")
        self.v_chan.setText(f"{s.ap_channel} (2.4GHz)" if s.ap_channel else "—")
        self.v_route.setText(s.vpn or "—")
        warn = s.warnings()
        self.lbl_warn.setText("\n\n".join(warn))
        self.lbl_warn.setVisible(bool(warn))
        self.btn_toggle.setText(
            "خاموش کردن هات‌اسپات" if s.state != "off" else "روشن کردن هات‌اسپات"
        )
        self.act_toggle.setText(self.btn_toggle.text())
        self.act_state.setText(f"هات‌اسپات: {STATE_TEXT[s.state]}")
        icon = make_icon(tone)
        self.tray.setIcon(icon)
        self.setWindowIcon(icon)
        tooltip = f"هات‌اسپات {STATE_TEXT[s.state]}"
        if s.state == "on":
            tooltip += f" — {s.ap_ssid} (کانال {s.ap_channel})"
        if not s.vpn:
            tooltip += " — وی‌پی‌ان وصل نیست"
        self.tray.setToolTip(tooltip)
        # the warning block appears and disappears; keep the window just tall enough
        self.setFixedHeight(self.sizeHint().height())

    # ---------- actions ----------
    def on_toggle(self):
        if self.status.state != "off":
            self.run_helper("down")
            return
        blockers = self.status.blockers()
        if blockers:
            QMessageBox.warning(
                self, "فعلاً نمی‌شود روشن کرد", "\n\n".join(blockers)
            )
            return
        self.run_helper("up")

    def on_change_pass(self):
        dlg = PassDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.value:
            return
        # a file, not an argument: the secret must not show up in `ps`
        fd, path = tempfile.mkstemp(prefix="vh-psk-")
        with os.fdopen(fd, "w") as fh:
            fh.write(dlg.value)
        self._psk_file = path
        self.run_helper("passwd", [path])

    def run_helper(self, action: str, args: list[str] | None = None) -> bool:
        """Run `pkexec vpn-hotspot <action>`; returns False if one is already running."""
        if self.proc is not None:
            return False
        self.btn_toggle.setEnabled(False)
        self.btn_pass.setEnabled(False)
        self.act_toggle.setEnabled(False)
        self.lbl_action.setText("در حال اجرای دستور… (اگر پنجره‌ی رمز مدیر آمد، تأیید کن)")
        self.proc = QProcess(self)
        self.proc.setProgram("pkexec")
        self.proc.setArguments([hs.HELPER, action, *(args or [])])
        self.proc.finished.connect(lambda code, _: self._finished(action, code))
        self.proc.errorOccurred.connect(lambda _: self._finished(action, -1))
        self.proc.start()
        return True

    def _finished(self, action: str, code: int):
        proc, self.proc = self.proc, None
        out = ""
        if proc is not None:
            out = (
                bytes(proc.readAllStandardError()).decode("utf-8", "replace").strip()
                or bytes(proc.readAllStandardOutput()).decode("utf-8", "replace").strip()
            )
            proc.deleteLater()
        if self._psk_file:
            try:
                os.unlink(self._psk_file)
            except OSError:
                pass
            self._psk_file = None
        self.btn_toggle.setEnabled(True)
        self.btn_pass.setEnabled(True)
        self.act_toggle.setEnabled(True)
        self.lbl_action.setText("انجام شد." if code == 0 else "")
        if code == 0:
            done = "رمز عوض شد" if action == "passwd" else f"دستور {action} اجرا شد"
            self.tray.showMessage(
                "هات‌اسپات", done, QSystemTrayIcon.MessageIcon.Information, 4000
            )
        else:
            reason = "اجازه‌ی مدیر داده نشد." if code in (-1, 126, 127) else f"کد خطا {code}"
            text = f"{reason}\n\n{out}" if out else reason
            self.lbl_action.setText(reason)
            QMessageBox.critical(self, "اجرای دستور ناموفق بود", text)
            self.tray.showMessage(
                "هات‌اسپات", reason, QSystemTrayIcon.MessageIcon.Critical, 6000
            )
        self.refresh()
        # the station may drop for a few seconds when the AP starts and come back
        QTimer.singleShot(7000, self.refresh)

    def on_help(self):
        QMessageBox.information(
            self,
            "راهنما",
            "هات‌اسپات از همان آنتن وای‌فای استفاده می‌کند، پس وای‌فای خودت قطع نمی‌شود، "
            "اما هر دو باید روی یک کانال باشند:\n\n"
            "• وای‌فای خودت باید ۲.۴ هرتزی باشد؛ اگر روی ۵ گیگاهرتز باشی گوشی‌های ۲.۴ هرتزی "
            "نمی‌توانند به هات‌اسپات وصل شوند.\n"
            "• وی‌پی‌ان باید وصل باشد؛ وگرنه ترافیک گوشی‌ها از اینترنت عادی خارج می‌شود و "
            "برنامه اجازه‌ی روشن کردن نمی‌دهد.\n"
            "• رمز و نام شبکه در فایل /etc/vpn-hotspot.env روی همین دستگاه است.\n"
            "• بستن پنجره برنامه را نمی‌بندد؛ از «خروج» در سینی سیستم خارج شو.\n\n"
            "برای تست، در گوشی api.ipify.org را باز کن: باید IP وی‌پی‌ان را ببینی.",
        )

    def show_window(self):
        self.refresh()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "هات‌اسپات وی‌پی‌ان",
            "برنامه در سینی سیستم می‌ماند. برای خروج از منوی سینی «خروج» را بزن.",
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )


def main(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("VPN Hotspot Tray")
    # colours only: a font in the stylesheet would swap the family for generic
    # "Sans Serif", which has no Arabic shaping (broken Persian)
    app.setStyleSheet(
        f"QLabel#state[tone=\"on\"] {{ color: {ON}; }}"
        f"QLabel#state[tone=\"off\"] {{ color: {OFF}; }}"
        f"QLabel#state[tone=\"partial\"] {{ color: {WARN}; }}"
        f"QLabel#warn {{ color: {ERR}; }}"
        "QLabel#hint, QLabel#action { color: #6b7280; }"
    )
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("no system tray on this desktop", file=sys.stderr)
    win = HotspotApp(app)
    if "--hidden" not in argv:
        win.show()
    return app.exec()


if __name__ == "__main__":
    if "--print-status" in sys.argv:
        import json

        print(json.dumps(hs.snapshot().as_dict(), ensure_ascii=False, indent=2))
        raise SystemExit(0)
    raise SystemExit(main(sys.argv))
