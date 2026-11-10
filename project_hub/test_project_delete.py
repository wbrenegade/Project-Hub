import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from project_app import App, QMenu
from project_models import Project, ProjectManager
from project_storage import ProjectStore


class ContextEvent:
    def __init__(self):
        self.accepted = False

    def screenPos(self):
        return QPoint(0, 0)

    def accept(self):
        self.accepted = True


class ProjectDeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / 'source'
        self.source.mkdir()
        (self.source / 'keep.txt').write_text('keep', encoding='utf8')
        self.store = ProjectStore(root / 'projects.json')
        self.first = Project(name='Alpha', source_location=str(self.source))
        self.second = Project(name='Beta')
        self.store.save(ProjectManager(projects=[self.first, self.second]))
        for target, value in (
            ('project_app.ProjectStore', self.store),
            ('project_app.load_recent', []),
            ('project_app.load_map_layout', {}),
            ('project_app.load_loose_items', []),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        recent = patch('project_app.save_recent')
        recent.start()
        self.addCleanup(recent.stop)
        watcher = patch('project_app.FolderWatcher.start')
        watcher.start()
        self.addCleanup(watcher.stop)
        self.window = App()
        self.window.show()
        self.qt.processEvents()
        self.addCleanup(self.window.close)
        self.window.select_index(1)

    @staticmethod
    def choose_delete(menu, *_args):
        return next(action for action in menu.actions() if action.text() == 'Delete project...')

    def test_map_right_click_deletes_clicked_project_not_selected(self):
        event = ContextEvent()
        with patch.object(QMenu, 'exec', autospec=True, side_effect=self.choose_delete), \
             patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes) as confirm:
            self.window.map.roots[self.first.id].contextMenuEvent(event)
            self.qt.processEvents()
        self.assertTrue(event.accepted)
        self.assertIn('Alpha', confirm.call_args.args[2])
        self.assertEqual([p.name for p in self.store.load().projects], ['Beta'])
        self.assertTrue((self.source / 'keep.txt').exists())
        self.assertEqual(self.window.stack.currentIndex(), 1)
        self.assertIs(self.window.views.currentWidget(), self.window.map)

    def test_list_right_click_cancel_preserves_both_projects(self):
        self.window.toggle_view()
        self.qt.processEvents()
        item = self.window.tree.topLevelItem(0)
        pos = self.window.tree.visualItemRect(item).center()
        with patch.object(QMenu, 'exec', autospec=True, side_effect=self.choose_delete), \
             patch.object(QMessageBox, 'question', return_value=QMessageBox.No) as confirm:
            self.window.project_list_menu(pos)
        self.assertIn('Alpha', confirm.call_args.args[2])
        self.assertEqual([p.name for p in self.store.load().projects], ['Alpha', 'Beta'])

    def test_list_right_click_deletes_clicked_project(self):
        self.window.toggle_view()
        self.qt.processEvents()
        pos = self.window.tree.visualItemRect(self.window.tree.topLevelItem(0)).center()
        with patch.object(QMenu, 'exec', autospec=True, side_effect=self.choose_delete), \
             patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.window.project_list_menu(pos)
        self.assertEqual([p.name for p in self.store.load().projects], ['Beta'])
        self.assertTrue((self.source / 'keep.txt').exists())

    def test_delete_selected_and_dismissed_menu(self):
        event = ContextEvent()
        with patch.object(QMenu, 'exec', return_value=None), \
             patch.object(QMessageBox, 'question') as confirm:
            self.window.map.roots[self.first.id].contextMenuEvent(event)
            self.qt.processEvents()
        confirm.assert_not_called()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.window.delete()
        self.assertEqual([p.name for p in self.store.load().projects], ['Alpha'])
        self.assertEqual(self.window.stack.currentIndex(), 1)
        self.assertIs(self.window.views.currentWidget(), self.window.map)

    def test_keyboard_delete_key_on_draggable_view(self):
        self.window.select_index(0)
        self.qt.processEvents()
        self.assertTrue(self.window.map.roots[self.first.id].selected)
        event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes) as confirm:
            self.window.map.keyPressEvent(event)
            self.qt.processEvents()
        self.assertIn('Alpha', confirm.call_args.args[2])
        self.assertEqual([p.name for p in self.store.load().projects], ['Beta'])
        self.assertEqual(self.window.stack.currentIndex(), 1)
        self.assertIs(self.window.views.currentWidget(), self.window.map)

    def test_keyboard_delete_key_in_tree_view(self):
        self.window.toggle_view()
        self.qt.processEvents()
        self.window.select_index(0)
        self.qt.processEvents()
        event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes) as confirm:
            self.window.tree.keyPressEvent(event)
            self.qt.processEvents()
        self.assertIn('Alpha', confirm.call_args.args[2])
        self.assertEqual([p.name for p in self.store.load().projects], ['Beta'])
        self.assertEqual(self.window.stack.currentIndex(), 1)
        self.assertEqual(self.window.views.currentIndex(), 1)


if __name__ == '__main__':
    unittest.main()
