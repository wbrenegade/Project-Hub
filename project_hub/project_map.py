"""Mind-map view of the project inventory.

Each project is a draggable folder. Clicking a node opens or closes its children, following the
tab / sub-category grouping in PROJECT_FIELD_GROUPING.md: project -> category -> sub-category -> field.
Children are Qt child items, so dragging a node carries its whole open branch with it.
"""
from __future__ import annotations

import math
import os
import uuid

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QGraphicsItem, QGraphicsLineItem, QGraphicsProxyWidget, QGraphicsScene,
                               QGraphicsView, QLineEdit, QMenu as _QMenu)

class QMenu(_QMenu):
    def exec(self, *args, **kwargs):
        return super().exec(*args, **kwargs)

from project_dialogs import BOOL, PROJECT_FORM, RECORDS, SECRET, TEXT, credential_fields, renewal_fields
from project_models import get_path

FIELD_LABELS = {path: label for _, sections in PROJECT_FORM for _, _, group in sections for path, label, *_ in group}
FIELD_KINDS = {path: (kind[0] if kind else TEXT) for _, sections in PROJECT_FORM for _, _, group in sections
               for path, _, *kind in group}

def credentials(prefix):
    # Passwords live in the vault and never in the project; the vault key field is shown instead.
    return [path for path, _, *kind in credential_fields(prefix) if kind != [SECRET]]

def renewal(prefix, what):
    return [path for path, *_ in renewal_fields(prefix, what)]

# (category, [(sub-category, [model paths])]) — mirrors the table in PROJECT_FIELD_GROUPING.md.
PROJECT_MAP = [
 ('Overview', [('Identity', ['name', 'description', 'status', 'date_created', 'notes']),
               ('Classification', ['project_type', 'deployment.stage'])]),
 ('Source & Environment', [('Local source', ['source_location', 'machine']),
                           ('Paths', ['paths.config', 'paths.data', 'paths.logs', 'paths.artifacts']),
                           ('Commands', ['commands.start', 'commands.restart', 'commands.stop'])]),
 ('Hosting & Deployment', [
   ('Deployment target', ['deployment.environment', 'deployment.platform', 'deployment.runtime', 'deployment.service']),
   ('Host / server', ['deployment.provider_name', 'website.hosting.provider', 'website.hosting.provider_url',
                      'website.hosting.plan', 'website.hosting.ip_address', 'deployment.remote_path']),
   ('Access', [*credentials('deployment.credentials'), 'deployment.ssh_key_path']),
   ('Endpoints & health', ['deployment.site_url', 'deployment.health_url']),
   ('Billing / renewal', ['website.hosting.purchased_on', *renewal('website.hosting', 'Hosting')])]),
 ('Domain & DNS', [
   ('Domain', ['website.domain.name', 'website.domain.registrar', 'website.domain.registrar_url', 'website.domain.registered_on']),
   ('DNS', ['website.dns.provider', 'website.dns.provider_url', 'website.dns.nameservers', 'website.dns.records']),
   ('Billing / renewal', renewal('website.domain', 'Domain'))]),
 ('Cloud', [('Account', ['cloud.provider', 'cloud.account', 'cloud.project']),
            ('Resources', ['cloud.region', 'cloud.service']),
            ('Access', ['cloud.credential_ref'])]),
 ('Data & Services', [
   ('Database: provider', ['database.provider']),
   ('Database: connection', ['database.db_name', *credentials('database.credentials')]),
   ('Payments', ['payments.provider', 'payments.mode', 'payments.account', 'payments.webhook_url', 'payments.credential_ref']),
   ('AI', ['ai.provider', 'ai.model', 'ai.endpoint', 'ai.credential_ref'])]),
]

BACKGROUND = '#e6e6e6'
GRID_COLOUR = '#c4c4c4'
PATH_FIELDS = {'source_location', 'paths.config', 'paths.data', 'paths.logs', 'paths.artifacts'}
GRID_STEP = 40
COVERED_OPACITY = 0.25
LOOSE_Z = 100000
EDGE_PEN = QPen(QColor('#555555'), 2.5, Qt.SolidLine, Qt.RoundCap)
# kind: (font size, bold, fill, border, text colour, (x, y) padding)
STYLES = {
    'category': (10, False, '#0b0ba8', '#3d4bff', '#ffffff', (10, 5)),
    'sub': (7, False, '#0b0b96', '#3d4bff', '#ffffff', (6, 3)),
    'field': (8, False, '#f7f9fc', '#8795ad', '#1b2130', (9, 3)),
}
MAX_TEXT = 56
CLICK_SLOP = 10  # pixels the mouse may move and still count as a click

def field_value(value):
    if value in (None, '', []):
        return ''
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return f'{len(value)} record(s)'
        return ', '.join(map(str, value))
    return str(value)

def field_spec(data, path):
    label = FIELD_LABELS.get(path, path.rsplit('.', 1)[-1]).replace(' (YYYY-MM-DD)', '')
    raw = get_path(data, path)
    value = field_value(raw)
    children = []
    if path == 'website.dns.records' and raw:
        children = [('field', f"{r['type']}  {r['name']}  →  {r['value']}", [], False) for r in raw]
    kind = FIELD_KINDS.get(path, TEXT)
    edit = None
    if kind != RECORDS and kind != SECRET:
        current = bool(raw) if kind == BOOL else ', '.join(map(str, raw)) if isinstance(raw, list) else '' if raw is None else str(raw)
        edit = (path, kind, current)
    return ('field', f'{label}: {value or "—"}', children, not value, edit)

def linked_specs(data):
    """Sub-categories for the files and folders dropped onto a project."""
    items = data.get('linked_items') or []
    dirs = [p for p in items if os.path.isdir(p)]
    files = [p for p in items if not os.path.isdir(p)]
    def leaf(path):
        return ('field', os.path.basename(path.rstrip('\\/')) or path, [], not os.path.exists(path), None, path)
    subs = [('sub', title, [leaf(p) for p in paths], False) for title, paths in (('Folders', dirs), ('Files', files)) if paths]
    return subs or [('sub', 'Drag files or folders onto this project', [], True)]

def project_children(data):
    specs = [('category', category,
              lambda subs=subs: [('sub', sub, lambda paths=paths: [field_spec(data, p) for p in paths], False)
                                 for sub, paths in subs], False)
             for category, subs in PROJECT_MAP]
    specs.append(('category', 'Files & folders', lambda: linked_specs(data), False))
    return specs


class FieldEditor(QLineEdit):
    cancelled = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)


class MapNode(QGraphicsItem):
    """A box on the map. Children are created the first time it is opened, then hidden and shown."""
    def __init__(self, view, kind, text, children, muted=False, parent=None, edit=None, link=None):
        super().__init__(parent)
        self.view = view
        self.edit = edit  # (model path, field kind, current value) for fields the user can change here
        self.link_path = link  # a file/folder linked to the project, shown as a leaf
        self.editor = None
        self.kind = kind
        self.full_text = text
        self.text = text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + '…'
        self.muted = muted
        self.children_spec = children
        self.child_nodes = None
        self.expanded = False
        self.edge = None
        self._press = None
        self._press_pos = QPointF()
        self._skip_release = False
        self.rect = QRectF()
        self.setup_shape()
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setCursor(self.hover_cursor())

    def hover_cursor(self):
        if self.has_children:
            return Qt.PointingHandCursor
        if self.edit:
            return Qt.PointingHandCursor if self.edit[1] == BOOL else Qt.IBeamCursor
        return Qt.OpenHandCursor
        if self.text != text:
            self.setToolTip(text)
        if link:
            self.setToolTip(link)

    @property
    def has_children(self):
        return bool(self.children_spec)

    @property
    def key(self):
        return self.full_text

    def setup_shape(self):
        size, bold, *_ , (px, py) = STYLES[self.kind]
        self.font = QFont('Arial', size, QFont.Bold if bold else QFont.Normal)
        metrics = QFontMetricsF(self.font)
        w, h = metrics.horizontalAdvance(self.text) + 2 * px, metrics.height() + 2 * py
        self.rect = QRectF(-w / 2, -h / 2, w, h)

    def boundingRect(self):
        return self.rect.adjusted(-2, -2, 2, 2)

    def paint(self, painter, option, widget=None):
        _, _, fill, border, colour, _ = STYLES[self.kind]
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(self.rect, self.rect.height() / 2, self.rect.height() / 2)
        painter.setFont(self.font)
        painter.setPen(QColor('#7a8696' if self.muted else colour))
        painter.drawText(self.rect, Qt.AlignCenter, self.text)
        if self.has_children and not self.expanded:
            # A small notch on the right edge marks a node that still has something to open.
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(border))
            painter.drawEllipse(QPointF(self.rect.right(), 0), 2.5, 2.5)

    # ---- opening and closing ----
    def toggle(self):
        if self.expanded:
            self.close()
        else:
            self.open()

    def open(self):
        if not self.has_children or self.expanded:
            return
        if self.child_nodes is None:
            spec = self.children_spec() if callable(self.children_spec) else self.children_spec
            self.child_nodes = []
            for kind, text, children, muted, *extra in spec:
                child = MapNode(self.view, kind, text, children, muted, parent=self,
                                edit=extra[0] if extra else None, link=extra[1] if len(extra) > 1 else None)
                child.edge = QGraphicsLineItem(self)
                child.edge.setPen(EDGE_PEN)
                child.edge.setFlag(QGraphicsItem.ItemStacksBehindParent)
                self.child_nodes.append(child)
            self.layout_children()
        for child in self.child_nodes:
            child.setVisible(True)
            child.edge.setVisible(True)
        self.expanded = True
        self.update()
        self.view.schedule_overlaps()

    def close(self):
        for child in self.child_nodes or ():
            child.setVisible(False)
            child.edge.setVisible(False)
        self.expanded = False
        self.update()
        self.view.schedule_overlaps()

    def outward_angle(self):
        """Direction pointing away from this node's parent, so children grow outward."""
        if isinstance(self.parentItem(), MapNode):
            return math.atan2(self.pos().y(), self.pos().x())
        return -math.pi / 2

    def layout_children(self):
        nodes = self.child_nodes
        if not nodes:
            return
        base = self.outward_angle()
        if self.kind in ('sub', 'field'):
            # Field lists read best as a column beside their parent, on the side facing away from it.
            side = 1 if math.cos(base) >= -0.2 else -1
            gap = 6
            total = sum(n.rect.height() for n in nodes) + gap * (len(nodes) - 1)
            y = -total / 2 + (math.sin(base) * total / 3 if abs(math.cos(base)) < 0.5 else 0)
            x0 = side * (self.rect.width() / 2 + 45)
            for n in nodes:
                h = n.rect.height()
                n.setPos(x0 + side * n.rect.width() / 2, y + h / 2)
                y += h + gap
            return
        count = len(nodes)
        if isinstance(self.parentItem(), MapNode):
            # A fan centred on the outward direction.
            step = min(math.radians(34), math.radians(170) / max(count - 1, 1))
            start = base - step * (count - 1) / 2
        else:
            # A full ring for the project, first category at the top, going clockwise.
            step = 2 * math.pi / count
            start = base
        # Far enough out that neighbouring boxes on the arc do not overlap.
        widest = max(n.rect.width() for n in nodes)
        radius = (widest + 12) / (2 * math.sin(step / 2)) if count > 1 else 0
        for i, n in enumerate(nodes):
            angle = start + step * i
            reach = max(radius, 40 + self.extent(angle) + n.extent(angle))
            n.setPos(math.cos(angle) * reach, math.sin(angle) * reach)

    def extent(self, angle):
        """Distance from the centre to the box edge in the given direction."""
        c, s = abs(math.cos(angle)), abs(math.sin(angle))
        half_w, half_h = self.rect.width() / 2, self.rect.height() / 2
        return min(half_w / c if c > 1e-6 else math.inf, half_h / s if s > 1e-6 else math.inf)

    # ---- remembering state across rebuilds ----
    def snapshot(self):
        """{key: (position, expanded, child snapshot)} for every child built so far."""
        return {n.key: (n.pos(), n.expanded, n.snapshot()) for n in self.child_nodes or ()}

    def restore(self, state, expanded=True):
        if not expanded:
            return
        self.open()
        for n in self.child_nodes or ():
            if n.key in state:
                pos, open_, child_state = state[n.key]
                n.setPos(pos)
                n.restore(child_state, open_)

    # ---- mouse ----
    def openable_path(self):
        """The folder/file this node stands for, if it is a path field with a value or a linked item."""
        if self.link_path:
            return self.link_path
        if self.edit and self.edit[0] in PATH_FIELDS and self.edit[2]:
            return self.edit[2]
        return None

    def contextMenuEvent(self, event):
        path = self.openable_path()
        if not path:
            event.ignore()
            return
        menu = QMenu()
        menu.addAction('Open in editor', lambda: self.view.openInEditor.emit(path))
        menu.addAction('Git...', lambda: self.view.gitRequested.emit(path))
        if self.edit and self.edit[1] != BOOL:
            menu.addAction('Edit value', self.start_edit)
        if self.link_path:
            index = self.view.index_of(self)
            menu.addAction('Remove from project', lambda: self.view.unlinkRequested.emit(index, path))
        menu.exec(event.screenPos())
        event.accept()
    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            if self.edge:
                self.edge.setLine(QLineF(QPointF(0, 0), value))
            self.view.schedule_overlaps()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        self.view.raise_root(self)
        self.view.setFocus()
        self._press = event.screenPos()
        self._press_pos = self.pos()
        self._skip_release = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        moved = self._press is not None and (event.screenPos() - self._press).manhattanLength() > CLICK_SLOP
        if self._skip_release:
            self._skip_release = False
        elif not moved and event.button() == Qt.LeftButton:
            self.setPos(self._press_pos)  # undo any jitter the movable flag applied during the click
            self.clicked()
        elif moved:
            self.dragged()

    def mouseDoubleClickEvent(self, event):
        self._skip_release = True
        self.double_clicked()

    def clicked(self):
        if self.edit and not self.has_children:
            self.start_edit()
        else:
            self.toggle()

    def start_edit(self):
        path, kind, current = self.edit
        if self.editor is not None:
            return
        previous = self.view.active_editor
        if previous is not None and previous is not self:
            previous.finish_edit()
            # Saving the previous field rebuilds the map; reopen this field on the fresh node afterwards.
            project_id = self.view.root_of(self).project.id
            QTimer.singleShot(0, lambda: self.view.edit_field_at(project_id, path))
            return
        if kind == BOOL:
            self.commit(not current)
            return
        if isinstance(kind, tuple):
            widget = QComboBox()
            widget.setEditable(True)
            widget.addItems(kind)
            widget.setCurrentText(current)
            line = widget.lineEdit()
            line.editingFinished.connect(lambda: self.commit(widget.currentText()))
            widget.activated.connect(lambda _i: self.commit(widget.currentText()))
        else:
            widget = line = FieldEditor(current)
            widget.editingFinished.connect(lambda: self.commit(widget.text()))
        widget.setMinimumWidth(max(int(self.rect.width()), 240))
        widget.setStyleSheet('background: #ffffff; color: #101820; border: 2px solid #0b0ba8; border-radius: 4px; padding: 2px;')
        if isinstance(line, FieldEditor):
            line.cancelled.connect(self.cancel_edit)
        proxy = QGraphicsProxyWidget(self)
        proxy.setWidget(widget)
        proxy.setZValue(20)
        proxy.setPos(self.rect.left(), -proxy.size().height() / 2)
        self.editor = proxy
        self.view.active_editor = self
        proxy.setFocus()
        line.setFocus()
        line.selectAll()

    def finish_edit(self):
        """Save whatever is typed and close the editor, as if the user had pressed Enter."""
        text = self.editor_text()
        if text is not None:
            self.commit(text)

    def editor_text(self):
        widget = self.editor.widget() if self.editor is not None else None
        if widget is None:
            return None
        return widget.currentText() if isinstance(widget, QComboBox) else widget.text()

    def commit(self, value):
        if self.editor is None and not (self.edit and self.edit[1] == BOOL):
            return
        self.close_editor()
        path, kind, current = self.edit
        if isinstance(value, str):
            value = value.strip()
        if value == current:
            return
        # Deferred: saving rebuilds the map, which deletes this node while its own signal is still running.
        index = self.view.index_of(self)
        QTimer.singleShot(0, lambda: self.view.fieldEdited.emit(index, path, value))

    def cancel_edit(self):
        self.close_editor()

    def close_editor(self):
        if self.view.active_editor is self:
            self.view.active_editor = None
        if self.editor is not None:
            proxy, self.editor = self.editor, None
            proxy.setVisible(False)
            widget = proxy.widget()
            if widget is not None:
                proxy.setWidget(None)
                widget.deleteLater()
            if proxy.scene() is not None:
                proxy.scene().removeItem(proxy)
            proxy.deleteLater()

    def double_clicked(self):
        if self.link_path:
            self.view.openInEditor.emit(self.link_path)

    def dragged(self):
        pass


class ProjectNode(MapNode):
    """The project itself: a cardboard package with the name across it and the status beneath."""
    FOLDER = QRectF(-30, -20, 60, 40)  # the box is drawn inside this rectangle

    def __init__(self, view, index, project):
        self.index = index
        self.project = project
        self.selected = False
        self.drop_target = False
        self.changed = False
        self.changed_files = []
        data = project.model_dump(mode='json')
        super().__init__(view, 'project', project.name, lambda: project_children(data))
        self.status = project.status or ''
        self.setZValue(1)

    @property
    def key(self):
        return self.project.id

    def contextMenuEvent(self, event):
        project_id = self.project.id
        view = self.view
        menu = QMenu(view)
        delete = menu.addAction('Delete project...')
        selected = menu.exec(event.screenPos())
        event.accept()
        if selected == delete:
            QTimer.singleShot(0, lambda: view.deleteRequested.emit(project_id))

    def setup_shape(self):
        self.font = QFont('Arial', 11, QFont.Bold)
        self.status_font = QFont('Arial', 7)
        name_w = QFontMetricsF(self.font).horizontalAdvance(self.text) + 6
        w = max(self.FOLDER.width(), name_w)
        self.rect = QRectF(-w / 2, self.FOLDER.top() - 8, w, self.FOLDER.height() + 24)

    def boundingRect(self):
        # Room for the drop-target ring drawn around the box.
        return self.rect.adjusted(-8, -8, 8, 8)

    def paint_box(self, painter, f):
        """An oblique cardboard box with a strip of tape across the lid."""
        left, right, top, bottom, depth = f.left(), f.right() - 10, f.top() + 10, f.bottom(), 10
        front = QPainterPath()
        front.addRect(QRectF(left, top, right - left, bottom - top))
        lid = QPainterPath(QPointF(left, top))
        for x, y in ((right, top), (right + depth, top - depth), (left + depth, top - depth)):
            lid.lineTo(x, y)
        lid.closeSubpath()
        side = QPainterPath(QPointF(right, top))
        for x, y in ((right + depth, top - depth), (right + depth, bottom - depth), (right, bottom)):
            side.lineTo(x, y)
        side.closeSubpath()
        painter.setPen(QPen(QColor('#7a5522'), 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for path, colour in ((front, '#c8964a'), (lid, '#e2b76e'), (side, '#a67a36')):
            painter.setBrush(QColor(colour))
            painter.drawPath(path)
        mid = (left + right) / 2
        tape = QPainterPath(QPointF(mid - 5, top))
        for x, y in ((mid + 5, top), (mid + 5 + depth, top - depth), (mid - 5 + depth, top - depth)):
            tape.lineTo(x, y)
        tape.closeSubpath()
        tape.addRect(QRectF(mid - 5, top, 10, 9))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#f3e6bf'))
        painter.drawPath(tape)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        f = self.FOLDER
        if self.drop_target:
            painter.setPen(QPen(QColor('#00c853'), 3))
            painter.setBrush(QColor(0, 200, 83, 55))
            painter.drawRoundedRect(self.boundingRect().adjusted(2, 2, -2, -2), 10, 10)
        self.paint_box(painter, f)
        if self.selected:
            painter.setPen(QPen(QColor('#00e5ff'), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(f.adjusted(-4, -8, 4, 4), 6, 6)
        if self.changed:
            # Orange dot on the box's corner: files in the project folder changed since it was last opened.
            painter.setPen(QPen(QColor('#ffffff'), 1.5))
            painter.setBrush(QColor('#ff6a00'))
            painter.drawEllipse(QPointF(f.right() + 2, f.top() - 4), 6, 6)

        # White name with a dark outline so it reads over the folder and the background alike.
        path = QPainterPath()
        width = QFontMetricsF(self.font).horizontalAdvance(self.text)
        path.addText(-width / 2, f.center().y() + 9, self.font, self.text)
        painter.setPen(QPen(QColor('#000000'), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#ffffff'))
        painter.drawPath(path)

        if self.status:
            painter.setFont(self.status_font)
            painter.setPen(QColor('#c00000'))
            painter.drawText(QRectF(self.rect.left(), f.bottom() + 1, self.rect.width(), 12), Qt.AlignHCenter | Qt.AlignTop, self.status)

    def clicked(self):
        self.toggle()
        self.view.projectSelected.emit(self.index)
        self.view.clear_changed(self)

    def dragged(self):
        self.view.projectSelected.emit(self.index)
        self.view.remember_position(self)

    def double_clicked(self):
        self.view.projectSelected.emit(self.index)
        self.view.projectOpened.emit(self.index)

    def openable_path(self):
        return self.project.source_location or None


class LooseNode(QGraphicsItem):
    """A file or folder dropped on empty space. Drag it onto a project to link it there."""
    W, H = 110, 66

    def __init__(self, view, item_id, path):
        self.rect = QRectF(-self.W / 2, -self.H / 2, self.W, self.H)
        super().__init__()
        self.view = view
        self.item_id = item_id
        self.path = path
        self.is_dir = os.path.isdir(path)
        self.name = os.path.basename(path.rstrip('\\/')) or path
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(path)
        self.setZValue(LOOSE_Z)

    def boundingRect(self):
        return self.rect.adjusted(-4, -4, 4, 4)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        missing = not os.path.exists(self.path)
        painter.setOpacity(0.5 if missing else 1.0)
        painter.setPen(QPen(QColor('#555555'), 1.2))
        painter.setBrush(QColor('#fbfbfb'))
        painter.drawRoundedRect(self.rect, 8, 8)
        cx, top = 0, self.rect.top() + 8
        if self.is_dir:
            painter.setPen(QPen(QColor('#a88600'), 1))
            painter.setBrush(QColor('#f2c40f'))
            painter.drawPolygon([QPointF(cx - 14, top + 4), QPointF(cx - 14, top), QPointF(cx - 4, top),
                                 QPointF(cx - 1, top + 4)])
            painter.drawRoundedRect(QRectF(cx - 14, top + 4, 28, 18), 2, 2)
        else:
            painter.setPen(QPen(QColor('#4a6fa5'), 1))
            painter.setBrush(QColor('#ffffff'))
            painter.drawPolygon([QPointF(cx - 10, top), QPointF(cx + 4, top), QPointF(cx + 10, top + 6),
                                 QPointF(cx + 10, top + 22), QPointF(cx - 10, top + 22)])
            painter.drawLine(QPointF(cx - 6, top + 10), QPointF(cx + 6, top + 10))
            painter.drawLine(QPointF(cx - 6, top + 15), QPointF(cx + 6, top + 15))
        painter.setPen(QColor('#222222'))
        painter.setFont(QFont('Arial', 8))
        text = QFontMetricsF(painter.font()).elidedText(self.name, Qt.ElideMiddle, self.W - 10)
        painter.drawText(QRectF(self.rect.left() + 4, top + 26, self.W - 8, 16), Qt.AlignCenter, text)

    def mousePressEvent(self, event):
        self.view.raise_loose(self)
        self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self.view.set_drop_target(self.view.project_at(event.scenePos()))

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.OpenHandCursor)
        self.view.loose_released(self, event.scenePos())

    def mouseDoubleClickEvent(self, event):
        self.view.openInEditor.emit(self.path)

    def contextMenuEvent(self, event):
        menu = QMenu()
        menu.addAction('Open in editor', lambda: self.view.openInEditor.emit(self.path))
        menu.addAction('Git...', lambda: self.view.gitRequested.emit(self.path))
        menu.addAction('Remove', lambda: self.view.remove_loose(self))
        menu.exec(event.screenPos())
        event.accept()


class ProjectMapView(QGraphicsView):
    itemsDropped = Signal(int, list)  # project index, paths
    unlinkRequested = Signal(int, str)  # project index, path
    projectSelected = Signal(int)
    projectOpened = Signal(int)
    deleteRequested = Signal(str)  # project id
    addRequested = Signal()
    scanRequested = Signal()
    fieldEdited = Signal(int, str, object)  # project index, model path, new value
    openInEditor = Signal(str)
    gitRequested = Signal(str)

    def index_of(self, node):
        return self.root_of(node).index

    @staticmethod
    def root_of(node):
        while node.parentItem() is not None:
            node = node.parentItem()
        return node

    def edit_field_at(self, project_id, path, text=None):
        root = self.roots.get(project_id)
        stack = [root] if root else []
        while stack:
            node = stack.pop()
            if node.edit and node.edit[0] == path and node.isVisible():
                node.start_edit()
                if text is not None and node.editor is not None:
                    widget = node.editor.widget()
                    (widget.lineEdit() if isinstance(widget, QComboBox) else widget).setText(text)
                return
            stack.extend(node.child_nodes or ())

    def mark_changed(self, project_id, files):
        root = self.roots.get(project_id)
        if root is None:
            return
        root.changed = True
        root.changed_files = (files + root.changed_files)[:200]
        shown = ', '.join(root.changed_files[:8]) + (' …' if len(root.changed_files) > 8 else '')
        root.setToolTip(f'Changed on disk: {shown}')
        root.update()

    def clear_changed(self, root):
        if root.changed:
            root.changed = False
            root.changed_files = []
            root.setToolTip('')
            root.update()

    def raise_root(self, node):
        root = self.root_of(node)
        self._top_z += 1
        root.setZValue(self._top_z)

    def schedule_overlaps(self):
        if not self._overlap_pending:
            self._overlap_pending = True
            QTimer.singleShot(0, self.update_overlaps)

    @staticmethod
    def shown_rects(node, out):
        out.append(node.mapRectToScene(node.boundingRect()))
        for child in node.child_nodes or ():
            if child.isVisible():
                ProjectMapView.shown_rects(child, out)
        return out

    def update_overlaps(self):
        """Dim and disable any project whose shown bubbles sit under a project that is in front of it."""
        self._overlap_pending = False
        rects = {id(r): self.shown_rects(r, []) for r in self.roots.values() if r.isVisible()}
        for root in self.roots.values():
            covered = root.isVisible() and any(
                other is not root and other.isVisible() and other.zValue() > root.zValue()
                and any(a.intersects(b) for a in rects[id(root)] for b in rects[id(other)])
                for other in self.roots.values())
            root.setOpacity(COVERED_OPACITY if covered else 1.0)
            if root.isEnabled() == covered:
                root.setEnabled(not covered)

    @staticmethod
    def dropped_paths(mime):
        paths = [os.path.abspath(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]
        return [p for p in paths if os.path.exists(p)]

    def project_at(self, scene_pos):
        """The enabled, visible project under the point (covered projects are not valid targets)."""
        for item in self.map_scene.items(scene_pos):
            if isinstance(item, LooseNode):
                continue
            root = self.root_of(item)
            if isinstance(root, ProjectNode) and root.isVisible() and root.isEnabled():
                return root
        return None

    def set_drop_target(self, root):
        for r in self.roots.values():
            want = r is root
            if r.drop_target != want:
                r.drop_target = want
                r.update()

    def dragEnterEvent(self, event):
        if self.dropped_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not self.dropped_paths(event.mimeData()):
            event.ignore()
            return
        self.set_drop_target(self.project_at(self.mapToScene(event.position().toPoint())))
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.set_drop_target(None)
        event.accept()

    def dropEvent(self, event):
        paths = self.dropped_paths(event.mimeData())
        pos = self.mapToScene(event.position().toPoint())
        root = self.project_at(pos)
        self.set_drop_target(None)
        if not paths:
            event.ignore()
            return
        if root is not None:
            self.itemsDropped.emit(root.index, paths)
        else:
            for n, path in enumerate(paths):
                self.add_loose(path, pos + QPointF(n * (LooseNode.W + 10), 0))
            self.save_loose_items()
        event.acceptProposedAction()

    def add_loose(self, path, pos, item_id=None):
        node = LooseNode(self, item_id or uuid.uuid4().hex, path)
        self.map_scene.addItem(node)
        node.setPos(pos)
        self.loose.append(node)
        return node

    def raise_loose(self, node):
        self._loose_z += 1
        node.setZValue(self._loose_z)

    def remove_loose(self, node):
        self.map_scene.removeItem(node)
        self.loose.remove(node)
        self.save_loose_items()

    def loose_released(self, node, scene_pos):
        root = self.project_at(scene_pos)
        self.set_drop_target(None)
        if root is not None:
            path, index = node.path, root.index
            self.remove_loose(node)
            QTimer.singleShot(0, lambda: self.itemsDropped.emit(index, [path]))
        else:
            self.save_loose_items()

    def save_loose_items(self):
        if self.save_loose:
            self.save_loose([{'id': n.item_id, 'path': n.path, 'x': n.pos().x(), 'y': n.pos().y()} for n in self.loose])

    def __init__(self, positions=None, save_positions=None, loose=None, save_loose=None, parent=None):
        super().__init__(parent)
        self.positions = dict(positions or {})
        self.save_positions = save_positions
        self.save_loose = save_loose
        self.loose = []
        self._loose_z = LOOSE_Z
        self.setAcceptDrops(True)
        self.roots = {}
        self.map_scene = QGraphicsScene(-5000, -5000, 10000, 10000, self)
        self.setScene(self.map_scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(f'QGraphicsView {{ background: {BACKGROUND}; border: 1px solid #1f2a37; border-radius: 6px; }}')
        self._centred = False
        self._top_z = 1
        self.active_editor = None
        self.reopen = None
        self._overlap_pending = False
        for item in loose or ():
            self.add_loose(item['path'], QPointF(item.get('x', 0), item.get('y', 0)), item.get('id'))

    def set_projects(self, projects, visible):
        """Show `projects`, hiding those whose index is not in `visible`. Unchanged projects keep their open branches."""
        old = self.roots
        self.roots = {}
        for index, p in enumerate(projects):
            root = old.pop(p.id, None)
            if root is None or root.project is not p:
                state = root.snapshot() if root else {}
                was_open = root.expanded if root else False
                was_changed = (root.changed, root.changed_files) if root else (False, [])
                if root:
                    pos = root.pos()
                    if self.active_editor is not None and self.root_of(self.active_editor) is root:
                        self.reopen = (p.id, self.active_editor.edit[0], self.active_editor.editor_text())
                        self.active_editor.close_editor()
                        self.active_editor = None
                    self.map_scene.removeItem(root)
                else:
                    pos = QPointF(*self.positions[p.id]) if p.id in self.positions else self.free_spot(index)
                root = ProjectNode(self, index, p)
                self.map_scene.addItem(root)
                root.setPos(pos)
                root.restore(state, was_open)
                root.changed, root.changed_files = was_changed
                if root.changed:
                    root.setToolTip('Changed on disk: ' + ', '.join(root.changed_files[:8]))
            root.index = index
            root.setVisible(index in visible)
            self.roots[p.id] = root
        for root in old.values():
            if self.active_editor is not None and self.root_of(self.active_editor) is root:
                self.active_editor.close_editor()
                self.active_editor = None
            self.map_scene.removeItem(root)
        if self.reopen:
            # A save elsewhere rebuilt the project holding the open editor; put it back with the typed text.
            QTimer.singleShot(0, lambda args=self.reopen: self.edit_field_at(*args))
            self.reopen = None
        if not self._centred and self.roots:
            self._centred = True
            self.centerOn(self.map_scene.itemsBoundingRect().center())

    def free_spot(self, index):
        cols = 4
        return QPointF((index % cols) * 600, (index // cols) * 520)

    def select(self, index):
        for root in self.roots.values():
            selected = root.index == index
            if root.selected == selected:
                continue
            root.selected = selected
            root.update()
            if selected and root.isVisible() and not self.viewport().rect().contains(self.mapFromScene(root.pos())):
                self.centerOn(root)

    def remember_position(self, root):
        self.positions[root.project.id] = (root.pos().x(), root.pos().y())
        if self.save_positions:
            self.save_positions(self.positions)

    def contextMenuEvent(self, event):
        if self.itemAt(event.pos()) is not None:
            super().contextMenuEvent(event)
            return
        # popup() avoids nesting Qt's event loop inside graphics-view event delivery.
        menu = _QMenu(self)
        menu.addAction('Add new project', self.addRequested.emit)
        menu.addAction('Scan folder...', self.scanRequested.emit)
        self._context_menu = menu
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(event.globalPos())
        event.accept()

    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor(BACKGROUND))
        step = GRID_STEP
        left, top = math.floor(rect.left() / step) * step, math.floor(rect.top() / step) * step
        lines = []
        x = left
        while x <= rect.right():
            lines.append(QLineF(x, rect.top(), x, rect.bottom()))
            x += step
        y = top
        while y <= rect.bottom():
            lines.append(QLineF(rect.left(), y, rect.right(), y))
            y += step
        painter.setPen(QPen(QColor(GRID_COLOUR), 0))
        painter.drawLines(lines)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        scale = self.transform().m11() * factor
        if 0.2 <= scale <= 4:
            self.scale(factor, factor)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete and self.active_editor is None:
            for root in self.roots.values():
                if root.selected and root.isVisible():
                    self.deleteRequested.emit(root.project.id)
                    event.accept()
                    return
        super().keyPressEvent(event)
