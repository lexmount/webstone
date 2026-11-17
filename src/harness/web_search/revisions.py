"""Re-evaluate saved evidence with explicit external correction authority."""

import json
from copy import deepcopy
from pathlib import Path

from lexbench_websearch.answer_rules import POLICY_NAME, RULE_VERSION, reference_rule
from lexbench_websearch.contracts import validate_cases
from lexbench_websearch.inputs import digest
from lexbench_websearch.qualification import _equivalent, _qualified, validate_regressions
from lexbench_websearch.scoring import canonical_url, evaluate, task_acceptance


def _rescore(profile, rows):
    cases = {case["id"]: case for case in profile["cases"]}
    updated, changes = [], []
    for row in rows:
        new = {**row, "metrics": evaluate(cases[row["case_id"]], row["results"])}
        if new["metrics"] != row["metrics"]:
            changes.append({"id": row["id"], "before": row["metrics"], "after": new["metrics"]})
        updated.append(new)
    return profile, updated, changes


NATIVE_CONTROLS = Path(__file__).with_name("data") / "native-normalization-regressions.json"


def capture_mirror_observations(dataset, previous, output, token, *, collect=False, full=False, previous_root=None):
    """Capture reference-derived pages once, retrying prior fetch failures once.

    Native responses, request reservations and the execution contract are retained;
    a resumed capture never repeats an already completed physical request.
    Use previous_root to carry evidence into a new output directory. Full mode
    upgrades retained complete bodies without another HTTP request.
    Successful fetches retain their body bindings in both modes.
    """
    import time
    from datetime import UTC, datetime

    from harness.benchmark_execution import ExecutionPolicy, Job, assess_execution, run_jobs
    from harness.web_search.core import response_entries, write_json
    from harness.web_search.runner import http_call
    from lexbench_webfetch.paths import contained_path
    from lexbench_websearch.simpleqa import article_text, mirror_candidates

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    references = {u for case in dataset['cases'] for u in case['reference_urls'] if mirror_candidates(u)}
    required = references | {u for ref in references for u in mirror_candidates(ref)}
    indexed = {item['url']: deepcopy(item) for item in previous}
    if len(indexed) != len(previous) or set(indexed) - required:
        raise ValueError('Capture observations must be unique reference-derived URLs')
    urls = sorted(u for u in required if indexed.get(u, {}).get('status') != 200
                  and indexed.get(u, {}).get('fetch_attempts', int(u in indexed)) < 2)
    previous_raw = json.dumps(previous, sort_keys=True).encode()
    inherited = 'inherited/' + digest(previous_raw)
    source_root = Path(previous_root) if previous_root is not None else output
    retained, retained_hashes = {}, {}
    def retain(relative, expected):
        target = inherited + '/' + relative
        if target not in retained:
            destination = contained_path(output, target, reject_symlinks=True)
            source = destination if destination.is_file() else contained_path(source_root, relative, reject_symlinks=True)
            if not source.is_file():
                raise ValueError('Previous capture artifact is missing; supply its previous_root')
            retained[target] = source.read_bytes()
            retained_hashes[target] = digest(retained[target])
        if retained_hashes[target] != expected:
            raise ValueError('Previous capture artifact changed')
        return retained[target], target
    for observation in indexed.values():
        body_hash = observation.get('body_sha256', '')
        needs_body = observation.get('status') == 200 and not observation.get('body_artifact')
        valid_hash = isinstance(body_hash, str) and len(body_hash) == 64
        if full and needs_body and not valid_hash:
            raise ValueError('Full capture requires retained body artifacts')
        if needs_body and valid_hash:
            candidate = 'bodies/' + body_hash + '.md'
            available = (contained_path(output, inherited + '/' + candidate, reject_symlinks=True).is_file()
                         or contained_path(source_root, candidate, reject_symlinks=True).is_file())
            source_title = observation.get('title')
            if not available and observation.get('native_evidence'):
                native = observation['native_evidence']
                raw, _ = retain(native['path'], native['sha256'])
                try:
                    matches = response_entries(json.loads(raw), 'results', observation['url'])
                except ValueError:
                    matches = []
                if len(matches) == 1 and isinstance(matches[0].get('text'), str):
                    body = matches[0]['text'].encode()
                    if digest(body) == body_hash:
                        retained[inherited + '/' + candidate] = body
                        retained_hashes[inherited + '/' + candidate] = body_hash
                        source_title, available = matches[0].get('title'), True
            if available:
                observation.update(body_artifact=dict(path=candidate, sha256=body_hash),
                                   media_type='text/markdown', source_title=source_title)
        if full and observation.get('status') == 200 and (
                not observation.get('body_artifact') or observation.get('media_type') not in {'text/html', 'text/markdown'}
                or observation['body_artifact'].get('sha256') != observation.get('body_sha256')):
            raise ValueError('Previous full capture metadata is incomplete')
        for field in ('body_artifact', 'native_evidence', 'capture'):
            binding = observation.get(field)
            if not binding:
                continue
            _, relative = retain(binding['path'], binding['sha256'])
            binding['path'] = relative
        if full and observation.get('status') == 200:
            title, text = article_text(retained[observation['body_artifact']['path']],
                                       media_type=observation['media_type'], source_title=observation.get('source_title'))
            if not title or len(text) < 200:
                raise ValueError('Previous observation has no complete retained article')
    batches = [urls[i:i + 12] for i in range(0, len(urls), 12)]
    policy = dict(purpose='quality', max_in_flight=4, resources={'reference-pages': {'capacity': 4}})
    options = dict(text={'verbosity': 'full'}, maxAgeHours=0, livecrawlTimeout=20000)
    contract = dict(schema='reference-page-capture/2', dataset_sha256=digest(json.dumps(dataset, sort_keys=True).encode()),
                    previous_sha256=digest(previous_raw), urls=urls,
                    execution_policy=policy, maximum_calls=len(batches), timeout_s=45,
                    retries=1, request=options, maximum_url_charge_usd=len(urls) * .001)
    if full:
        contract['full_capture'] = True
    if retained:
        retained[inherited + '.json'] = previous_raw
        contract['previous_capture'] = dict(path=inherited + '.json', sha256=digest(previous_raw), artifact_root=inherited)
    path = output / 'contract.json'
    if path.exists() and json.loads(path.read_bytes()) != contract:
        raise ValueError('Reference capture contract changed after preparation')
    write_json(path, contract)
    for relative, raw in retained.items():
        destination = contained_path(output, relative, reject_symlinks=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    if not batches:
        observations = [indexed[url] for url in sorted(required)]
        write_json(output / 'observations.json', observations)
        return observations
    def execute(payload):
        key, batch = payload
        receipt_path, raw_path = output / (key + '.json'), output / (key + '-response.json')
        request = dict(options, urls=batch)
        if receipt_path.exists():
            receipt, raw = json.loads(receipt_path.read_bytes()), raw_path.read_bytes()
            if receipt['sha256'] != digest(raw) or receipt['request'] != request:
                raise ValueError('Saved reference capture changed')
            return receipt, raw
        with (output / (key + '-reservation.json')).open('x', encoding='utf-8') as stream:
            json.dump(dict(request=request, started_at=datetime.now(UTC).isoformat()), stream)
        receipt, raw = http_call('https://api.exa.ai/contents', request, 'x-api-key: ' + token, 45)
        receipt.update(request=request, sha256=digest(raw), completed_at=datetime.now(UTC).isoformat())
        raw_path.write_bytes(raw)
        write_json(receipt_path, receipt)
        return receipt, raw
    completed = []
    def commit(job, result, error):
        if error:
            raise error
        receipt, raw = result
        if receipt['http_status'] in (401, 402, 403, 429):
            raise ValueError('Reference capture account/rate limit; response preserved')
        completed.append(job.key)
    jobs = [Job(f'batch-{i:04d}', ('reference-pages',), (f'batch-{i:04d}', batch))
            for i, batch in enumerate(batches)]
    start = time.monotonic()
    if collect:
        assess_execution(json.loads((output / 'execution-plan.json').read_bytes()), evidence_root=output)
    run_jobs(jobs if collect else jobs[:4], execute, commit, ExecutionPolicy.from_dict(policy))
    if not collect:
        preflight = dict(completed_calls=len(completed), wall_seconds=time.monotonic() - start, max_in_flight=4)
        write_json(output / 'preflight.json', preflight)
        plan = dict(execution_policy=policy, planned_calls=len(jobs), setup_seconds=0,
                    wall_time_budget_seconds=7200, preflight=dict(preflight, evidence_path='preflight.json',
                    evidence_sha256=digest((output / 'preflight.json').read_bytes())))
        write_json(output / 'execution-plan.json', plan)
        return assess_execution(plan, evidence_root=output)
    bodies = output / 'bodies'
    bodies.mkdir(exist_ok=True)
    for i, batch in enumerate(batches):
        key = f'batch-{i:04d}'
        receipt = json.loads((output / (key + '.json')).read_bytes())
        raw = (output / (key + '-response.json')).read_bytes()
        try:
            payload = json.loads(raw) if receipt['http_status'] == 200 and not receipt['transport_exit'] else {}
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        for url in batch:
            matches = response_entries(payload, 'results', url)
            statuses = response_entries(payload, 'statuses', url)
            item, status = (matches[0] if len(matches) == 1 else {}), (statuses[0] if len(statuses) == 1 else {})
            successful = (status.get('status') == 'success' and status.get('source') == 'crawled'
                          and isinstance(item.get('text'), str) and bool(item['text'].strip()))
            body = item['text'].encode() if successful else b''
            observation = article_observation(body, url, status=200 if successful else 0,
                verified_at=receipt['completed_at'], media_type='text/markdown', source_title=item.get('title'))
            if body:
                observation.update(media_type='text/markdown', source_title=item.get('title'),
                                   body_artifact=dict(path='bodies/' + digest(body) + '.md', sha256=digest(body)))
            if full and body:
                title, text = article_text(body, media_type='text/markdown', source_title=item.get('title'))
                if title and len(text) >= 200:
                    observation['title'] = title
                else:
                    observation['status'] = 0
            observation.update(source_status=status, native_evidence=dict(path=key + '-response.json', sha256=digest(raw)),
                               capture=dict(path=key + '.json', sha256=digest((output / (key + '.json')).read_bytes())),
                               fetch_attempts=indexed.get(url, {}).get('fetch_attempts', int(url in indexed)) + 1)
            if body:
                (bodies / (digest(body) + '.md')).write_bytes(body)
            indexed[url] = observation
    observations = [indexed[url] for url in sorted(required)]
    write_json(output / 'observations.json', observations)
    return observations


def article_observation(raw, url, *, status, verified_at, media_type='text/html', source_title=None):
    """Extract a bounded article lead from frozen HTML for mirror verification."""
    from bs4 import BeautifulSoup

    from lexbench_websearch.simpleqa import MIRROR_LEAD_CHARACTERS

    canonical_url(url)
    if media_type == 'text/markdown':
        from markdown_it import MarkdownIt

        rendered = MarkdownIt('default').render(raw.decode('utf-8'))
    elif media_type == 'text/html':
        rendered = raw
    else:
        raise ValueError('Unsupported article evidence media type')
    soup = BeautifulSoup(rendered, 'html.parser')
    for node in soup.select('script,style,nav,aside,table,sup,.hatnote,.shortdescription,.metadata'):
        node.decompose()
    heading = soup.find('h1')
    if heading is None and media_type == 'text/markdown' and source_title:
        first = soup.find('p')
        if first and first.get_text(' ',strip=True) == source_title:
            heading = first
    body = soup.select_one('.mw-parser-output') or soup.find('article') or soup.find('main')
    if media_type == 'text/markdown':
        body = soup
    paragraphs = []
    if body:
        after_heading = media_type != 'text/markdown'
        for node in body.find_all(['h1', 'p', 'h2']):
            if node is heading:
                after_heading = True
                continue
            if not after_heading:
                continue
            if node.name == 'h2' and node.get_text(' ',strip=True).casefold() != 'from wikipedia, the free encyclopedia':
                break
            if node.name != 'p':
                continue
            text = node.get_text(' ', strip=True)
            if text:
                paragraphs.append(text)
            if sum(len(part) for part in paragraphs) >= MIRROR_LEAD_CHARACTERS:
                break
    return dict(url=url, status=status, verified_at=verified_at, body_sha256=digest(raw),
                title=heading.get_text(' ', strip=True) if heading else '',
                lead='\n'.join(paragraphs)[:MIRROR_LEAD_CHARACTERS])

def normalization_regressions(expected_sha256, normalizer):
    """Exercise frozen projection controls through the execution owner's adapter."""
    path = NATIVE_CONTROLS
    if digest(path.read_bytes()) != expected_sha256:
        raise ValueError("Normalization regression binding mismatch")
    if not callable(normalizer):
        raise ValueError("Native normalization requires the execution adapter")
    for group in json.loads(path.read_bytes())["regressions"]:
        for control in group["controls"]:
            try:
                results = normalizer(
                    control["payload"],
                    control["method"],
                    control["protocol"],
                    control["request"],
                )
            except ValueError:
                if control["error"]:
                    continue
                raise
            if (
                control["error"]
                or [r["url"] for r in results] != control["expected_urls"]
            ):
                raise ValueError("Normalization regression failed: " + control["id"])
            metrics = evaluate(control["case"], results)
            if any(metrics[k] != v for k, v in control["expected_metrics"].items()):
                raise ValueError("Task metric regression failed: " + control["id"])


def canonical_metadata(final_url, tags):
    """Read explicit publisher canonical links, without evaluating page content."""
    from html.parser import HTMLParser
    from urllib.parse import urljoin

    targets = set()
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        return targets

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'link' and 'canonical' in (attrs.get('rel') or '').lower().split() and attrs.get('href'):
                targets.add(canonical_url(urljoin(final_url, attrs['href'])))

    parser = Links()
    for tag in tags:
        parser.feed(tag)
    return targets


def capture_source_identity(url, timeout=6):
    """Capture redirects and publisher canonical metadata as reference inputs."""
    import io
    import time
    import urllib.error
    import urllib.request
    from datetime import UTC, datetime
    from html.parser import HTMLParser
    from http.client import HTTPException

    canonical_url(url)
    chain, tags = [], []
    deadline = time.monotonic() + timeout

    class Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            canonical_url(newurl)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Source identity timeout')
            req.timeout = remaining
            chain.append(dict(url=req.full_url, status=code, location=newurl))
            redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
            if redirected is not None:
                # urllib otherwise drains the redirect body without the deadline.
                fp.close()
            return redirected

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == 'link' and 'canonical' in (dict(attrs).get('rel') or '').lower().split():
                tags.append(self.get_starttag_text())

    observed = dict(url=url, verified_at=datetime.now(UTC).isoformat(), chain=chain)
    try:
        opener = urllib.request.build_opener(Redirects())
        try:
            response = opener.open(urllib.request.Request(url, headers={'User-Agent': 'Webstone reference identity'}), timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            observed.update(status=response.status, final=response.url)
            if response.status == 200 and 'html' in response.headers.get('Content-Type', '').lower():
                source = response.fp
                sock = source.raw._sock

                class DeadlineBody(io.RawIOBase):
                    def readable(self):
                        return True

                    def readinto(self, buffer):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('Source identity timeout')
                        sock.settimeout(remaining)
                        data = source.read1(len(buffer))
                        buffer[:len(data)] = data
                        return len(data)

                    def close(self):
                        source.close()
                        super().close()

                # Apply the deadline to socket reads, including chunk framing.
                response.fp = io.BufferedReader(DeadlineBody())
                prefix = response.read(262144).decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
                parser = Links()
                parser.feed(prefix)
                targets = canonical_metadata(response.url, tags)
                if len(targets) == 1:
                    observed.update(canonical=next(iter(targets)), canonical_evidence=tags)
    except (OSError, ValueError, UnicodeError, LookupError, HTTPException) as exc:
        observed['error'] = str(exc)
    return observed


def factual_source_identity(case, item):
    """Qualify a reference URI alias without judging page relevance or availability."""
    from urllib.parse import urlsplit

    try:
        alias, target = item['alias'], item['target']
        observed = item['observation']
        if (item['case_id'] != case['id'] or item['query'] != case['query']
                or 'reference_answer' not in case or target not in case.get('reference_urls', [])
                or observed.get('url') != alias or not observed.get('verified_at')):
            return False
        canonical_url(alias)
        wanted = canonical_url(target)
        current = alias
        for step in observed.get('chain', []):
            if step.get('url') != current or step.get('status') not in (301, 302, 303, 307, 308):
                return False
            current = step['location']
            if canonical_url(current) == wanted:
                return True
        # Same-publisher canonical metadata can identify query/path variants.
        final = canonical_url(observed.get('final', ''))
        canonical = observed.get('canonical')
        return bool(observed.get('status') == 200 and not observed.get('error')
                    and final == canonical_url(current)
                    and canonical and canonical_url(canonical) == wanted
                    and urlsplit(final).netloc == urlsplit(wanted).netloc
                    and canonical_metadata(observed['final'], observed.get('canonical_evidence', [])) == {wanted})
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def validate_revision_controls(revision, normalizer=None):
    from importlib.resources import files

    ids = revision.get('regression_ids', [])
    if not revision.get('root_cause') or not ids or len(ids) != len(set(ids)):
        raise ValueError('Correction requires cause and unique regression provenance')
    if revision.get('factual_source_identities') and 'verified-factual-source-identities' not in ids:
        raise ValueError('Factual source identities require their paired regression controls')
    registry = json.loads(files('harness.benchmarks').joinpath('scoring-regressions.json').read_bytes())
    controls = registry.get('web_search', {})
    selected = set(ids) & controls.keys()
    for identity in selected:
        group = controls[identity]
        if {item['expected'] for item in group} != {True, False}:
            raise ValueError('Source identity regressions require paired controls')
        for control in group:
            if control.get('kind') == 'scenario_grade':
                from lexbench_websearch.answers import scenario_grade

                try:
                    result = scenario_grade({'answer': control['answer']}, json.dumps(control['judgment']))
                    accepted = result['evidence'] == control['answer']
                except ValueError:
                    accepted = False
                if accepted != control['expected']:
                    raise ValueError('Scenario decision-evidence regression failed')
                continue
            if control.get('kind') == 'citation_grade':
                from lexbench_websearch.answers import answer_packet, citation_grades

                packet = answer_packet(dict(id='citation-control', query='Who wrote it?'), control)
                try:
                    citation_grades(packet, json.dumps(dict(judgments=control['judgments'])))
                    accepted = True
                except ValueError:
                    accepted = False
                if accepted != control['expected']:
                    raise ValueError('Citation decision-evidence regression failed')
                continue
            if control.get('kind') == 'citation':
                from lexbench_websearch.answers import citation_inventory

                citations = citation_inventory(control['answer'], control['results'])
                accepted = bool(citations and all(c['in_results'] and control.get('claim_contains', '') in c['claim_context'] for c in citations))
                if 'claims' in control:
                    accepted = accepted and [c['claim_context'] for c in citations] == control['claims']
                if 'span_starts' in control:
                    accepted = accepted and [c['span'][0] for c in citations] == control['span_starts']
                if 'sources' in control:
                    accepted = accepted and [c['source'] for c in citations] == control['sources']
                if accepted != control['expected']:
                    raise ValueError('Native citation identity regression failed')
                continue
            if control.get('kind') == 'native_projection':
                from harness.web_search.core import normalize_response

                try:
                    results = normalize_response(control['payload'], control['method'], control['protocol'],
                                                 {'url': 'https://api.test/search'})
                    row = dict(results=results, http_status=200, error='')
                    accepted = (task_acceptance({'family': 'web'}, row, metric='availability')
                                and [bool(item.get('invalid_slot')) for item in results] == control.get('invalid_slots'))
                except ValueError:
                    accepted = False
                if accepted != control['expected']:
                    raise ValueError('Native slot-projection regression failed')
                continue
            if control.get('kind') == 'factual_output_level':
                from lexbench_websearch.outputs import project_rows
                from lexbench_websearch.scoring import answer_evidence_key

                profile, rows = project_rows(control['profile'], control['rows'], control['level'],
                                             qualified_fields=control.get('qualified_fields'))
                case, row = profile['cases'][0], rows[0]
                key = 'answer_in_preview_5' if control['level'] == 'l1' else answer_evidence_key(case, 5)
                if (bool(row['metrics'][key]) != control['expected']
                        or task_acceptance(case, row, cutoff=5, metric='answer') != control['expected']):
                    raise ValueError('Factual output-level contamination regression failed')
                continue
            if control.get('kind') == 'output_level':
                from lexbench_websearch.outputs import project_rows
                from lexbench_websearch.relevance import candidate_packet, score

                profile, rows = project_rows(control['profile'], control['rows'], control['level'],
                                             qualified_fields=control.get('qualified_fields'))
                packet, _ = candidate_packet(profile, rows)
                case = profile['cases'][0]
                labels = {case['id']:{c['candidate_id']:control['grades'][c['previews'][0]['snippet']]
                                     for c in packet['blind_candidate_packets'][0]['candidates']}}
                metrics = next(r for r in score(profile, rows, packet['plan'], labels) if r['method'] == control['method'])
                row = next(r for r in rows if r['method'] == control['method'])
                if task_acceptance(case, row, relevance_grades=metrics['ranked_grades']) != control['expected']:
                    raise ValueError('Output-level regression failed: ' + identity)
                continue
            if control.get('kind') == 'document_dedup':
                from lexbench_websearch.relevance import candidate_packet, score

                profile, rows = control['profile'], control['rows']
                packet, _ = candidate_packet(profile, rows)
                labels = {p['case_id']: {c['candidate_id']: 3 for c in p['candidates']}
                          for p in packet['blind_candidate_packets']}
                metrics = score(profile, rows, packet['plan'], labels)[0]
                if (metrics['duplicates'] != control['duplicates']
                        or any(metrics['duplicates']) != control['expected']
                        or not task_acceptance(profile['cases'][0], rows[0], relevance_grades=metrics['ranked_grades'])):
                    raise ValueError('Document-level relevance deduplication regression failed')
                continue
            if control.get('kind') == 'reference_mirror':
                from lexbench_websearch.simpleqa import qualify_mirror

                try:
                    proof = qualify_mirror(control['reference'], control['candidate'],
                        answer=control['case']['reference_answer'] if control.get('full_capture') else None)
                except ValueError:
                    if not control.get('error'):
                        raise
                    proof = {'qualified': False}
                case = deepcopy(control['case'])
                if proof['qualified']:
                    case['equivalent_reference_urls'] = [proof['url']]
                results = control['results']
                row = dict(results=results, metrics=evaluate(case, results))
                if (bool(row['metrics']['labeled_source_hit_5']) != control['expected']
                        or task_acceptance(case, row, 'source') != control['expected']):
                    raise ValueError('Reference mirror regression failed: ' + identity)
                continue
            if control.get('kind') == 'source_url':
                case, results = control['case'], control['results']
                row = dict(results=results, metrics=evaluate(case, results))
                if (bool(row['metrics']['labeled_source_hit_5']) != control['expected']
                        or task_acceptance(case, row, 'source') != control['expected']):
                    raise ValueError('Source URL regression failed: ' + identity)
                continue
            if control.get('kind') == 'factual_dataset':
                case, results = control['case'], control['results']
                row = dict(results=results, metrics=evaluate(case, results))
                if task_acceptance(case, row, 'answer') != control['expected']:
                    raise ValueError('Factual acceptance regression failed')
                if any(task_acceptance(case, row, metric) is not None
                       for metric in control.get('unavailable_metrics', [])):
                    raise ValueError('Unsupported factual metric was scored')
                continue
            if control.get('kind') == 'delivered_preview':
                from lexbench_websearch.relevance import candidate_packet, score

                profile, rows = control['profile'], control['rows']
                packet, _ = candidate_packet(profile, rows)
                candidates = packet['blind_candidate_packets'][0]['candidates']
                if len(candidates) != 2 or any(len(c['previews']) != 1 for c in candidates):
                    raise ValueError('Delivered preview isolation regression failed')
                labels = {profile['cases'][0]['id']: {
                    c['candidate_id']: control['grades'][c['previews'][0]['snippet']] for c in candidates}}
                scored = next(row for row in score(profile, rows, packet['plan'], labels) if row['method'] == control['method'])
                row = next(row for row in rows if row['method'] == control['method'])
                if task_acceptance(profile['cases'][0], row, relevance_grades=scored['ranked_grades']) != control['expected']:
                    raise ValueError('Delivered preview acceptance regression failed')
                continue
            case = deepcopy(control['case'])
            if factual_source_identity(case, control['identity']):
                case['reference_urls'].append(control['identity']['alias'])
            results = control['results']
            row = {'results': results, 'metrics': evaluate(case, results)}
            if (bool(row['metrics']['labeled_source_hit_5']) != control['expected']
                    or task_acceptance(case, row, metric='source') != control['expected']):
                raise ValueError('Source identity regression failed: ' + identity)
    if selected:
        revision = dict(revision, regression_ids=[item for item in ids if item not in selected])
        if not revision['regression_ids'] and not revision.get('normalization'):
            return
    if not revision.get("normalization"):
        validate_regressions(revision)
        return
    native = {"relative-serp-links", "unsafe-serp-links", "top-five-boundary"}
    if revision['normalization'] == 'request-url/2':
        native.add('invalid-result-slots')
        if not callable(normalizer):
            raise ValueError('Native normalization requires the execution adapter')
        for control in registry['web_search_normalization']['invalid-result-slots']:
            try:
                results = normalizer(control['payload'], control['method'], control['protocol'], control.get('request'))
            except ValueError:
                if control.get('error'):
                    continue
                raise
            if control.get('error'):
                raise ValueError('Error envelope regression failed')
            row = dict(results=results, metrics=evaluate(control['case'], results))
            if (row['metrics']['labeled_source_rr_5'] != control['expected_rr']
                    or task_acceptance(control['case'], row, 'source') != control['expected']):
                raise ValueError('Invalid result slot regression failed')
    ids = revision.get("regression_ids", [])
    if (
        revision["normalization"] not in ("request-url/1", "request-url/2")
        or not native.issubset(ids)
        or len(ids) != len(set(ids))
        or not revision.get("root_cause")
    ):
        raise ValueError(
            "Native normalization revision needs paired URL and cutoff controls"
        )
    shared = [i for i in ids if i not in native]
    if shared:
        validate_regressions(dict(revision, regression_ids=shared))
    normalization_regressions(revision.get("regressions_sha256"), normalizer)


def apply_reference_dataset(cases, selected, *, regression_ids=None):
    """Apply only validated reference extensions from a locked dataset revision."""
    from harness.benchmarks.data import binding, definition_path, safe_path
    from lexbench_websearch.contracts import validate_cases

    if (not isinstance(selected, dict) or set(selected) != {'track', 'revision', 'path', 'sha256'}
            or selected['track'] != 'simpleqa-verified'
            or selected['path'] != 'datasets/factual-v1.json'):
        raise ValueError('Reference extensions require the canonical SimpleQA dataset binding')
    source = binding(selected['track'], selected['revision'])
    descriptor = source['files']['definitions/' + selected['path']]
    if selected['sha256'] != descriptor['sha256']:
        raise ValueError('Reference dataset hash differs from the locked snapshot')
    dataset_path = definition_path(selected['track'], selected['path'], selected['revision'])
    raw = dataset_path.read_bytes()
    dataset = json.loads(raw)
    validate_cases(dataset['cases'])
    from lexbench_websearch.simpleqa import repair_cases, validate_reference_evidence

    repairs = dataset.get('case_repairs')
    if repairs and not set(repairs.get('regression_ids', [])).issubset(regression_ids or []):
        raise ValueError('Task repair requires its bound regression controls')
    prepared = ({case['id']: case for case in repair_cases(list(cases.values()), repairs, reuse_calls=True)}
                if repairs else cases)

    indexed = {case['id']: case for case in dataset['cases']}
    from lexbench_websearch.simpleqa import FULL_MIRROR_POLICIES, FULL_MIRROR_POLICY

    full = dataset.get('reference_equivalence', {}).get('method') in FULL_MIRROR_POLICIES
    if full and regression_ids is not None and 'reference-question-evidence' not in regression_ids:
        raise ValueError('Full reference revision requires question-evidence regression controls')
    if (dataset.get('reference_equivalence', {}).get('method') == FULL_MIRROR_POLICY
            and 'reference-answer-annotations' not in (regression_ids or [])):
        raise ValueError('Annotated reference answers require paired mirror controls')
    for case in prepared.values():
        if 'reference_answer' not in case:
            continue
        reference = indexed.get(case['id'])
        if reference is None or any(case.get(key) != reference.get(key)
                                    for key in ('id', 'query', 'reference_answer', 'reference_urls')):
            raise ValueError('Reference revision changes a frozen question, answer or original URL')

    def captured(path):
        expected = source['files'].get('definitions/' + path)
        if expected is None:
            raise ValueError('Reference capture is outside the locked snapshot')
        raw = safe_path(dataset_path.parent.parent, path).read_bytes()
        if len(raw) != expected['size'] or digest(raw) != expected['sha256']:
            raise ValueError('Reference capture differs from the locked snapshot')
        return raw

    validate_reference_evidence(dataset, captured)
    if repairs:
        for cid, case in cases.items():
            case.clear()
            case.update(prepared[cid])
    coverage = {item['case_id']: item for item in dataset.get('reference_equivalence', {}).get('case_coverage', [])}
    for case in cases.values():
        if 'reference_answer' not in case:
            continue
        reference = indexed.get(case['id'])
        for key in ('equivalent_reference_urls', 'equivalent_reference_provenance', 'url_equivalence'):
            if key not in reference:
                raise ValueError('Selected dataset has no qualified reference extension')
            case[key] = deepcopy(reference[key])
        if full:
            case['reference_verification_coverage'] = coverage[case['id']]
            case['capture_failed_reference_urls'] = [item['url'] for item in dataset['reference_equivalence_audit']
                if item['case_id'] == case['id'] and item['outcome'] == 'capture_failed']
            case['reference_verification_audit_url'] = ('https://huggingface.co/datasets/' + source['repository']
                + '/resolve/' + selected['revision'] + '/definitions/' + selected['path'])
    return {item['case_id'] for item in repairs['repairs'] if item['case_id'] in cases} if repairs else set()


def apply_report_revision(profile, rows, revision, *, artifacts=None, normalizer=None):
    """Add independently observed reference annotations to immutable saved calls."""
    if revision.get("schema") != "web-search-assessment-revision/1":
        raise ValueError("Unsupported assessment revision")
    validate_revision_controls(revision, normalizer)
    if revision.get("provider_calls") != 0:
        raise ValueError("An annotation revision cannot introduce provider calls")
    revised = deepcopy(profile)
    document_policy = revision.get('relevance_document_policy')
    if document_policy:
        if document_policy != 'document-url/2' or 'relevance-document-dedup' not in revision['regression_ids']:
            raise ValueError('Document relevance correction requires paired controls')
        revised['protocol']['relevance_document_policy'] = document_policy
    preview_rule = revision.get('relevance_input')
    if preview_rule:
        if preview_rule != 'delivered-previews/1' or 'delivered-preview-isolation' not in revision['regression_ids']:
            raise ValueError('Delivered preview correction requires paired controls')
        revised['protocol']['relevance_input'] = preview_rule
    contamination_rule = revision.get('factual_contamination_rule')
    if contamination_rule:
        from lexbench_websearch.evidence_quality import DATASET_ROWS_RULE

        if contamination_rule != DATASET_ROWS_RULE or 'structured-dataset-contamination' not in revision['regression_ids']:
            raise ValueError('Dataset contamination correction requires paired controls')
        for case in revised['cases']:
            if 'reference_answer' in case:
                case['factual_contamination_rule'] = contamination_rule
    answer_scope = revision.get('answer_evidence_scope')
    if answer_scope:
        if answer_scope != 'preview' or 'serp-answer-field-scope' not in revision['regression_ids']:
            raise ValueError('Answer field correction requires paired controls')
        for case in revised['cases']:
            if 'reference_answer' in case:
                case['answer_evidence_scope'] = answer_scope
    normalization = revision.get("normalization")
    if normalization:
        revised["protocol"]["result_url_resolution"] = normalization
    cases = {c["id"]: c for c in revised["cases"]}
    original_sources = {cid: deepcopy(case) for cid, case in cases.items()}
    if revision.get('reference_dataset'):
        if revision.get('factual_source_identities'):
            raise ValueError('A canonical reference revision cannot add a second URL authority')
        if not {'generic-document-urls', 'reference-mirror-title-and-lead'}.issubset(revision['regression_ids']):
            raise ValueError('Reference dataset revision requires URL and mirror regression controls')
        resolved = apply_reference_dataset(cases, revision['reference_dataset'],
                                           regression_ids=revision['regression_ids'])
        if resolved:
            revised['factual_quality_exclusions'] = [
                cid for cid in revised.get('factual_quality_exclusions', []) if cid not in resolved]
            revised['reference_adjudications'] = [
                item for item in revised.get('reference_adjudications', [])
                if item['case_id'] not in resolved]
    identities = set()
    for item in revision.get('factual_source_identities', []):
        cid, alias = item.get('case_id'), item.get('alias')
        if ((cid, alias) in identities
                or not factual_source_identity(original_sources.get(cid, {}), item)):
            raise ValueError('Unqualified factual source identity')
        identities.add((cid, alias))
        if alias not in cases[cid]['reference_urls']:
            cases[cid]['reference_urls'].append(alias)
    seen = set()
    for item in revision.get("cases", []):
        cid = item["case_id"]
        if cid in seen or cid not in cases:
            raise ValueError("Duplicate or unknown annotated case")
        seen.add(cid)
        if cases[cid].get("anchors"):
            raise ValueError("Additional annotations cannot replace existing anchors")
        anchors = item.get("anchors", [])
        if not _qualified(item):
            raise ValueError("Unqualified source anchor")
        cases[cid]["anchors"] = anchors
        cases[cid]["reference_annotation"] = item
        if revised.get('protocol', {}).get('reference_scope') == 'identity':
            checks = revised.setdefault('reference_checks', {})
            for anchor in anchors:
                source = next(s for s in item['sources'] if anchor in (s['url'], s.get('final_url'), s.get('qualifying_link')))
                checks[anchor] = {'url': source['url'], 'contains': [source['title']]}
    if revision.get("answer_match_rule"):
        if revision["answer_match_rule"] != POLICY_NAME:
            raise ValueError("Unsupported answer matching rule")
        for case in cases.values():
            if case.get("reference_answer"):
                case["answer_match_rule"] = revision["answer_match_rule"]

    if revision.get("answer_annotation_rule") not in (None, RULE_VERSION):
        raise ValueError("Answer derivation revision uses an unsupported annotation rule")
    derivations = revision.get("answer_derivations", [])
    if derivations:

        if (revision.get("answer_annotation_rule") != RULE_VERSION
                or not {"published-answer-citation-metadata", "literal-bracketed-answer"}.issubset(revision["regression_ids"])):
            raise ValueError("Reference citation cleanup requires a bound scoring revision")
        seen = set()
        for item in derivations:
            case = cases.get(item["case_id"], {})
            metadata = {k: v for k, v in item.items() if k not in {"case_id", "query"}}
            if (item["case_id"] in seen or item.get("query") != case.get("query")
                    or item.get("reference") != case.get("reference_answer")
                    or case.get("answer_derivation", metadata) != metadata):
                raise ValueError("Answer derivation does not bind the unchanged case")
            reference_rule(case["reference_answer"], derivation=metadata)
            seen.add(item["case_id"])
            case["answer_derivation"] = deepcopy(metadata)
    exclusions = set(revised.get("factual_quality_exclusions", []))
    decisions = {}
    incoming = revision.get("reference_adjudications", [])
    for batch in (revised.get("reference_adjudications", []), incoming):
        seen = set()
        for item in batch:
            cid = item["case_id"]
            if cid in seen or cid in decisions and decisions[cid] != item:
                raise ValueError("Duplicate or conflicting reference adjudication")
            seen.add(cid)
            decisions[cid] = item
    for item in decisions.values():
        cid = item["case_id"]
        case = cases.get(cid, {})
        if (
            "reference_answer" not in case
            or item.get("query") != case.get("query")
            or item.get("reference_answer") != case["reference_answer"]
            or item.get("quality_excluded") is not True
            or not item.get("reason")
            or not item.get("evidence")
        ):
            raise ValueError(
                "Reference adjudication does not bind the unchanged factual case"
            )
        exclusions.add(cid)
    if exclusions:
        revised["factual_quality_exclusions"] = sorted(exclusions)
    if decisions:
        revised["reference_adjudications"] = [
            decisions[cid] for cid in sorted(decisions)
        ]
    validate_cases(revised["cases"], reference_checks=revised.get("reference_checks"), reference_scope=revised.get("protocol", {}).get("reference_scope"))
    updated, changes = [], []
    for row in rows:
        new = dict(row)
        if (
            normalization
            and row["http_status"] == 200
            and not row.get("transport_exit")
        ):
            path = row["evidence"]["path"]
            if (
                artifacts is None
                or path not in artifacts
                or digest(artifacts[path]) != row["evidence"]["sha256"]
            ):
                raise ValueError("Native artifact binding mismatch")
            try:
                method = next(m for m in revised["methods"] if m["id"] == row["method"])
                new["results"] = normalizer(
                    json.loads(artifacts[path]),
                    method,
                    assessment_protocol(revised, row, revision),
                    row.get("request"),
                )
                new["error"] = ""
            except (ValueError, TypeError, AttributeError, KeyError):
                # Preserve the original failed response and its unchanged evidence.
                new = dict(row)
        new["metrics"] = evaluate(cases[row["case_id"]], new["results"])
        if (
            new["metrics"] != row["metrics"]
            or new["results"] != row["results"]
            or new.get("error") != row.get("error")
        ):
            change = {
                "id": row["id"],
                "before": row["metrics"],
                "after": new["metrics"],
            }
            if normalization:
                change["normalization"] = {
                    "before_sha256": digest(
                        json.dumps(
                            row["results"], sort_keys=True, ensure_ascii=False
                        ).encode()
                    ),
                    "after_sha256": digest(
                        json.dumps(
                            new["results"], sort_keys=True, ensure_ascii=False
                        ).encode()
                    ),
                    "before_count": len(row["results"]),
                    "after_count": len(new["results"]),
                    "before_error": row.get("error"),
                    "after_error": new.get("error"),
                }
            changes.append(change)
        updated.append(new)
    return revised, updated, changes


def collection_protocol(profile, row):
    """Use the row's frozen source controls; assessment revisions remain explicit."""
    if profile.get('collection_composition') == 'matrix':
        batches = [b for b in profile.get('method_collections', []) if b['id'] == row.get('collection')]
        if len(batches) != 1:
            raise ValueError('Missing unique source protocol for matrix row')
        return batches[0]['protocol']
    return profile['protocol']


def assessment_protocol(profile, row, revision=None):
    """Apply only explicitly revised controls to each row's collection protocol."""
    protocol = dict(collection_protocol(profile, row))
    revision = (revision or {}).get('contract', revision or {})
    if (revision or {}).get('normalization'):
        protocol['result_url_resolution'] = revision['normalization']
    return protocol


def collected_projection(profile, rows, artifacts, normalizer):
    """Reconstruct collection projections through the frozen execution adapter."""
    cases = {c["id"]: c for c in profile["cases"]}
    methods = {m["id"]: m for m in profile["methods"]}
    restored = []
    for row in rows:
        original = dict(row, results=[])
        if row["http_status"] == 200 and not row.get("transport_exit"):
            path = row["evidence"]["path"]
            if (
                path not in artifacts
                or digest(artifacts[path]) != row["evidence"]["sha256"]
            ):
                raise ValueError("Native artifact binding mismatch")
            try:
                original["results"] = normalizer(
                    json.loads(artifacts[path]),
                    methods[row["method"]],
                    collection_protocol(profile, row),
                    row.get("request"),
                )
                original["error"] = ""
            except (ValueError, TypeError, AttributeError, KeyError) as exc:
                original["error"] = str(exc)
        original["metrics"] = evaluate(cases[row["case_id"]], original["results"])
        restored.append(original)
    return restored


def apply_identity_correction(profile, rows, correction):
    validate_regressions(correction)
    observations = {x["url"]: x for x in correction["observations"]}
    revised = deepcopy(profile)
    cases = {c["id"]: c for c in revised["cases"]}
    for cid, aliases in correction["aliases"].items():
        case = cases[cid]
        for alias, target in aliases.items():
            a, b = observations[alias], observations[target]
            if not _equivalent(a, b):
                raise ValueError("Unproven equivalent page")
            if target not in case.get("anchors", []):
                raise ValueError("Correction must retain the original known target")
        case["identity_aliases"] = dict(aliases)
    validate_cases(
        revised["cases"],
        reference_checks=revised.get("reference_checks"),
        reference_scope=revised.get("protocol", {}).get("reference_scope"),
    )
    return _rescore(revised, rows)

def validate_revision_binding(revision, *, authority=None, normalizer=None):
    registered = (authority or {}).get("assessment_revisions", {})
    if registered.get("schema") != "search-assessment-authority/1":
        raise ValueError("Assessment revision requires an explicit external scoring authority")
    identity = digest(json.dumps(revision, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    records = [p for p in registered.get("provenance", []) if p.get("sha256") == identity]
    if identity not in registered.get("approved_sha256", []) or len(records) != 1:
        raise ValueError("Assessment revision is not bound by the versioned scoring authority")
    for key in ("root_cause", "regression_ids", "provider_calls"):
        if records[0].get(key) != revision.get(key):
            raise ValueError("Assessment revision provenance mismatch")
    validate_revision_controls(revision, normalizer)
