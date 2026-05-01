#!/usr/bin/env python3
"""
gh-archive: a simple PyQt5 GUI wrapper around the `iagitup` CLI tool.
Archives a GitHub repository to the Internet Archive.
"""

import sys
import os
import json
import shutil
import subprocess
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal, Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QPlainTextEdit,
    QLabel,
    QMessageBox,
)


CONFIG_DIR = Path.home() / ".gh-archive"
CONFIG_FILE = CONFIG_DIR / "config.json"
# iagitup hard-codes its credential lookup to three paths:
#   ~/.config/internetarchive/ia.ini  (checked first)
#   ~/.ia
#   ~/.config/ia.ini
# It ignores IA_CONFIG_FILE.  Write to the first candidate so iagitup
# finds our credentials before it falls through to interactive ia configure.
IA_CONFIG_DIR = Path.home() / ".config" / "internetarchive"
IA_CONFIG_FILE = IA_CONFIG_DIR / "ia.ini"


class WorkerThread(QThread):
    """Runs iagitup as a subprocess and streams merged stdout/stderr line by line."""

    output_line = pyqtSignal(str)

    def __init__(self, cmd, env, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.env = env
        self.exit_code = None

    def run(self):
        try:
            proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.DEVNULL,  # never let the child block on a prompt
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # merge stderr into stdout, in order
                env=self.env,
                bufsize=1,
                universal_newlines=True,
            )
        except Exception as e:
            self.output_line.emit(f"Failed to start iagitup: {e}")
            self.exit_code = -1
            return

        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                self.output_line.emit(line.rstrip("\n"))
        except Exception as e:
            self.output_line.emit(f"Error reading output: {e}")

        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass

        self.exit_code = proc.wait()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("gh-archive")
        self.resize(720, 620)

        self.worker = None
        self.detected_url = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # --- Form ---
        form = QFormLayout()

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://github.com/user/repo")
        form.addRow("GitHub repository URL:", self.url_input)

        self.access_key_input = QLineEdit()
        self.access_key_input.setPlaceholderText("Internet Archive S3 access key")
        form.addRow("IA Access Key:", self.access_key_input)

        self.secret_key_input = QLineEdit()
        self.secret_key_input.setEchoMode(QLineEdit.Password)
        self.secret_key_input.setPlaceholderText("Internet Archive S3 secret key")
        form.addRow("IA Secret Key:", self.secret_key_input)

        self.download_limit = QSpinBox()
        self.download_limit.setRange(0, 1_000_000)
        self.download_limit.setSuffix(" KB/s")
        self.download_limit.setSpecialValueText("unlimited")
        form.addRow("Download limit (0 = unlimited):", self.download_limit)

        self.upload_limit = QSpinBox()
        self.upload_limit.setRange(0, 1_000_000)
        self.upload_limit.setSuffix(" KB/s")
        self.upload_limit.setSpecialValueText("unlimited")
        form.addRow("Upload limit (0 = unlimited):", self.upload_limit)

        root.addLayout(form)

        # --- Archive button ---
        self.archive_btn = QPushButton("Archive")
        self.archive_btn.clicked.connect(self.on_archive_clicked)
        root.addWidget(self.archive_btn)

        # --- Output box ---
        self.output_box = QPlainTextEdit()
        self.output_box.setReadOnly(True)
        self.output_box.setPlaceholderText("iagitup output will appear here...")
        root.addWidget(self.output_box, 1)

        # --- Detected archive.org URL ---
        self.url_label = QLabel("No archive.org URL detected yet.")
        self.url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.url_label.setWordWrap(True)
        root.addWidget(self.url_label)

        self.open_btn = QPushButton("Open on archive.org")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self.on_open_clicked)
        root.addWidget(self.open_btn)

        self.load_config()

    # ---------- config persistence ----------

    def load_config(self):
        try:
            if not CONFIG_FILE.exists():
                return
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.access_key_input.setText(str(data.get("access_key", "")))
            self.secret_key_input.setText(str(data.get("secret_key", "")))
            self.download_limit.setValue(int(data.get("download_limit", 0) or 0))
            self.upload_limit.setValue(int(data.get("upload_limit", 0) or 0))
        except Exception as e:
            # Don't block startup on a bad config file; report into output box.
            self.output_box.appendPlainText(f"[config] failed to load: {e}")

    def save_config(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "access_key": self.access_key_input.text(),
                "secret_key": self.secret_key_input.text(),
                "download_limit": self.download_limit.value(),
                "upload_limit": self.upload_limit.value(),
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.output_box.appendPlainText(f"[config] failed to save: {e}")

    def _write_ia_config(self):
        """Write an ia.ini the `iagitup` credential lookup will find.

        iagitup checks three hard-coded paths in order; the first is
        ``~/.config/internetarchive/ia.ini``.  We write there so that
        credentials are found before iagitup falls through to the
        interactive ``ia configure`` prompt (which fails under a headless
        subprocess).

        Format expected:
            [s3]
            access = <access key>
            secret = <secret key>
        """
        try:
            IA_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(IA_CONFIG_DIR, 0o700)
            except OSError:
                pass
            access = self.access_key_input.text().strip()
            secret = self.secret_key_input.text().strip()
            content = f"[s3]\naccess = {access}\nsecret = {secret}\n"
            with open(IA_CONFIG_FILE, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                os.chmod(IA_CONFIG_FILE, 0o600)
            except OSError:
                pass
        except Exception as e:
            self.output_box.appendPlainText(f"[ia config] failed to write: {e}")

    # ---------- archive flow ----------

    def on_archive_clicked(self):
        url = self.url_input.text().strip()
        if not url.startswith("https://github.com"):
            QMessageBox.warning(
                self,
                "Invalid URL",
                "GitHub URL must start with https://github.com",
            )
            return

        iagitup_path = shutil.which("iagitup")
        if not iagitup_path:
            QMessageBox.critical(
                self,
                "iagitup not found",
                "iagitup not found. Run: pip3 install --user iagitup",
            )
            return

        # Persist settings on Archive click (no separate Save button).
        self.save_config()

        cmd = [iagitup_path]
        dl = self.download_limit.value()
        ul = self.upload_limit.value()
        if dl > 0:
            cmd.extend(["--download-limit", str(dl)])
        if ul > 0:
            cmd.extend(["--upload-limit", str(ul)])
        cmd.append(url)

        env = os.environ.copy()
        # iagitup has its own hard-coded credential lookup that checks
        # only three specific paths (ignoring IA_CONFIG_FILE):
        #   1. ~/.config/internetarchive/ia.ini   ← we write here
        #   2. ~/.ia
        #   3. ~/.config/ia.ini
        # If none exist it shells out to interactive `ia configure`,
        # which dies under a headless subprocess (EOFError on stdin).
        #
        # We also set IA_CONFIG_FILE for the `ia` CLI itself, and scrub
        # credential env vars so nothing trips the "set together" check.
        self._write_ia_config()
        env["IA_CONFIG_FILE"] = str(IA_CONFIG_FILE)
        for stale in (
            "IA_ACCESS_KEY",
            "IA_SECRET_KEY",
            "IA_ACCESS_KEY_ID",
            "IA_SECRET_ACCESS_KEY",
            "IAS3_ACCESS_KEY",
            "IAS3_SECRET_KEY",
        ):
            env.pop(stale, None)

        # Reset UI state for a fresh run.
        self.output_box.clear()
        self.output_box.appendPlainText(f"$ {' '.join(cmd)}")
        self.detected_url = None
        self.url_label.setText("No archive.org URL detected yet.")
        self.open_btn.setEnabled(False)

        self.archive_btn.setEnabled(False)
        self.archive_btn.setText("Running...")

        self.worker = WorkerThread(cmd, env)
        self.worker.output_line.connect(self.on_output_line)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

    # ---------- worker callbacks ----------

    def on_output_line(self, line):
        self.output_box.appendPlainText(line)
        if self.detected_url is None and "archive.org/details" in line:
            self.detected_url = self._extract_archive_url(line)
            if self.detected_url:
                self.url_label.setText(f"Archive URL: {self.detected_url}")
                self.open_btn.setEnabled(True)

    def on_worker_finished(self):
        code = self.worker.exit_code if self.worker is not None else -1
        self.output_box.appendPlainText(f"\n[process exited with code {code}]")
        self.archive_btn.setEnabled(True)
        self.archive_btn.setText("Archive")

    def on_open_clicked(self):
        if self.detected_url:
            QDesktopServices.openUrl(QUrl(self.detected_url))

    # ---------- helpers ----------

    @staticmethod
    def _extract_archive_url(line):
        """Pull a clean archive.org/details URL out of a log line."""
        for token in line.split():
            if "archive.org/details" not in token:
                continue
            url = token.strip().strip("\"'.,;:<>()[]{}")
            idx = url.find("archive.org/details")
            if idx > 0 and not url.startswith(("http://", "https://")):
                url = "https://" + url[idx:]
            elif idx > 0:
                # already had a scheme; keep as-is
                pass
            return url
        # Fallback: scan substring directly.
        idx = line.find("archive.org/details")
        if idx >= 0:
            tail = line[idx:].split()[0].strip("\"'.,;:<>()[]{}")
            return "https://" + tail
        return None


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
