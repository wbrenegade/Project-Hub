"""Main Project Hub window and application entry point."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import asdict, fields
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QDialog, QMainWindow, QSplitter, QStackedWidget, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from project_data import (
    DATA, VAULT, CredentialVault, Project, VaultError, human, load,
    load_recent, renewal_text, save, save_recent, upcoming_renewals,
)
from project_dialogs import (
    CloudImportDialog, ConnectedAccountsDialog, CredentialDialog, ProjectDialog, RemoteBrowserDialog, SettingsDialog,
    SeoDialog, TECH_STYLE,
)
from project_services import (
    ScanWorker, SiteCheckWorker, scp_download, seo_audit, ssh_exec,
)

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Project Hub')
        self.resize(1170, 750)
        self.setMinimumSize(890, 580)
        self.projects = load()
        self.recent_ids = load_recent()
        self.selected = None
        self._scan_workers = []
        self._site_workers = []
        self.search_text = ''
        self.site_status = ''
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.home_page = self.build_home()
        self.work_page = QWidget()
        self.build_work()
        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.work_page)
        self.show_home()
        self.create_menus()

    def create_menus(self):
        mb = self.menuBar()
        proj = mb.addMenu('Project')
        proj.addAction('Home', self.show_home)
        proj.addAction('Add project (from scratch)', self.add)
        proj.addAction('Scan folder', self.add_folder)
        proj.addAction('Import from Hostinger / Namecheap...', self.import_cloud)
        proj.addAction('Edit selected', self.edit)
        proj.addAction('Delete selected', self.delete)
        proj.addAction('Rescan selected', self.rescan)
        proj.addSeparator()
        proj.addAction('Export JSON', self.export)
        site = mb.addMenu('Site')
        site.addAction('Open site', self.open_site)
        site.addAction('Check live', lambda: self.check_live(False))
        site.addAction('SEO check', self.seo_check)
        remote = mb.addMenu('Remote (SSH)')
        remote.addAction('Browse remote folder', self.browse_remote)
        remote.addAction('Download via SCP', self.download_remote)
        remote.addAction('Run remote command', self.run_remote)
        tools = mb.addMenu('Tools')
        tools.addAction('Credentials (vault)', self.manage_credentials)
        tools.addAction('Connected accounts (APIs)', self.manage_connected_accounts)
        tools.addAction('Settings', self.open_settings)
        helpm = mb.addMenu('Help')
        helpm.addAction('About', lambda: QMessageBox.information(self, 'About', 'Project Hub — offline-first project inventory.'))

    def build_home(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)
        title = QLabel('PROJECT HUB')
        title.setObjectName('title')
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        sub = QLabel('Offline-first project inventory & launcher')
        sub.setObjectName('subtitle')
        sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(sub)
        layout.addSpacing(10)

        cards = QHBoxLayout()
        cards.setSpacing(16)
        def make_card(text, subtext, cb):
            f = QFrame()
            f.setObjectName('card')
            f.setFixedSize(200, 140)
            fl = QVBoxLayout(f)
            b = QPushButton(text)
            b.clicked.connect(cb)
            fl.addWidget(b, 0, Qt.AlignCenter)
            s = QLabel(subtext)
            s.setStyleSheet('color: #7a8a9a; font-size: 11px;')
            s.setAlignment(Qt.AlignCenter)
            s.setWordWrap(True)
            fl.addWidget(s)
            return f
        cards.addStretch()
        cards.addWidget(make_card('Create project', 'From scratch', self.add))
        cards.addWidget(make_card('Scan folder', 'Pick a folder', self.add_folder))
        cards.addWidget(make_card('Import online', 'Hostinger & Namecheap', self.import_cloud))
        cards.addWidget(make_card('Settings', 'App settings', self.open_settings))
        cards.addStretch()
        layout.addLayout(cards)

        layout.addWidget(QLabel('Renewal reminders'))
        self.reminder_list = QListWidget()
        self.reminder_list.setMaximumHeight(110)
        self.reminder_list.itemDoubleClicked.connect(self.open_reminder)
        layout.addWidget(self.reminder_list)

        layout.addWidget(QLabel('Recent projects'))
        self.recent_list = QListWidget()
        self.recent_list.itemDoubleClicked.connect(self.open_recent)
        layout.addWidget(self.recent_list, 1)
        self.refresh_recent()
        return page

    def build_work(self):
        layout = QVBoxLayout(self.work_page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        home_btn = QPushButton('Home')
        home_btn.clicked.connect(self.show_home)
        top.addWidget(home_btn)
        title = QLabel('PROJECT HUB')
        title.setObjectName('title')
        top.addWidget(title)
        top.addSpacing(20)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search projects, machines, domains...')
        self.search.setFixedWidth(260)
        self.search.textChanged.connect(lambda t: (setattr(self, 'search_text', t), self.refresh()))
        top.addWidget(self.search)
        top.addStretch()
        self.site_status_label = QLabel('')
        self.site_status_label.setStyleSheet('color: #7a8a9a; font-size: 11px;')
        top.addWidget(self.site_status_label)
        root_layout = layout
        root_layout.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(['Project', 'Machine', 'Size*', 'Status'])
        self.tree.setColumnWidth(0, 205)
        self.tree.setColumnWidth(1, 125)
        self.tree.setColumnWidth(2, 90)
        self.tree.setColumnWidth(3, 100)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemSelectionChanged.connect(self.on_select)
        left_layout.addWidget(self.tree)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setFont(QFont('Consolas', 10))
        right_layout.addWidget(self.details)
        splitter.addWidget(right)
        splitter.setSizes([420, 750])
        root_layout.addWidget(splitter, 1)

        self.status = QLabel('Ready. Sizes exclude common dependency/build folders; a partial scan is marked.')
        self.status.setObjectName('status')
        root_layout.addWidget(self.status)

        self.setStyleSheet(TECH_STYLE)

    def show_home(self):
        self.refresh_recent()
        self.stack.setCurrentIndex(0)

    def show_work(self):
        self.stack.setCurrentIndex(1)

    def refresh_recent(self):
        self.refresh_reminders()
        self.recent_list.clear()
        projects_by_id = {p.id: p for p in self.projects}
        for pid in self.recent_ids:
            p = projects_by_id.get(pid)
            if p:
                item = QListWidgetItem(f'{p.name}  —  {p.machine or "local"}')
                item.setData(Qt.UserRole, p.id)
                self.recent_list.addItem(item)

    def refresh_reminders(self):
        self.reminder_list.clear()
        renewals = upcoming_renewals(self.projects)
        if not renewals:
            self.reminder_list.addItem('No renewals due soon.')
            return
        for days_left, p, label, expires in renewals:
            item = QListWidgetItem(f'{p.name}: {label} renewal {renewal_text(days_left)} ({expires.isoformat()})')
            item.setData(Qt.UserRole, p.id)
            if days_left < 0:
                item.setForeground(QColor('#ff4d6d'))
            elif days_left <= 7:
                item.setForeground(QColor('#ffb347'))
            self.reminder_list.addItem(item)

    def open_reminder(self, item):
        if item.data(Qt.UserRole):
            self.open_recent(item)

    def alert_renewals(self):
        renewals = upcoming_renewals(self.projects)
        if renewals:
            lines = [f'{p.name}: {label} {renewal_text(days_left)} ({expires.isoformat()})'
                     for days_left, p, label, expires in renewals]
            QMessageBox.warning(self, 'Renewals due soon', '\n'.join(lines))

    def open_recent(self, item):
        pid = item.data(Qt.UserRole)
        self.selected = pid
        self.show_work()
        self.refresh()
        # select in tree
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, Qt.UserRole) == pid:
                self.tree.setCurrentItem(it)
                break

    def push_recent(self, pid):
        if pid in self.recent_ids:
            self.recent_ids.remove(pid)
        self.recent_ids.insert(0, pid)
        self.recent_ids = self.recent_ids[:15]
        save_recent(self.recent_ids)

    def open_settings(self):
        SettingsDialog(self).exec()

    def manage_connected_accounts(self):
        ConnectedAccountsDialog(self).exec()

    def import_cloud(self):
        dlg = CloudImportDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.selected_data:
            data = dlg.selected_data
            name = data.get('domain') or data.get('server_name') or data.get('server_host') or 'New Project'
            p = Project(id=uuid4().hex, name=name, **{k: v for k, v in data.items() if hasattr(Project, k)})
            dialog = ProjectDialog(self, p)
            if dialog.exec() == QDialog.Accepted and dialog.result:
                for k, v in dialog.result.items():
                    setattr(p, k, v)
                self.projects.append(p)
                self.selected = p.id
                self.push_recent(p.id)
                self.persist()
                self.show_work()

    def selected_project(self):
        return next((p for p in self.projects if p.id == self.selected), None)

    def refresh(self):
        existing = self.selected
        self.tree.clear()
        term = self.search_text.lower()
        for p in sorted(self.projects, key=lambda p: p.name.lower()):
            searchable_fields = [f.name for f in fields(Project) if f.name not in {'modules', 'clues'}]
            if term and term not in ' '.join(str(getattr(p, k, '')) for k in searchable_fields).lower():
                continue
            item = QTreeWidgetItem([p.name, p.machine,
                                    (human(p.size_bytes) if p.scanned_at else 'Not scanned'),
                                    p.status])
            item.setData(0, Qt.UserRole, p.id)
            self.tree.addTopLevelItem(item)
        if existing:
            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                if it.data(0, Qt.UserRole) == existing:
                    self.tree.setCurrentItem(it)
                    break
        self.show_details()

    def on_select(self):
        items = self.tree.selectedItems()
        self.selected = items[0].data(0, Qt.UserRole) if items else None
        if self.selected:
            self.push_recent(self.selected)
        self.site_status = ''
        self.site_status_label.setText('')
        self.show_details()
        p = self.selected_project()
        if p and p.site:
            self.check_live(silent=True)

    def show_details(self):
        p = self.selected_project()
        if p:
            lines = []
            def group(label, items):
                lines.append(label.upper())
                lines.append('-' * 53)
                for name, value in items:
                    lines.append(f'{name}: {value if value not in (None, "") else "-"}')
                lines.append('')
            group('Project', [('Name', p.name), ('Description', p.description), ('Status', p.status),
                              ('Type', p.kind), ('Machine', p.machine), ('Local path', p.path), ('Notes', p.notes)])
            group('Inventory', [('Size (excluding dependency/build folders)', human(p.size_bytes) if p.scanned_at else 'Not scanned'),
                                ('Files scanned', p.file_count if p.scanned_at else 'Not scanned'),
                                ('Last scan', p.scanned_at),
                                ('Dependencies / modules', ', '.join(p.modules) or 'None detected'),
                                ('Detected files / frameworks', ', '.join(p.clues) or 'None detected')])
            if p.is_website:
                group('Website: domain', [('Domain', p.domain), ('Registrar / purchased from', p.domain_registrar),
                                          ('Registrar site', p.domain_registrar_url),
                                          ('Registered on', p.domain_registered_on),
                                          ('Renews / expires', p.domain_expires_on), ('Renewal cost', p.domain_cost),
                                          ('Auto-renew', 'Yes' if p.domain_auto_renew else 'No'),
                                          ('Reminder', f'{p.domain_remind_days} days before' if p.domain_remind else 'Off')])
                group('Website: hosting', [('Purchased from', p.hosting_provider), ('Provider site', p.hosting_url),
                                           ('Plan', p.hosting_plan), ('IP address', p.hosting_ip),
                                           ('Purchased on', p.hosting_purchased_on),
                                           ('Renews / expires', p.hosting_expires_on), ('Renewal cost', p.hosting_cost),
                                           ('Auto-renew', 'Yes' if p.hosting_auto_renew else 'No'),
                                           ('Reminder', f'{p.hosting_remind_days} days before' if p.hosting_remind else 'Off')])
            group('Server and runtime', [('Website', p.site), ('Domain', p.domain), ('Health URL', p.health_url),
                                         ('Server / host', p.server_host), ('Server port', p.server_port),
                                         ('Server user', p.server_user), ('Credential reference', p.server_credential_ref),
                                         ('Remote path', p.server_path), ('Deployment platform', p.deployment_platform),
                                         ('Runtime / web stack', p.site_runtime), ('Service', p.service),
                                         ('Environment', p.environment)])
            group('Cloud', [('Provider', p.cloud_provider), ('Account / tenant', p.cloud_account),
                            ('Project / subscription', p.cloud_project), ('Region', p.cloud_region),
                            ('Service / resource', p.cloud_service), ('Credential reference', p.cloud_credential_ref)])
            group('Payments', [('Provider', p.payment_provider), ('Mode', p.payment_mode),
                               ('Merchant / account', p.payment_account), ('Webhook URL', p.payment_webhook_url),
                               ('Credential reference', p.payment_credential_ref)])
            group('AI', [('Provider', p.ai_provider), ('Model', p.ai_model), ('Endpoint', p.ai_endpoint),
                         ('Credential reference', p.ai_credential_ref)])
            group('Files and paths', [('Configuration', p.config_path), ('Data', p.data_path),
                                      ('Logs', p.log_path), ('Build / artifacts', p.artifact_path)])
            group('Commands', [('Start', p.start_cmd), ('Restart', p.restart_cmd), ('Stop', p.stop_cmd)])
            group('SSH / Remote', [('Host', p.ssh_host or p.server_host), ('Port', p.ssh_port),
                                   ('User', p.ssh_user or p.server_user), ('Key', p.ssh_key_path),
                                   ('Remote path', p.ssh_remote_path or p.server_path)])
            if self.site_status:
                lines.append('Site status: ' + self.site_status)
                lines.append('')
            self.details.setPlainText('\n'.join(lines))
        else:
            self.details.setPlainText('Select a project to see its full inventory.\n\nStart by clicking "Scan folder" to add a local project.')

    def persist(self):
        try:
            save(self.projects)
        except OSError as e:
            QMessageBox.critical(self, 'Save failed', str(e))
            return
        self.refresh_reminders()
        self.refresh()

    def manage_credentials(self):
        if os.name != 'nt':
            QMessageBox.critical(self, 'Credential vault unavailable', 'Encrypted credential storage is supported on Windows only.')
            return
        vault = CredentialVault()
        try:
            credentials = vault.load()
        except (OSError, VaultError) as exc:
            QMessageBox.critical(self, 'Credential vault error', str(exc))
            return
        dialog = CredentialDialog(self, vault, credentials)
        dialog.exec()

    def check_live(self, silent=False):
        p = self.selected_project()
        if not p or not p.site:
            if not silent:
                QMessageBox.information(self, 'No site', 'This project has no website URL.')
            return
        url = p.site
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https'):
            url = 'https://' + url
        self.site_status_label.setText('Checking...')
        worker = SiteCheckWorker(url)
        worker.finished.connect(lambda u, code, err, s=silent: self.on_site_checked(u, code, err, s))
        self._site_workers.append(worker)
        worker.start()

    def on_site_checked(self, url, code, err, silent):
        if code and code < 400:
            self.site_status = f'LIVE (HTTP {code})'
            self.site_status_label.setText(f'Site: LIVE ({code})')
            self.site_status_label.setStyleSheet('color: #00ff88; font-size: 11px; font-weight: bold;')
        elif code:
            self.site_status = f'DOWN (HTTP {code})'
            self.site_status_label.setText(f'Site: DOWN ({code})')
            self.site_status_label.setStyleSheet('color: #ff4d6d; font-size: 11px; font-weight: bold;')
        else:
            self.site_status = f'UNREACHABLE ({err})'
            self.site_status_label.setText('Site: UNREACHABLE')
            self.site_status_label.setStyleSheet('color: #ff4d6d; font-size: 11px; font-weight: bold;')
        self.show_details()
        if not silent:
            QMessageBox.information(self, 'Site status', self.site_status)

    def seo_check(self):
        p = self.selected_project()
        if not p or not p.site:
            QMessageBox.information(self, 'No site', 'This project has no website URL.')
            return
        url = p.site
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https'):
            url = 'https://' + url
        self.status.setText(f'Running SEO check on {url}...')
        try:
            report = seo_audit(url)
        except Exception as exc:
            report = f'Error: {exc}'
        self.status.setText('SEO check complete.')
        dlg = SeoDialog(self, url, report)
        dlg.exec()

    def browse_remote(self):
        p = self.selected_project()
        if not p:
            return
        if not (p.ssh_host or p.server_host):
            QMessageBox.information(self, 'No SSH host', 'Set an SSH host for this project first.')
            return
        dlg = RemoteBrowserDialog(self, p)
        dlg.exec()

    def download_remote(self):
        p = self.selected_project()
        if not p or not (p.ssh_host or p.server_host):
            QMessageBox.information(self, 'No SSH host', 'Set an SSH host for this project first.')
            return
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, 'Download', 'Remote file name:')
        if not ok or not name.strip():
            return
        local, _ = QFileDialog.getSaveFileName(self, 'Save as', name.strip())
        if not local:
            return
        out, err, rc = scp_download(p, name.strip(), local)
        if rc == 0:
            QMessageBox.information(self, 'Download complete', f'Saved to {local}')
        else:
            QMessageBox.critical(self, 'Download failed', err or out or f'exit {rc}')

    def run_remote(self):
        p = self.selected_project()
        if not p or not (p.ssh_host or p.server_host):
            QMessageBox.information(self, 'No SSH host', 'Set an SSH host for this project first.')
            return
        from PySide6.QtWidgets import QInputDialog
        cmd, ok = QInputDialog.getText(self, 'Remote command', 'Command to run in remote project folder:')
        if not ok or not cmd.strip():
            return
        out, err, rc = ssh_exec(p, cmd.strip())
        text = (out or '') + (('\n--- stderr ---\n' + err) if err else '')
        text += f'\n(exit {rc})'
        QMessageBox.information(self, 'Remote command result', text[:4000])

    def add(self):
        dialog = ProjectDialog(self)
        if dialog.exec() == QDialog.Accepted and dialog.result:
            from uuid import uuid4
            p = Project(id=uuid4().hex, **dialog.result)
            self.projects.append(p)
            self.selected = p.id
            self.push_recent(p.id)
            self.persist()
            self.show_work()
            if p.path and Path(p.path).is_dir():
                self.rescan()

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Choose project root folder')
        if not path:
            return
        from uuid import uuid4
        if any(os.path.normcase(os.path.abspath(p.path)) == os.path.normcase(os.path.abspath(path)) for p in self.projects if p.path):
            QMessageBox.information(self, 'Already tracked', 'That folder is already in the inventory.')
            return
        p = Project(id=uuid4().hex, name=Path(path).name, path=path,
                    machine=os.environ.get('COMPUTERNAME') or (os.uname().nodename if hasattr(os, 'uname') else ''))
        self.projects.append(p)
        self.selected = p.id
        self.push_recent(p.id)
        self.persist()
        self.show_work()
        self.rescan()

    def edit(self):
        p = self.selected_project()
        if not p:
            return
        dialog = ProjectDialog(self, p)
        if dialog.exec() == QDialog.Accepted and dialog.result:
            for k, v in dialog.result.items():
                setattr(p, k, v)
            self.persist()

    def delete(self):
        p = self.selected_project()
        if p and QMessageBox.question(self, 'Remove from inventory',
                                     f'Remove {p.name} from Project Hub?\n\nProject files will NOT be deleted.') == QMessageBox.Yes:
            self.projects.remove(p)
            self.selected = None
            self.persist()
            self.show_home()

    def rescan(self):
        p = self.selected_project()
        if not p or not p.path:
            return
        project_id = p.id
        path = p.path
        self.status.setText(f'Scanning {p.name}...')
        worker = ScanWorker(project_id, path)
        worker.finished.connect(self.on_scan_finished)
        self._scan_workers.append(worker)
        worker.start()

    def on_scan_finished(self, project_id, path, result, error):
        if error:
            self.status.setText(f'Scan failed: {error}')
            return
        obj = next((x for x in self.projects if x.id == project_id), None)
        if obj and obj.path == path:
            obj.size_bytes, obj.file_count, obj.modules, obj.clues = result
            obj.scanned_at = time.strftime('%Y-%m-%d %H:%M:%S')
            self.persist()
            self.status.setText(f'Scan complete: {obj.name} - {human(obj.size_bytes)}, {obj.file_count:,} files')

    def open_folder(self):
        p = self.selected_project()
        if not p or not Path(p.path).is_dir():
            QMessageBox.information(self, 'Folder unavailable', 'This project folder is not accessible here.')
            return
        if sys.platform == 'win32':
            os.startfile(p.path)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', p.path])
        else:
            subprocess.Popen(['xdg-open', p.path])

    def open_site(self):
        p = self.selected_project()
        if not p or not p.site:
            return
        parsed = urlsplit(p.site)
        url = p.site if parsed.scheme in ('http', 'https') else 'https://' + p.site
        if urlsplit(url).scheme not in ('http', 'https'):
            return
        webbrowser.open(url)

    def copy_command(self, field):
        p = self.selected_project()
        if not p or not getattr(p, field):
            return
        QApplication.clipboard().setText(getattr(p, field))
        self.status.setText('Command copied to clipboard.')

    def run_command(self, field):
        p = self.selected_project()
        if not p or not getattr(p, field):
            return
        if not Path(p.path).is_dir():
            QMessageBox.critical(self, 'Folder unavailable', 'Local folder is not accessible.')
            return
        command = getattr(p, field)
        if QMessageBox.question(self, 'Execute local command',
                                f'Run this command on THIS computer in:\n{p.path}\n\n{command}\n\nOnly run commands you trust.') != QMessageBox.Yes:
            return
        if sys.platform == 'win32':
            subprocess.Popen(['cmd.exe', '/k', command], cwd=p.path, creationflags=subprocess.CREATE_NEW_CONSOLE)
        elif sys.platform == 'darwin':
            QMessageBox.information(self, 'Copy instead', 'On macOS, copy the command and run it in Terminal.')
            return
        else:
            terminal = shutil.which('x-terminal-emulator') or shutil.which('gnome-terminal') or shutil.which('xterm')
            if not terminal:
                QMessageBox.information(self, 'Copy instead', 'No terminal launcher detected. Use Copy start.')
                return
            if Path(terminal).name == 'gnome-terminal':
                args = [terminal, '--', 'bash', '-lc', command + '; exec bash']
            else:
                args = [terminal, '-e', 'bash', '-lc', command + '; exec bash']
            subprocess.Popen(args, cwd=p.path)
        self.status.setText('Command launched in a separate terminal (not monitored).')

    def export(self):
        target, _ = QFileDialog.getSaveFileName(self, 'Export project inventory', '', 'JSON (*.json)')
        if not target:
            return
        try:
            Path(target).write_text(json.dumps([asdict(p) for p in self.projects], indent=2), encoding='utf8')
        except OSError as exc:
            QMessageBox.critical(self, 'Export failed', str(exc))

    def run(self):
        self.show()

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(TECH_STYLE)
    window = App()
    window.show()
    window.alert_renewals()
    sys.exit(app.exec())
