import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import SecretStr

from project_inference import apply_inference, infer_project
from project_app import App
from project_models import Project, ProjectManager
from project_services import IGNORE, ScanWorker
from project_storage import ProjectStore


class ProjectInferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, text):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding='utf8')

    def infer(self):
        return infer_project(str(self.root), IGNORE)

    def test_project_clues_fill_expected_fields_without_secrets(self):
        self.write('package.json', '{"dependencies": {"next": "1", "stripe": "1", "pg": "1"}}')
        self.write(r'src\db.ts', 'import { Pool } from "pg";')
        self.write('.env.local', 'STRIPE_SECRET_KEY=do-not-save-this\nR2_BUCKET=private-bucket\n'
                   'DATABASE_URL=postgres://user:password@hostname/db\n')
        self.write('wrangler.toml', 'main = "src/index.ts"\n[[r2_buckets]]\nbinding = "BUCKET"\n')
        fields, clues = self.infer()
        self.assertEqual(fields['database.provider'], 'postgres')
        self.assertEqual(fields['cloud.provider'], 'Cloudflare')
        self.assertEqual(fields['cloud.service'], 'Cloudflare R2')
        self.assertEqual(fields['payments.provider'], 'Stripe')
        self.assertEqual(fields['deployment.provider_name'], 'Cloudflare')
        self.assertEqual(fields['project_type'], 'website')
        self.assertNotIn('website.domain.registrar', fields)
        self.assertNotIn('do-not-save-this', str((fields, clues)))
        self.assertNotIn('private-bucket', str((fields, clues)))
        self.assertNotIn('password', str((fields, clues)))
        result = apply_inference(Project(name='example'), fields, Project(name='example').model_dump())
        self.assertEqual(result.database.provider, 'postgres')
        self.assertEqual(result.cloud.provider, 'Cloudflare')
        self.assertEqual(result.payments.provider, 'Stripe')

    def test_conflicts_and_environment_values_are_not_guessed(self):
        self.write('app.py', 'import stripe\nimport sqlite3\nimport psycopg\n')
        self.write('.env', 'OTHER=STRIPE_SECRET_KEY\nDATABASE_URL=mysql://secret\n')
        fields, clues = self.infer()
        self.assertNotIn('database.provider', fields)
        self.assertEqual(fields['payments.provider'], 'Stripe')
        self.assertTrue(any('Conflicting database.provider' in clue for clue in clues))

    def test_skips_dependencies_tests_locks_and_env_values(self):
        self.write(r'node_modules\stripe\index.js', 'import stripe')
        self.write(r'tests\example.py', 'import stripe')
        self.write('package-lock.json', '{"stripe": "1"}')
        self.write('.env', 'UNRELATED=STRIPE_SECRET_KEY\n')
        self.assertNotIn('payments.provider', self.infer()[0])

    def test_sql_server_and_cloudflare_s3_are_not_aws(self):
        self.write('app.py', 'import mssql\n')
        self.write('.env', 'R2_BUCKET_NAME=test\nS3_BUCKET_NAME=test\n')
        fields, _ = self.infer()
        self.assertEqual(fields['database.provider'], 'mssql')
        self.assertEqual(fields['cloud.provider'], 'Cloudflare')

    def test_preserves_initial_and_concurrent_user_edits_and_passwords(self):
        project = Project.model_validate({'name': 'demo', 'payments': {'provider': 'PayPal'},
                                          'deployment': {'credentials': {'password': SecretStr('secret')}}})
        original = project.model_dump()
        current = project.model_copy(deep=True)
        current.cloud = Project.model_validate({'name': 'x', 'cloud': {'provider': 'Custom'}}).cloud
        result = apply_inference(current, {'payments.provider': 'Stripe', 'cloud.provider': 'Cloudflare',
                                          'project_type': 'website'}, original, infer_type=False)
        self.assertEqual(result.payments.provider, 'PayPal')
        self.assertEqual(result.cloud.provider, 'Custom')
        self.assertEqual(result.project_type, 'app')
        self.assertEqual(result.deployment.credentials.password.get_secret_value(), 'secret')

    def test_inference_limits_are_reported(self):
        self.write('a.py', 'import stripe')
        self.write('b.py', 'import sqlite3')
        with patch('project_inference.MAX_SOURCE_FILES', 1):
            _, clues = self.infer()
        self.assertTrue(any('partial' in clue for clue in clues))

    def test_explicit_choices_and_conflicting_services_are_preserved(self):
        project = Project.model_validate({'name': 'demo', 'cloud': {'provider': 'Custom'}})
        fields = {'database.provider': 'sqlite', 'cloud.provider': 'Cloudflare',
                  'cloud.service': 'Cloudflare R2', 'deployment.environment': 'hosted',
                  'deployment.provider_name': 'Vercel', 'project_type': 'website'}
        result = apply_inference(project, fields, project.model_dump(), protected={
            'project_type', 'database.provider', 'deployment.environment'})
        self.assertEqual(result.project_type, 'app')
        self.assertIsNone(result.database)
        self.assertEqual(result.cloud.provider, 'Custom')
        self.assertEqual(result.cloud.service, '')
        self.assertIsNone(result.deployment)

    def test_completion_persists_inferred_values_and_rescans_do_not_overwrite(self):
        self.write('.env', 'STRIPE_API_KEY=secret\n')
        project = Project(name='demo', source_location=str(self.root))
        manager = ProjectManager(projects=[project])
        store = ProjectStore(self.root / 'inventory.json')
        app = SimpleNamespace(manager=manager, status=SimpleNamespace(setText=lambda _: None),
                              persist=lambda: store.save(manager))
        outputs = []
        worker = ScanWorker(project.id, str(self.root), project.model_dump())
        worker.finished.connect(lambda *args: outputs.append(args))
        worker.run()
        App.on_scan_finished(app, *outputs[-1])
        self.assertEqual(store.load().projects[0].payments.provider, 'Stripe')
        manager.projects[0].payments.provider = 'PayPal'
        worker = ScanWorker(project.id, str(self.root))
        worker.finished.connect(lambda *args: outputs.append(args))
        worker.run()
        App.on_scan_finished(app, *outputs[-1])
        loaded = store.load().projects[0]
        self.assertEqual(loaded.payments.provider, 'PayPal')
        self.assertTrue(any('payments.provider' in clue for clue in loaded.scan.clues))

    def test_worker_only_infers_for_new_project(self):
        self.write('.env', 'STRIPE_API_KEY=secret\n')
        outputs = []
        worker = ScanWorker('id', str(self.root))
        worker.finished.connect(lambda *args: outputs.append(args))
        worker.run()
        self.assertIsNone(outputs[-1][2][1])
        initial = Project(name='demo').model_dump()
        worker = ScanWorker('id', str(self.root), initial)
        worker.finished.connect(lambda *args: outputs.append(args))
        worker.run()
        self.assertEqual(outputs[-1][2][1][0]['payments.provider'], 'Stripe')
        self.assertFalse(outputs[-1][3])


if __name__ == '__main__':
    unittest.main()
