"""Project Hub dialogs and shared application styling."""
from __future__ import annotations

from datetime import date

from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QScrollArea, QTabWidget,
    QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from project_models import (
    DATABASE_PROVIDERS, DEPLOYMENT_ENVIRONMENTS, DNS_RECORD_TYPES, DOMAIN_PROVIDERS, PROJECT_TYPES,
    DnsConfig, DnsRecord, Project, get_path, set_path,
)
from project_services import (
    AccountSyncWorker, TaskWorker, detect_public_ip, fetch_hostinger_domains,
    fetch_hostinger_vps, fetch_namecheap_domains, remote_path, scp_download,
    seo_audit, ssh_exec, ssh_list, ssh_target,
)
from project_dns_sync import PROVIDER_LABELS, DnsSyncError, apply_push, build_plan, fetch_live, prepare_push, sync_target
from project_storage import DATA, VAULT, CredentialVault, VaultError, find_editor, load_settings, save_settings

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
QComboBox {
  background: #0f141b;
  color: #e6f1ff;
  border: 1px solid #1f2a37;
  border-radius: 6px;
  padding: 4px 8px;
}
QComboBox:focus { border: 1px solid #00e5ff; }
QComboBox QAbstractItemView { background: #0f141b; color: #e6f1ff; selection-background-color: #00e5ff; selection-color: #0a0e14; }
QGroupBox {
  border: 1px solid #1f2a37;
  border-radius: 6px;
  margin-top: 14px;
  padding-top: 8px;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #00e5ff; }
"""

TEXT, BOOL, DATE, SECRET, FOLDER, FILE, RECORDS = 'text', 'bool', 'date', 'secret', 'folder', 'file', 'records'

class Suggest(tuple):
    """Field kind: an editable combo box offering these values but accepting any text."""

def credential_fields(prefix):
    return [(f'{prefix}.host', 'Host'), (f'{prefix}.port', 'Port'), (f'{prefix}.username', 'Username'),
            (f'{prefix}.password', 'Password', SECRET), (f'{prefix}.credential_ref', 'Credential vault key')]

def renewal_fields(prefix, what):
    return [(f'{prefix}.expires_on', f'{what} renewal / expiry date (YYYY-MM-DD)', DATE),
            (f'{prefix}.cost', f'{what} renewal cost'),
            (f'{prefix}.auto_renew', f'{what} auto-renew is on', BOOL),
            (f'{prefix}.reminder.enabled', f'Remind me before {what.lower()} renewal', BOOL),
            (f'{prefix}.reminder.days_before', f'{what} reminder: days before')]

# (tab title, [(group title or None, (controlling path, values that show it) or None, [(path, label[, kind])])])
# A kind that is a tuple renders a combo box of those values. Fields in hidden groups/tabs keep their saved values.
PROJECT_FORM = [
 ('Overview', [(None, None, [
   ('name', 'Project name'), ('description', 'What it does'), ('source_location', 'Local folder', FOLDER),
   ('machine', 'Computer / location'), ('project_type', 'Project type', PROJECT_TYPES), ('status', 'Status'),
   ('date_created', 'Date created (YYYY-MM-DD)', DATE), ('notes', 'Notes')])]),
 ('Database', [
   (None, None, [('database.provider', 'Database provider', DATABASE_PROVIDERS)]),
   ('Connection', ('database.provider', set(DATABASE_PROVIDERS) - {'none'}),
    [('database.db_name', 'Database name'), *credential_fields('database.credentials')])]),
 ('Deployment', [
   (None, None, [('deployment.environment', 'Environment', DEPLOYMENT_ENVIRONMENTS),
                 ('deployment.stage', 'Stage (dev / staging / prod)'),
                 ('deployment.site_url', 'Website URL'), ('deployment.health_url', 'Health-check URL')]),
   ('Hosting / server', ('deployment.environment', {'hosted'}),
    [('deployment.provider_name', 'Hosting provider'), ('deployment.platform', 'Deployment platform'),
     ('deployment.runtime', 'Runtime / web stack'), ('deployment.service', 'Service / container name'),
     ('deployment.remote_path', 'Path on server'), *credential_fields('deployment.credentials'),
     ('deployment.ssh_key_path', 'SSH private key file', FILE)])]),
 ('Website: domain & hosting', [
   ('Domain', None, [('website.domain.name', 'Domain name'), ('website.domain.registrar', 'Domain registrar / purchased from', Suggest(DOMAIN_PROVIDERS)),
                     ('website.domain.registrar_url', 'Registrar site URL'),
                     ('website.domain.registered_on', 'Domain registered on (YYYY-MM-DD)', DATE),
                     *renewal_fields('website.domain', 'Domain')]),
   ('Hosting', None, [('website.hosting.provider', 'Hosting purchased from'), ('website.hosting.provider_url', 'Hosting provider site URL'),
                      ('website.hosting.plan', 'Hosting plan'), ('website.hosting.ip_address', 'Hosting IP address'),
                      ('website.hosting.purchased_on', 'Hosting purchased on (YYYY-MM-DD)', DATE),
                      *renewal_fields('website.hosting', 'Hosting')]),
   ('DNS', None, [('website.dns.provider', 'DNS provider (hosts the zone)', Suggest(DOMAIN_PROVIDERS)),
                  ('website.dns.provider_url', 'DNS provider site URL'),
                  ('website.dns.nameservers', 'Nameservers (comma separated)'),
                  ('website.dns.records', 'DNS records', RECORDS)])]),
 ('Cloud', [(None, None, [('cloud.provider', 'Cloud provider'), ('cloud.account', 'Account / tenant'),
                          ('cloud.project', 'Cloud project / subscription'), ('cloud.region', 'Region'),
                          ('cloud.service', 'Cloud service / resource'), ('cloud.credential_ref', 'Credential vault key (optional)')])]),
 ('Payments', [(None, None, [('payments.provider', 'Payment provider'), ('payments.mode', 'Mode (test / live)'),
                             ('payments.account', 'Merchant / account ID'), ('payments.webhook_url', 'Webhook URL'),
                             ('payments.credential_ref', 'Credential vault key (optional)')])]),
 ('AI', [(None, None, [('ai.provider', 'AI provider'), ('ai.model', 'Model'), ('ai.endpoint', 'API endpoint'),
                       ('ai.credential_ref', 'Credential vault key (optional)')])]),
 ('Files & paths', [(None, None, [('paths.config', 'Configuration file / directory'), ('paths.data', 'Data location'),
                                  ('paths.logs', 'Log location'), ('paths.artifacts', 'Build / artifact location')])]),
 ('Commands', [(None, None, [('commands.start', 'Start command'), ('commands.restart', 'Restart command'),
                             ('commands.stop', 'Stop command')])]),
]
WEBSITE_TAB = 'Website: domain & hosting'

class ProjectDialog(QDialog):
    """Edits a Project through the dotted model paths in PROJECT_FORM; Pydantic validates on save."""
    def __init__(self, parent, project=None, prefill=None):
        super().__init__(parent)
        self.setWindowTitle('Edit project' if project else 'Add project')
        self.resize(740, 710)
        self.setMinimumSize(600, 500)
        self.original = project
        self.result = None
        self.protected_scan_fields = set()
        self.inputs = {}
        self.labels = {}
        self.rules = []
        data = project.model_dump() if project else {}
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        for title, sections in PROJECT_FORM:
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            if title == WEBSITE_TAB:
                top_row = QHBoxLayout()
                fetch_btn = QPushButton('🌐 Autofill from Hostinger or Namecheap...')
                fetch_btn.clicked.connect(self.open_cloud_import)
                top_row.addWidget(fetch_btn)
                top_row.addStretch()
                tab_layout.addLayout(top_row)
            for group_title, rule, group in sections:
                container = QGroupBox(group_title) if group_title else QWidget()
                grid = QGridLayout(container)
                if not group_title:
                    grid.setContentsMargins(0, 0, 0, 0)
                grid.setColumnStretch(1, 1)
                for row, (path, label, *kind) in enumerate(group):
                    self.add_field(grid, row, path, label, kind[0] if kind else TEXT, data)
                tab_layout.addWidget(container)
                if rule:
                    self.rules.append((container, *rule, {f[0] for f in group}))
            tab_layout.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(tab)
            self.tabs.addTab(scroll, title)
        self.website_tab = [t for t, _ in PROJECT_FORM].index(WEBSITE_TAB)
        layout.addWidget(self.tabs)
        for path in {'project_type'} | {ctrl for _, ctrl, _, _ in self.rules}:
            self.inputs[path][0].currentTextChanged.connect(self.sync_visibility)
        for path in ('project_type', 'database.provider', 'deployment.environment'):
            self.inputs[path][0].activated.connect(
                lambda _index, field=path: self.protected_scan_fields.add(field))
        self.sync_visibility()
        if prefill:
            self.apply_import(prefill)
        note = QLabel('Commands are stored as plain text. Passwords are moved into the encrypted credential vault on save; other secrets should be linked by vault key, not typed into project fields. Remote commands are copy-only. Renewal reminders appear on the Home page and when Project Hub starts.')
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

    def add_field(self, grid, row, path, label, kind, data):
        value = get_path(data, path)
        self.labels[path] = label
        if kind == BOOL:
            ent = QCheckBox(label)
            ent.setChecked(bool(value))
            grid.addWidget(ent, row, 0, 1, 2)
            self.inputs[path] = (ent, kind)
            return
        if kind == RECORDS:
            ent = DnsRecordsEditor([DnsRecord.model_validate(r) for r in value or []])
            grid.addWidget(ent, row, 0, 1, 3)
            self.inputs[path] = (ent, kind)
            return
        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignTop)
        grid.addWidget(lbl, row, 0)
        if isinstance(kind, Suggest):
            ent = QComboBox()
            ent.setEditable(True)
            ent.addItems(kind)
            ent.setCurrentText(value or '')
        elif isinstance(kind, tuple):
            ent = QComboBox()
            ent.addItems(kind)
            ent.setCurrentText(value or ('none' if 'none' in kind else kind[0]))
        else:
            ent = QLineEdit(', '.join(value) if isinstance(value, list) else '' if value is None else str(value))
            if kind == DATE:
                ent.setPlaceholderText('YYYY-MM-DD')
            elif kind == SECRET:
                ent.setEchoMode(QLineEdit.Password)
                stored = get_path(data, path.rsplit('.', 1)[0] + '.credential_ref')
                ent.setPlaceholderText(f'Stored in vault as "{stored}" - leave blank to keep' if stored else 'Saved to the encrypted vault, not the project file')
        grid.addWidget(ent, row, 1)
        self.inputs[path] = (ent, kind)
        if kind in (FOLDER, FILE):
            btn = QPushButton('Browse...')
            btn.clicked.connect(lambda checked=False, e=ent, k=kind: self.choose(e, k))
            grid.addWidget(btn, row, 2)

    def value(self, path):
        ent, kind = self.inputs[path]
        if kind == BOOL:
            return ent.isChecked()
        if kind == RECORDS:
            return [r.model_dump() for r in ent.records]
        if isinstance(kind, tuple):
            return ent.currentText().strip()
        return ent.text().strip()

    def set_value(self, path, value):
        ent, kind = self.inputs[path]
        if kind == BOOL:
            ent.setChecked(bool(value))
        elif kind == RECORDS:
            ent.set_records([DnsRecord.model_validate(r) for r in value])
        elif isinstance(kind, tuple):
            ent.setCurrentText(str(value))
        else:
            ent.setText('' if value is None else str(value))

    def is_hidden(self, path):
        if path.startswith('website.') and self.value('project_type') != 'website':
            return True
        return any(path in paths and self.value(ctrl) not in values for _, ctrl, values, paths in self.rules)

    def sync_visibility(self):
        for container, ctrl, values, _ in self.rules:
            container.setVisible(self.value(ctrl) in values)
        self.tabs.setTabVisible(self.website_tab, self.value('project_type') == 'website')

    def apply_import(self, data):
        for path, value in data.items():
            if path in self.inputs and path != 'name':
                self.set_value(path, value)
        if not self.value('name') and data.get('name'):
            self.set_value('name', data['name'])

    def open_cloud_import(self):
        dlg = CloudImportDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.selected_data:
            self.apply_import(dlg.selected_data)
            QMessageBox.information(self, 'Data imported', 'Project fields were updated from your selected account resource.')

    def choose(self, entry, kind):
        if kind == FILE:
            f, _ = QFileDialog.getOpenFileName(self, 'Choose private key')
            if f:
                entry.setText(f)
        else:
            folder = QFileDialog.getExistingDirectory(self, 'Choose folder')
            if folder:
                entry.setText(folder)

    def submit(self):
        # Start from the saved project so hidden fields, scan results and the id carry over unchanged.
        data = self.original.model_dump() if self.original else {}
        for path, (_, kind) in self.inputs.items():
            if self.is_hidden(path):
                continue
            value = self.value(path)
            if kind == SECRET and not value:
                continue
            set_path(data, path, value)
        try:
            self.result = Project.model_validate(data)
        except ValidationError as exc:
            QMessageBox.warning(self, 'Check project details', self.describe(exc))
            return
        self.accept()

    def describe(self, exc):
        return validation_text(exc, self.labels)


def validation_text(exc, labels=None):
    """Turn a Pydantic ValidationError into one readable line per problem."""
    lines = []
    for err in exc.errors():
        path = '.'.join(str(part) for part in err['loc'])
        label = (labels or {}).get(path) or path.replace('.', ' > ').replace('_', ' ').capitalize()
        message = err['msg'].removeprefix('Value error, ')
        lines.append(f'{label}: {message}' if label else message)
    return '\n'.join(lines)


DNS_HINTS = {
    'A': 'IPv4 address, e.g. 203.0.113.10',
    'AAAA': 'IPv6 address, e.g. 2001:db8::1',
    'CNAME': 'Target hostname, e.g. myapp.netlify.app',
    'MX': 'Mail server hostname, e.g. mx1.privateemail.com',
    'TXT': 'Text, e.g. v=spf1 include:_spf.google.com ~all',
    'NS': 'Nameserver hostname, e.g. ns1.example-dns.com',
    'SRV': 'weight port target, e.g. 5 5060 sip.example.com',
    'CAA': 'flags tag value, e.g. 0 issue "letsencrypt.org"',
}


class DnsRecordDialog(QDialog):
    """Add or edit one DNS record; DnsRecord validates the value for the chosen type."""
    def __init__(self, parent, record=None):
        super().__init__(parent)
        self.setWindowTitle('Edit DNS record' if record else 'Add DNS record')
        self.resize(520, 300)
        self.result = None
        layout = QVBoxLayout(self)
        form = QGridLayout()
        form.setColumnStretch(1, 1)
        self.type = QComboBox()
        self.type.addItems(DNS_RECORD_TYPES)
        self.name = QLineEdit(record.name if record else '@')
        self.name.setPlaceholderText('@ for the domain itself, or www, mail, *')
        self.value = QLineEdit(record.value if record else '')
        self.ttl = QLineEdit(str(record.ttl) if record else '3600')
        self.priority = QLineEdit('' if not record or record.priority is None else str(record.priority))
        self.notes = QLineEdit(record.notes if record else '')
        self.priority_label = QLabel('Priority')
        for row, (label, widget) in enumerate([(QLabel('Type'), self.type), (QLabel('Name / host'), self.name),
                                               (QLabel('Value / points to'), self.value), (QLabel('TTL (seconds)'), self.ttl),
                                               (self.priority_label, self.priority), (QLabel('Notes'), self.notes)]):
            form.addWidget(label, row, 0)
            form.addWidget(widget, row, 1)
        layout.addLayout(form)
        self.type.currentTextChanged.connect(self.sync_type)
        self.type.setCurrentText(record.type if record else 'A')
        self.sync_type()
        layout.addStretch()
        btns = QHBoxLayout()
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        save = QPushButton('Save record')
        save.clicked.connect(self.submit)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(save)
        layout.addLayout(btns)
        self.setStyleSheet(TECH_STYLE)

    def sync_type(self):
        record_type = self.type.currentText()
        self.value.setPlaceholderText(DNS_HINTS.get(record_type, ''))
        uses_priority = record_type in ('MX', 'SRV')
        self.priority.setVisible(uses_priority)
        self.priority_label.setVisible(uses_priority)
        if uses_priority and not self.priority.text():
            self.priority.setText('10')

    def submit(self):
        try:
            self.result = DnsRecord.model_validate({
                'type': self.type.currentText(), 'name': self.name.text().strip(), 'value': self.value.text().strip(),
                'ttl': self.ttl.text().strip(), 'priority': self.priority.text().strip(), 'notes': self.notes.text().strip(),
            })
        except ValidationError as exc:
            QMessageBox.warning(self, 'Check DNS record', validation_text(exc, {
                'type': 'Type', 'name': 'Name / host', 'value': 'Value', 'ttl': 'TTL', 'priority': 'Priority', '': ''}))
            return
        self.accept()


class DnsRecordsEditor(QWidget):
    """Table of DNS records with add / edit / remove. Rows map to self.records by index."""
    def __init__(self, records=None, parent=None):
        super().__init__(parent)
        self.records = list(records or [])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Type', 'Name', 'Value', 'TTL', 'Priority'])
        self.tree.setColumnWidth(0, 70)
        self.tree.setColumnWidth(1, 120)
        self.tree.setColumnWidth(2, 260)
        self.tree.setRootIsDecorated(False)
        self.tree.setMinimumHeight(160)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit())
        layout.addWidget(self.tree)
        btns = QHBoxLayout()
        for text, cb in [('Add record', self.add), ('Edit selected', self.edit)]:
            b = QPushButton(text)
            b.clicked.connect(cb)
            btns.addWidget(b)
        remove = QPushButton('Remove selected')
        remove.setObjectName('danger')
        remove.clicked.connect(self.remove)
        btns.addWidget(remove)
        btns.addStretch()
        layout.addLayout(btns)
        self.refresh()

    def set_records(self, records):
        self.records = list(records)
        self.refresh()

    def refresh(self):
        self.tree.clear()
        order = sorted(range(len(self.records)), key=lambda i: (self.records[i].name != '@', self.records[i].name, DNS_RECORD_TYPES.index(self.records[i].type)))
        for index in order:
            r = self.records[index]
            item = QTreeWidgetItem([r.type, r.name, r.value, str(r.ttl), '' if r.priority is None else str(r.priority)])
            item.setToolTip(2, r.notes or r.value)
            item.setData(0, Qt.UserRole, index)
            self.tree.addTopLevelItem(item)

    def selected_index(self):
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.UserRole) if items else None

    def commit(self, records):
        # Zone-level rules (duplicates, CNAME conflicts) live on DnsConfig.
        try:
            DnsConfig(records=records)
        except ValidationError as exc:
            QMessageBox.warning(self, 'DNS conflict', validation_text(exc, {'': ''}))
            return False
        self.set_records(records)
        return True

    def add(self):
        dlg = DnsRecordDialog(self)
        while dlg.exec() == QDialog.Accepted:
            if self.commit(self.records + [dlg.result]):
                return

    def edit(self):
        index = self.selected_index()
        if index is None:
            QMessageBox.information(self, 'Select a record', 'Select a DNS record to edit.')
            return
        dlg = DnsRecordDialog(self, self.records[index])
        while dlg.exec() == QDialog.Accepted:
            updated = list(self.records)
            updated[index] = dlg.result
            if self.commit(updated):
                return

    def remove(self):
        index = self.selected_index()
        if index is None:
            QMessageBox.information(self, 'Select a record', 'Select a DNS record to remove.')
            return
        r = self.records[index]
        if QMessageBox.question(self, 'Remove DNS record', f'Remove this record from the project?\n\n{r.type}  {r.name}  {r.value}') == QMessageBox.Yes:
            self.set_records(self.records[:index] + self.records[index + 1:])


class DnsPushPreviewDialog(QDialog):
    """Shows exactly what a push will change on the live zone and asks for confirmation."""
    def __init__(self, parent, plan):
        super().__init__(parent)
        provider = PROVIDER_LABELS[plan.zone.provider]
        self.setWindowTitle(f'Push DNS changes to {provider}')
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        head = QLabel(f'These changes will be made to the LIVE DNS for {plan.zone.domain} at {provider}:')
        head.setStyleSheet('color: #ffb347; font-weight: bold;')
        head.setWordWrap(True)
        layout.addWidget(head)
        fmt = lambda r: f'{r.type:<6} {r.name:<18} {r.value}  (TTL {r.ttl}{"" if r.priority is None else f", priority {r.priority}"})'
        lines = [f'+ ADD     {fmt(r)}' for r in plan.added]
        lines += [f'- REMOVE  {fmt(r)}' for r in plan.removed]
        lines += [f'~ CHANGE  {fmt(new)}   was TTL {old.ttl}{"" if old.priority is None else f", priority {old.priority}"}'
                  for old, new in plan.changed]
        if plan.warnings:
            lines += ['', 'Notes:'] + [f'  - {w}' for w in plan.warnings]
        lines += ['', 'The current live records are backed up to a file before anything is written.',
                  'DNS changes can take up to the old TTL to be seen everywhere.']
        text = QTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont('Consolas', 10))
        text.setPlainText('\n'.join(lines))
        layout.addWidget(text, 1)
        btns = QHBoxLayout()
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        cancel.setDefault(True)
        push = QPushButton(f'Push {plan.count} change(s) live')
        push.setObjectName('danger')
        push.clicked.connect(self.accept)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(push)
        layout.addLayout(btns)
        self.setStyleSheet(TECH_STYLE)


class DnsRecordsDialog(QDialog):
    """Manage a website project's DNS records, and fetch / push them from / to Hostinger or Namecheap.

    save(records, sync) persists to the inventory; it is called after a fetch or push so the sync
    fingerprint is never lost, and by "Save records" for purely local edits.
    """
    def __init__(self, parent, project, save, vault=None):
        super().__init__(parent)
        self.setWindowTitle(f'DNS records: {project.name}')
        self.resize(800, 520)
        self.project = project
        self.save = save
        self.vault = vault or CredentialVault()
        self.worker = None
        dns = project.website.dns if project.website else None
        self.sync = dns.sync if dns else None
        domain = project.website.domain.name if project.website and project.website.domain else ''
        layout = QVBoxLayout(self)
        info = QLabel(f'Domain: {domain or "-"}    DNS provider: {(dns.provider if dns else "") or "-"}    '
                      f'Nameservers: {", ".join(dns.nameservers) if dns and dns.nameservers else "-"}')
        info.setStyleSheet('color: #7a8a9a; font-size: 11px;')
        info.setWordWrap(True)
        layout.addWidget(info)
        sync_row = QHBoxLayout()
        self.fetch_btn = QPushButton('⇩ Fetch from provider')
        self.fetch_btn.clicked.connect(self.fetch)
        self.push_btn = QPushButton('⇧ Push to provider...')
        self.push_btn.setObjectName('danger')
        self.push_btn.clicked.connect(self.push)
        sync_row.addWidget(self.fetch_btn)
        sync_row.addWidget(self.push_btn)
        self.sync_label = QLabel()
        self.sync_label.setWordWrap(True)
        sync_row.addWidget(self.sync_label, 1)
        layout.addLayout(sync_row)
        self.editor = DnsRecordsEditor(dns.records if dns else [])
        layout.addWidget(self.editor, 1)
        note = QLabel('Edits stay local until you push. A push first re-reads the live zone and is refused if it changed '
                      'since your last fetch; you see every change before it is sent.')
        note.setStyleSheet('color: #7a8a9a; font-size: 11px;')
        note.setWordWrap(True)
        layout.addWidget(note)
        btns = QHBoxLayout()
        cancel = QPushButton('Close')
        cancel.clicked.connect(self.reject)
        save_btn = QPushButton('Save records')
        save_btn.clicked.connect(self.submit)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(save_btn)
        layout.addLayout(btns)
        self.show_sync_state()
        self.setStyleSheet(TECH_STYLE)

    def show_sync_state(self, message='', color='#7a8a9a'):
        if not message:
            if self.sync and self.sync.fetched_at:
                message = f'Last fetched from {PROVIDER_LABELS[self.sync.provider]} at {self.sync.fetched_at:%Y-%m-%d %H:%M}.'
                if self.sync.unsupported:
                    message += f' {len(self.sync.unsupported)} record(s) not editable here are kept as-is.'
            else:
                message = 'Not fetched yet. Fetch the live records before pushing.'
        self.sync_label.setText(message)
        self.sync_label.setStyleSheet(f'color: {color}; font-size: 11px;')

    def credentials(self):
        try:
            return self.vault.load()
        except Exception as exc:
            raise DnsSyncError(f'Could not read connected account credentials: {exc}') from exc

    def run(self, message, fn, *args, done):
        self.fetch_btn.setEnabled(False)
        self.push_btn.setEnabled(False)
        self.show_sync_state(message, '#00e5ff')
        def finished(result, error):
            self.fetch_btn.setEnabled(True)
            self.push_btn.setEnabled(True)
            if error:
                self.show_sync_state()
                QMessageBox.critical(self, 'DNS sync', error)
            else:
                done(result)
        self.worker = TaskWorker(fn, *args)
        self.worker.finished.connect(finished)
        self.worker.start()

    def target(self):
        try:
            return sync_target(self.project), self.credentials()
        except DnsSyncError as exc:
            QMessageBox.warning(self, 'DNS sync', str(exc))
            return None, None

    def fetch(self):
        (target, creds) = self.target()
        if not target:
            return
        provider, domain = target
        self.run(f'Fetching live records from {PROVIDER_LABELS[provider]}...', fetch_live, provider, domain, creds,
                 done=self.on_fetched)

    def on_fetched(self, zone):
        local = self.editor.records
        if local and not build_plan(zone, local).empty:
            answer = QMessageBox.question(self, 'Replace local records?',
                f'{PROVIDER_LABELS[zone.provider]} has {len(zone.records)} editable record(s) that differ from the '
                f'{len(local)} record(s) here.\n\nReplace the local list with the live records? Unsaved local edits will be lost.')
            if answer != QMessageBox.Yes:
                self.show_sync_state()
                return
        self.editor.set_records(zone.records)
        self.sync = zone.sync_state()
        self.save(list(self.editor.records), self.sync)
        self.show_sync_state(f'Fetched {len(zone.records)} record(s) from {PROVIDER_LABELS[zone.provider]}'
                             + (f'; {len(zone.passthrough)} not editable here are kept as-is.' if zone.passthrough else '.'),
                             '#00ff88')

    def push(self):
        (target, creds) = self.target()
        if not target:
            return
        provider, domain = target
        local = list(self.editor.records)
        self.run('Re-reading the live zone to check for changes...', prepare_push, provider, domain, self.sync, local, creds,
                 done=lambda plan: self.confirm_push(plan, creds))

    def confirm_push(self, plan, creds):
        if plan.empty:
            self.show_sync_state()
            QMessageBox.information(self, 'Nothing to push', 'The live records already match the records here.')
            return
        if DnsPushPreviewDialog(self, plan).exec() != QDialog.Accepted:
            self.show_sync_state()
            return
        self.run(f'Pushing {plan.count} change(s) to {PROVIDER_LABELS[plan.zone.provider]}...', apply_push, plan, creds,
                 done=lambda result: self.on_pushed(plan, *result))

    def on_pushed(self, plan, zone, backup):
        leftover = build_plan(zone, plan.local)
        self.editor.set_records(zone.records)
        self.sync = zone.sync_state()
        self.save(list(self.editor.records), self.sync)
        self.show_sync_state(f'Pushed {plan.count} change(s) to {PROVIDER_LABELS[zone.provider]}.', '#00ff88')
        message = f'The live DNS was updated and re-read.\n\nBackup of the previous records:\n{backup}'
        if not leftover.empty:
            message += (f'\n\nNote: {leftover.count} record(s) read back differently than sent (the provider may have '
                        'normalized them). The list now shows exactly what is live.')
        QMessageBox.information(self, 'Push complete', message)

    def submit(self):
        self.save(list(self.editor.records), self.sync)
        self.accept()

    def done(self, result):
        # Never destroy the dialog while a provider request is still running (or mid-push).
        if self.worker and self.worker.isRunning():
            self.worker.wait()
        super().done(result)

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
        info = QLabel(f'{ssh_target(project)}  ->  {remote_path(project) or "."}')
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
        self.resize(460, 340)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Project Hub settings'))
        layout.addWidget(QLabel('Editor for "Open in editor" (blank = VS Code, or Notepad if VS Code is not installed):'))
        row = QHBoxLayout()
        self.editor_edit = QLineEdit(load_settings().get('editor', ''))
        self.editor_edit.setPlaceholderText('code')
        row.addWidget(self.editor_edit, 1)
        browse = QPushButton('Browse...')
        browse.clicked.connect(self.browse_editor)
        row.addWidget(browse)
        layout.addLayout(row)
        self.editor_hint = QLabel()
        self.editor_hint.setStyleSheet('color: #7a8a9a; font-size: 11px;')
        self.editor_edit.textChanged.connect(self.update_editor_hint)
        self.update_editor_hint()
        layout.addWidget(self.editor_hint)
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

    def browse_editor(self):
        f, _ = QFileDialog.getOpenFileName(self, 'Choose editor program', '', 'Programs (*.exe *.cmd *.bat);;All files (*)')
        if f:
            self.editor_edit.setText(f)

    def update_editor_hint(self):
        self.editor_hint.setText(f'Will use: {find_editor(self.editor_edit.text())}')

    def accept(self):
        settings = load_settings()
        settings['editor'] = self.editor_edit.text().strip()
        try:
            save_settings(settings)
        except OSError as exc:
            QMessageBox.critical(self, 'Save failed', str(exc))
            return
        super().accept()


class ConnectedAccountsDialog(QDialog):
    def __init__(self, parent=None, vault=None):
        super().__init__(parent)
        self.setWindowTitle('Connected accounts (Hostinger & Namecheap)')
        self.resize(700, 560)
        self.vault = vault or CredentialVault()
        self.load_error = None
        try:
            self.credentials = self.vault.load()
        except (OSError, VaultError) as exc:
            self.credentials = {}
            self.load_error = exc

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
        if self.load_error:
            warn = QLabel(f'The credential vault could not be read. Saving here is blocked so the secrets already in it are not overwritten:\n{self.load_error}')
            warn.setStyleSheet('color: #ff4d6d; font-size: 11px;')
            warn.setWordWrap(True)
            layout.addWidget(warn)

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
        # Re-read the vault and merge, so other secrets (database passwords, other vault keys) are kept.
        # If it can't be read, refuse: saving a fresh vault would silently delete everything else in it.
        try:
            creds = self.vault.load()
        except (OSError, VaultError) as exc:
            QMessageBox.critical(self, 'Credential vault error',
                                 f'Could not read the credential vault, so nothing was saved (this protects the other secrets stored in it):\n{exc}')
            return
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

        # Keys are dotted Project model paths, applied by ProjectDialog.apply_import().
        if item_type == 'domain':
            self.selected_data = {
                'name': d.get('domain', ''),
                'project_type': 'website',
                'website.domain.name': d.get('domain', ''),
                'website.domain.registrar': d.get('provider', ''),
                'website.domain.registrar_url': d.get('provider_url', ''),
                'website.domain.registered_on': d.get('registered_on', ''),
                'website.domain.expires_on': d.get('expires_on', ''),
                'website.domain.auto_renew': bool(d.get('auto_renew', False)),
                'website.domain.reminder.enabled': True,
                'deployment.site_url': f"https://{d.get('domain', '')}",
            }
        else:
            self.selected_data = {
                'name': d.get('name') or d.get('ip_address', ''),
                'project_type': 'website',
                'website.hosting.provider': 'Hostinger',
                'website.hosting.provider_url': 'https://hpanel.hostinger.com',
                'website.hosting.plan': d.get('plan', ''),
                'website.hosting.ip_address': d.get('ip_address', ''),
                'deployment.environment': 'hosted',
                'deployment.provider_name': 'Hostinger',
                'deployment.credentials.host': d.get('ip_address', ''),
                'deployment.runtime': d.get('os', ''),
            }
        self.accept()
