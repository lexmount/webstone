"""Rebuild the preregistered length-qualified SimpleQA retrieval population."""

import csv
import io
import re

from .inputs import digest


MIRROR_POLICY = 'reference-title-lead/1'
FULL_MIRROR_POLICY = 'reference-question-evidence/1'
MIRROR_LEAD_CHARACTERS = 4000
MIRROR_MINIMUM_CHARACTERS = 200
MIRROR_OVERLAP = .8


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


def qualify_question_mirror(reference, candidate, answer):
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
    reference_has_answer = contains_answer(left, answer)
    mirror_has_answer = contains_answer(right, answer)
    title_matches = normalized_text(reference['title']) == normalized_text(candidate['title'])
    overlap = min(block_overlap(right, left), block_overlap(left, right))
    return dict(url=candidate['url'], reference_url=reference['url'], method=FULL_MIRROR_POLICY,
                verified_at=candidate['verified_at'], title_matches=title_matches,
                reference_observation_sha256=reference['body_sha256'], observation_sha256=candidate['body_sha256'],
                reference_has_answer=reference_has_answer, mirror_has_answer=mirror_has_answer,
                article_overlap=overlap,
                qualified=title_matches and (mirror_has_answer if reference_has_answer else overlap >= MIRROR_OVERLAP))


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


def extend_reference_urls(dataset, observations, *, artifacts=None):
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
                    proof = qualify_mirror(indexed.get(reference, {}), indexed.get(candidate, dict(url=candidate)), answer=case['reference_answer'])
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
    extended['reference_equivalence'] = dict(method=FULL_MIRROR_POLICY,
        minimum_article_characters=MIRROR_MINIMUM_CHARACTERS, minimum_block_overlap=MIRROR_OVERLAP,
        candidate_scope='Publisher reference URLs only', url_policy='document-url/2', case_coverage=coverage)
    extended['reference_equivalence_audit'] = audit
    extended['reference_equivalence_observations'] = observations
    return extended


def validate_reference_evidence(dataset, artifacts):
    """Replay all full captures and reject incomplete or altered admissions/audits."""
    if dataset.get('reference_equivalence', {}).get('method') != FULL_MIRROR_POLICY:
        return
    observations = dataset.get('reference_equivalence_observations')
    if not isinstance(observations, list):
        raise ValueError('Missing full reference capture inventory')
    references = {url for case in dataset['cases'] for url in case['reference_urls'] if mirror_candidates(url)}
    required = references | {url for reference in references for url in mirror_candidates(reference)}
    if {item.get('url') for item in observations} != required:
        raise ValueError('Incomplete full reference capture inventory')
    rebuilt = extend_reference_urls(dataset, observations, artifacts=artifacts)
    for field in ('reference_equivalence', 'reference_equivalence_audit'):
        if rebuilt[field] != dataset.get(field):
            raise ValueError('Reference candidate audit differs from full capture replay')
    for actual, expected in zip(dataset['cases'], rebuilt['cases'], strict=True):
        for field in ('equivalent_reference_urls', 'equivalent_reference_provenance'):
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
        full = proof.get('method') == FULL_MIRROR_POLICY
        overlap = proof.get('article_overlap' if full else 'lead_overlap')
        if (reference not in case.get('reference_urls',[]) or proof['url'] not in mirror_candidates(reference)
                or proof.get('method') not in (MIRROR_POLICY, FULL_MIRROR_POLICY) or proof.get('title_matches') is not True
                or proof.get('qualified') is not True or type(overlap) not in (float,int)
                or not 0 <= overlap <= 1
                or (not full and overlap < MIRROR_OVERLAP)
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


def rebuild_dataset(csv_bytes, *, manifest=None):
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
    return {
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
