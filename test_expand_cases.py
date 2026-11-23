import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import evidence
import expand_cases


class ExpansionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_csv(self, name, rows):
        path = self.root / name
        fields = sorted({key for row in rows for key in row})
        with path.open('w', encoding='utf-8', newline='') as out:
            writer = csv.DictWriter(out, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_classification_requires_relevant_title_instead_of_incidental_body_mention(self):
        for title, expected in [('Adolescent idiopathic scoliosis', 'spine'),
                                ('Kyphoscoliosis case report', 'spine'),
                                ('Failure of total hip arthroplasty', 'joint'),
                                ('Knee replacement complication', 'joint'),
                                ('Comminuted tibial plateau fracture', 'trauma'),
                                ('Fracture of the distal radius', 'trauma'),
                                ('Radial scar of the breast', None),
                                ('Dental prosthesis case report', None),
                                ('Skull fracture after a fall', None)]:
            with self.subTest(title=title):
                self.assertEqual(expand_cases.classify(title), expected)

    def test_cross_source_heldout_exclusion_and_article_deduplication(self):
        text = 'Case narrative and documented follow up. ' * 5
        medcase = self.write_csv('medcase.csv', [
            {'pmcid': 'PMC1234567', 'split': 'train', 'title': 'Scoliosis case report', 'case_prompt': text},
            {'pmcid': 'PMC2345678', 'split': 'test', 'title': 'Tibial fracture', 'case_prompt': text},
            {'pmcid': 'PMC3456789', 'split': 'val', 'title': 'Hip replacement', 'case_prompt': text}])
        pmc = self.write_csv('pmc.csv', [
            {'file_path': 'comm/PMC001xxxxxx/PMC1234567.xml', 'title': 'Scoliosis case report', 'patient': text},
            {'file_path': 'comm/PMC002xxxxxx/PMC2345678.xml', 'title': 'Tibial fracture', 'patient': text},
            {'file_path': 'comm/PMC003xxxxxx/PMC3456789.xml', 'title': 'Hip replacement', 'patient': text},
            {'file_path': 'comm/PMC004xxxxxx/PMC4567890.xml', 'title': 'Tibial fracture', 'patient': text, 'patient_uid': 'one'},
            {'file_path': 'comm/PMC004xxxxxx/PMC4567890.xml', 'title': 'Tibial fracture', 'patient': text, 'patient_uid': 'two'}])
        rows, stats = expand_cases.build(medcase, pmc)
        self.assertEqual([row['id'] for row in rows], ['PMC1234567', 'PMC4567890'])
        self.assertEqual(rows[0]['dataset'], 'zou-lab/MedCaseReasoning')
        self.assertEqual(rows[1]['source_patient_uid'], 'one')
        self.assertEqual(rows[1]['license'], 'CC-BY-NC-SA-4.0')
        self.assertEqual(rows[1]['split'], 'reference')
        self.assertEqual(stats['pmc_heldout_excluded'], 2)
        path = self.root / 'corpus.jsonl'
        import json
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
        self.assertEqual(len(evidence.load_records(path)), 2)

    def test_mismatched_dataset_license_is_rejected(self):
        import json
        path = self.root / 'corpus.jsonl'
        row = {'id': 'PMC1234567', 'scenario': 'spine', 'case_text': 'text',
               'source_url': 'https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/',
               'dataset': 'zhengyun21/PMC-Patients', 'license': 'CC-BY-4.0', 'split': 'reference'}
        path.write_text(json.dumps(row), encoding='utf-8')
        with self.assertRaises(ValueError):
            evidence.load_records(path)

    def test_corrupt_download_never_replaces_verified_cache(self):
        source = expand_cases.SOURCES['medcase']
        partial = self.root / 'corrupt.part'
        partial.write_bytes(b'bad')
        metadata = {'sha': 'revision'}
        tree = [{'path': source['filename'], 'size': 3, 'lfs': {'oid': '0' * 64}}]
        with patch.object(expand_cases, 'get_json', side_effect=[metadata, tree]), \
                patch.object(expand_cases, 'download_ranges', return_value=partial):
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                expand_cases.download(source, self.root)
        self.assertFalse(list(self.root.glob('*.csv')))


if __name__ == '__main__':
    unittest.main()
