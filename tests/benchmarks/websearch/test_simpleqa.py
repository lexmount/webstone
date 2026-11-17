"""Frozen population selection follows input limits, never provider outcomes."""

import csv
import io

import pytest

from lexbench_websearch.simpleqa import eligible_cases, rebuild_dataset


def repair_fixture():
    from copy import deepcopy

    before = dict(query='What caused the artist’s death?', reference_answer='Pneumonia',
                  reference_urls=['https://archive.test/wrong-entry'])
    after = dict(before, reference_answer='Bronchitis', reference_urls=['https://archive.test/biography'])
    case = dict(id='sqv-1', **before, topic='Art', answer_type='Text')
    recipe = dict(schema='simpleqa-case-repairs/1', root_cause='Incorrect attribution in the original reference',
                  predecessor=dict(revision='a'*40, sha256='b'*64),
                  regression_ids=['corrected-factual-reference'], repairs=[dict(
                      case_id=case['id'], before=before, after=after, root_cause='Wrong biographical entry',
                      evidence=[dict(url=after['reference_urls'][0], sha256='c'*64,
                                     quote='The artist died of bronchitis.')])])
    return deepcopy(case), recipe


def test_reference_repair_rescores_existing_answers_without_mutating_captured_calls():
    from copy import deepcopy
    from lexbench_websearch.simpleqa import repair_cases
    from lexbench_websearch.scoring import evaluate, task_acceptance

    case, recipe = repair_fixture()
    untouched = dict(case, id='sqv-2')
    originals = deepcopy([case, untouched])
    cases = repair_cases(originals, recipe, reuse_calls=True)
    assert originals == [case, untouched]
    assert cases[1] == untouched
    assert repair_cases(cases, recipe, reuse_calls=True) == cases
    for mid in ('a', 'b'):
        for answer, accepted in [('Bronchitis', True), ('Pneumonia', False)]:
            row = dict(method=mid, results=[dict(url='https://archive.test/biography', snippet=answer)],
                       elapsed_ms=143.0, evidence={'sha256': 'd'*64})
            frozen = deepcopy(row)
            metrics = evaluate(cases[0], row['results'])
            assert task_acceptance(cases[0], dict(row, metrics=metrics), 'answer') is accepted
            assert metrics['labeled_source_hit_5'] == 1
            assert row == frozen


def test_changed_query_requires_new_call_but_accepts_a_call_collected_with_the_repaired_question():
    from lexbench_websearch.simpleqa import repair_cases

    case, recipe = repair_fixture()
    recipe['repairs'][0]['after']['query'] = 'What caused the death of the sculptor in the biography?'
    repaired = repair_cases([case], recipe)[0]
    with pytest.raises(ValueError, match='new provider call'):
        repair_cases([case], recipe, reuse_calls=True)
    assert repair_cases([repaired], recipe, reuse_calls=True) == [repaired]


def test_reference_amendment_reuses_the_same_question_across_annotation_versions():
    from lexbench_websearch.simpleqa import repair_cases

    case, recipe = repair_fixture()
    # References are assessment inputs, never part of the provider request.
    captured = dict(case, reference_urls=['https://archive.test/earlier-source'])
    revised = repair_cases([captured], recipe, reuse_calls=True)[0]
    assert revised['query'] == captured['query']
    assert revised['reference_urls'] == ['https://archive.test/biography']
    assert captured['reference_urls'] == ['https://archive.test/earlier-source']


@pytest.mark.parametrize(('answer', 'supported', 'stale'), [
    ('Alex Stone (also known as A. Stone)', 'A. Stone wrote the book.', 'B. Stone wrote the book.'),
    ('20 m (acceptable range: anything between 19 and 21 m)', 'The mast is 20 m tall.', 'The mast is 25 m tall.'),
])
@pytest.mark.parametrize('copy_is_current', [False, True])
def test_mirror_checks_the_accepted_answer_instead_of_its_annotation_text(answer, supported, stale, copy_is_current):
    from lexbench_websearch.simpleqa import qualify_question_mirror

    common = ' '.join(f'Independent historical record {n} documents the survey and its background.' for n in range(40))
    reference = dict(url='https://en.wikipedia.org/wiki/Survey', title='Survey',
                     article_text=common + ' ' + supported, status=200, body_sha256='a'*64,
                     verified_at='2026-11-17T00:00:00Z')
    copy = dict(reference, url='https://wiki2.org/en/Survey', body_sha256='b'*64,
                article_text=common + ' ' + (supported if copy_is_current else stale))
    result = qualify_question_mirror(reference, copy, answer)
    assert result['article_overlap'] >= .8
    assert result['reference_has_answer'] is True
    assert result['qualified'] is copy_is_current


@pytest.mark.parametrize('policy', ['reference-question-evidence/1', 'reference-question-evidence/2'])
def test_historical_mirror_policies_keep_their_recorded_annotation_behavior(policy):
    from lexbench_websearch.simpleqa import qualify_question_mirror

    text = 'Independent historical background for the author and this edition. ' * 20
    reference = dict(url='https://en.wikipedia.org/wiki/Survey', title='Survey',
                     article_text=text + 'A. Stone wrote the book.', status=200,
                     body_sha256='a'*64, verified_at='2026-11-17T00:00:00Z')
    copy = dict(reference, url='https://wiki2.org/en/Survey', body_sha256='b'*64,
                article_text=text + 'B. Stone wrote the book.')
    result = qualify_question_mirror(reference, copy, 'Alex Stone (also known as A. Stone)', policy=policy)
    assert result['method'] == policy
    assert result['reference_has_answer'] is False
    assert result['qualified'] is True


def test_mirror_retains_the_same_fact_with_discretionary_word_hyphens():
    from lexbench_websearch.simpleqa import qualify_question_mirror

    text = 'Independent historical background for the survey and its measurements. ' * 20
    reference = dict(url='https://en.wikipedia.org/wiki/Survey', title='Survey',
                     article_text=text + 'The mast is 20 metres tall.', status=200,
                     body_sha256='a'*64, verified_at='2026-11-17T00:00:00Z')
    copy = dict(reference, url='https://wiki2.org/en/Survey', body_sha256='b'*64,
                article_text=text + 'The mast is 20 me\u00adtres tall.')
    result = qualify_question_mirror(reference, copy,
                                   '20 metres (acceptable range: anything between 19 and 21 metres)')
    assert result['reference_has_answer'] is result['mirror_has_answer'] is True
    assert result['qualified'] is True


def test_locked_dataset_repair_updates_gold_for_all_saved_methods(tmp_path, monkeypatch):
    import json
    from copy import deepcopy
    from harness.benchmarks import data
    from harness.web_search.revisions import apply_report_revision
    from lexbench_websearch.simpleqa import repair_cases
    from lexbench_websearch.scoring import evaluate

    case, recipe = repair_fixture()
    corrected = repair_cases([case], recipe)[0]
    corrected.update(equivalent_reference_urls=[], equivalent_reference_provenance=[], url_equivalence='document-url/2')
    dataset = tmp_path / 'factual-v1.json'
    dataset.write_text(json.dumps(dict(cases=[corrected], case_repairs=recipe)), encoding='utf-8')
    descriptor = dict(sha256=__import__('hashlib').sha256(dataset.read_bytes()).hexdigest())
    monkeypatch.setattr(data, 'binding', lambda *args: dict(files={'definitions/datasets/factual-v1.json': descriptor}))
    monkeypatch.setattr(data, 'definition_path', lambda *args: dataset)
    profile = dict(cases=[case], methods=[dict(id='a'), dict(id='b')], protocol={'top_k': 5},
                   factual_quality_exclusions=[case['id']], reference_adjudications=[dict(
                       case_id=case['id'], query=case['query'], reference_answer=case['reference_answer'],
                       quality_excluded=True, reason='Wrong original reference', evidence='Original archive')])
    results = [dict(url=corrected['reference_urls'][0], snippet='Bronchitis')]
    rows = [dict(id=mid, method=mid, case_id=case['id'], results=results, metrics=evaluate(case, results), elapsed_ms=123)
            for mid in ('a', 'b')]
    original = deepcopy(rows)
    revision = dict(schema='web-search-assessment-revision/1', provider_calls=0, root_cause=recipe['root_cause'],
                    regression_ids=['generic-document-urls', 'reference-mirror-title-and-lead', 'corrected-factual-reference'],
                    reference_dataset=dict(track='simpleqa-verified', revision='a'*40,
                                           path='datasets/factual-v1.json', sha256=descriptor['sha256']))
    revised, updated, changes = apply_report_revision(profile, rows, revision)
    assert revised['cases'][0]['reference_answer'] == 'Bronchitis'
    assert revised['factual_quality_exclusions'] == revised['reference_adjudications'] == []
    assert profile['factual_quality_exclusions'] == [case['id']]
    assert len(changes) == 2
    assert all(r['metrics']['answer_in_delivered_text_5'] == r['metrics']['labeled_source_hit_5'] == 1 for r in updated)
    assert rows == original
    assert all(r['elapsed_ms'] == 123 for r in updated)
    case['query'] = 'A query from an unrelated task'
    with pytest.raises(ValueError, match='new provider call'):
        apply_report_revision(profile, rows, revision)


def test_rebuild_replays_repairs_from_the_pinned_csv_and_retained_reference_inputs():
    from lexbench_websearch import simpleqa

    raw = csv_fixture(['Who wrote the book?'])
    manifest = simpleqa.source_manifest()
    manifest['sha256'] = simpleqa.digest(raw)
    manifest['selection'].update(source_count=1, selected_count=1)
    original = simpleqa.rebuild_dataset(raw, manifest=manifest)
    case = original['cases'][0]
    _, recipe = repair_fixture()
    item = recipe['repairs'][0]
    item.update(case_id=case['id'], before={k: case[k] for k in item['before']})
    item['after'] = dict(item['before'], query='Who wrote the 1990 edition of the book?', reference_answer='Verified Author')
    expected = simpleqa.extend_reference_urls(dict(original,
        cases=simpleqa.repair_cases(original['cases'], recipe), case_repairs=recipe), [])
    rebuilt = simpleqa.rebuild_dataset(raw, manifest=manifest, reference_dataset=expected, artifacts={})
    assert rebuilt == expected
    assert original['cases'][0]['query'] == 'Who wrote the book?'
    assert rebuilt['cases'][0]['derivation'] == 'Question repaired from cited source; see case_repairs'


@pytest.mark.parametrize('defect', ['unknown-answer', 'duplicate', 'missing-proof', 'unbound-source', 'overlong-query'])
def test_task_repair_rejects_unbound_or_incomplete_authority(defect):
    from lexbench_websearch.simpleqa import repair_cases

    case, recipe = repair_fixture()
    if defect == 'unknown-answer':
        case['reference_answer'] = 'A third unsupported cause'
    elif defect == 'duplicate':
        recipe['repairs'].append(recipe['repairs'][0])
    elif defect == 'missing-proof':
        recipe['repairs'][0]['evidence'] = []
    elif defect == 'unbound-source':
        recipe['repairs'][0]['evidence'][0]['sha256'] = ''
    else:
        recipe['repairs'][0]['after']['query'] = 'x'*129
    with pytest.raises(ValueError):
        repair_cases([case], recipe)


def csv_fixture(questions):
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(
        ["original_index", "problem", "answer", "topic", "answer_type", "urls"]
    )
    for index, question in enumerate(questions):
        writer.writerow(
            [index, question, "Answer", "Topic", "Text", "https://example.org/a)"]
        )
    return text.getvalue().encode()


def test_shared_limit_is_inclusive_unicode_character_count_and_keeps_order():
    cases = eligible_cases(csv_fixture(["A" * 128, "B" * 129, "\u4e2d" * 128, "short"]))
    assert [c["id"] for c in cases] == ["sqv-0", "sqv-2", "sqv-3"]
    assert cases[1]["query"] == "\u4e2d" * 128
    assert cases[0]["reference_urls"] == ["https://example.org/a"]


def test_duplicate_publisher_ids_rejected():
    raw = csv_fixture(["one", "two"]).replace(b"1,two", b"0,two")
    with pytest.raises(ValueError, match="Duplicate"):
        eligible_cases(raw)


def test_builder_rejects_unpinned_csv_before_selection():
    with pytest.raises(ValueError, match="SHA256"):
        rebuild_dataset(csv_fixture(["one"]))


def test_builder_retains_source_artifact_metadata():
    from lexbench_websearch import simpleqa

    raw = csv_fixture(["one"])
    manifest = simpleqa.source_manifest()
    manifest["sha256"] = simpleqa.digest(raw)
    # Correctly hashed but partial publisher inputs must still be rejected.
    with pytest.raises(ValueError, match="Frozen population"):
        simpleqa.rebuild_dataset(raw, manifest=manifest)


def test_mirror_admission_requires_both_matching_title_and_original_lead():
    from lexbench_websearch.simpleqa import mirror_candidates, qualify_mirror

    reference = dict(url='https://en.wikipedia.org/wiki/Example_article', title='Example article',
                     lead='An independently observed encyclopedia article about a particular subject. ' * 10,
                     body_sha256='a'*64, verified_at='2026-11-13T00:00:00Z', status=200)
    candidates = mirror_candidates(reference['url'])
    same = dict(reference, url=candidates[0], body_sha256='b'*64)
    assert qualify_mirror(reference, same)['qualified']
    assert not qualify_mirror(reference, dict(same, title='Another article'))['qualified']
    assert not qualify_mirror(reference, dict(same, lead='Unrelated content about a different person and place. '*20))['qualified']
    for changed in (dict(same, status=403), dict(same, lead='short'),
                    dict(same, body_sha256=''), dict(same, url='https://provider-selected.test/article')):
        with pytest.raises(ValueError):
            qualify_mirror(reference, changed)
    assert mirror_candidates('https://en.wikipedia.org.evil.test/wiki/Example') == []
    assert mirror_candidates('https://example.org/page') == []
    assert mirror_candidates('https://en.wikipedia.org/wiki/Special:Search') == []
    assert mirror_candidates('https://en.wikipedia.org/wiki/Star_Trek:_First_Contact')


def test_article_observations_exclude_navigation_tables_and_later_sections():
    from harness.web_search.revisions import article_observation

    paragraph = 'This independently observed article paragraph identifies the subject.'
    html = ('<nav><p>Navigation text</p></nav><h1>Article</h1>'
            '<div class="mw-parser-output"><table><tr><td>Metadata</td></tr></table>'
            f'<p>{paragraph}</p><h2>History</h2><p>Later section</p></div>').encode()
    markdown = ('Site navigation\n\n# Article\n\n## From Wikipedia, the free encyclopedia\n\n'
                '| Field | Value |\n| --- | --- |\n| Metadata | Other |\n\n'
                + paragraph + '\n\n## History\n\nLater section').encode()
    options = dict(status=200,verified_at='2026-11-13T00:00:00Z')
    a = article_observation(html,'https://en.wikipedia.org/wiki/Article',**options)
    b = article_observation(markdown,'https://wiki2.org/en/Article',media_type='text/markdown',**options)
    assert a['title'] == b['title'] == 'Article'
    assert a['lead'] == b['lead'] == paragraph
    assert a['body_sha256'] != b['body_sha256']
    plain = ('Article\n\n' + paragraph + '\n\n## History\n\nLater section').encode()
    fallback = article_observation(plain,'https://en.wikipedia.org/wiki/Article',
                                  media_type='text/markdown',source_title='Article',**options)
    assert fallback['title'] == 'Article' and fallback['lead'] == paragraph
    wrong_title = article_observation(plain,'https://en.wikipedia.org/wiki/Article',
                                     media_type='text/markdown',source_title='Different',**options)
    assert wrong_title['title'] == '' and wrong_title['lead'] == ''


def test_reference_extension_preserves_publisher_data_and_shares_aliases_by_document():
    from copy import deepcopy
    from lexbench_websearch.simpleqa import extend_reference_urls, mirror_candidates
    reference = dict(url='https://en.wikipedia.org/wiki/Article',title='Article',
                     lead='A reference paragraph identifying the subject and its independently documented history. '*6,
                     body_sha256='a'*64,status=200,verified_at='2026-11-13T00:00:00Z')
    reference['article_text'] = reference['lead']
    mirror = dict(reference,url='https://wiki2.org/en/Article',body_sha256='b'*64)
    dataset = dict(cases=[dict(id=str(i),query='Question '+str(i),reference_answer='Answer '+str(i),
                              reference_urls=[reference['url']]) for i in range(2)])
    original = deepcopy(dataset)
    extended = extend_reference_urls(dataset,[reference,mirror])
    from lexbench_websearch.contracts import validate_cases

    validate_cases(extended['cases'])
    assert dataset == original
    for case, previous in zip(extended['cases'], original['cases'], strict=True):
        assert {key:case[key] for key in previous} == previous
        assert case['equivalent_reference_urls'] == [mirror['url']]
        assert case['equivalent_reference_provenance'][0]['reference_url'] == reference['url']
    assert not extend_reference_urls(dataset,[mirror])['cases'][0]['equivalent_reference_urls']
    audit = extended['reference_equivalence_audit']
    assert len(audit) == 18
    assert {entry['outcome'] for entry in audit} == {'verified', 'capture_failed'}
    assert {entry['url'] for entry in audit} == set(mirror_candidates(reference['url']))
    with pytest.raises(ValueError,match='independently derived'):
        extend_reference_urls(dataset,[dict(mirror,url='https://provider-result.test/document')])
    for key,value in [('equivalent_reference_provenance',[]),('equivalent_reference_urls',[mirror['url'],mirror['url']])]:
        modified=deepcopy(extended['cases'])
        modified[0][key]=value
        with pytest.raises(ValueError,match='provenance'):
            validate_cases(modified)
    modified=deepcopy(extended['cases'])
    modified[0]['equivalent_reference_provenance'][0]['title_matches']=False
    with pytest.raises(ValueError,match='proof'):
        validate_cases(modified)


def test_mirror_rejections_and_failed_fetches_remain_auditable():
    from lexbench_websearch.simpleqa import extend_reference_urls, mirror_candidates

    reference = dict(url='https://en.wikipedia.org/wiki/Article', title='Article',
                     lead='A documented subject with independently published history. ' * 10,
                     body_sha256='a' * 64, status=200, verified_at='2026-11-16T00:00:00Z')
    reference['article_text'] = reference['lead']
    urls = mirror_candidates(reference['url'])
    observations = [reference, dict(reference, url=urls[0], title='Other'),
                    dict(reference, url=urls[1], article_text='Different material concerning another topic. ' * 20),
                    dict(reference, url=urls[2], status=503)]
    data = dict(cases=[dict(id='q', query='Question', reference_answer='Answer', reference_urls=[reference['url']])])
    extended = extend_reference_urls(data, observations)
    assert not extended['cases'][0]['equivalent_reference_urls']
    assert [item['outcome'] for item in extended['reference_equivalence_audit'][:3]] == [
        'title_mismatch', 'evidence_mismatch', 'capture_failed']


@pytest.mark.parametrize('change', ['none', 'missing_answer', 'stale', 'lookalike'])
def test_full_mirror_proof_is_question_scoped_and_replayable(change):
    from copy import deepcopy
    from lexbench_websearch.inputs import digest
    from lexbench_websearch.simpleqa import extend_reference_urls, validate_reference_evidence, mirror_candidates

    lead = 'A documented article with matching introductory material. ' * 100
    reference = ('# Article\n\n' + lead + '\n\nSupporting fact: sapphire.').encode()
    mirror = reference if change not in ('missing_answer', 'stale') else ('# Article\n\n' + lead).encode()
    urls = ['https://en.wikipedia.org/wiki/Article', 'https://wiki2.org/en/Article']
    if change == 'lookalike':
        urls[1] = 'https://wiki2.org.attacker.test/en/Article'
    artifacts = {'reference.md': reference, 'mirror.md': mirror}
    observations = [dict(url=url, status=200, verified_at='2026-11-16T00:00:00Z',
        body_sha256=digest(artifacts[path]), media_type='text/markdown',
        body_artifact=dict(path=path, sha256=digest(artifacts[path])))
        for url, path in zip(urls, artifacts, strict=True)]
    data = dict(cases=[dict(id='q', query='What is the fact?', reference_answer='sapphire', reference_urls=[urls[0]]),
                       dict(id='other', query='Another fact?', reference_answer='absent', reference_urls=[urls[0]])])
    if change == 'lookalike':
        with pytest.raises(ValueError, match='independently derived'):
            extend_reference_urls(data, observations, artifacts=artifacts)
        return
    observations += [dict(url=url, status=0, verified_at='2026-11-16T00:00:00Z')
                     for url in mirror_candidates(urls[0]) if url != urls[1]]
    result = extend_reference_urls(data, observations, artifacts=artifacts)
    assert bool(result['cases'][0]['equivalent_reference_urls']) == (change == 'none')
    assert result['cases'][1]['equivalent_reference_urls'] == [urls[1]]
    assert len(result['reference_equivalence_audit']) == 18
    validate_reference_evidence(result, artifacts)
    for policy in (None, 'literal/1'):
        changed = deepcopy(result)
        if policy is None:
            changed['cases'][0].pop('url_equivalence')
        else:
            changed['cases'][0]['url_equivalence'] = policy
        with pytest.raises(ValueError, match='admission'):
            validate_reference_evidence(changed, artifacts)
    incomplete = deepcopy(result)
    incomplete['reference_equivalence_audit'].pop()
    with pytest.raises(ValueError, match='audit'):
        validate_reference_evidence(incomplete, artifacts)
    modified = dict(artifacts, **{'mirror.md': b'changed'})
    with pytest.raises(ValueError, match='hash'):
        validate_reference_evidence(result, modified)


@pytest.mark.parametrize('complete', [False, True])
def test_short_answer_token_requires_full_article_coverage(complete):
    from lexbench_websearch.simpleqa import qualify_question_mirror
    original = ' '.join('Distinct reference sentence %d about the composer.' % n for n in range(100))
    candidate = original if complete else 'An unrelated article about 2 performances. ' * 15
    def observed(url, text):
        return dict(url=url, title='Composer', article_text=text + ' Answer: 2.', status=200,
                    verified_at='2026-11-17T00:00:00Z', body_sha256='a'*64)
    proof = qualify_question_mirror(observed('https://en.wikipedia.org/wiki/Composer', original),
                                   observed('https://wiki2.org/en/Composer', candidate), '2')
    assert proof['mirror_has_answer']
    assert proof['qualified'] is complete
