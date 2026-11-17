"""Rebuild the preregistered length-qualified SimpleQA retrieval population."""

import csv
import io
import re

from .inputs import digest


MIRROR_POLICY = 'reference-title-lead/1'
FULL_MIRROR_POLICY = 'reference-question-evidence/3'
FULL_MIRROR_POLICIES = {'reference-question-evidence/1', 'reference-question-evidence/2', FULL_MIRROR_POLICY}
MIRROR_LEAD_CHARACTERS = 4000
MIRROR_MINIMUM_CHARACTERS = 200
MIRROR_OVERLAP = .8


def repair_cases(cases, revision, *, reuse_calls=False):
    """Replay reviewed task repairs; changed queries require new provider calls.

    Question, answer, source URLs and optional labels belong to this contract.
    Execution annotations are outside it, so a saved call can be rescored
    without changing its response, identity or timing.
    """
    from copy import deepcopy
    from .contracts import validate_cases

    fields = {'query', 'reference_answer', 'reference_urls'}
    if (not isinstance(revision, dict) or revision.get('schema') != 'simpleqa-case-repairs/1'
            or not revision.get('root_cause') or not revision.get('regression_ids')
            or not re.fullmatch('[0-9a-f]{40}', revision.get('predecessor', {}).get('revision', ''))
            or not re.fullmatch('[0-9a-f]{64}', revision.get('predecessor', {}).get('sha256', ''))
            or not isinstance(revision.get('repairs'), list) or not revision['repairs']):
        raise ValueError('Task repairs require a versioned predecessor and correction provenance')
    indexed = {}
    for item in revision['repairs']:
        cid = item.get('case_id')
        before, after = item.get('before'), item.get('after')
        if (not isinstance(cid, str) or cid in indexed
                or not isinstance(before, dict) or not isinstance(after, dict)
                or not fields.issubset(before) or set(before) != set(after)
                or set(before) - fields - {'topic', 'answer_type'} or before == after
                or not item.get('root_cause') or not item.get('evidence')):
            raise ValueError('Task repairs require unique before/after contracts and source evidence')
        for contract in (before, after):
            if (not isinstance(contract['query'], str) or not 0 < len(contract['query']) <= 128
                    or not isinstance(contract['reference_answer'], str) or not contract['reference_answer'].strip()
                    or not isinstance(contract['reference_urls'], list) or not contract['reference_urls']):
                raise ValueError('Invalid repaired task contract')
            validate_cases([dict(id=cid, **contract)])
        for source in item['evidence']:
            if (not isinstance(source, dict) or not (source.get('quote') or source.get('observation'))
                    or not re.fullmatch('[0-9a-f]{64}', source.get('sha256', ''))):
                raise ValueError('Task repairs require hash-bound source evidence')
            from .scoring import canonical_url

            canonical_url(source['url'])
        indexed[cid] = item
    repaired = deepcopy(cases)
    for case in repaired:
        item = indexed.get(case['id'])
        if item is None:
            continue
        actual = {key: case.get(key) for key in item['before']}
        if actual == item['after']:
            case['derivation'] = 'Repaired task contract; see case_repairs'
            continue
        if not reuse_calls and actual != item['before']:
            raise ValueError('Task repair does not bind the captured task: ' + case['id'])
        if reuse_calls and actual['query'] != item['after']['query']:
            raise ValueError('Changed query requires a new provider call: ' + case['id'])
        if reuse_calls and actual not in (item['before'], item['after']):
            raise ValueError('Task repair does not bind the captured task: ' + case['id'])
        case.update(deepcopy(item['after']))
        case['derivation'] = 'Repaired task contract; see case_repairs'
        # These judgments bind the old answer or old reference documents.
        for key in ('answer_derivation', 'equivalent_reference_urls',
                    'equivalent_reference_provenance', 'reference_verification_coverage',
                    'capture_failed_reference_urls', 'reference_verification_audit_url'):
            case.pop(key, None)
    return repaired


def validate_repair_evidence(dataset, artifacts):
    """Verify published repair excerpts and distinguish interpretation from quotes."""
    import json
    from pathlib import PurePosixPath
    from .answer_rules import match_reference_answer

    receipt = dataset.get('case_repairs')
    if not receipt:
        return
    for repair in receipt['repairs']:
        if repair.get('resolution') not in {'corrected', 'replaced'}:
            raise ValueError('Repair evidence requires a resolved correction or replacement')
        discovery = repair.get('source_discovery', {})
        if (discovery.get('kind') not in {'publisher_reference', 'independent_search', 'legacy_result_audit'}
                or not discovery.get('description')):
            raise ValueError('Repair evidence requires recorded source discovery')
        if discovery['kind'] == 'legacy_result_audit' and repair.get('requires_fresh_calls') is not True:
            raise ValueError('Output-informed references require fresh calls for every method')
        for evidence in repair['evidence']:
            if (discovery['kind'] == 'publisher_reference'
                    and evidence['url'] not in repair['before']['reference_urls']):
                raise ValueError('Publisher-reference discovery cannot introduce an unlisted source')
            path = evidence.get('path', '')
            if (not path or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts
                    or '\\' in path):
                raise ValueError('Repair evidence requires a contained source artifact')
            try:
                raw = artifacts(path) if callable(artifacts) else artifacts[path]
            except (KeyError, FileNotFoundError) as error:
                raise ValueError('Missing repair evidence artifact') from error
            if digest(raw) != evidence['sha256']:
                raise ValueError('Repair evidence artifact hash mismatch')
            source = json.loads(raw)
            if (source.get('schema') != 'source-excerpt/1' or source.get('url') != evidence['url']
                    or not re.fullmatch('[0-9a-f]{64}', source.get('source_sha256', ''))
                    or not isinstance(source.get('text'), str)):
                raise ValueError('Repair evidence must bind its original source capture')
            quote = ' '.join(evidence.get('quote', '').split())
            if not quote or quote not in ' '.join(source['text'].split()):
                raise ValueError('Repair quote differs from its stored source excerpt')
            interpretation = evidence.get('interpretation', {})
            if interpretation.get('answer') != repair['after']['reference_answer']:
                raise ValueError('Repair evidence interpretation must bind the accepted answer')
            kind = interpretation.get('kind')
            if kind == 'verbatim':
                if not match_reference_answer(interpretation['answer'], quote):
                    raise ValueError('Repair quote does not contain the declared answer')
            else:
                source_form = ' '.join(interpretation.get('source_form', '').split())
                if (kind not in {'translation', 'calculation', 'date_context', 'visual_record'}
                        or not interpretation.get('explanation') or not source_form or source_form not in quote):
                    raise ValueError('Nonliteral repair evidence requires its source form and interpretation')


def mirror_candidates(reference_url):
    """Derive possible copies from publisher references, never search results."""
    from urllib.parse import quote, unquote, urlsplit

    parsed = urlsplit(reference_url)
    if (parsed.scheme not in ('http', 'https') or parsed.hostname != 'en.wikipedia.org'
            or parsed.port not in (None, 80, 443) or not parsed.path.startswith('/wiki/')
            or parsed.query or parsed.username or parsed.password):
        return []
    title = unquote(parsed.path[len('/wiki/'):])
    namespace = title.partition(':')[0].replace('_',' ').casefold()
    if not title or ':' in title and namespace in {
        'special','file','image','media','help','category','wikipedia','wp','portal',
        'template','talk','user','user talk','draft','module','mediawiki',
    }:
        return []
    path = quote(title, safe='()_,:-')
    return ['https://www.wikiwand.com/en/articles/' + path,
            'https://www.wikiwand.com/en/' + path,
            'https://wiki2.org/en/' + path,
            *[gateway + path + suffix for gateway in (
                'https://en.wikipedia-on-ipfs.org/wiki/',
                'https://ipfs.io/ipns/en.wikipedia-on-ipfs.org/wiki/',
                'https://en-wikipedia--on--ipfs-org.ipns.dweb.link/wiki/',
            ) for suffix in ('', '.html')]]


def qualify_mirror(reference, candidate, *, answer=None):
    """Return a proof summary only for independently observed same-page copies."""
    from .outputs import block_overlap, normalized_text

    if answer is not None:
        return qualify_question_mirror(reference, candidate, answer)
    if candidate.get('url') not in mirror_candidates(reference.get('url', '')):
        raise ValueError('Mirror candidates must derive from a publisher reference URL')
    for observation in (reference, candidate):
        if (observation.get('status') != 200 or not observation.get('verified_at')
                or not re.fullmatch('[0-9a-f]{64}', observation.get('body_sha256', ''))
                or not observation.get('title') or not observation.get('lead')):
            raise ValueError('Mirror verification requires complete successful page evidence')
    left, right = [normalized_text(o['lead'][:MIRROR_LEAD_CHARACTERS])
                   for o in (reference, candidate)]
    if min(len(left), len(right)) < MIRROR_MINIMUM_CHARACTERS:
        raise ValueError('Insufficient article lead text to verify a mirror')
    overlap = block_overlap(right, left)
    title_matches = normalized_text(reference['title']) == normalized_text(candidate['title'])
    return {'url': candidate['url'], 'reference_url': reference['url'],
            'method': MIRROR_POLICY, 'verified_at': candidate['verified_at'],
            'reference_observation_sha256': reference['body_sha256'],
            'observation_sha256': candidate['body_sha256'],
            'title_matches': title_matches, 'lead_overlap': overlap,
            'qualified': title_matches and overlap >= MIRROR_OVERLAP}


def article_text(raw, *, media_type='text/html', source_title=None):
    """Retain the complete article, including answer-bearing tables and lists."""
    from bs4 import BeautifulSoup
    from markdown_it import MarkdownIt

    rendered = MarkdownIt('default').render(raw.decode('utf-8')) if media_type == 'text/markdown' else raw
    if media_type not in ('text/html', 'text/markdown'):
        raise ValueError('Unsupported full capture media type')
    soup = BeautifulSoup(rendered, 'html.parser')
    for node in soup.select('script,style,nav,aside,.hatnote,.metadata'):
        node.decompose()
    heading = soup.find('h1')
    title = heading.get_text(' ', strip=True) if heading else source_title or ''
    body = soup.select_one('.mw-parser-output') or soup.find('article') or soup.find('main')
    if media_type == 'text/markdown':
        body = soup
    return title, body.get_text(' ', strip=True) if body else ''


def qualify_question_mirror(reference, candidate, answer, *, policy=FULL_MIRROR_POLICY):
    """Require the same article and this question's supporting evidence."""
    from .scoring import contains_answer
    from .outputs import block_overlap, normalized_text

    if candidate.get('url') not in mirror_candidates(reference.get('url', '')):
        raise ValueError('Mirror candidates must derive from a publisher reference URL')
    for observation in (reference, candidate):
        if (observation.get('status') != 200 or not observation.get('verified_at')
                or not re.fullmatch('[0-9a-f]{64}', observation.get('body_sha256', ''))
                or not observation.get('title') or not observation.get('article_text')
                or len(normalized_text(observation['article_text'])) < MIRROR_MINIMUM_CHARACTERS):
            raise ValueError('Mirror verification requires successful full article captures')
    left, right = [o['article_text'] for o in (reference, candidate)]
    matcher = contains_answer
    if policy == FULL_MIRROR_POLICY:
        from .answer_rules import match_reference_answer

        # Compound prose retains literal token evidence (for example a match
        # score with a compact dash). Quantities keep the signed/unit guards.
        matcher = lambda text, reference: (
            (not re.match(r'\s*[+\-]?\d', reference) and contains_answer(text, reference))
            or match_reference_answer(reference, text))
    reference_has_answer = matcher(left, answer)
    mirror_has_answer = matcher(right, answer)
    title_matches = normalized_text(reference['title']) == normalized_text(candidate['title'])
    overlap = min(block_overlap(right, left), block_overlap(left, right))
    qualified = (mirror_has_answer if reference_has_answer else overlap >= MIRROR_OVERLAP)
    if policy in {'reference-question-evidence/2', FULL_MIRROR_POLICY}:
        qualified = overlap >= MIRROR_OVERLAP and (not reference_has_answer or mirror_has_answer)
    elif policy not in FULL_MIRROR_POLICIES:
        raise ValueError('Unsupported full reference policy')
    return dict(url=candidate['url'], reference_url=reference['url'], method=policy,
                verified_at=candidate['verified_at'], title_matches=title_matches,
                reference_observation_sha256=reference['body_sha256'], observation_sha256=candidate['body_sha256'],
                reference_has_answer=reference_has_answer, mirror_has_answer=mirror_has_answer,
                article_overlap=overlap,
                qualified=title_matches and qualified)


def _full_capture(observation, artifacts):
    if not observation or observation.get('status') != 200:
        return observation or {}
    value = dict(observation)
    binding = value.get('body_artifact')
    if artifacts is not None:
        if not binding:
            raise ValueError('Successful mirror observations require full capture artifacts')
        raw = artifacts(binding['path']) if callable(artifacts) else artifacts[binding['path']]
        if digest(raw) != binding['sha256'] or binding['sha256'] != value['body_sha256']:
            raise ValueError('Full capture hash mismatch')
        value['title'], value['article_text'] = article_text(raw, media_type=value['media_type'], source_title=value.get('source_title'))
    return value


def extend_reference_urls(dataset, observations, *, artifacts=None, policy=FULL_MIRROR_POLICY):
    """Audit every case/candidate under one question-specific full-page rule."""
    from copy import deepcopy

    cases = dataset['cases']
    references = {url for case in cases for url in case['reference_urls']}
    candidates = {url for reference in references for url in mirror_candidates(reference)}
    indexed = {item['url']: _full_capture(item, artifacts) for item in observations}
    if len(indexed) != len(observations) or set(indexed) - references - candidates:
        raise ValueError('Reference observations must be unique and independently derived')
    extended = deepcopy(dataset)
    audit, coverage = [], []
    for case in extended['cases']:
        proofs, records = [], []
        for reference in case['reference_urls']:
            for candidate in mirror_candidates(reference):
                record = dict(case_id=case['id'], reference_url=reference, url=candidate)
                try:
                    proof = qualify_question_mirror(indexed.get(reference, {}), indexed.get(candidate, dict(url=candidate)), case['reference_answer'], policy=policy)
                except ValueError:
                    records.append(dict(record, outcome='capture_failed'))
                    continue
                outcome = ('verified' if proof['qualified'] else 'title_mismatch' if not proof['title_matches'] else 'evidence_mismatch')
                records.append(dict(record, **{k: v for k, v in proof.items() if k not in record}, outcome=outcome))
                if proof['qualified']:
                    proofs.append(proof)
        case['equivalent_reference_urls'] = list(dict.fromkeys(proof['url'] for proof in proofs))
        case['equivalent_reference_provenance'] = proofs
        case['url_equivalence'] = 'document-url/2'
        audit.extend(records)
        coverage.append(dict(case_id=case['id'], candidate_count=len(records),
                             **{key: sum(r['outcome'] == key for r in records) for key in ('verified', 'title_mismatch', 'evidence_mismatch', 'capture_failed')}))
    extended['reference_equivalence'] = dict(method=policy,
        minimum_article_characters=MIRROR_MINIMUM_CHARACTERS, minimum_block_overlap=MIRROR_OVERLAP,
        candidate_scope='Publisher reference URLs only', url_policy='document-url/2', case_coverage=coverage)
    extended['reference_equivalence_audit'] = audit
    extended['reference_equivalence_observations'] = observations
    return extended


def validate_reference_evidence(dataset, artifacts):
    """Replay all full captures and reject incomplete or altered admissions/audits."""
    policy = dataset.get('reference_equivalence', {}).get('method')
    if policy not in FULL_MIRROR_POLICIES:
        return
    observations = dataset.get('reference_equivalence_observations')
    if not isinstance(observations, list):
        raise ValueError('Missing full reference capture inventory')
    references = {url for case in dataset['cases'] for url in case['reference_urls'] if mirror_candidates(url)}
    required = references | {url for reference in references for url in mirror_candidates(reference)}
    if {item.get('url') for item in observations} != required:
        raise ValueError('Incomplete full reference capture inventory')
    rebuilt = extend_reference_urls(dataset, observations, artifacts=artifacts, policy=policy)
    for field in ('reference_equivalence', 'reference_equivalence_audit'):
        if rebuilt[field] != dataset.get(field):
            raise ValueError('Reference candidate audit differs from full capture replay')
    for actual, expected in zip(dataset['cases'], rebuilt['cases'], strict=True):
        for field in ('equivalent_reference_urls', 'equivalent_reference_provenance', 'url_equivalence'):
            if actual.get(field) != expected[field]:
                raise ValueError('Reference admission differs from full capture replay')


def validate_reference_extensions(case):
    """Require provenance for every added URL before a dataset is accepted."""
    from datetime import datetime

    aliases = case.get('equivalent_reference_urls',[])
    proofs = case.get('equivalent_reference_provenance',[])
    if (not isinstance(aliases,list) or any(not isinstance(url,str) for url in aliases)
            or len(aliases) != len(set(aliases)) or not isinstance(proofs,list)
            or any(not isinstance(proof,dict) for proof in proofs)
            or {proof.get('url') for proof in proofs} != set(aliases)):
        raise ValueError('Equivalent reference URLs require complete unique provenance')
    for proof in proofs:
        reference = proof.get('reference_url')
        full = proof.get('method') in FULL_MIRROR_POLICIES
        overlap = proof.get('article_overlap' if full else 'lead_overlap')
        if (reference not in case.get('reference_urls',[]) or proof['url'] not in mirror_candidates(reference)
                or proof.get('method') not in {MIRROR_POLICY, *FULL_MIRROR_POLICIES} or proof.get('title_matches') is not True
                or proof.get('qualified') is not True or type(overlap) not in (float,int)
                or not 0 <= overlap <= 1
                or (not full and overlap < MIRROR_OVERLAP)
                or (proof.get('method') in {'reference-question-evidence/2', FULL_MIRROR_POLICY} and overlap < MIRROR_OVERLAP)
                or (full and any(type(proof.get(field)) is not bool for field in ('reference_has_answer', 'mirror_has_answer')))
                or (full and not (proof.get('mirror_has_answer') is True if proof.get('reference_has_answer') is True else overlap >= MIRROR_OVERLAP))
                or any(not re.fullmatch('[0-9a-f]{64}',proof.get(field,'')) for field in
                       ('reference_observation_sha256','observation_sha256'))):
            raise ValueError('Invalid equivalent reference URL proof')
        try:
            observed = datetime.fromisoformat(proof['verified_at'].replace('Z','+00:00'))
        except (KeyError,TypeError,ValueError,AttributeError) as error:
            raise ValueError('Equivalent references require an observation date') from error
        if observed.tzinfo is None:
            raise ValueError('Equivalent reference observation dates require a time zone')


def source_manifest():
    from harness.benchmarks.data import definition_path
    import json

    return json.loads(definition_path("simpleqa-verified", "factual-source.json").read_bytes())


def _urls(value):
    urls = []
    for part in value.split(","):
        match = re.match(r"https?://\S+", part.strip())
        if not match:
            continue
        url = match.group(0)
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        if url not in urls:
            urls.append(url)
    return urls


def eligible_cases(csv_bytes, *, manifest=None):
    """Select by the pre-call shared input limit, retaining original CSV order."""
    manifest = manifest or source_manifest()
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    if len({row["original_index"] for row in rows}) != len(rows):
        raise ValueError("Duplicate publisher question identity")
    return [
        {
            **{k: v for k, v in manifest["case"].items() if k != "id_prefix"},
            "id": manifest["case"]["id_prefix"] + row["original_index"],
            "query": row["problem"],
            "origin": manifest["url"],
            "reference_answer": row["answer"],
            "reference_urls": _urls(row["urls"]),
            "topic": row["topic"],
            "answer_type": row["answer_type"],
        }
        for row in rows
        if len(row["problem"]) <= manifest["selection"]["max_query_characters"]
    ]


def rebuild_dataset(csv_bytes, *, manifest=None, reference_dataset=None, artifacts=None):
    """Verify the pinned publisher input before rebuilding the declared population."""
    manifest = manifest or source_manifest()
    if digest(csv_bytes) != manifest["sha256"]:
        raise ValueError("Publisher CSV SHA256 does not match the frozen source")
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    cases = eligible_cases(csv_bytes, manifest=manifest)
    cases = [{key: case[key] for key in manifest["case_field_order"]} for case in cases]
    if (
        len(rows) != manifest["selection"]["source_count"]
        or len(cases) != manifest["selection"]["selected_count"]
    ):
        raise ValueError("Frozen population differs from the pinned source selection")
    dataset = {
        "schema": "lexbench-websearch-dataset/1",
        "id": manifest["dataset"],
        "cases": cases,
        "reference_checks": {},
        "reference_artifacts": {
            manifest["url"]: {
                "sha256": manifest["sha256"],
                "license": manifest["license"],
                "scope": manifest["scope"],
            }
        },
    }
    if reference_dataset is not None:
        repairs = reference_dataset.get('case_repairs')
        if repairs:
            dataset['cases'] = repair_cases(dataset['cases'], repairs)
            dataset['case_repairs'] = repairs
        if reference_dataset.get('reference_equivalence'):
            dataset = extend_reference_urls(dataset,
                reference_dataset['reference_equivalence_observations'], artifacts=artifacts,
                policy=reference_dataset['reference_equivalence']['method'])
    return dataset
