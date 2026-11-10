"""Project Hub dialogs and shared application styling."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QScrollArea, QTabWidget,
    QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from project_data import (
    BOOL_FIELDS, DATE_FIELDS, DATA, DAY_FIELDS, FIELD_GROUPS, RENEWALS, VAULT,
    CredentialVault, VaultError, parse_date,
)
from project_services import (
    AccountSyncWorker, detect_public_ip, fetch_hostinger_domains,
    fetch_hostinger_vps, fetch_namecheap_domains, scp_download,
    seo_audit, ssh_exec, ssh_list, ssh_target,
)

TECH_STYLE = """
QMainWindow, QDialog, QWidget {
  background: #0a0e14;
  color: #c8d6e5;
  font-family: 'Consolas', 'Cascadia Code', 'Segoe UI', monospace;
  font-size: 13px;
}
QLabel { color: #c8d6e5; }
QLabel#title { color: #00e5ff; font-size: 22px; font-weight: bold; letter-spacing: 2px; }
QLabel#subtitle { color: #7a8a9a; font-size: 11px; }
QLineEdit, QTextEdit, QPlainTextEdit, QTreeWidget, QListWidget, QTabWidget::pane {
  background: #0f141b;
  color: #e6f1ff;
  border: 1px solid #1f2a37;
  border-radius: 6px;
  selection-background-color: #00e5ff;
  selection-color: #0a0e14;
}
QLineEdit:focus, QTextEdit:focus { border: 1px solid #00e5ff; }
QPushButton {
  background: #111827;
  color: #00e5ff;
  border: 1px solid #00e5ff;
  border-radius: 6px;
  padding: 6px 12px;
  font-weight: bold;
}
QPushButton:hover { background: #00e5ff; color: #0a0e14; }
QPushButton:pressed { background: #00b8cc; color: #0a0e14; }
QPushButton#danger { border-color: #ff4d6d; color: #ff4d6d; }
QPushButton#danger:hover { background: #ff4d6d; color: #0a0e14; }
QTreeWidget {
  background: #0f141b;
  alternate-background-color: #131a24;
  color: #c8d6e5;
  border: 1px solid #1f2a37;
  border-radius: 6px;
}
QTreeWidget::item { padding: 4px; }
QTreeWidget::item:selected { background: #1a2740; color: #00e5ff; }
QHeaderView::section {
  background: #111827;
  color: #00e5ff;
  padding: 6px;
  border: none;
  border-bottom: 1px solid #1f2a37;
  font-weight: bold;
}
QTabWidget::pane { border: 1px solid #1f2a37; border-radius: 6px; top: -1px; }
QTabBar::tab {
  background: #111827;
  color: #7a8a9a;
  padding: 8px 14px;
  border: 1px solid #1f2a37;
  border-bottom: none;
  border-top-left-radius: 6px;
  border-top-right-radius: 6px;
  margin-right: 2px;
}
QTabBar::tab:selected { background: #0f141b; color: #00e5ff; border-color: #00e5ff; }
QTabBar::tab:hover { color: #c8d6e5; }
QScrollBar:vertical {
  background: #0a0e14;
  width: 10px;
  margin: 0;
}
QScrollBar::handle:vertical {
  background: #1f2a37;
  border-radius: 5px;
  min-height: 20px;
}
QScrollBar::handle:vertical:hover { background: #00e5ff; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSplitter::handle { background: #1f2a37; }
QStatusBar, QLabel#status { color: #7a8a9a; font-size: 11px; }
QFrame#card {
  background: #0f141b;
  border: 1px solid #1f2a37;
  border-radius: 10px;
}
QFrame#card:hover { border-color: #00e5ff; }
"""

class ProjectDialog(QDialog):
    def __init__(self, parent, project=None):
        super().__init__(parent)
        self.setWindowTitle('Edit project' if project else 'Add project')
        self.resize(740, 710)
        self.setMinimumSize(600, 500)
        self.original = project
        self.result = None
        self.inputs = {}
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        for title, group in FIELD_GROUPS:
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            if title.startswith('Website'):
                top_row = QHBoxLayout()
                fetch_btn = QPushButton('🌐 Autofill from Hostinger or Namecheap...')
                fetch_btn.clicked.connect(self.open_cloud_import)
                top_row.addWidget(fetch_btn)
                top_row.addStretch()
                tab_layout.addLayout(top_row)
            grid = QGridLayout()
            tab_layout.addLayout(grid)
            grid.setColumnStretch(1, 1)
            grid.setRowStretch(len(group), 1)
            for row, (key, label) in enumerate(group):
                if key in BOOL_FIELDS:
                    ent = QCheckBox(label)
                    ent.setChecked(bool(getattr(project, key, False)))
                    grid.addWidget(ent, row, 0, 1, 2)
                    self.inputs[key] = ent
                    continue
                lbl = QLabel(label)
                lbl.setAlignment(Qt.AlignTop)
                grid.addWidget(lbl, row, 0)
                ent = QLineEdit()
                ent.setText(str(getattr(project, key, '') or ''))
                if key in DATE_FIELDS:
                    ent.setPlaceholderText('YYYY-MM-DD')
                grid.addWidget(ent, row, 1)
                self.inputs[key] = ent
                if key in ('path', 'ssh_key_path'):
                    btn = QPushButton('Browse...')
                    btn.clicked.connect(lambda checked=False, e=ent, k=key: self.choose(e, k))
                    grid.addWidget(btn, row, 2)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(tab)
            tabs.addTab(scroll, title)
        layout.addWidget(tabs)
        self.website_keys = [k for t, g in FIELD_GROUPS if t.startswith('Website') for k, _ in g if k != 'is_website']
        self.inputs['is_website'].toggled.connect(self.sync_website_fields)
        self.sync_website_fields()
        note = QLabel('Commands are stored as plain text. Use a credential vault key to link a secret; do not put secret values in project fields. Remote commands are copy-only. Renewal reminders appear on the Home page and when Project Hub starts.')
        note.setStyleSheet('color: #7a8a9a; font-size: 11px;')
        note.setWordWrap(True)
        layout.addWidget(note)
        btns = QHBoxLayout()
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        save = QPushButton('Save project')
        save.clicked.connect(self.submit)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(save)
        layout.addLayout(btns)
        self.setStyleSheet(TECH_STYLE)

    def open_cloud_import(self):
        dlg = CloudImportDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.selected_data:
            data = dlg.selected_data
            if 'is_website' in self.inputs:
                self.inputs['is_website'].setChecked(True)
            for k, v in data.items():
                if k in self.inputs:
                    if k in BOOL_FIELDS:
                        self.inputs[k].setChecked(bool(v))
                    else:
                        self.inputs[k].setText(str(v))
            name_input = self.inputs.get('name')
            if name_input and not name_input.text().strip():
                if data.get('domain'):
                    name_input.setText(data['domain'])
                elif data.get('server_host'):
                    name_input.setText(data.get('server_name') or data['server_host'])
            site_input = self.inputs.get('site')
            if site_input and not site_input.text().strip() and data.get('domain'):
                site_input.setText(f"https://{data['domain']}")
            QMessageBox.information(self, 'Data imported', 'Project fields were updated from your selected account resource.')

    def choose(self, entry, key):
        if 'key' in key:
            f, _ = QFileDialog.getOpenFileName(self, 'Choose private key')
            if f:
                entry.setText(f)
        else:
            folder = QFileDialog.getExistingDirectory(self, 'Choose folder')
            if folder:
                entry.setText(folder)

    def sync_website_fields(self):
        enabled = self.inputs['is_website'].isChecked()
        for key in self.website_keys:
            self.inputs[key].setEnabled(enabled)

    def submit(self):
        vals = {}
        for key, entry in self.inputs.items():
            vals[key] = entry.isChecked() if key in BOOL_FIELDS else entry.text().strip()
        if not vals['name']:
            QMessageBox.warning(self, 'Name required', 'Enter a project name.')
            return
        for key in DATE_FIELDS:
            if vals[key] and parse_date(vals[key]) is None:
                QMessageBox.warning(self, 'Invalid date', f'"{vals[key]}" is not a valid date. Use YYYY-MM-DD, for example 2027-03-15.')
                return
        for key in DAY_FIELDS:
            if vals[key] and not (vals[key].isdigit() and int(vals[key]) > 0):
                QMessageBox.warning(self, 'Invalid reminder', 'Reminder days must be a positive whole number.')
                return
        for _, expires_key, remind_key, days_key in RENEWALS:
            if vals['is_website'] and vals[remind_key] and not vals[expires_key]:
                QMessageBox.warning(self, 'Renewal date needed', 'Enter a renewal / expiry date to enable its reminder.')
                return
            vals[days_key] = vals[days_key] or '30'
        self.result = vals
        self.accept()

class CredentialDialog(QDialog):
    def __init__(self, parent, vault, credentials):
        super().__init__(parent)
        self.setWindowTitle('Encrypted credential vault')
        self.resize(660, 440)
        self.setMinimumSize(540, 360)
        self.setModal(True)
        self.vault = vault
        self.credentials = dict(credentials)
        self.alias_edit = QLineEdit()
        layout = QVBoxLayout(self)
        info = QLabel('Secrets are encrypted for your Windows account. Project fields should contain the matching vault key, not the secret itself.')
        info.setWordWrap(True)
        layout.addWidget(info)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.currentItemChanged.connect(lambda cur, prev: self.on_select())
        layout.addWidget(self.list, 1)
        form = QGridLayout()
        form.setColumnStretch(1, 1)
        form.addWidget(QLabel('Vault key'), 0, 0)
        form.addWidget(self.alias_edit, 0, 1)
        form.addWidget(QLabel('Secret value'), 1, 0)
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.Password)
        form.addWidget(self.secret, 1, 1)
        layout.addLayout(form)
        btns = QHBoxLayout()
        for text, cb in [('Store / update', self.store), ('Reveal selected', self.reveal),
                         ('Hide / clear', self.hide), ('Delete selected', self.delete)]:
            b = QPushButton(text)
            b.clicked.connect(cb)
            btns.addWidget(b)
        btns.addStretch()
        close = QPushButton('Close')
        close.clicked.connect(self.close_dialog)
        btns.addWidget(close)
        layout.addLayout(btns)
        self.refresh()
        self.setStyleSheet(TECH_STYLE)

    def refresh(self):
        self.list.clear()
        for alias in sorted(self.credentials, key=str.casefold):
            self.list.addItem(alias)

    def on_select(self):
        items = self.list.selectedItems()
        alias = items[0].text() if items else ''
        self.alias_edit.setText(alias)
        self.hide()

    def hide(self):
        self.secret.clear()
        self.secret.setEchoMode(QLineEdit.Password)

    def close_dialog(self):
        self.hide()
        self.credentials.clear()
        self.reject()

    def persist(self, updated):
        try:
            self.vault.save(updated)
        except (OSError, VaultError) as exc:
            QMessageBox.critical(self, 'Credential vault error', str(exc))
            return False
        self.credentials = updated
        self.refresh()
        return True

    def store(self):
        alias = self.alias_edit.text().strip()
        secret = self.secret.text()
        if not alias or not secret:
            QMessageBox.warning(self, 'Credential required', 'Enter both a vault key and a secret value.')
            return
        updated = dict(self.credentials)
        updated[alias] = secret
        if self.persist(updated):
            self.hide()
            self.alias_edit.setText(alias)

    def reveal(self):
        alias = self.alias_edit.text().strip()
        if alias not in self.credentials:
            QMessageBox.information(self, 'Select a credential', 'Select a saved credential to reveal it.')
            return
        self.secret.clear()
        self.secret.setText(self.credentials[alias])
        self.secret.setEchoMode(QLineEdit.Normal)
        self.secret.setFocus()
        self.secret.selectAll()

    def delete(self):
        alias = self.alias_edit.text().strip()
        if alias not in self.credentials:
            QMessageBox.information(self, 'Select a credential', 'Select a saved credential to delete.')
            return
        if QMessageBox.question(self, 'Delete credential', f'Delete vault key "{alias}"?') != QMessageBox.Yes:
            return
        updated = dict(self.credentials)
        del updated[alias]
        if self.persist(updated):
            self.alias_edit.clear()
            self.hide()

class SeoDialog(QDialog):
    def __init__(self, parent, url, report):
        super().__init__(parent)
        self.setWindowTitle('SEO check')
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'URL: {url}'))
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setFont(QFont('Consolas', 10))
        txt.setPlainText(report)
        layout.addWidget(txt, 1)
        btn = QPushButton('Close')
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)
        self.setStyleSheet(TECH_STYLE)

class RemoteBrowserDialog(QDialog):
    def __init__(self, parent, project):
        super().__init__(parent)
        self.setWindowTitle(f'Remote folder: {project.name}')
        self.resize(720, 520)
        self.project = project
        layout = QVBoxLayout(self)
        info = QLabel(f'{ssh_target(project)}  ->  {project.ssh_remote_path or project.server_path or "."}')
        info.setStyleSheet('color: #7a8a9a; font-size: 11px;')
        layout.addWidget(info)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont('Consolas', 10))
        layout.addWidget(self.output, 1)
        btns = QHBoxLayout()
        refresh = QPushButton('Refresh listing')
        refresh.clicked.connect(self.refresh)
        btns.addWidget(refresh)
        dl = QPushButton('Download selected file')
        dl.clicked.connect(self.download)
        btns.addWidget(dl)
        run = QPushButton('Run remote command')
        run.clicked.connect(self.run_remote)
        btns.addWidget(run)
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        btns.addStretch()
        btns.addWidget(close)
        layout.addLayout(btns)
        self.setStyleSheet(TECH_STYLE)
        self.refresh()

    def refresh(self):
        self.output.setPlainText('Loading...')
        out, err, rc = ssh_list(self.project)
        text = out if out else err
        if rc != 0 and not out:
            text = f'(exit {rc})\n' + err
        self.output.setPlainText(text)

    def download(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, 'Download', 'Remote file name:')
        if not ok or not name.strip():
            return
        local, _ = QFileDialog.getSaveFileName(self, 'Save as', name.strip())
        if not local:
            return
        out, err, rc = scp_download(self.project, name.strip(), local)
        if rc == 0:
            QMessageBox.information(self, 'Download complete', f'Saved to {local}')
        else:
            QMessageBox.critical(self, 'Download failed', err or out or f'exit {rc}')

    def run_remote(self):
        from PySide6.QtWidgets import QInputDialog
        cmd, ok = QInputDialog.getText(self, 'Remote command', 'Command to run in remote project folder:')
        if not ok or not cmd.strip():
            return
        self.output.setPlainText('Running...')
        out, err, rc = ssh_exec(self.project, cmd.strip())
        text = (out or '') + (('\n--- stderr ---\n' + err) if err else '')
        text += f'\n(exit {rc})'
        self.output.setPlainText(text)

class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.resize(420, 260)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Project Hub settings'))
        layout.addWidget(QLabel('Data file:'))
        p = QLineEdit(str(DATA))
        p.setReadOnly(True)
        layout.addWidget(p)
        layout.addWidget(QLabel('Vault file:'))
        v = QLineEdit(str(VAULT))
        v.setReadOnly(True)
        layout.addWidget(v)
        acc_btn = QPushButton('Connected accounts (Hostinger & Namecheap)...')
        acc_btn.clicked.connect(lambda: ConnectedAccountsDialog(self).exec())
        layout.addWidget(acc_btn)
        layout.addStretch()
        btn = QPushButton('Close')
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)
        self.setStyleSheet(TECH_STYLE)


class ConnectedAccountsDialog(QDialog):
    def __init__(self, parent=None, vault=None):
        super().__init__(parent)
        self.setWindowTitle('Connected accounts (Hostinger & Namecheap)')
        self.resize(700, 560)
        self.vault = vault or CredentialVault()
        try:
            self.credentials = self.vault.load()
        except Exception:
            self.credentials = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info = QLabel(
            'Connect your Namecheap and Hostinger accounts to fetch domain registration, '
            'renewal dates, auto-renew status, and VPS IP addresses directly into your projects.\n'
            'Credentials are encrypted on your computer using Windows DPAPI.'
        )
        info.setStyleSheet('color: #7a8a9a; font-size: 11px;')
        info.setWordWrap(True)
        layout.addWidget(info)

        tabs = QTabWidget()

        # Tab 1: Hostinger
        h_tab = QWidget()
        h_layout = QVBoxLayout(h_tab)
        h_layout.setSpacing(10)

        h_info = QLabel(
            'Hostinger API lets Project Hub fetch your domains and VPS instances.\n'
            '1. Log into your Hostinger hPanel (hpanel.hostinger.com).\n'
            '2. Go to Dev tools -> API -> API tokens.\n'
            '3. Click "New token", set an expiration, and click "Generate".'
        )
        h_info.setStyleSheet('color: #7a8a9a; font-size: 11px;')
        h_info.setWordWrap(True)
        h_layout.addWidget(h_info)

        h_form = QGridLayout()
        h_form.setColumnStretch(1, 1)
        h_form.addWidget(QLabel('API Token:'), 0, 0)
        self.h_token_edit = QLineEdit(self.credentials.get('hostinger_api_token', ''))
        self.h_token_edit.setEchoMode(QLineEdit.Password)
        self.h_token_edit.setPlaceholderText('Paste your Hostinger API Bearer Token')
        h_form.addWidget(self.h_token_edit, 0, 1)

        h_toggle_btn = QPushButton('Show/Hide')
        h_toggle_btn.clicked.connect(lambda: self.toggle_echo(self.h_token_edit))
        h_form.addWidget(h_toggle_btn, 0, 2)
        h_layout.addLayout(h_form)

        h_test_row = QHBoxLayout()
        self.h_test_btn = QPushButton('Test Hostinger Connection')
        self.h_test_btn.clicked.connect(self.test_hostinger)
        h_test_row.addWidget(self.h_test_btn)
        self.h_status_lbl = QLabel('')
        self.h_status_lbl.setWordWrap(True)
        h_test_row.addWidget(self.h_status_lbl, 1)
        h_layout.addLayout(h_test_row)
        h_layout.addStretch()
        tabs.addTab(h_tab, 'Hostinger')

        # Tab 2: Namecheap
        nc_tab = QWidget()
        nc_layout = QVBoxLayout(nc_tab)
        nc_layout.setSpacing(10)

        nc_info = QLabel(
            'Namecheap API lets Project Hub fetch your domains and expiry dates.\n'
            'Requirements:\n'
            '• Enable API Access in Namecheap: Profile -> Tools -> Namecheap API Access.\n'
            '  (Note: Namecheap requires $50+ total spend or 20+ active domains).\n'
            '• Whitelist your public IP address under "Whitelisted IPs" in Namecheap.'
        )
        nc_info.setStyleSheet('color: #7a8a9a; font-size: 11px;')
        nc_info.setWordWrap(True)
        nc_layout.addWidget(nc_info)

        nc_form = QGridLayout()
        nc_form.setColumnStretch(1, 1)
        nc_form.addWidget(QLabel('Username:'), 0, 0)
        self.nc_user_edit = QLineEdit(self.credentials.get('namecheap_username', ''))
        self.nc_user_edit.setPlaceholderText('Your Namecheap account username')
        nc_form.addWidget(self.nc_user_edit, 0, 1, 1, 2)

        nc_form.addWidget(QLabel('API Key:'), 1, 0)
        self.nc_key_edit = QLineEdit(self.credentials.get('namecheap_api_key', ''))
        self.nc_key_edit.setEchoMode(QLineEdit.Password)
        self.nc_key_edit.setPlaceholderText('Paste your Namecheap API key')
        nc_form.addWidget(self.nc_key_edit, 1, 1)
        nc_toggle_btn = QPushButton('Show/Hide')
        nc_toggle_btn.clicked.connect(lambda: self.toggle_echo(self.nc_key_edit))
        nc_form.addWidget(nc_toggle_btn, 1, 2)

        nc_form.addWidget(QLabel('Client IP:'), 2, 0)
        self.nc_ip_edit = QLineEdit(self.credentials.get('namecheap_client_ip', ''))
        self.nc_ip_edit.setPlaceholderText('Public IP whitelisted in Namecheap')
        nc_form.addWidget(self.nc_ip_edit, 2, 1)
        detect_ip_btn = QPushButton('Detect My IP')
        detect_ip_btn.clicked.connect(self.detect_ip)
        nc_form.addWidget(detect_ip_btn, 2, 2)
        nc_layout.addLayout(nc_form)

        nc_test_row = QHBoxLayout()
        self.nc_test_btn = QPushButton('Test Namecheap Connection')
        self.nc_test_btn.clicked.connect(self.test_namecheap)
        nc_test_row.addWidget(self.nc_test_btn)
        self.nc_status_lbl = QLabel('')
        self.nc_status_lbl.setWordWrap(True)
        nc_test_row.addWidget(self.nc_status_lbl, 1)
        nc_layout.addLayout(nc_test_row)
        nc_layout.addStretch()
        tabs.addTab(nc_tab, 'Namecheap')

        layout.addWidget(tabs, 1)

        bottom_btns = QHBoxLayout()
        save_btn = QPushButton('Save & Close')
        save_btn.clicked.connect(self.save)
        cancel_btn = QPushButton('Cancel')
        cancel_btn.clicked.connect(self.reject)
        bottom_btns.addStretch()
        bottom_btns.addWidget(cancel_btn)
        bottom_btns.addWidget(save_btn)
        layout.addLayout(bottom_btns)

        self.setStyleSheet(TECH_STYLE)

    def toggle_echo(self, edit):
        edit.setEchoMode(QLineEdit.Normal if edit.echoMode() == QLineEdit.Password else QLineEdit.Password)

    def detect_ip(self):
        self.nc_status_lbl.setText('Detecting public IP...')
        self.nc_status_lbl.setStyleSheet('color: #c8d6e5; font-size: 11px;')
        ip = detect_public_ip()
        if ip:
            self.nc_ip_edit.setText(ip)
            self.nc_status_lbl.setText(f'Detected IP: {ip}. Ensure this IP is whitelisted in Namecheap.')
            self.nc_status_lbl.setStyleSheet('color: #00ff88; font-size: 11px;')
        else:
            self.nc_status_lbl.setText('Could not auto-detect public IP. Please enter it manually.')
            self.nc_status_lbl.setStyleSheet('color: #ff4d6d; font-size: 11px;')

    def test_hostinger(self):
        token = self.h_token_edit.text().strip()
        if not token:
            self.h_status_lbl.setText('Enter an API token first.')
            self.h_status_lbl.setStyleSheet('color: #ff4d6d; font-size: 11px;')
            return
        self.h_status_lbl.setText('Connecting to Hostinger...')
        self.h_status_lbl.setStyleSheet('color: #c8d6e5; font-size: 11px;')
        QApplication.processEvents()
        try:
            domains = fetch_hostinger_domains(token)
            vms = fetch_hostinger_vps(token)
            self.h_status_lbl.setText(f'Connected! Found {len(domains)} domain(s) and {len(vms)} VPS.')
            self.h_status_lbl.setStyleSheet('color: #00ff88; font-size: 11px; font-weight: bold;')
        except Exception as exc:
            self.h_status_lbl.setText(str(exc))
            self.h_status_lbl.setStyleSheet('color: #ff4d6d; font-size: 11px;')

    def test_namecheap(self):
        user = self.nc_user_edit.text().strip()
        key = self.nc_key_edit.text().strip()
        ip = self.nc_ip_edit.text().strip()
        if not user or not key:
            self.nc_status_lbl.setText('Enter Namecheap username and API key.')
            self.nc_status_lbl.setStyleSheet('color: #ff4d6d; font-size: 11px;')
            return
        if not ip:
            ip = detect_public_ip()
            if ip:
                self.nc_ip_edit.setText(ip)
            else:
                self.nc_status_lbl.setText('Enter whitelisted client IP.')
                self.nc_status_lbl.setStyleSheet('color: #ff4d6d; font-size: 11px;')
                return
        self.nc_status_lbl.setText('Connecting to Namecheap...')
        self.nc_status_lbl.setStyleSheet('color: #c8d6e5; font-size: 11px;')
        QApplication.processEvents()
        try:
            domains = fetch_namecheap_domains(user, key, ip)
            self.nc_status_lbl.setText(f'Connected! Found {len(domains)} domain(s).')
            self.nc_status_lbl.setStyleSheet('color: #00ff88; font-size: 11px; font-weight: bold;')
        except Exception as exc:
            self.nc_status_lbl.setText(str(exc))
            self.nc_status_lbl.setStyleSheet('color: #ff4d6d; font-size: 11px;')

    def save(self):
        try:
            creds = self.vault.load()
        except Exception:
            creds = {}
        creds['hostinger_api_token'] = self.h_token_edit.text().strip()
        creds['namecheap_username'] = self.nc_user_edit.text().strip()
        creds['namecheap_api_key'] = self.nc_key_edit.text().strip()
        creds['namecheap_client_ip'] = self.nc_ip_edit.text().strip()
        try:
            self.vault.save(creds)
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, 'Save error', f'Could not save credentials to vault:\n{exc}')


class CloudImportDialog(QDialog):
    def __init__(self, parent=None, vault=None):
        super().__init__(parent)
        self.setWindowTitle('Import / Autofill from Hostinger & Namecheap')
        self.resize(840, 590)
        self.vault = vault or CredentialVault()
        self.selected_data = None
        self._worker = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        top_bar = QHBoxLayout()
        title = QLabel('Online Accounts: Domains & Servers')
        title.setObjectName('title')
        title.setStyleSheet('font-size: 16px;')
        top_bar.addWidget(title)
        top_bar.addStretch()

        self.refresh_btn = QPushButton('↻ Refresh from Cloud')
        self.refresh_btn.clicked.connect(self.sync)
        top_bar.addWidget(self.refresh_btn)

        settings_btn = QPushButton('⚙️ Account Settings')
        settings_btn.clicked.connect(self.open_settings)
        top_bar.addWidget(settings_btn)
        layout.addLayout(top_bar)

        self.status_bar = QLabel('')
        self.status_bar.setStyleSheet('color: #7a8a9a; font-size: 11px;')
        layout.addWidget(self.status_bar)

        self.tabs = QTabWidget()

        # Tab 1: Hostinger Domains
        self.h_domains_tree = QTreeWidget()
        self.h_domains_tree.setHeaderLabels(['Domain', 'Status', 'Expires On', 'Auto-Renew'])
        self.h_domains_tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.tabs.addTab(self.h_domains_tree, 'Hostinger Domains (0)')

        # Tab 2: Hostinger VPS
        self.h_vps_tree = QTreeWidget()
        self.h_vps_tree.setHeaderLabels(['Name / Hostname', 'IP Address', 'Status', 'OS', 'Plan / Specs'])
        self.h_vps_tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.tabs.addTab(self.h_vps_tree, 'Hostinger VPS (0)')

        # Tab 3: Namecheap Domains
        self.nc_domains_tree = QTreeWidget()
        self.nc_domains_tree.setHeaderLabels(['Domain', 'Status', 'Expires On', 'Registered On', 'Auto-Renew'])
        self.nc_domains_tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.tabs.addTab(self.nc_domains_tree, 'Namecheap Domains (0)')

        self.tabs.currentChanged.connect(lambda idx: self.on_selection_changed())
        layout.addWidget(self.tabs, 1)

        self.preview_lbl = QLabel('Select a domain or server above to preview import fields.')
        self.preview_lbl.setStyleSheet('color: #00e5ff; font-size: 12px; padding: 6px; background: #0f141b; border: 1px solid #1f2a37; border-radius: 6px;')
        self.preview_lbl.setWordWrap(True)
        layout.addWidget(self.preview_lbl)

        bottom = QHBoxLayout()
        self.apply_btn = QPushButton('Apply to Project')
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.apply_selection)
        close_btn = QPushButton('Close')
        close_btn.clicked.connect(self.reject)

        bottom.addStretch()
        bottom.addWidget(close_btn)
        bottom.addWidget(self.apply_btn)
        layout.addLayout(bottom)

        self.setStyleSheet(TECH_STYLE)
        self.sync()

    def open_settings(self):
        dlg = ConnectedAccountsDialog(self, self.vault)
        if dlg.exec() == QDialog.Accepted:
            self.sync()

    def sync(self):
        try:
            creds = self.vault.load()
        except Exception:
            creds = {}
        h_token = creds.get('hostinger_api_token', '')
        nc_user = creds.get('namecheap_username', '')
        nc_key = creds.get('namecheap_api_key', '')
        nc_ip = creds.get('namecheap_client_ip', '')

        if not h_token and not (nc_user and nc_key):
            self.status_bar.setText('No connected account credentials found. Click "⚙️ Account Settings" above to set up Hostinger or Namecheap.')
            self.status_bar.setStyleSheet('color: #ffb86c; font-size: 11px;')
            return

        self.status_bar.setText('Fetching live data from connected accounts...')
        self.status_bar.setStyleSheet('color: #00e5ff; font-size: 11px;')
        self.refresh_btn.setEnabled(False)

        self._worker = AccountSyncWorker(h_token, nc_user, nc_key, nc_ip)
        self._worker.finished.connect(self.on_sync_finished)
        self._worker.start()

    def on_sync_finished(self, data, errors):
        self.refresh_btn.setEnabled(True)
        h_domains = data.get('hostinger_domains', [])
        h_vps = data.get('hostinger_vps', [])
        nc_domains = data.get('namecheap_domains', [])

        self.h_domains_tree.clear()
        for item in h_domains:
            it = QTreeWidgetItem([
                item.get('domain', ''),
                item.get('status', ''),
                item.get('expires_on', ''),
                'Yes' if item.get('auto_renew') else 'No',
            ])
            it.setData(0, Qt.UserRole, {'type': 'domain', 'data': item})
            self.h_domains_tree.addTopLevelItem(it)
        self.tabs.setTabText(0, f'Hostinger Domains ({len(h_domains)})')

        self.h_vps_tree.clear()
        for item in h_vps:
            it = QTreeWidgetItem([
                item.get('name', ''),
                item.get('ip_address', ''),
                item.get('status', ''),
                item.get('os', ''),
                item.get('plan', ''),
            ])
            it.setData(0, Qt.UserRole, {'type': 'vps', 'data': item})
            self.h_vps_tree.addTopLevelItem(it)
        self.tabs.setTabText(1, f'Hostinger VPS ({len(h_vps)})')

        self.nc_domains_tree.clear()
        for item in nc_domains:
            it = QTreeWidgetItem([
                item.get('domain', ''),
                item.get('status', ''),
                item.get('expires_on', ''),
                item.get('registered_on', ''),
                'Yes' if item.get('auto_renew') else 'No',
            ])
            it.setData(0, Qt.UserRole, {'type': 'domain', 'data': item})
            self.nc_domains_tree.addTopLevelItem(it)
        self.tabs.setTabText(2, f'Namecheap Domains ({len(nc_domains)})')

        if errors:
            err_msgs = [f"{k}: {v}" for k, v in errors.items()]
            self.status_bar.setText('Sync notice: ' + ' | '.join(err_msgs))
            self.status_bar.setStyleSheet('color: #ffb86c; font-size: 11px;')
        else:
            self.status_bar.setText(f'Sync complete. Found {len(h_domains)} Hostinger domain(s), {len(h_vps)} VPS, and {len(nc_domains)} Namecheap domain(s).')
            self.status_bar.setStyleSheet('color: #00ff88; font-size: 11px;')

    def current_tree(self):
        idx = self.tabs.currentIndex()
        if idx == 0:
            return self.h_domains_tree
        elif idx == 1:
            return self.h_vps_tree
        else:
            return self.nc_domains_tree

    def on_selection_changed(self):
        tree = self.current_tree()
        selected = tree.selectedItems()
        if not selected:
            self.apply_btn.setEnabled(False)
            self.preview_lbl.setText('Select a domain or server above to preview import fields.')
            return

        item_info = selected[0].data(0, Qt.UserRole)
        if not item_info:
            return

        item_type = item_info.get('type')
        d = item_info.get('data', {})

        if item_type == 'domain':
            text = (
                f"Selected Domain: {d.get('domain')} ({d.get('provider')})\n"
                f"• Renewal / Expiration: {d.get('expires_on') or 'N/A'}\n"
                f"• Auto-Renew: {'Yes' if d.get('auto_renew') else 'No'}\n"
                f"• Registrar Site: {d.get('provider_url') or 'N/A'}"
            )
        else:
            text = (
                f"Selected VPS: {d.get('name')} ({d.get('provider')})\n"
                f"• IP Address: {d.get('ip_address') or 'N/A'}\n"
                f"• Plan / Specs: {d.get('plan') or 'N/A'}\n"
                f"• OS / Runtime: {d.get('os') or 'N/A'}"
            )
        self.preview_lbl.setText(text)
        self.apply_btn.setEnabled(True)

    def apply_selection(self):
        tree = self.current_tree()
        selected = tree.selectedItems()
        if not selected:
            return
        item_info = selected[0].data(0, Qt.UserRole)
        if not item_info:
            return

        item_type = item_info.get('type')
        d = item_info.get('data', {})

        if item_type == 'domain':
            self.selected_data = {
                'is_website': True,
                'domain': d.get('domain', ''),
                'domain_registrar': d.get('provider', ''),
                'domain_registrar_url': d.get('provider_url', ''),
                'domain_registered_on': d.get('registered_on', ''),
                'domain_expires_on': d.get('expires_on', ''),
                'domain_auto_renew': bool(d.get('auto_renew', False)),
                'domain_remind': True,
                'site': f"https://{d.get('domain', '')}",
            }
        else:
            self.selected_data = {
                'is_website': True,
                'hosting_provider': 'Hostinger',
                'hosting_url': 'https://hpanel.hostinger.com',
                'hosting_plan': d.get('plan', ''),
                'hosting_ip': d.get('ip_address', ''),
                'server_host': d.get('ip_address', ''),
                'ssh_host': d.get('ip_address', ''),
                'site_runtime': d.get('os', ''),
                'server_name': d.get('name', ''),
            }
        self.accept()
