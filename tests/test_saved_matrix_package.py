"""Frozen comparisons may be hosted without entering certified assembly."""
import pytest

from harness.web_fetch.report_package import build as package
from tests.test_saved_matrix_evidence import build, fixture


def test_saved_matrix_cannot_be_packaged_for_release(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    with pytest.raises(ValueError, match='Diagnostic evidence cannot enter certified assembly or reports'):
        package(output, tmp_path / 'report.zip')
    assert not (tmp_path / 'report.zip').exists()


def test_frozen_composition_packages_as_noncertified_report(tmp_path):
    import json
    from bs4 import BeautifulSoup
    from harness.reporting.presentation import check_presentation
    from harness.web_fetch.report_package import validate_release
    from harness.web_fetch.report_saved_matrix import build_saved_matrix
    from tests.test_saved_matrix_composition import reports
    path, _ = reports(tmp_path/'inputs')
    spec = json.loads(path.read_text())
    spec.update(ranking='saved_task_pass_rate', scope_notes='external')
    path.write_text(json.dumps(spec))
    output = build_saved_matrix(path, tmp_path/'combined').parent
    assert validate_release(output) is False
    package(output, tmp_path/'combined.zip')
    assert (tmp_path/'combined.zip').is_file()
    page = BeautifulSoup((output/'index.html').read_text(), 'html.parser')
    check_presentation(page)
    for table in page.select('table[data-highlight-ties]'):
        del table['data-highlight-ties']
    with pytest.raises(ValueError, match='extremum highlight'):
        check_presentation(page)
    (output/'dataset.json').write_text('{}')
    with pytest.raises(ValueError):
        validate_release(output)


def test_composed_public_answers_keep_original_profile_authority(tmp_path):
    import json
    from copy import deepcopy
    from harness.web_fetch.publication_check import scored_public_view
    from harness.web_fetch.report_saved_matrix import build_saved_matrix
    from tests.test_saved_matrix_composition import reports
    path, _ = reports(tmp_path/'inputs', public=True)
    output = build_saved_matrix(path, tmp_path/'combined').parent
    rows = [json.loads(line) for line in (output/'results.jsonl').read_text().splitlines()]
    expected = rows[0]['metrics']['content_sha256']
    view = scored_public_view(output, rows[0], expected)
    assert view is not None and view[1]['original_sha256'] == expected
    assert scored_public_view(output, rows[0], '0'*64) is None
    forged = deepcopy(rows[0])
    forged['provider_metadata']['publication_views'] = rows[1]['provider_metadata']['publication_views']
    with pytest.raises(ValueError, match='escapes its original profile'):
        scored_public_view(output, forged, expected)
