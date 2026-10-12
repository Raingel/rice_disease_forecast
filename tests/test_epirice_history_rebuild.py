import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.rebuild_epirice_history import merge, verify_states


class HistoryValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / '2025_1' / 'TEST.csv'
        self.path.parent.mkdir()
        self.expected = {'TEST_2025_1': {'start': '2025-02-01', 'end': '2025-02-02', 'rows': 2}}
        self.frame = pd.DataFrame({'date': ['2025-02-01', '2025-02-02'],
                                   'is_forecast': [False, False],
                                   **{k: [1., 1.] for k in ['TEMP', 'RHUM', 'RAIN', 'healthy_sites', 'latent', 'infectious', 'removed']}})

    def test_complete_historical_dates_pass(self):
        self.frame.to_csv(self.path, index=False)
        verify_states(self.root, self.expected)

    def test_repeated_date_cannot_replace_missing_day(self):
        self.frame.loc[1, 'date'] = '2025-02-01'
        self.frame.to_csv(self.path, index=False)
        with self.assertRaisesRegex(ValueError, 'Incomplete state dates'):
            verify_states(self.root, self.expected)

    def test_forecast_cannot_be_published_as_history(self):
        self.frame.loc[1, 'is_forecast'] = True
        self.frame.to_csv(self.path, index=False)
        with self.assertRaisesRegex(ValueError, 'Invalid historical state'):
            verify_states(self.root, self.expected)

    def test_missing_scenario_fails(self):
        with self.assertRaisesRegex(ValueError, 'Missing state scenarios'):
            verify_states(self.root, self.expected)

    def test_merge_requires_all_batch_completion_markers(self):
        inputs = self.root / 'inputs'
        inputs.mkdir()
        (inputs / 'manifest.json').write_text(json.dumps({'groups': [['TEST']]}))
        output = self.root / 'output'
        with self.assertRaises(FileNotFoundError):
            merge(inputs, self.root / 'parts', output)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
