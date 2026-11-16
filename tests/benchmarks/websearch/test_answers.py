"""Generated-answer correctness and citation evidence remain separate checks."""

import hashlib
import json

import pytest

from lexbench_websearch import answers


def test_official_grader_uses_hash_pinned_template_without_executing_upstream(monkeypatch):
    source = (
        b'raise RuntimeError("Must never execute")\n'
        b'GRADER_TEMPLATE = "Question: {question}\\nTarget: {target}\\n'
        b'Answer: {predicted_answer}".strip()\n'
    )
    monkeypatch.setitem(answers.SIMPLEQA_UPSTREAM, "sha256", hashlib.sha256(source).hexdigest())
    case = dict(id="q", query="Question text", reference_answer="Target text")
    messages = answers.factual_messages(case, "Predicted {answer}", source)
    assert messages == [
        dict(
            role="user",
            content="Question: Question text\nTarget: Target text\nAnswer: Predicted {answer}",
        )
    ]
    with pytest.raises(ValueError, match="hash mismatch"):
        answers.factual_messages(case, "Answer", source + b"\n")
    assert [answers.factual_grade(v) for v in ("A", " B\n", "C")] == [
        "CORRECT",
        "INCORRECT",
        "NOT_ATTEMPTED",
    ]
    for invalid in ("", "CORRECT", "The answer is A", "AB", "not attempted"):
        with pytest.raises(ValueError):
            answers.factual_grade(invalid)


def packet():
    case = dict(id="q", query="Who wrote the document?")
    row = dict(
        answer="Ada wrote it [引用:7]. More detail [引用:99]. See [source](http://www.example.test/doc).",
        results=[
            dict(
                url="https://example.test/doc",
                citation_id=7,
                title="Document",
                snippet="Ada wrote the document.",
                summary="Extra original text.",
                content="Full body must stay out",
            )
        ],
    )
    return answers.answer_packet(case, row)


def test_citations_resolve_native_ids_and_links_without_borrowing_page_body():
    value = packet()
    assert [c["in_results"] for c in value["citations"]] == [True, False, True]
    assert "content" not in value["citations"][0]["source"]
    user = json.loads(answers.answer_messages(value)[1]["content"])
    assert set(user) == {"case_id", "query", "answer"}
    selected = json.loads(answers.answer_messages(value, citations=True)[1]["content"])
    assert selected["citations"][0]["claim_context"] == value["citations"][0]["claim_context"]
    assert selected["citations"][0]["source_fields"][1] == dict(
        id="0:snippet", field="snippet", text="Ada wrote the document."
    )
    assert "content" not in json.dumps(selected)
    assert answers.citation_inventory("![image](https://example.test/doc)", []) == []
    inventory = answers.citation_inventory(
        "Claim [引用：7，8]",
        [
            dict(url="https://example.test/a", citation_id="7"),
            dict(url="https://example.test/b", citation_id=8),
        ],
    )
    assert len(inventory) == 2 and all(c["in_results"] for c in inventory)
    with pytest.raises(ValueError, match="different documents"):
        answers.citation_inventory(
            "Claim [引用:1]",
            [
                dict(url="https://example.test/a", citation_id=1),
                dict(url="https://example.test/b", citation_id=1),
            ],
        )


def test_citation_support_requires_complete_original_source_references():
    value = packet()
    judgments = [dict(citation_index=i, supported=False, source_refs=[]) for i in range(3)]
    judgments[0].update(supported=True, source_refs=["0:snippet"])
    scored = answers.citation_grades(value, json.dumps(dict(judgments=judgments)))
    assert scored[0]["claim"] == value["citations"][0]["claim_context"]
    assert scored[0]["evidence"] == [
        dict(source_ref="0:snippet", field="snippet", quote="Ada wrote the document.")
    ]
    for changed in (
        judgments[:-1],
        [judgments[0]] * 3,
        [dict(judgments[0], source_refs=["0:content"]), *judgments[1:]],
        [dict(judgments[0], source_refs=["invented"]), *judgments[1:]],
        [judgments[0], dict(judgments[1], supported=True, source_refs=["0:snippet"]), judgments[2]],
        [dict(judgments[0], evidence={"quote": "Invented quotation"}), *judgments[1:]],
    ):
        with pytest.raises(ValueError):
            answers.citation_grades(value, json.dumps(dict(judgments=changed)))


def test_scenario_answer_score_binds_complete_native_answer_and_valid_grade():
    value = packet()
    judgment = dict(grade=3, reason="Names the author")
    assert answers.scenario_grade(value, json.dumps(judgment)) == dict(judgment, evidence=value["answer"])
    for altered in (
        dict(judgment, grade=True),
        dict(judgment, grade=4),
        dict(judgment, evidence="Invented answer"),
        dict(judgment, reason=""),
    ):
        with pytest.raises(ValueError):
            answers.scenario_grade(value, json.dumps(altered))
    with pytest.raises(ValueError):
        answers.scenario_grade(dict(answer=""), json.dumps(judgment))
    assert answers.scenario_grade(dict(answer=""), json.dumps(dict(grade=0, reason="No answer")))["evidence"] == ""


@pytest.mark.parametrize('text', [
    'Source: <https://example.test/doc>',
    '[Source](https://example.test/doc)',
    '[Source][ref]\n\n[ref]: https://example.test/doc',
])
def test_native_markdown_links_keep_exact_occurrences_and_source_identity(text):
    citations = answers.citation_inventory(text, [dict(url='https://example.test/doc')])
    assert len(citations) == 1 and citations[0]['in_results']
    start, end = citations[0]['span']
    assert text[start:end] == citations[0]['marker']


def test_one_based_citations_resolve_zero_based_webpage_source_uris_without_changing_rank():
    results = [
        dict(url="https://source.test/a", citation_id="https://api.example.test/v1/#WebPages.0"),
        dict(url="https://source.test/b", citation_id="https://api.example.test/v1/#WebPages.3"),
    ]
    before = json.dumps(results, sort_keys=True)
    citations = answers.citation_inventory(
        "First [引用:1]; fourth [引用:4]; absent [引用:2].", results
    )
    assert [item["in_results"] for item in citations] == [True, True, False]
    assert [item["source"]["url"] if item["source"] else None for item in citations] == [
        "https://source.test/a",
        "https://source.test/b",
        None,
    ]
    assert json.dumps(results, sort_keys=True) == before


def test_citation_source_schemas_cannot_silently_resolve_to_different_documents():
    with pytest.raises(ValueError, match="different documents"):
        answers.citation_inventory(
            "A claim [引用:1]",
            [
                dict(
                    url="https://source.test/a",
                    citation_id="https://api.example.test/v1/#WebPages.0",
                ),
                dict(url="https://source.test/b", citation_id=1),
            ],
        )
    for malformed in [
        "WebPages.0",
        "https://api.example.test/#WebPages.-1",
        "https://api.example.test/#WebPages.00",
        "https://api.example.test/#other.0",
    ]:
        assert not answers.citation_inventory(
            "A claim [引用:1]", [dict(url="https://source.test/a", citation_id=malformed)]
        )[0]["in_results"]


@pytest.mark.parametrize("style", ["inline", "reference"])
def test_repeated_link_destinations_keep_each_occurrence_and_claim(style):
    if style == "inline":
        text = "Ada wrote it [first](https://test.example/a). Bob edited it [other](https://test.example/b). Eve wrote it [last](https://test.example/a)."
    else:
        text = "Ada wrote it [first][a]. Bob edited it [other][b]. Eve wrote it [last][a].\n\n[a]: https://test.example/a\n[b]: https://test.example/b"
    results = [
        dict(url="https://test.example/a", title="Authors", snippet="Ada wrote it"),
        dict(url="https://test.example/b", title="Editors", snippet="Bob edited it"),
    ]
    value = answers.answer_packet(dict(id="q", query="Who?"), dict(answer=text, results=results))
    citations = value["citations"]
    assert [c["source"]["url"] for c in citations] == [
        results[0]["url"],
        results[1]["url"],
        results[0]["url"],
    ]
    assert all(text[c["span"][0] : c["span"][1]] == c["marker"] for c in citations)
    assert citations[0]["span"][1] < citations[1]["span"][0] < citations[2]["span"][0]
    judgments = [dict(citation_index=i, supported=False, source_refs=[]) for i in range(3)]
    scored = answers.citation_grades(value, json.dumps(dict(judgments=judgments)))
    assert "Ada wrote it" in scored[0]["claim"]
    assert "Eve wrote it" in scored[2]["claim"]
    judgments[2].update(supported=True, source_refs=["0:snippet"])
    with pytest.raises(ValueError, match="source references"):
        answers.citation_grades(value, json.dumps(dict(judgments=judgments)))


def test_explanation_is_optional_but_evidence_ids_and_verdict_are_required():
    value = packet()
    judgments = [dict(citation_index=i, supported=False, source_refs=[]) for i in range(3)]
    judgments[0].update(supported=True, source_refs=["0:snippet"])
    original = json.dumps(judgments, sort_keys=True)
    assert answers.citation_grades(value, json.dumps(dict(judgments=judgments)))[0]["supported"]
    assert json.dumps(judgments, sort_keys=True) == original
    for field in ("supported", "source_refs"):
        changed = json.loads(original)
        del changed[0][field]
        with pytest.raises(ValueError):
            answers.citation_grades(value, json.dumps(dict(judgments=changed)))


@pytest.mark.parametrize('text', ['`[引用:1]`', '```text\n[引用:1]\n```', r'\[引用:1]', '    [引用:1]'])
def test_literal_citation_examples_do_not_become_evidence(text):
    assert answers.citation_inventory(text, [dict(url='https://example.test/a', citation_id=1)]) == []


@pytest.mark.parametrize('text', ['A fact [引用:1]', '> A fact [引用:1]', '- A fact [引用:1]'])
def test_rendered_native_citations_preserve_original_spans(text):
    citation, = answers.citation_inventory(text, [dict(url='https://example.test/a', citation_id=1)])
    assert citation['in_results']
    assert text[slice(*citation['span'])] == '[引用:1]'


@pytest.mark.parametrize('label', ['Paris', '**Paris**', '`Paris`', 'Paris is the capital of France'])
def test_cited_claim_contains_visible_link_facts(label):
    text = 'The capital is [' + label + '](https://example.test/a).'
    citation, = answers.citation_inventory(text, [dict(url='https://example.test/a')])
    assert 'Paris' in citation['claim_context']
    assert text[slice(*citation['span'])] == citation['marker']


def test_native_marker_in_link_label_counts_one_link():
    citation, = answers.citation_inventory('Fact [引用:1](https://example.test/a)', [dict(url='https://example.test/a', citation_id=1)])
    assert citation['in_results']


def test_linked_subject_keeps_its_following_predicate_without_the_next_sentence():
    citation, = answers.citation_inventory('[Paris](https://example.test/a) is the capital. Another claim.',
                                           [dict(url='https://example.test/a')])
    assert citation['claim_context'] == 'Paris is the capital.'


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_rendered_duplicate_of_fenced_text_binds_its_real_markdown_block(newline):
    text = newline.join(['```text', 'A fact [引用:1]', '```', '', 'A fact [引用:1]'])
    citation, = answers.citation_inventory(text, [dict(url='https://source.test/a', citation_id=1)])
    assert citation['span'][0] == text.rindex('[引用:1]')
    assert citation['claim_context'] == 'A fact'


def test_consecutive_linked_subjects_keep_separate_sentence_claims():
    text = '[Paris](https://source.test/paris) is the capital of France. [Rome](https://source.test/rome) is the capital of Italy.'
    citations = answers.citation_inventory(text, [dict(url='https://source.test/'+city) for city in ['paris','rome']])
    assert [c['claim_context'] for c in citations] == ['Paris is the capital of France.', 'Rome is the capital of Italy.']


def test_link_label_with_complete_sentence_does_not_borrow_the_next_sentence():
    citation, = answers.citation_inventory('[Paris is in France.](https://source.test/paris) Rome is in Italy.', [dict(url='https://source.test/paris')])
    assert citation['claim_context'] == 'Paris is in France.'


def test_multiple_links_in_a_sentence_share_the_complete_visible_claim():
    text='[Paris](https://source.test/paris) is the capital of [France](https://source.test/france).'
    citations=answers.citation_inventory(text,[dict(url='https://source.test/'+c) for c in ['paris','france']])
    assert [c['claim_context'] for c in citations]==['Paris is the capital of France.']*2


def test_citation_only_paragraph_has_no_inherited_claim_and_cannot_be_supported():
    text='Ada wrote it [引用:1].\n\n[引用:1]'
    packet=answers.answer_packet(dict(id='q',query='Who wrote it?'),dict(answer=text,results=[dict(url='https://source.test/a',citation_id=1,snippet='Ada wrote it.')]))
    assert [c['claim_context'] for c in packet['citations']]==['Ada wrote it.','']
    judgments=[dict(citation_index=0,supported=True,source_refs=['0:snippet']),dict(citation_index=1,supported=True,source_refs=['1:snippet'])]
    with pytest.raises(ValueError,match='source references'):
        answers.citation_grades(packet,json.dumps(dict(judgments=judgments)))


def test_local_claim_keeps_abbreviations_and_inline_formatting():
    text='The U.S. capital is [**Washington**](https://source.test/a).'
    citation,=answers.citation_inventory(text,[dict(url='https://source.test/a')])
    assert citation['claim_context']=='The U.S. capital is Washington.'


@pytest.mark.parametrize('space', ['', ' ', '  ', '\n'])
@pytest.mark.parametrize('following', ['', ' ', '\n'])
def test_native_sentence_final_citation_does_not_include_the_next_sentence(space, following):
    text = 'Alpha.' + space + '[引用：1]' + following + 'Beta.'
    citation, = answers.citation_inventory(text, [dict(url='https://source.test/a', citation_id=1)])
    assert citation['claim_context'] == 'Alpha.'


@pytest.mark.parametrize('first', [
    'She said "First claim."', 'She said “First claim.”',
    '(First claim.)', '("First claim.")', '[First claim.]', '『First claim。』',
])
def test_citation_claim_starts_after_quoted_or_parenthesized_sentence(first):
    text = first + ' Second claim [引用:1]'
    citation, = answers.citation_inventory(text, [dict(url='https://source.test/a', citation_id=1)])
    assert citation['claim_context'] == 'Second claim'


@pytest.mark.parametrize('label', [
    '<span title="Hidden assertion">Paris</span>', '**Paris**',
    '[Paris](https://hidden.test/assertion)', '![Paris](https://hidden.test/nested.png)',
])
def test_image_alternative_text_uses_rendered_children_without_hidden_markup(label):
    text = '![' + label + '](https://source.test/image.png)[引用:1]'
    citation, = answers.citation_inventory(text, [dict(url='https://source.test/a', citation_id=1)])
    assert citation['claim_context'] == 'Paris'


@pytest.mark.parametrize('separator', [
    '<br>', '<br/>', '<br />', '<BR>', '<br title="Hidden assertion">', '</br>',
])
def test_inline_html_line_break_is_a_visible_separator(separator):
    text = 'Paris' + separator + 'is the capital of France. [引用:1]'
    citation, = answers.citation_inventory(text, [dict(url='https://source.test/a', citation_id=1)])
    assert citation['claim_context'] == 'Paris is the capital of France.'


@pytest.mark.parametrize('opening,closing', [
    ('script', 'script'), ('style', 'style'), ('template', 'template'),
    ('span hidden', 'span'), ('span style="display:none"', 'span'),
    ('span style="visibility:hidden"', 'span'),
    ('span style="display:inline-block;content-visibility:hidden"', 'span'),
    ('noscript', 'noscript'), ('title', 'title'),
    ('span style="display:none!important;display:block"', 'span'),
])
def test_hidden_html_subtrees_do_not_contribute_claims_or_citations(opening, closing):
    text = ('Visible claim <' + opening + '>hidden assertion [引用:2] '
            '[hidden link](https://source.test/b)</' + closing + '> [引用:1]')
    citations = answers.citation_inventory(text, [dict(url='https://source.test/'+c, citation_id=i)
                                                 for i, c in enumerate(['a', 'b'], 1)])
    assert len(citations) == 1
    assert citations[0]['claim_context'] == 'Visible claim'
    assert text[slice(*citations[0]['span'])] == '[引用:1]'
    assert answers.citation_inventory('Prefix <' + opening + '>hidden [引用:1]</' + closing + '>',
                                      [dict(url='https://source.test/a', citation_id=1)]) == []


def test_hidden_contents_in_link_labels_preserve_visible_text_and_original_span():
    text = '[Before<script>hidden assertion</script>after](https://source.test/a)'
    citation, = answers.citation_inventory(text, [dict(url='https://source.test/a', citation_id=1)])
    assert citation['claim_context'] == 'Beforeafter'
    assert text[slice(*citation['span'])] == text
    assert answers.citation_inventory('[<span hidden>hidden</span>](https://source.test/a)', []) == []


@pytest.mark.parametrize('reset', ['visible', 'initial'])
def test_html_visibility_state_preserves_explicitly_visible_children_and_hidden_breaks(reset):
    text = '<span style="visibility:hidden">hidden <span style="visibility:'+reset+'">Visible [引用:1]</span></span>'
    # A leading raw HTML block is not an inline Markdown claim.
    text = 'Prefix ' + text
    citation, = answers.citation_inventory(text, [dict(url='https://source.test/a', citation_id=1)])
    assert citation['claim_context'] == 'Prefix Visible'
    citation, = answers.citation_inventory('Pa<br hidden>ris [引用:1]', [dict(url='https://source.test/a', citation_id=1)])
    assert citation['claim_context'] == 'Paris'


@pytest.mark.parametrize('markup,claim', [
    ('<span title="Paris is in France"></span>', ''),
    ('<span title="Hidden assertion">Paris is in France.</span>', 'Paris is in France.'),
])
def test_citation_support_uses_visible_inline_html_text(markup, claim):
    packet = answers.answer_packet(dict(id='q', query='Where is Paris?'), dict(
        answer=markup+'[引用:1]',
        results=[dict(url='https://source.test/a', citation_id=1, snippet='Paris is in France.')]))
    assert packet['citations'][0]['claim_context'] == claim
    response = json.dumps(dict(judgments=[dict(citation_index=0, supported=True, source_refs=['0:snippet'])]))
    if claim:
        assert answers.citation_grades(packet, response)
    else:
        with pytest.raises(ValueError, match='source references'):
            answers.citation_grades(packet, response)


@pytest.mark.parametrize('answer,claims', [
    ('Claim <a href="https://source.test/a">source</a>.', ['Claim source.']),
    ('<div><p>First <a href="https://source.test/a">source</a>.</p><p>Second <a href="https://source.test/a">link</a>.</p></div>', ['First source.', 'Second link.']),
    ('Claim <a href="https://source.test/a">a <em>formatted</em> source</a>.', ['Claim a formatted source.']),
    ('Claim <a href="https://source.test/a">source', ['Claim source']),
    ('Claim <a hidden href="https://source.test/a">source</a>.', []),
    ('Claim <a style="visibility:hidden" href="https://source.test/a"><span style="visibility:visible">source</span></a>.', ['Claim source.']),
    ('Claim <a href="https://source.test/a"><img src="badge.png" alt="source"></a>.', ['Claim source.']),
    ('Claim <a href="https://source.test/a"><img hidden alt="source"></a>.', []),
    ('Claim <a style="display:none" href="https://source.test/a"><span style="visibility:visible">source</span></a>.', []),
    ('<div><a href="https://source.test/a">First claim\n\nSecond claim</a></div>', ['First claim Second claim']),
    ('<div hidden>Claim <a href="https://source.test/a">source</a>.</div>', []),
    ('Claim <span style="display:none"><a href="https://source.test/a">source</a></span>.', []),
])
def test_visible_html_links_keep_original_spans_and_rendered_local_claims(answer, claims):
    citations = answers.citation_inventory(answer, [dict(url='https://source.test/a', snippet='Source text')])
    assert [c['claim_context'] for c in citations] == claims
    for citation in citations:
        begin, end = citation['span']
        assert answer[begin:end] == citation['marker']
        assert citation['marker'].startswith('<a ')
        if '</a>' in answer:
            assert citation['marker'].endswith('</a>')
        assert citation['in_results']


def test_html_links_outside_results_still_count_in_the_support_denominator():
    citation, = answers.citation_inventory('Claim <a href="https://missing.test/a">source</a>.', [])
    assert citation['claim_context'] == 'Claim source.'
    assert not citation['in_results'] and citation['source'] is None


@pytest.mark.parametrize('answer,context', [
    ('<div>Claim.</div><div>[引用:1]</div>', ''),
    ('<div><p>Claim.</p><p>[引用:1]</p></div>', ''),
    ('<div><p>Claim. [引用:1]</p></div>', 'Claim.'),
    ('<div><img alt="Claim">[引用:1]</div>', 'Claim'),
    ('<div><img style="display:none" alt="Claim">[引用:1]</div>', ''),
])
def test_html_native_markers_use_only_their_own_visible_block(answer, context):
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1', snippet='Claim.')])
    assert citation['claim_context'] == context
    assert citation['marker'] == '[引用:1]'


@pytest.mark.parametrize('answer', [
    '<pre><code>[引用:1]</code></pre>', '<code>[引用:1]</code>',
    '<kbd>[引用:1]</kbd>', '<samp>[引用:1]</samp>',
    '`[引用:1]`', '```text\n[引用:1]\n```',
])
def test_native_markers_in_literal_code_are_not_citations(answer):
    assert answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')]) == []


@pytest.mark.parametrize('link', [
    '<a href="https://source.test/a"><span style="visibility:visible">Paris</span></a>',
    '[<span style="visibility:visible">Paris</span>](https://source.test/a)',
])
@pytest.mark.parametrize('hidden,expected', [('visibility:hidden', True), ('display:none', False)])
def test_both_link_syntaxes_follow_the_visibility_of_their_label(link, hidden, expected):
    answer = 'Prefix <span style="' + hidden + '">' + link + '</span>.'
    citations = answers.citation_inventory(answer, [dict(url='https://source.test/a')])
    assert bool(citations) is expected
    if expected:
        assert citations[0]['claim_context'] == 'Prefix Paris.'
        start, end = citations[0]['span']
        assert citations[0]['marker'] == answer[start:end]


def test_html_code_text_stays_visible_without_interpreting_its_example_marker():
    answer = '<p>Example <code>[引用:1]</code>. Claim.[引用:2]</p>'
    citations = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1'),
        dict(url='https://source.test/b', citation_id='2')])
    assert [c['marker'] for c in citations] == ['[引用:2]']
    assert citations[0]['claim_context'] == 'Claim.'


@pytest.mark.parametrize('wrapper', ['textarea', 'xmp'])
def test_html_text_containers_keep_literal_markup_without_inventing_links(wrapper):
    answer = f'<{wrapper}><a href="https://source.test/a">Claim [引用:1]</a></{wrapper}><p>Real <a href="https://source.test/a">source</a>.</p>'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])
    assert citation['marker'] == '<a href="https://source.test/a">source</a>'
    assert citation['claim_context'] == 'Real source.'


@pytest.mark.parametrize('answer,expected', [
    ('<p hidden>Hidden<p>Visible claim [引用:1]</p>', ['Visible claim']),
    ('<ul><li hidden>Hidden<li>Visible claim [引用:1]</ul>', ['Visible claim']),
    ('<ul hidden><li hidden>Hidden<li>Still hidden [引用:1]</ul>', []),
    ('<ul><li hidden>Hidden<ul><li>Still hidden [引用:1]</li></ul></li></ul>', []),
    ('<dl><dt hidden>Hidden<dd>Visible claim [引用:1]</dl>', ['Visible claim']),
])
def test_omitted_html_end_tags_respect_sibling_and_container_scopes(answer, expected):
    citations = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])
    assert [c['claim_context'] for c in citations] == expected


def test_adjacent_html_blocks_inside_one_link_keep_word_boundaries():
    answer = '<div><a href="https://source.test/a"><p>Paris is</p><p>in France.</p></a></div>'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a')])
    assert citation['claim_context'] == 'Paris is in France.'
    assert citation['marker'] == '<a href="https://source.test/a"><p>Paris is</p><p>in France.</p></a>'


def test_inline_textarea_markup_is_literal_while_an_outside_markdown_link_is_real():
    answer = 'Example <textarea><a href="https://source.test/a">Claim [引用:1]</a></textarea>. Real [source](https://source.test/a).'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])
    assert citation['marker'] == '[source](https://source.test/a)'
    assert citation['claim_context'] == 'Real source.'


def test_html5_reconstructed_hidden_anchor_stays_hidden():
    answer = '<p><a hidden href="https://source.test/a">Hidden<div>Visible</div>'
    assert answers.citation_inventory(answer, [dict(url='https://source.test/a')]) == []


def test_link_starting_with_a_block_does_not_borrow_the_preceding_block_text():
    answer = '<div>Unrelated<a href="https://source.test/a"><p>Supported claim.</p></a></div>'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a')])
    assert citation['claim_context'] == 'Supported claim.'


@pytest.mark.parametrize('attributes,claim', [
    ('hidden', 'Claim'), ('style="display:none"', 'Claim'),
    ('style="visibility:hidden"', 'Claim'), ('', ''),
])
def test_only_visible_rules_separate_claims(attributes, claim):
    answer = '<div>Claim<hr ' + attributes + '>[引用:1]</div>'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])
    assert citation['claim_context'] == claim


@pytest.mark.parametrize('body,claim', [
    ('<tr><td>Alpha.</td></tr><tr><td>[引用:1]</td></tr>', ''),
    ('<tr><td>Alpha.</td><td>[引用:1]</td></tr>', 'Alpha.'),
])
def test_table_citations_stay_in_their_row(body, claim):
    citation, = answers.citation_inventory('<table>' + body + '</table>', [dict(url='https://source.test/a', citation_id='1')])
    assert citation['claim_context'] == claim


def test_nested_anchor_opening_closes_previous_link_and_original_span():
    first = '<a href="https://a.example">First. '
    second = '<a href="https://b.example">Second.</a>'
    citations = answers.citation_inventory(first + second + ' Third.', [dict(url='https://a.example'), dict(url='https://b.example')])
    assert [c['claim_context'] for c in citations] == ['First.', 'Second.']
    assert [c['marker'] for c in citations] == [first, second]
    assert [c['span'] for c in citations] == [[0, len(first)], [len(first), len(first + second)]]


def test_markdown_link_also_closes_an_open_html_anchor():
    first = '<a href="https://a.example">First. '
    second = '[Second.](https://b.example)'
    citations = answers.citation_inventory(first + second + ' Third.', [dict(url='https://a.example'), dict(url='https://b.example')])
    assert [c['marker'] for c in citations] == [first, second]
    assert [c['claim_context'] for c in citations] == ['First.', 'Second.']


@pytest.mark.parametrize('style,visible', [
    ('display:none;display:invalid', False),
    ('display:/*comment*/none', False),
    ('display:none;display:inline', True),
    ('visibility:hidden;visibility:invalid', False),
    ('visibility:/*comment*/hidden', False),
    ('content-visibility:hidden;content-visibility:invalid', True),
    ('display:none!important;display:block', False),
    ('display:none;display:inline flow-root', True),
    (r'display:n\6f ne', False),
    ('display:none;display:run-in', False),
    ('display:none;display:ruby-base', False),
])
def test_inline_visibility_obeys_css_declarations(style, visible):
    answer = '<div><span style="' + style + '">Claim [引用:1]</span></div>'
    assert bool(answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])) is visible


def test_duplicate_href_uses_the_first_browser_target_and_its_evidence():
    answer = '<a href="https://a.example" href="https://b.example">Claim</a>'
    first, second = dict(url='https://a.example', snippet='First evidence'), dict(url='https://b.example', snippet='Second evidence')
    citation, = answers.citation_inventory(answer, [first, second])
    assert citation['source']['url'] == first['url']
    assert citation['source']['snippet'] == first['snippet']


@pytest.mark.parametrize('attributes,markers', [('', ['[引用:1]']), ('open', ['[引用:1]', '[引用:2]'])])
def test_disclosure_citations_follow_the_initial_open_state(attributes, markers):
    answer = '<details ' + attributes + '><summary>Sources [引用:1]</summary><p>Hidden claim [引用:2]</p></details>'
    citations = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1'), dict(url='https://source.test/b', citation_id='2')])
    assert [c['marker'] for c in citations] == markers


def test_closed_disclosure_exposes_only_its_first_summary_link():
    answer = '<details>Hidden <a href="https://source.test/a">before</a><summary><a href="https://source.test/a">Visible</a></summary><summary><a href="https://source.test/a">Second</a></summary><a href="https://source.test/a">After</a></details>'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a')])
    assert citation['marker'] == '<a href="https://source.test/a">Visible</a>'
    assert citation['claim_context'] == 'Visible'


def test_html_target_uses_browser_attribute_entity_rules():
    url = 'https://source.test/a?ref=&notit;'
    citation, = answers.citation_inventory('<a href="' + url + '">Claim</a>', [dict(url=url, snippet='Correct evidence')])
    assert citation['in_results']
    assert citation['source']['url'] == url


def test_anchor_without_a_target_does_not_hide_a_native_citation():
    citation, = answers.citation_inventory('<a>Claim [引用:1]</a>', [dict(url='https://source.test/a', citation_id='1')])
    assert citation['marker'] == '[引用:1]'
    assert citation['claim_context'] == 'Claim'


@pytest.mark.parametrize('identifier', ['1', 'https://api.example.test/v1/#WebPages.0'])
def test_repeated_source_identifier_uses_first_ranked_evidence_for_every_syntax(identifier):
    results = [dict(url='https://source.test/a', citation_id=identifier, snippet=snippet) for snippet in ['First evidence', 'Later evidence']]
    for answer in ['Claim [引用:1]', 'Claim [source](https://source.test/a)', 'Claim <a href="https://source.test/a">source</a>']:
        citation, = answers.citation_inventory(answer, results)
        assert citation['source']['snippet'] == 'First evidence'


@pytest.mark.parametrize('depth', [1, 1500])
@pytest.mark.parametrize('hidden', [False, True])
def test_deep_answers_keep_the_same_citations_and_visibility_as_shallow_answers(depth, hidden):
    body = '<div>' * depth + 'Claim [引用:1]' + '</div>' * depth
    answer = '<section' + (' hidden' if hidden else '') + '>' + body + '</section>'
    citations = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])
    assert [c['claim_context'] for c in citations] == ([] if hidden else ['Claim'])
    if citations:
        assert citations[0]['marker'] == '[引用:1]'


@pytest.mark.parametrize('tag', ['dialog', 'datalist', 'rp'])
@pytest.mark.parametrize('attributes,visible', [
    ('', False), ('style="display:block"', True), ('style="display:initial"', True),
    ('style="display:invalid"', False), ('style="display:revert"', False),
])
def test_default_hidden_elements_follow_supported_display_overrides(tag, attributes, visible):
    answer = '<' + tag + ' ' + attributes + '>Claim [引用:1] <a href="https://source.test/a">source</a></' + tag + '>'
    assert bool(answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])) is visible


@pytest.mark.parametrize('attributes,visible', [('open', True), ('open style="display:none"', False)])
def test_open_dialogs_follow_their_display_state(attributes, visible):
    assert bool(answers.citation_inventory('<dialog ' + attributes + '>Claim [引用:1]</dialog>', [dict(url='https://source.test/a', citation_id='1')])) is visible


@pytest.mark.parametrize('tag', ['audio', 'video', 'canvas', 'progress', 'meter'])
@pytest.mark.parametrize('attributes', ['', 'style="display:block"'])
def test_supported_browser_controls_do_not_expose_their_fallback_children(tag, attributes):
    answer = '<' + tag + ' ' + attributes + '>Fallback [引用:1] <a href="https://source.test/a">source</a></' + tag + '>'
    assert answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')]) == []


@pytest.mark.parametrize('attributes,visible', [
    ('hidden', False), ('hidden style="display:block"', True),
    ('hidden="until-found" style="display:block"', False),
    ('hidden="until-found" style="content-visibility:visible"', True),
])
def test_hidden_attributes_follow_their_browser_style_rules(attributes, visible):
    assert bool(answers.citation_inventory('<div ' + attributes + '>Claim [引用:1]</div>', [dict(url='https://source.test/a', citation_id='1')])) is visible


@pytest.mark.parametrize('tag', ['main', 'figure', 'figcaption', 'footer', 'header', 'address', 'nav', 'fieldset'])
def test_semantic_block_siblings_do_not_share_native_claims(tag):
    answer = '<' + tag + '>Claim.</' + tag + '><' + tag + '>[引用:1]</' + tag + '>'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])
    assert citation['claim_context'] == ''


@pytest.mark.parametrize('style,claims', [('', ['Supported claim']), ('display:contents', [])])
def test_display_contents_cannot_supply_an_image_only_citation(style, claims):
    answer = '<a href="https://source.test/a"><img style="' + style + '" alt="Supported claim"></a>'
    citations = answers.citation_inventory(answer, [dict(url='https://source.test/a')])
    assert [c['claim_context'] for c in citations] == claims


@pytest.mark.parametrize('tag', ['object', 'textarea', 'select'])
def test_display_contents_suppresses_replaced_element_content(tag):
    answer = '<a href="https://source.test/a"><' + tag + ' style="display:contents">Claim</' + tag + '></a>'
    assert answers.citation_inventory(answer, [dict(url='https://source.test/a')]) == []


def test_display_contents_keeps_ordinary_container_children_visible():
    answer = '<a href="https://source.test/a"><span style="display:contents">Claim</span></a>'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a')])
    assert citation['claim_context'] == 'Claim'


def test_distinct_native_ids_keep_distinct_excerpts_when_the_document_url_repeats():
    results = [dict(url='https://source.test/a', citation_id=str(i), snippet=text)
               for i, text in [(1, 'Early event.'), (2, 'Later event.')]]
    packet = answers.answer_packet(dict(id='q', query='What happened?'), dict(
        answer='Early event. [引用:1] Later event. [引用:2]', results=results))
    assert [c['source']['snippet'] for c in packet['citations']] == ['Early event.', 'Later event.']
    grades = answers.citation_grades(packet, json.dumps(dict(judgments=[
        dict(citation_index=i, supported=True, source_refs=[str(i)+':snippet']) for i in [0, 1]])))
    assert [g['evidence'][0]['quote'] for g in grades] == ['Early event.', 'Later event.']
    for answer in ['[Later event.](https://source.test/a)', '<a href="https://source.test/a">Later event.</a>']:
        citation, = answers.citation_inventory(answer, results)
        assert citation['source']['snippet'] == 'Early event.'


@pytest.mark.parametrize('tag,style,visible', [
    ('span', '', True), ('div', '', False), ('legend', '', False), ('center', '', False),
    ('dir', '', False), ('search', '', False), ('span', 'display:inline-block;', False),
    ('span', 'display:block;', False), ('div', 'display:inline;', True),
    ('span', 'float:left;', False), ('span', 'position:absolute;', False),
    ('span', 'display:contents;', True), ('span', 'display:table;', True),
    ('span', 'display:inline-table;', True), ('span', 'display:table-row;', True),
    ('span', 'display:table-cell;', False), ('span', 'display:table-caption;', True),
    ('span', 'display:ruby;', True), ('span', 'display:ruby-text;', True),
    ('span', 'display:table-column;', False), ('span', 'display:table-column-group;', False),
    ('span', 'display:flow;', False), ('span', 'display:inline flow-root;', False),
])
def test_content_visibility_applies_only_to_eligible_containers(tag, style, visible):
    answer = '<' + tag + ' style="' + style + 'content-visibility:hidden">Claim [引用:1]</' + tag + '>'
    assert bool(answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])) is visible


@pytest.mark.parametrize('outer_style,inner_style,visible', [
    ('display:block', 'display:inherit;content-visibility:hidden', False),
    ('display:inline', 'display:inherit;content-visibility:hidden', True),
    ('content-visibility:hidden', 'display:block;content-visibility:inherit', False),
    ('content-visibility:hidden', 'display:inline;content-visibility:inherit', True),
    ('position:absolute', 'position:inherit;content-visibility:hidden', False),
    ('float:left', 'float:inherit;content-visibility:hidden', False),
])
def test_content_visibility_uses_computed_inherited_box_properties(outer_style, inner_style, visible):
    answer = '<span style="' + outer_style + '"><span style="' + inner_style + '">Claim [引用:1]</span></span>'
    assert bool(answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])) is visible


def test_inapplicable_content_visibility_does_not_remove_part_of_a_claim():
    answer = 'Prefix <span style="content-visibility:hidden">other assertion</span> [引用:1]'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])
    assert citation['claim_context'] == 'Prefix other assertion'


def test_content_visibility_does_not_erase_a_replaced_image_alternative():
    citation, = answers.citation_inventory('<a href="https://source.test/a"><img style="content-visibility:hidden" alt="Claim"></a>', [dict(url='https://source.test/a')])
    assert citation['claim_context'] == 'Claim'


@pytest.mark.parametrize('tag,style,claim', [
    ('span', 'display:block', 'Supported claim'),
    ('div', 'display:inline', 'UnrelatedSupported claim'),
    ('div', 'display:contents', 'UnrelatedSupported claim'),
])
def test_claims_follow_resolved_block_boundaries(tag, style, claim):
    answer = '<section><' + tag + ' style="' + style + '">Unrelated</' + tag + '><' + tag + ' style="' + style + '">Supported claim [引用:1]</' + tag + '></section>'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])
    assert citation['claim_context'] == claim


@pytest.mark.parametrize('layout', ['block', 'flex', 'grid', 'inline-flex', 'inline-grid'])
@pytest.mark.parametrize('transparent', [False, True])
def test_layout_items_are_blockified_before_content_visibility(layout, transparent):
    child = '<span style="content-visibility:hidden">Claim [引用:1]</span>'
    if transparent:
        child = '<span style="display:contents">' + child + '</span>'
    answer = '<div style="display:' + layout + '">' + child + '</div>'
    assert bool(answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])) is (layout == 'block')


@pytest.mark.parametrize('display,visible', [('ruby', False), ('ruby-text', False), ('table-cell', False),
    ('table-row', False), ('inline list-item', False), ('inline flow-root list-item', False),
    ('table', True), ('inline-table', True), ('contents', True)])
def test_layout_blockification_retains_the_inner_display_type(display, visible):
    answer = '<div style="display:flex"><span style="display:' + display + ';content-visibility:hidden">Claim [引用:1]</span></div>'
    assert bool(answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])) is visible


def test_native_marker_projection_does_not_create_a_separate_layout_item_claim():
    citation, = answers.citation_inventory('<div style="display:flex">Claim [引用:1]</div>', [dict(url='https://source.test/a', citation_id='1')])
    assert citation['claim_context'] == 'Claim'


@pytest.mark.parametrize('container', ['tr', 'tbody', 'thead', 'tfoot'])
@pytest.mark.parametrize('visibility,visible', [('collapse', False), ('hidden', True)])
def test_collapsed_table_rows_cannot_be_restored_by_visible_descendants(container, visibility, visible):
    cell = '<td style="visibility:visible">Claim [引用:1]</td>'
    body = cell if container == 'tr' else '<tr style="visibility:visible">' + cell + '</tr>'
    answer = '<table><' + container + ' style="visibility:' + visibility + '">' + body + '</' + container + '></table>'
    assert bool(answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])) is visible


@pytest.mark.parametrize('cell_display,visible', [('table-cell', False), ('block', True)])
def test_collapse_on_anonymous_table_rows_uses_the_parent_visibility(cell_display, visible):
    answer = '<div style="visibility:collapse"><span style="display:' + cell_display + ';visibility:visible">Claim [引用:1]</span></div>'
    assert bool(answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])) is visible


def test_collapse_on_an_ordinary_container_keeps_overridable_visibility():
    citation, = answers.citation_inventory('<div style="visibility:collapse"><span style="visibility:visible">Claim [引用:1]</span></div>', [dict(url='https://source.test/a', citation_id='1')])
    assert citation['claim_context'] == 'Claim'


@pytest.mark.parametrize('options,label', [
    ('<option>False </option><option selected>True </option>', 'True'),
    ('<option>First</option><option>Other</option>', 'First'),
    ('<option disabled>False</option><option>True</option>', 'True'),
    ('<option disabled>False</option>', ''),
    ('<option selected>False</option><option selected>True</option>', 'True'),
    ('<option disabled selected>Chosen</option><option>Other</option>', 'Chosen'),
    ('<optgroup disabled label="Group"><option>False</option></optgroup><option>True</option>', 'True'),
    ('<optgroup disabled label="Group"><option selected>Chosen</option></optgroup><option>Other</option>', 'Chosen'),
    ('<option hidden selected>Chosen</option><option>Other</option>', 'Chosen'),
    ('<option label="True" selected>False</option>', 'True'),
    ('<option label="">Body</option>', 'Body'),
    ('<option>A<script>False</script>B<!-- False --></option>', 'AB'),
])
def test_single_select_claims_use_the_initial_displayed_option(options, label):
    answer = '<select>' + options + '</select>claim[引用:1]'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])
    assert citation['claim_context'] == (label + ' claim').strip()


@pytest.mark.parametrize('attributes,label', [
    ('multiple', 'False True'), ('multiple="false" size="1"', 'False True'),
    ('size="2"', 'False True'), ('size="2x"', 'False True'), ('size="+2"', 'False True'),
    ('size="1"', 'True'), ('size="-2"', 'True'), ('size="4294967295"', 'True'),
    ('size="' + '9'*5000 + '"', 'True'),
])
def test_select_display_size_and_multiple_mode_choose_the_visible_label_set(attributes, label):
    answer = '<select ' + attributes + '><option>False</option><option selected>True</option></select>claim[引用:1]'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])
    assert citation['claim_context'] == label + ' claim'


def test_listbox_keeps_group_labels_and_visible_disabled_options():
    answer = '<select multiple><optgroup disabled label="Group"><option label="First">Ignored</option><option>Second</option></optgroup><option hidden>Hidden</option></select>claim[引用:1]'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])
    assert citation['claim_context'] == 'Group First Second claim'


@pytest.mark.parametrize('attributes', ['', 'multiple', 'size="3"'])
def test_hidden_select_does_not_expose_option_labels(attributes):
    answer = '<select ' + attributes + ' style="visibility:hidden"><option selected style="visibility:visible">Hidden</option></select>Claim [引用:1]'
    citation, = answers.citation_inventory(answer, [dict(url='https://source.test/a', citation_id='1')])
    assert citation['claim_context'] == 'Claim'


@pytest.mark.parametrize('attributes,label', [
    ('type="button" value="Supported claim"', 'Supported claim'),
    ('type="submit" value="Supported claim"', 'Supported claim'),
    ('type="reset" value="Supported claim"', 'Supported claim'),
    ('type="submit"', 'Submit'), ('type="reset"', 'Reset'),
    ('type="button" placeholder="Hidden"', ''), ('type="submit" value=""', ''),
    ('value="Supported claim"', 'Supported claim'),
    ('type="invalid" value="Supported claim"', 'Supported claim'),
    ('type="text" placeholder="Supported claim"', 'Supported claim'),
    ('type="email" value="  a@example.com  "', 'a@example.com'),
    ('type="number" value="1e3"', '1e3'), ('type="number" value="false"', ''),
    ('type="number" value="1e9999"', ''),
    ('type="password" value="secret"', '••••••'),
    ('type="image" alt="Supported claim"', 'Supported claim'),
    ('type="hidden" value="Hidden" style="display:block"', ''),
    ('type="checkbox" value="Hidden"', ''), ('type="radio" value="Hidden"', ''),
    ('type="button" value="Hidden" hidden', ''),
    ('type="button" value="Hidden" style="visibility:hidden"', ''),
    ('type="button" value="Hidden" style="display:none"', ''),
])
def test_input_labels_are_retained_in_citation_links_and_sentence_claims(attributes, label):
    results = [dict(url='https://source.test/a', citation_id='1')]
    control = '<input ' + attributes + '>'
    citations = answers.citation_inventory('<a href="https://source.test/a">' + control + '</a>', results)
    assert [item['claim_context'] for item in citations] == ([label] if label else [])
    citation, = answers.citation_inventory('The claim is ' + control + ' [引用:1]', results)
    assert citation['claim_context'] == ('The claim is ' + label).strip()


@pytest.mark.parametrize('attributes,value,label', [
    ('placeholder="Paris"', '', 'Paris'),
    ('placeholder="Paris"', 'London', 'London'),
    ('placeholder="Paris"', '\n', 'Paris'),
    ('placeholder="Paris"', ' ', ''),
    ('placeholder="Paris" hidden', '', ''),
    ('placeholder="Paris" style="visibility:hidden"', '', ''),
    ('placeholder="Paris" style="display:none"', '', ''),
    ('placeholder="&lt;b&gt;Paris&lt;/b&gt; [引用:2]"', '', '<b>Paris</b> [引用:2]'),
])
def test_textarea_initial_text_and_placeholder_are_literal_citation_labels(attributes, value, label):
    results = [dict(url='https://source.test/a', citation_id='1')]
    control = '<textarea ' + attributes + '>' + value + '</textarea>'
    citations = answers.citation_inventory('<a href="https://source.test/a">' + control + '</a>', results)
    assert [item['claim_context'] for item in citations] == ([label] if label else [])
    citation, = answers.citation_inventory('The city is ' + control + ' [引用:1]', results)
    assert citation['claim_context'] == ('The city is ' + label).strip()
