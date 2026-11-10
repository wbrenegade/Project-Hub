"""Git panel for a project folder: status, staging, commits, sync, branches, history, stash and raw commands."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from project_dialogs import TECH_STYLE

TIMEOUT = 120
SEP = '\x1f'


def run_git(cwd, args, timeout=TIMEOUT):
    """Run git and return (returncode, stdout, stderr). Never prompts for credentials on the console."""
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C.UTF-8'}
    try:
        p = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, encoding='utf8', errors='replace',
                           timeout=timeout, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, '', 'git was not found. Install Git for Windows and make sure it is on PATH.'
    except subprocess.TimeoutExpired:
        return 124, '', f'git {" ".join(args)} timed out after {timeout}s.'
    except OSError as exc:
        return 126, '', str(exc)


def repo_root(path):
    """Top-level folder of the repository containing `path`, or None."""
    folder = Path(path)
    if folder.is_file():
        folder = folder.parent
    if not folder.is_dir():
        return None
    rc, out, _ = run_git(str(folder), ['rev-parse', '--show-toplevel'], 15)
    return out.strip() if rc == 0 and out.strip() else None


class GitWorker(QThread):
    done = Signal(int, str, str)

    def __init__(self, cwd, args):
        super().__init__()
        self.cwd = cwd
        self.args = args

    def run(self):
        self.done.emit(*run_git(self.cwd, self.args))


def parse_status(text):
    """Entries of `git status --porcelain=v1 -b -z` as (branch line, [(XY, path)])."""
    parts = text.split('\0')
    branch, entries, i = '', [], 0
    while i < len(parts):
        part = parts[i]
        i += 1
        if not part:
            continue
        if part.startswith('## '):
            branch = part[3:]
            continue
        xy, path = part[:2], part[3:]
        if 'R' in xy or 'C' in xy:
            i += 1  # the original name follows a rename/copy entry
        entries.append((xy, path))
    return branch, entries


class GitDialog(QDialog):
    def __init__(self, parent, folder):
        super().__init__(parent)
        self.setWindowTitle('Git')
        self.resize(980, 700)
        self.folder = str(folder if Path(folder).is_dir() else Path(folder).parent)
        self.root = repo_root(self.folder)
        self.cwd = self.root or self.folder
        self.workers = []
        self.busy = 0
        self.has_upstream = False
        self.entries = []
        self.commits = []

        layout = QVBoxLayout(self)
        self.header = QLabel()
        self.header.setStyleSheet('font-weight: bold; color: #00e5ff;')
        layout.addWidget(self.header)

        self.action_row = QHBoxLayout()
        self.repo_buttons = []
        for text, cb in [('Refresh', self.refresh), ('Fetch', self.fetch), ('Pull', self.pull), ('Push', self.push),
                         ('Stash', self.stash), ('Stash pop', self.stash_pop)]:
            btn = QPushButton(text)
            btn.clicked.connect(cb)
            self.action_row.addWidget(btn)
            self.repo_buttons.append(btn)
        self.action_row.addStretch()
        self.init_btn = QPushButton('Init repository')
        self.init_btn.clicked.connect(self.init_repo)
        self.clone_btn = QPushButton('Clone into folder...')
        self.clone_btn.clicked.connect(self.clone_repo)
        self.action_row.addWidget(self.init_btn)
        self.action_row.addWidget(self.clone_btn)
        layout.addLayout(self.action_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.build_changes(), 'Changes')
        self.tabs.addTab(self.build_history(), 'History')
        self.tabs.addTab(self.build_branches(), 'Branches && remotes')
        self.tabs.addTab(self.build_console(), 'Console')
        layout.addWidget(self.tabs, 1)

        self.status = QLabel('')
        self.status.setStyleSheet('color: #7a8a9a; font-size: 11px;')
        layout.addWidget(self.status)
        self.setStyleSheet(TECH_STYLE)
        self.refresh()

    # ---- layout ----
    def button_row(self, items):
        row = QHBoxLayout()
        for text, cb in items:
            btn = QPushButton(text)
            btn.clicked.connect(cb)
            row.addWidget(btn)
            self.repo_buttons.append(btn)
        row.addStretch()
        return row

    def mono(self):
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(QFont('Consolas', 9))
        view.setLineWrapMode(QPlainTextEdit.NoWrap)
        return view

    def build_changes(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.files = QListWidget()
        self.files.setSelectionMode(QListWidget.ExtendedSelection)
        self.files.itemSelectionChanged.connect(self.show_diff)
        left_layout.addWidget(self.files, 1)
        left_layout.addLayout(self.button_row([('Stage', self.stage), ('Unstage', self.unstage)]))
        left_layout.addLayout(self.button_row([('Stage all', self.stage_all), ('Discard...', self.discard)]))
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText('Commit message')
        self.message.setMaximumHeight(80)
        left_layout.addWidget(self.message)
        self.amend = QCheckBox('Amend last commit')
        left_layout.addWidget(self.amend)
        left_layout.addLayout(self.button_row([('Commit', self.commit), ('Commit && push', lambda: self.commit(True))]))
        split.addWidget(left)
        self.diff = self.mono()
        split.addWidget(self.diff)
        split.setSizes([360, 600])
        layout.addWidget(split)
        return page

    def build_history(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.log = QListWidget()
        self.log.itemSelectionChanged.connect(self.show_commit)
        left_layout.addWidget(self.log, 1)
        left_layout.addLayout(self.button_row([('Checkout', lambda: self.on_commit('checkout')),
                                               ('Branch here...', lambda: self.on_commit('branch')),
                                               ('Tag here...', lambda: self.on_commit('tag'))]))
        left_layout.addLayout(self.button_row([('Cherry-pick', lambda: self.on_commit('cherry-pick')),
                                               ('Revert', lambda: self.on_commit('revert')),
                                               ('Reset to here...', lambda: self.on_commit('reset'))]))
        split.addWidget(left)
        self.commit_view = self.mono()
        split.addWidget(self.commit_view)
        split.setSizes([420, 540])
        layout.addWidget(split)
        return page

    def build_branches(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.branches = QListWidget()
        layout.addWidget(self.branches, 1)
        layout.addLayout(self.button_row([('Checkout', self.checkout_branch), ('New branch...', self.new_branch),
                                          ('Merge into current', self.merge_branch), ('Rebase current onto', self.rebase_branch)]))
        layout.addLayout(self.button_row([('Rename...', self.rename_branch), ('Delete...', self.delete_branch)]))
        layout.addWidget(QLabel('Remotes'))
        self.remotes = self.mono()
        self.remotes.setMaximumHeight(110)
        layout.addWidget(self.remotes)
        layout.addLayout(self.button_row([('Add remote...', self.add_remote), ('Remove remote...', self.remove_remote),
                                          ('Set upstream & push', self.push_upstream)]))
        return page

    def build_console(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.console = self.mono()
        layout.addWidget(self.console, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel('git'))
        self.command = QLineEdit()
        self.command.setPlaceholderText('any other git command, e.g. tag -l  or  remote prune origin')
        self.command.returnPressed.connect(self.run_custom)
        row.addWidget(self.command, 1)
        run = QPushButton('Run')
        run.clicked.connect(self.run_custom)
        row.addWidget(run)
        layout.addLayout(row)
        return page

    # ---- running git ----
    def git(self, args, then=None, quiet=False, report=True):
        """Run git off the UI thread. `then(rc, out, err)` runs when it finishes; failures are shown unless quiet."""
        worker = GitWorker(self.cwd, args)
        self.workers.append(worker)
        self.busy += 1
        self.status.setText('Running: git ' + ' '.join(args))
        worker.done.connect(lambda rc, out, err: self.on_finished(worker, args, rc, out, err, then, quiet, report))
        worker.start()

    def on_finished(self, worker, args, rc, out, err, then, quiet, report):
        self.workers.remove(worker)
        self.busy -= 1
        if not quiet or rc != 0:
            self.console.appendPlainText(f'$ git {" ".join(args)}')
            if out.strip():
                self.console.appendPlainText(out.rstrip())
            if err.strip():
                self.console.appendPlainText(err.rstrip())
            self.console.appendPlainText(f'(exit {rc})\n')
        if not self.busy:
            self.status.setText('')
        if rc != 0 and report:
            QMessageBox.warning(self, 'Git', (err or out).strip() or f'git {" ".join(args)} failed (exit {rc}).')
        elif not quiet and report:
            self.status.setText(f'Done: git {" ".join(args)}')
        if then:
            then(rc, out, err)

    def mutate(self, args, then=None):
        """A command that changes the repository: refresh everything afterwards."""
        def done(rc, out, err):
            self.refresh()
            if then and rc == 0:
                then(rc, out, err)
        self.git(args, done)

    def confirm(self, title, text):
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def ask(self, title, label, text=''):
        value, ok = QInputDialog.getText(self, title, label, text=text)
        return value.strip() if ok and value.strip() else None

    # ---- state ----
    def refresh(self, *_):
        self.root = repo_root(self.folder)
        is_repo = bool(self.root)
        if is_repo:
            self.cwd = self.root
        for btn in self.repo_buttons:
            btn.setEnabled(is_repo)
        self.init_btn.setVisible(not is_repo)
        self.clone_btn.setVisible(not is_repo)
        self.tabs.setTabEnabled(0, is_repo)
        self.tabs.setTabEnabled(1, is_repo)
        self.tabs.setTabEnabled(2, is_repo)
        if not is_repo:
            self.header.setText(f'{self.folder} is not a Git repository.')
            self.tabs.setCurrentIndex(3)
            return
        self.header.setText(self.cwd)
        self.git(['status', '--porcelain=v1', '-b', '-z', '-uall'], self.on_status, quiet=True, report=False)
        self.git(['log', '-n', '300', f'--format=%h{SEP}%an{SEP}%ar{SEP}%s{SEP}%D'], self.on_log, quiet=True, report=False)
        self.git(['branch', '-a', f'--format=%(HEAD){SEP}%(refname){SEP}%(refname:short)'], self.on_branches, quiet=True, report=False)
        self.git(['remote', '-v'], lambda rc, out, err: self.remotes.setPlainText(out.strip() or 'No remotes configured.'),
                 quiet=True, report=False)

    def on_status(self, rc, out, err):
        if rc != 0:
            return
        branch, self.entries = parse_status(out)
        self.has_upstream = '...' in branch
        self.header.setText(f'{self.cwd}    [{branch}]')
        self.files.clear()
        for xy, path in self.entries:
            item = QListWidgetItem(f'{xy}  {path}')
            item.setData(Qt.UserRole, (xy, path))
            self.files.addItem(item)
        if not self.entries:
            self.diff.setPlainText('Working tree clean.')

    def on_log(self, rc, out, err):
        self.log.clear()
        self.commits = []
        if rc != 0:
            self.log.addItem('No commits yet.')
            return
        for line in out.splitlines():
            h, author, when, subject, refs = (line.split(SEP) + [''] * 5)[:5]
            item = QListWidgetItem(f'{h}  {subject}   — {author}, {when}' + (f'   [{refs}]' if refs else ''))
            item.setData(Qt.UserRole, h)
            self.log.addItem(item)

    def on_branches(self, rc, out, err):
        self.branches.clear()
        for line in out.splitlines():
            head, ref, short = (line.split(SEP) + ['', ''])[:3]
            if ref.endswith('/HEAD') or not short:
                continue
            remote = ref.startswith('refs/remotes/')
            item = QListWidgetItem(('* ' if head == '*' else '  ') + short + ('   (remote)' if remote else ''))
            item.setData(Qt.UserRole, (short, remote, head == '*'))
            self.branches.addItem(item)

    # ---- changes ----
    def selected_entries(self):
        return [i.data(Qt.UserRole) for i in self.files.selectedItems()]

    def show_diff(self):
        sel = self.selected_entries()
        if len(sel) != 1:
            return
        xy, path = sel[0]
        if xy == '??':
            try:
                text = (Path(self.cwd) / path).read_text(encoding='utf8', errors='replace')[:20000]
            except OSError as exc:
                text = str(exc)
            self.diff.setPlainText(f'Untracked file {path}\n\n{text}')
            return
        self.git(['diff', '--no-color', 'HEAD', '--', path], lambda rc, out, err: self.diff.setPlainText(
            out if rc == 0 and out else self.staged_diff_fallback(path)), quiet=True, report=False)

    def staged_diff_fallback(self, path):
        # No HEAD yet (first commit) or no textual change: show the staged diff instead.
        rc, out, _ = run_git(self.cwd, ['diff', '--no-color', '--cached', '--', path], 15)
        return out if rc == 0 and out else '(no textual changes)'

    def stage(self):
        paths = [p for _, p in self.selected_entries()]
        if paths:
            self.mutate(['add', '--', *paths])

    def unstage(self):
        paths = [p for xy, p in self.selected_entries() if xy[0] not in ' ?']
        if paths:
            self.mutate(['reset', '-q', '--', *paths])

    def stage_all(self):
        self.mutate(['add', '-A'])

    def discard(self):
        sel = self.selected_entries()
        if not sel or not self.confirm('Discard changes', f'Permanently discard changes to {len(sel)} file(s)? This cannot be undone.'):
            return
        tracked = [p for xy, p in sel if xy != '??']
        untracked = [p for xy, p in sel if xy == '??']
        if untracked:
            self.git(['clean', '-f', '--', *untracked], report=True)
        if tracked:
            self.mutate(['restore', '--source=HEAD', '--staged', '--worktree', '--', *tracked])
        else:
            self.refresh()

    def commit(self, push_after=False):
        message = self.message.toPlainText().strip()
        amend = self.amend.isChecked()
        if not message and not amend:
            QMessageBox.information(self, 'Commit', 'Enter a commit message first.')
            return
        args = ['commit', '--amend'] if amend else ['commit']
        args += ['-m', message] if message else ['--no-edit']

        def done(rc, out, err):
            self.message.clear()
            self.amend.setChecked(False)
            if push_after:
                self.push()
        self.mutate(args, done)

    # ---- sync ----
    def fetch(self):
        self.mutate(['fetch', '--all', '--prune'])

    def pull(self):
        self.mutate(['pull'])

    def push(self):
        if self.has_upstream:
            self.mutate(['push'])
        else:
            self.push_upstream()

    def push_upstream(self):
        self.git(['rev-parse', '--abbrev-ref', 'HEAD'], lambda rc, out, err: rc == 0 and self.mutate(
            ['push', '-u', 'origin', out.strip()]), quiet=True)

    def stash(self):
        message = self.ask('Stash', 'Stash message (optional):') or ''
        self.mutate(['stash', 'push', '-u', *(['-m', message] if message else [])])

    def stash_pop(self):
        self.mutate(['stash', 'pop'])

    # ---- history ----
    def current_commit(self):
        item = self.log.currentItem()
        return item.data(Qt.UserRole) if item else None

    def show_commit(self):
        h = self.current_commit()
        if h:
            self.git(['show', '--no-color', '--stat', '--patch', h], lambda rc, out, err: self.commit_view.setPlainText(out or err),
                     quiet=True, report=False)

    def on_commit(self, action):
        h = self.current_commit()
        if not h:
            QMessageBox.information(self, 'Git', 'Select a commit first.')
            return
        if action == 'checkout':
            if self.confirm('Checkout commit', f'Check out {h}? You will be in a detached HEAD state.'):
                self.mutate(['checkout', h])
        elif action == 'branch':
            name = self.ask('New branch', f'Branch name (starting at {h}):')
            if name:
                self.mutate(['checkout', '-b', name, h])
        elif action == 'tag':
            name = self.ask('New tag', f'Tag name for {h}:')
            if name:
                self.mutate(['tag', name, h])
        elif action == 'cherry-pick':
            self.mutate(['cherry-pick', h])
        elif action == 'revert':
            self.mutate(['revert', '--no-edit', h])
        elif action == 'reset':
            mode, ok = QInputDialog.getItem(self, 'Reset', f'Move the current branch to {h}:',
                                            ['soft (keep changes staged)', 'mixed (keep changes unstaged)', 'hard (DISCARD all changes)'], 1, False)
            if ok and (not mode.startswith('hard') or self.confirm('Hard reset', 'This discards all uncommitted changes. Continue?')):
                self.mutate(['reset', '--' + mode.split()[0], h])

    # ---- branches & remotes ----
    def current_branch(self):
        item = self.branches.currentItem()
        return item.data(Qt.UserRole) if item else None

    def checkout_branch(self):
        b = self.current_branch()
        if not b:
            return
        name, remote, _ = b
        if remote:
            self.mutate(['checkout', '--track', name])
        else:
            self.mutate(['checkout', name])

    def new_branch(self):
        name = self.ask('New branch', 'Name of the new branch (created from the current one):')
        if name:
            self.mutate(['checkout', '-b', name])

    def merge_branch(self):
        b = self.current_branch()
        if b and self.confirm('Merge', f'Merge {b[0]} into the current branch?'):
            self.mutate(['merge', b[0]])

    def rebase_branch(self):
        b = self.current_branch()
        if b and self.confirm('Rebase', f'Rebase the current branch onto {b[0]}? This rewrites its history.'):
            self.mutate(['rebase', b[0]])

    def rename_branch(self):
        b = self.current_branch()
        if not b or b[1]:
            return
        name = self.ask('Rename branch', f'New name for {b[0]}:', b[0])
        if name:
            self.mutate(['branch', '-m', b[0], name])

    def delete_branch(self):
        b = self.current_branch()
        if not b or b[2]:
            QMessageBox.information(self, 'Git', 'Select a branch other than the current one.')
            return
        name, remote, _ = b
        if not self.confirm('Delete branch', f'Delete {"remote " if remote else ""}branch {name}?'):
            return
        if remote:
            remote_name, _, branch = name.partition('/')
            self.mutate(['push', remote_name, '--delete', branch])
            return

        def done(rc, out, err):
            if rc != 0 and 'not fully merged' in err and self.confirm('Force delete', f'{name} is not fully merged. Delete it anyway?'):
                self.mutate(['branch', '-D', name])
            else:
                self.refresh()
        self.git(['branch', '-d', name], done, report=False)

    def add_remote(self):
        name = self.ask('Add remote', 'Remote name:', 'origin')
        url = name and self.ask('Add remote', 'Remote URL:')
        if name and url:
            self.mutate(['remote', 'add', name, url])

    def remove_remote(self):
        name = self.ask('Remove remote', 'Name of the remote to remove:')
        if name and self.confirm('Remove remote', f'Remove remote {name}?'):
            self.mutate(['remote', 'remove', name])

    # ---- repository setup & raw commands ----
    def init_repo(self):
        if not shutil.which('git'):
            QMessageBox.warning(self, 'Git', 'git was not found. Install Git for Windows and make sure it is on PATH.')
            return
        self.git(['init'], lambda rc, out, err: self.refresh())

    def clone_repo(self):
        url = self.ask('Clone', 'Repository URL (cloned into this folder, which must be empty):')
        if url:
            self.git(['clone', url, '.'], lambda rc, out, err: self.refresh())

    def run_custom(self):
        text = self.command.text().strip()
        if not text:
            return
        try:
            args = shlex.split(text, posix=False)
        except ValueError as exc:
            QMessageBox.warning(self, 'Git', str(exc))
            return
        if args and args[0].lower() == 'git':
            args = args[1:]
        args = [a[1:-1] if len(a) > 1 and a[0] == a[-1] and a[0] in '"\'' else a for a in args]
        self.command.clear()
        self.git(args, lambda rc, out, err: self.refresh(), report=False)

    def closeEvent(self, event):
        for worker in self.workers:
            worker.wait(2000)
        super().closeEvent(event)

    def done(self, result):
        for worker in self.workers:
            worker.wait(2000)
        super().done(result)
