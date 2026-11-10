"""Main Project Hub window and application entry point."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QTimer
from pydantic import ValidationError
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QDialog, QMainWindow, QSplitter, QStackedWidget, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from project_models import (
    BYTES_PER_MB, Project, ProjectManager, ScanInfo, human, renewal_text, set_path, upcoming_renewals,
)
from project_storage import (
    CredentialVault, ProjectStore, StorageError, VaultError, load_loose_items, load_map_layout, load_recent, load_settings,
    open_in_editor, save_loose_items, save_map_layout, save_recent,
)
from project_dialogs import (
    CloudImportDialog, ConnectedAccountsDialog, CredentialDialog, DnsRecordsDialog, ProjectDialog, RemoteBrowserDialog, SettingsDialog,
    SeoDialog, TECH_STYLE, validation_text,
)
from project_map import ProjectMapView, QMenu
from project_git import GitDialog
from project_inference import apply_inference
from project_watch import FolderWatcher

RESCAN_DELAY_MS = 8000
from project_services import (
    ScanWorker, SiteCheckWorker, scp_download, seo_audit, ssh_exec,
)

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Project Hub')
        self.resize(1170, 750)
        self.setMinimumSize(890, 580)
        self.store = ProjectStore()
        try:
            self.manager = self.store.load()
        except StorageError as exc:
            QMessageBox.critical(None, 'Database error', str(exc))
            self.manager = ProjectManager()
        self.recent_ids = load_recent()
        # Index into self.manager.projects. Tree rows carry the same index; ids are kept only
        # where a reference must survive restarts or list changes (recents, reminders, scans).
        self.selected_index = None
        self._scan_workers = []
        self._site_workers = []
        self.git_dialog = None
        self._scan_timers = {}
        self._watched = {}
        self.watcher = FolderWatcher(self)
        self.watcher.changed.connect(self.on_folder_changed)
        self.search_text = ''
        self.site_status = ''
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.home_page = self.build_home()
        self.work_page = QWidget()
        self.build_work()
        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.work_page)
        self.refresh_recent()
        self.show_work()
        self.refresh()
        self.watcher.start()
        self.create_menus()

    def create_menus(self):
        mb = self.menuBar()
        proj = mb.addMenu('Project')
        proj.addAction('Home', self.show_home)
        proj.addAction('Add project (from scratch)', self.add)
        proj.addAction('Scan folder', self.add_folder)
        proj.addAction('Import from Hostinger / Namecheap...', self.import_cloud)
        proj.addAction('Edit selected', self.edit)
        proj.addAction('Delete project...', self.delete)
        proj.addAction('Rescan selected', self.rescan)
        proj.addAction('Open folder in editor', self.open_source_in_editor)
        proj.addAction('Git...', self.open_source_git)
        proj.addSeparator()
        proj.addAction('Export JSON', self.export)
        site = mb.addMenu('Site')
        site.addAction('Open site', self.open_site)
        site.addAction('Check live', lambda: self.check_live(False))
        site.addAction('SEO check', self.seo_check)
        site.addAction('DNS records...', self.manage_dns)
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
        add_btn = QPushButton('+ Add new')
        add_btn.clicked.connect(self.add)
        top.addWidget(add_btn)
        delete_btn = QPushButton('Delete project')
        delete_btn.clicked.connect(self.delete)
        top.addWidget(delete_btn)
        top.addSpacing(20)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search projects, machines, domains...')
        self.search.setFixedWidth(260)
        self.search.textChanged.connect(lambda t: (setattr(self, 'search_text', t), self.refresh()))
        top.addWidget(self.search)
        self.view_toggle = QPushButton('List view')
        self.view_toggle.clicked.connect(self.toggle_view)
        top.addWidget(self.view_toggle)
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
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.project_list_menu)
        def tree_key_press(event):
            if event.key() == Qt.Key_Delete:
                self.delete()
                event.accept()
                return
            QTreeWidget.keyPressEvent(self.tree, event)
        self.tree.keyPressEvent = tree_key_press
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

        # The map is the main view; the list + details splitter stays one click away.
        self.map = ProjectMapView(load_map_layout(), save_map_layout, load_loose_items(), save_loose_items)
        self.map.itemsDropped.connect(self.attach_items)
        self.map.unlinkRequested.connect(self.detach_item)
        self.map.projectSelected.connect(self.select_index)
        self.map.projectOpened.connect(lambda _index: self.edit())
        self.map.deleteRequested.connect(self.delete_project)
        self.map.addRequested.connect(self.add)
        self.map.scanRequested.connect(self.add_folder)
        self.map.fieldEdited.connect(self.edit_field)
        self.map.openInEditor.connect(self.open_path_in_editor)
        self.map.gitRequested.connect(self.open_git)
        self.views = QStackedWidget()
        self.views.addWidget(self.map)
        self.views.addWidget(splitter)
        root_layout.addWidget(self.views, 1)

        self.status = QLabel('Ready. Sizes exclude common dependency/build folders; a partial scan is marked.')
        self.status.setObjectName('status')
        root_layout.addWidget(self.status)

        self.setStyleSheet(TECH_STYLE)

    def toggle_view(self):
        to_list = self.views.currentWidget() is self.map
        self.views.setCurrentIndex(1 if to_list else 0)
        self.view_toggle.setText('Map view' if to_list else 'List view')

    def select_index(self, index):
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, Qt.UserRole) == index:
                self.tree.setCurrentItem(it)
                return

    def project_list_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        index = item.data(0, Qt.UserRole)
        if not isinstance(index, int) or not 0 <= index < len(self.manager):
            return
        project_id = self.manager[index].id
        menu = QMenu(self.tree)
        delete = menu.addAction('Delete project...')
        if menu.exec(self.tree.viewport().mapToGlobal(pos)) == delete:
            self.delete_project(project_id)

    def show_home(self):
        self.refresh_recent()
        self.stack.setCurrentIndex(0)

    def show_work(self):
        self.stack.setCurrentIndex(1)

    def refresh_recent(self):
        self.refresh_reminders()
        self.recent_list.clear()
        for pid in self.recent_ids:
            p = self.manager.find(pid)
            if p:
                item = QListWidgetItem(f'{p.name}  —  {p.machine or "local"}')
                item.setData(Qt.UserRole, p.id)
                self.recent_list.addItem(item)

    def refresh_reminders(self):
        self.reminder_list.clear()
        renewals = upcoming_renewals(self.manager.projects)
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
        renewals = upcoming_renewals(self.manager.projects)
        if renewals:
            lines = [f'{p.name}: {label} {renewal_text(days_left)} ({expires.isoformat()})'
                     for days_left, p, label, expires in renewals]
            QMessageBox.warning(self, 'Renewals due soon', '\n'.join(lines))

    def open_recent(self, item):
        self.selected_index = self.manager.index_of(item.data(Qt.UserRole))
        self.show_work()
        self.refresh()

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
            dialog = ProjectDialog(self, prefill=dlg.selected_data)
            if dialog.exec() == QDialog.Accepted and dialog.result:
                self.select_new(dialog.result)
                if dialog.result.source_location and Path(dialog.result.source_location).is_dir():
                    self.scan_project(dialog.result, initial=True, infer_type=False,
                                      protected=dialog.protected_scan_fields)

    def selected_project(self) -> Project | None:
        if self.selected_index is None or not 0 <= self.selected_index < len(self.manager):
            return None
        return self.manager[self.selected_index]

    def select_new(self, project):
        self.selected_index = self.manager.add(project)
        self.push_recent(project.id)
        self.persist()
        self.show_work()

    def refresh(self):
        existing = self.selected_index
        self.tree.clear()
        term = self.search_text.lower()
        shown = set()
        for index, p in sorted(enumerate(self.manager.projects), key=lambda entry: entry[1].name.lower()):
            if term and term not in p.searchable_text():
                continue
            shown.add(index)
            item = QTreeWidgetItem([p.name, p.machine, p.size_label, p.status])
            item.setData(0, Qt.UserRole, index)
            self.tree.addTopLevelItem(item)
        if existing is not None:
            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                if it.data(0, Qt.UserRole) == existing:
                    self.tree.setCurrentItem(it)
                    break
        self.map.set_projects(self.manager.projects, shown)
        self.sync_watcher()
        self.map.select(self.selected_index)
        self.show_details()

    def on_select(self):
        items = self.tree.selectedItems()
        self.selected_index = items[0].data(0, Qt.UserRole) if items else None
        p = self.selected_project()
        if p:
            self.push_recent(p.id)
        self.site_status = ''
        self.site_status_label.setText('')
        self.map.select(self.selected_index)
        self.show_details()
        if p and p.site_url:
            self.check_live(silent=True)

    def show_details(self):
        p = self.selected_project()
        if not p:
            self.details.setPlainText('Select a project to see its full inventory.\n\nStart by clicking "Scan folder" to add a local project.')
            return
        lines = []
        def group(label, items):
            lines.append(label.upper())
            lines.append('-' * 53)
            for name, value in items:
                lines.append(f'{name}: {value if value not in (None, "") else "-"}')
            lines.append('')
        def credential_rows(c):
            if not c:
                return []
            secret = f'stored in vault as "{c.credential_ref}"' if c.credential_ref else ('not saved yet' if c.password else '')
            return [('Host', c.host), ('Port', c.port), ('User', c.username), ('Password', secret)]
        def renewal_rows(item):
            r = item.reminder
            return [('Renews / expires', item.expires_on), ('Renewal cost', item.cost),
                    ('Auto-renew', 'Yes' if item.auto_renew else 'No'),
                    ('Reminder', f'{r.days_before} days before' if r and r.enabled else 'Off')]

        # Optional sections that are None (or a database provider of "none") are left out entirely.
        group('Project', [('Name', p.name), ('Description', p.description), ('Status', p.status),
                          ('Type', p.project_type), ('Created', p.date_created), ('Machine', p.machine),
                          ('Local path', p.source_location), ('Notes', p.notes)])
        scan = p.scan or ScanInfo()
        group('Inventory', [('Size (excluding dependency/build folders)', p.size_label),
                            ('Files scanned', f'{scan.file_count:,}' if scan.scanned_at else 'Not scanned'),
                            ('Last scan', scan.scanned_at.strftime('%Y-%m-%d %H:%M:%S') if scan.scanned_at else ''),
                            ('Dependencies / modules', ', '.join(scan.modules) or 'None detected'),
                            ('Detected files / frameworks', ', '.join(scan.clues) or 'None detected')])
        if p.uses_database:
            db = p.database
            group('Database', [('Provider', db.provider), ('Database name', db.db_name), *credential_rows(db.credentials)])
        if p.deployment:
            d = p.deployment
            rows = [('Environment', d.environment), ('Stage', d.stage), ('Website', d.site_url), ('Health URL', d.health_url)]
            if p.is_hosted:
                rows += [('Hosting provider', d.provider_name), ('Deployment platform', d.platform),
                         ('Runtime / web stack', d.runtime), ('Service', d.service), ('Remote path', d.remote_path),
                         *credential_rows(d.credentials), ('SSH key', d.ssh_key_path)]
            group('Deployment', rows)
        if p.is_website and p.website:
            if dom := p.website.domain:
                group('Website: domain', [('Domain', dom.name), ('Registrar / purchased from', dom.registrar),
                                          ('Registrar site', dom.registrar_url), ('Registered on', dom.registered_on),
                                          *renewal_rows(dom)])
            if host := p.website.hosting:
                group('Website: hosting', [('Purchased from', host.provider), ('Provider site', host.provider_url),
                                           ('Plan', host.plan), ('IP address', host.ip_address),
                                           ('Purchased on', host.purchased_on), *renewal_rows(host)])
            if dns := p.website.dns:
                group('Website: DNS', [('DNS provider', dns.provider), ('Provider site', dns.provider_url),
                                       ('Nameservers', ', '.join(dns.nameservers)), ('Records', len(dns.records) or 'None')])
                for r in dns.records:
                    priority = f'  priority {r.priority}' if r.priority is not None else ''
                    lines.insert(len(lines) - 1, f'  {r.type:<6} {r.name:<18} {r.value}  (TTL {r.ttl}{priority})')
        if c := p.cloud:
            group('Cloud', [('Provider', c.provider), ('Account / tenant', c.account),
                            ('Project / subscription', c.project), ('Region', c.region),
                            ('Service / resource', c.service), ('Credential reference', c.credential_ref)])
        if pay := p.payments:
            group('Payments', [('Provider', pay.provider), ('Mode', pay.mode), ('Merchant / account', pay.account),
                               ('Webhook URL', pay.webhook_url), ('Credential reference', pay.credential_ref)])
        if ai := p.ai:
            group('AI', [('Provider', ai.provider), ('Model', ai.model), ('Endpoint', ai.endpoint),
                         ('Credential reference', ai.credential_ref)])
        if paths := p.paths:
            group('Files and paths', [('Configuration', paths.config), ('Data', paths.data),
                                      ('Logs', paths.logs), ('Build / artifacts', paths.artifacts)])
        if cmd := p.commands:
            group('Commands', [('Start', cmd.start), ('Restart', cmd.restart), ('Stop', cmd.stop)])
        if self.site_status:
            lines.append('Site status: ' + self.site_status)
            lines.append('')
        self.details.setPlainText('\n'.join(lines))

    def persist(self):
        try:
            self.store.save(self.manager)
        except VaultError as e:
            QMessageBox.warning(self, 'Credential vault error', str(e))
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

    def site_url(self):
        p = self.selected_project()
        if not p or not p.site_url:
            return ''
        url = p.site_url
        return url if urlsplit(url).scheme in ('http', 'https') else 'https://' + url

    def check_live(self, silent=False):
        url = self.site_url()
        if not url:
            if not silent:
                QMessageBox.information(self, 'No site', 'This project has no website URL.')
            return
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
        url = self.site_url()
        if not url:
            QMessageBox.information(self, 'No site', 'This project has no website URL.')
            return
        self.status.setText(f'Running SEO check on {url}...')
        try:
            report = seo_audit(url)
        except Exception as exc:
            report = f'Error: {exc}'
        self.status.setText('SEO check complete.')
        dlg = SeoDialog(self, url, report)
        dlg.exec()

    def remote_project(self):
        p = self.selected_project()
        if not p or not p.remote:
            QMessageBox.information(self, 'No SSH host', 'Set a hosted deployment with a server host for this project first.')
            return None
        return p

    def manage_dns(self):
        p = self.selected_project()
        if not p or not p.is_website:
            QMessageBox.information(self, 'Not a website', 'Select a website project to manage its DNS records.')
            return
        project_id = p.id
        def save(records, sync):
            # Look the project up again: the list may have been re-sorted or edited while the dialog was open.
            index = self.manager.index_of(project_id)
            if index is None:
                return
            data = self.manager[index].model_dump()
            set_path(data, 'website.dns.records', [r.model_dump() for r in records])
            set_path(data, 'website.dns.sync', sync.model_dump() if sync else None)
            self.manager.replace(index, Project.model_validate(data))
            self.persist()
            self.status.setText(f'Saved {len(records)} DNS record(s) for {p.name}.')
        DnsRecordsDialog(self, p, save).exec()

    def browse_remote(self):
        p = self.remote_project()
        if p:
            RemoteBrowserDialog(self, p).exec()

    def download_remote(self):
        p = self.remote_project()
        if not p:
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
        p = self.remote_project()
        if not p:
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
        """Create a draggable project immediately; details can be filled in on the map."""
        names = {p.name.casefold() for p in self.manager.projects}
        name = 'New Project'
        number = 2
        while name.casefold() in names:
            name = f'New Project {number}'
            number += 1

        project = Project(name=name)
        # Ensure the new node won't be hidden by an existing text filter.
        if self.search.text():
            self.search.clear()
        self.select_new(project)
        self.views.setCurrentWidget(self.map)
        self.view_toggle.setText('List view')
        self.map.select(self.selected_index)
        self.status.setText(f'Added {name}. Drag it to position it; double-click to edit.')

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Choose project root folder')
        if not path:
            return
        if any(os.path.normcase(os.path.abspath(p.source_location)) == os.path.normcase(os.path.abspath(path))
               for p in self.manager.projects if p.source_location):
            QMessageBox.information(self, 'Already tracked', 'That folder is already in the inventory.')
            return
        p = Project(name=Path(path).name, source_location=path,
                    machine=os.environ.get('COMPUTERNAME') or (os.uname().nodename if hasattr(os, 'uname') else ''))
        self.select_new(p)
        self.scan_project(p, initial=True)

    def edit(self):
        p = self.selected_project()
        if not p:
            return
        dialog = ProjectDialog(self, p)
        if dialog.exec() == QDialog.Accepted and dialog.result:
            self.manager.replace(self.selected_index, dialog.result)
            self.persist()

    def edit_field(self, index, path, value):
        if not 0 <= index < len(self.manager):
            return
        data = self.manager[index].model_dump()
        set_path(data, path, value)
        try:
            project = Project.model_validate(data)
        except ValidationError as exc:
            QMessageBox.warning(self, 'Check value', validation_text(exc))
            return
        self.manager.replace(index, project)
        self.persist()

    def set_linked(self, index, items):
        data = self.manager[index].model_dump()
        data['linked_items'] = items
        self.manager.replace(index, Project.model_validate(data))
        self.persist()

    def attach_items(self, index, paths):
        if not 0 <= index < len(self.manager):
            return
        current = list(self.manager[index].linked_items)
        seen = {os.path.normcase(p) for p in current}
        added = []
        for path in paths:
            path = os.path.abspath(path)
            if os.path.normcase(path) not in seen:
                seen.add(os.path.normcase(path))
                added.append(path)
        name = self.manager[index].name
        if added:
            self.set_linked(index, current + added)
            self.status.setText(f'Added {len(added)} item(s) to {name}.')
        else:
            self.status.setText(f'Already linked to {name}.')

    def detach_item(self, index, path):
        if 0 <= index < len(self.manager):
            self.set_linked(index, [p for p in self.manager[index].linked_items if p != path])

    def delete(self):
        p = self.selected_project()
        if p:
            self.delete_project(p.id)

    def delete_project(self, project_id):
        p = self.manager.find(project_id)
        if p and QMessageBox.question(self, 'Delete project',
                                     f'Remove {p.name} from Project Hub?\n\nProject files will NOT be deleted.',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            name = p.name
            index = self.manager.index_of(project_id)
            if index is None:
                return
            was_selected = self.selected_project()
            self.manager.remove(index)
            if was_selected and was_selected.id == project_id:
                self.selected_index = None
            elif was_selected:
                self.selected_index = self.manager.index_of(was_selected.id)
            else:
                self.selected_index = None
            if project_id in self.recent_ids:
                self.recent_ids.remove(project_id)
                save_recent(self.recent_ids)
            if project_id in self.map.positions:
                self.map.positions.pop(project_id, None)
                if self.map.save_positions:
                    self.map.save_positions(self.map.positions)
            self.persist()
            self.status.setText(f'Removed project "{name}".')

    def rescan(self):
        p = self.selected_project()
        if p:
            self.scan_project(p)

    def scan_project(self, p, initial=False, infer_type=True, protected=None):
        if not p.source_location:
            return
        self.status.setText(f'Scanning {p.name}...')
        # Pass the id, not the index: the list may change while the scan runs.
        worker = ScanWorker(p.id, p.source_location, p.model_dump() if initial else None, infer_type, protected)
        worker.finished.connect(self.on_scan_finished)
        self._scan_workers.append(worker)
        worker.start()

    def on_scan_finished(self, project_id, path, result, error):
        if error:
            self.status.setText(f'Scan failed: {error}')
            return
        obj = self.manager.find(project_id)
        if obj and obj.source_location == path:
            inventory, inference, original, infer_type, protected = result
            size, count, modules, clues = inventory
            if inference is not None:
                fields, inference_clues = inference
                obj = apply_inference(obj, fields, original, infer_type, protected)
                self.manager.replace(next(i for i, p in enumerate(self.manager.projects) if p.id == project_id), obj)
                clues.extend(inference_clues)
            elif obj.scan:
                clues.extend(clue for clue in obj.scan.clues if clue.startswith((
                    'Detected ', 'Conflicting ', 'Configuration inference partial:')))
            obj.size_mb = size / BYTES_PER_MB
            obj.scan = ScanInfo(scanned_at=datetime.now().replace(microsecond=0), file_count=count, modules=modules, clues=clues)
            self.persist()
            review = ' - review detected configuration and scan clues' if inference is not None else ''
            self.status.setText(f'Scan complete: {obj.name} - {human(size)}, {count:,} files{review}')

    def open_folder(self):
        p = self.selected_project()
        if not p or not Path(p.source_location).is_dir():
            QMessageBox.information(self, 'Folder unavailable', 'This project folder is not accessible here.')
            return
        if sys.platform == 'win32':
            os.startfile(p.source_location)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', p.source_location])
        else:
            subprocess.Popen(['xdg-open', p.source_location])

    def open_path_in_editor(self, path):
        target = Path(path).expanduser()
        if not target.exists():
            QMessageBox.information(self, 'Path unavailable', f'This path is not accessible here:\n{path}')
            return
        try:
            open_in_editor(str(target), load_settings().get('editor', ''))
        except OSError as exc:
            QMessageBox.critical(self, 'Could not open editor', str(exc))

    def open_source_in_editor(self):
        p = self.selected_project()
        if not p or not p.source_location:
            QMessageBox.information(self, 'No folder', 'This project has no local folder set.')
            return
        self.open_path_in_editor(p.source_location)

    def on_folder_changed(self, project_id, files):
        p = self.manager.find(project_id)
        if not p:
            return
        self.map.mark_changed(project_id, files)
        more = f' (+{len(files) - 1} more)' if len(files) > 1 else ''
        self.status.setText(f'{p.name}: {files[0]} changed{more}')
        git = self.git_dialog
        if git is not None and not git.busy and os.path.normcase(os.path.abspath(p.source_location)) in (
                os.path.normcase(os.path.abspath(git.cwd)), os.path.normcase(os.path.abspath(git.folder))):
            git.refresh()
        # Wait for edits to settle before rescanning size and file count.
        timer = self._scan_timers.get(project_id)
        if timer is None:
            timer = self._scan_timers[project_id] = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda pid=project_id: self.rescan_changed(pid))
        timer.start(RESCAN_DELAY_MS)

    def rescan_changed(self, project_id):
        p = self.manager.find(project_id)
        if p:
            self.scan_project(p)

    def sync_watcher(self):
        folders = {p.id: p.source_location for p in self.manager.projects if p.source_location}
        if folders != self._watched:
            self._watched = folders
            self.watcher.set_folders(folders)

    def closeEvent(self, event):
        self.watcher.stop()
        super().closeEvent(event)

    def open_git(self, path):
        if not path or not Path(path).expanduser().exists():
            QMessageBox.information(self, 'Path unavailable', f'This path is not accessible here:\n{path}')
            return
        self.git_dialog = GitDialog(self, Path(path).expanduser())
        try:
            self.git_dialog.exec()
        finally:
            self.git_dialog = None

    def open_source_git(self):
        p = self.selected_project()
        if not p or not p.source_location:
            QMessageBox.information(self, 'No folder', 'This project has no local folder set.')
            return
        self.open_git(p.source_location)

    def open_site(self):
        url = self.site_url()
        if url:
            webbrowser.open(url)

    def copy_command(self, name):
        p = self.selected_project()
        if not p or not p.command(name):
            return
        QApplication.clipboard().setText(p.command(name))
        self.status.setText('Command copied to clipboard.')

    def run_command(self, name):
        p = self.selected_project()
        if not p or not p.command(name):
            return
        cwd = p.source_location
        if not Path(cwd).is_dir():
            QMessageBox.critical(self, 'Folder unavailable', 'Local folder is not accessible.')
            return
        command = p.command(name)
        if QMessageBox.question(self, 'Execute local command',
                                f'Run this command on THIS computer in:\n{cwd}\n\n{command}\n\nOnly run commands you trust.') != QMessageBox.Yes:
            return
        if sys.platform == 'win32':
            subprocess.Popen(['cmd.exe', '/k', command], cwd=cwd, creationflags=subprocess.CREATE_NEW_CONSOLE)
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
            subprocess.Popen(args, cwd=cwd)
        self.status.setText('Command launched in a separate terminal (not monitored).')

    def export(self):
        target, _ = QFileDialog.getSaveFileName(self, 'Export project inventory', '', 'JSON (*.json)')
        if not target:
            return
        try:
            self.store.export(self.manager, Path(target))
        except OSError as exc:
            QMessageBox.critical(self, 'Export failed', str(exc))

    def run(self):
        self.show()

class SafeApp(QApplication):
    def notify(self, obj, event):
        try:
            return super().notify(obj, event)
        except Exception:
            import traceback
            traceback.print_exc()
            traceback.print_exc(file=crash) if 'crash' in globals() else None
            return False

def main():
    import faulthandler
    global crash
    crash = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'crash.log'), 'w')
    faulthandler.enable(crash)
    try:
        app = SafeApp(sys.argv)
        app.setStyleSheet(TECH_STYLE)
        window = App()
        window.show()
        window.alert_renewals()
        sys.exit(app.exec())
    except Exception:
        import traceback
        traceback.print_exc()
        traceback.print_exc(file=crash)
        crash.flush()
        raise
