import json
from pathlib import Path

import pytest

from harness.web_fetch.config import load_dataset


def test_full_reference_provenance_cannot_point_to_another_site(tmp_path: Path):
    case = {
        'id': 'copied-source', 'name': 'copied-source', 'url': 'https://example.com/page',
        'category': 'static', 'task_prompt': 'read', 'tool': 'web_fetch',
        'ground_truth_evidence': [{
            'source': 'https://unrelated.test/page',
            'supports': '本版独立获取的完整参考正文',
        }],
    }
    path = tmp_path / 'dataset.json'
    path.write_text(json.dumps({'tool': 'web_fetch', 'cases': [case]}), encoding='utf-8')
    with pytest.raises(ValueError, match='full-reference evidence'):
        load_dataset(path)
