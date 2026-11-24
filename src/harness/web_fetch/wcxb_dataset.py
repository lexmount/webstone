"""Freeze a type-stratified WCXB test sample without reading provider outputs."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path

from harness.distribution import repository_root, source_path

SEED = "wcxb-v1-test-stratified-200-20260917"
ROOT = repository_root()


class VisibleBody(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_body = False
        self.skip = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'body': self.in_body = True
        if tag in ('script', 'style', 'template', 'noscript', 'svg'): self.skip.append(tag)

    def handle_endtag(self, tag):
        if tag == 'body': self.in_body = False
        if self.skip and self.skip[-1] == tag: self.skip.pop()

    def handle_data(self, data):
        if self.in_body and not self.skip: self.parts.append(data)


def selected_wcxb_input(source: Path | None = None, manifest: Path | None = None, *, profile: Path | None = None) -> tuple[Path, Path]:
    """Use locked defaults only when no source edition was explicitly selected."""
    from harness.benchmarks.data import definition_path
    default_source = source is None
    if manifest is None and profile is not None:
        declared = json.loads(profile.read_bytes()).get('sample_manifest')
        if declared:
            path = Path(declared)
            manifest = path if path.is_absolute() else source_path(path, root=ROOT)
    if default_source:
        source = definition_path('wcxb', 'source')
        locked_manifest = definition_path('wcxb', 'tasks.json')
        if manifest is not None and manifest.resolve() != locked_manifest.resolve():
            selected_hash = json.loads(manifest.read_bytes()).get('source_sha256')
            locked_hash = json.loads(locked_manifest.read_bytes()).get('source_sha256')
            if not selected_hash or selected_hash != locked_hash:
                raise ValueError('The WCXB profile selects another source edition; provide its explicit --source/--dataset')
    if manifest is None:
        if default_source:
            manifest = locked_manifest
        else:
            if (source.parent / 'tasks.json').is_file():
                manifest = source.parent / 'tasks.json'
            else:
                raise ValueError('An explicit WCXB source requires its manifest or a profile sample_manifest')
    return source, manifest


def load_wcxb_source(root: Path, split: str = "test", *, manifest: Path | None = None) -> tuple[list[dict], str]:
    """Bind every frozen input and annotation byte before provider calls."""
    directory = root / split
    html_dir = directory / "html"
    gt_dir = directory / "ground-truth"
    records = []
    inventory = hashlib.sha256()
    for gt_path in sorted(gt_dir.glob("*.json")):
        html_path = html_dir / f"{gt_path.stem}.html.gz"
        if not html_path.is_file() or gt_path.is_symlink() or html_path.is_symlink():
            raise ValueError(f"Missing or linked WCXB input: {gt_path.stem}")
        gt_bytes, html_bytes = gt_path.read_bytes(), html_path.read_bytes()
        for path, payload in ((gt_path, gt_bytes), (html_path, html_bytes)):
            inventory.update(str(path.relative_to(root)).encode() + b"\0")
            inventory.update(hashlib.sha256(payload).digest())
        data = json.loads(gt_bytes)
        content = gzip.decompress(html_bytes).decode("utf-8", errors="replace")
        page_type = data.get("_internal", {}).get("page_type", {}).get("primary", "article")
        if page_type == "category":
            page_type = "collection"
        records.append({
            "track_id": gt_path.stem,
            "url": data["url"],
            "html": content,
            "groundtruth_content": data["ground_truth"].get("main_content") or "",
            "ground_truth": data['ground_truth'],
            "meta": {"page_type": page_type, "split": split},
        })
    authority_path = manifest if manifest is not None else root.parent / 'tasks.json'
    authority = (json.loads(authority_path.read_bytes())
                 if manifest is not None or authority_path.is_file() else None)
    if (manifest is not None or authority is not None) and not isinstance(authority, dict):
        raise ValueError('Invalid WCXB source edition')
    # Discovered and explicit historical selections use the same full corpus.
    if authority is not None and authority.get('schema_version') in (None, 1):
        authority = None
    if authority is not None:
        if (authority.get('schema_version') != 2 or authority.get('split') != split
                or type(authority.get('source_count')) is not int or authority['source_count'] <= 0):
            raise ValueError('Invalid WCXB source edition')
        expected = authority['source_count']
    else:
        expected = 511 if split == "test" else 1497
    if len(records) != expected or len({r["track_id"] for r in records}) != expected:
        raise ValueError(f"Expected {expected} distinct WCXB {split} inputs, found {len(records)}")
    source_hash = inventory.hexdigest()
    if authority is not None:
        if source_hash != authority.get('source_sha256'):
            raise ValueError('WCXB source differs from its qualified edition')
        if split == 'test':
            if authority.get('scope') not in {'qualified_test', 'full_test', 'stratified_sample'}:
                raise ValueError('Unsupported WCXB source selection scope')
            validate_manifest(records, source_hash, authority)
        elif {r['track_id'] for r in records} != {c['file_id'] for c in authority['cases']}:
            raise ValueError('WCXB source differs from its qualified edition')
    return records, source_hash



def full_test_exclusions(records: list[dict]) -> list[dict]:
    """Source-only validity check; never consult provider outputs or scores."""
    excluded = []
    for record in records:
        reference = record['groundtruth_content']
        visible = VisibleBody()
        visible.feed(record['html'])
        body = ' '.join(' '.join(visible.parts).split())
        if not reference.strip():
            reason = 'empty_main_content_reference'
        elif not body:
            reason = 'reference_body_absent_from_frozen_html'
        else:
            continue
        excluded.append({'file_id': record['track_id'], 'reason': reason,
                         'visible_body_chars': len(body), 'reference_chars': len(reference)})
    return excluded


def validate_manifest(records: list[dict], source_hash: str, manifest: dict) -> tuple[list[dict], dict]:
    scope = manifest.get('scope')
    if scope in ('full_test', 'qualified_test'):
        invalid = {row['file_id'] for row in full_test_exclusions(records)}
        selected = sorted((r for r in records if scope == 'full_test' or r['track_id'] not in invalid), key=lambda r: r['track_id'])
        distribution = {'parent': dict(Counter(r['meta']['page_type'] for r in records)),
                        'sample': dict(Counter(r['meta']['page_type'] for r in selected))}
        if scope == 'full_test':
            expected_exclusions = full_test_exclusions(selected)
            if (manifest.get('size') != len(selected) or manifest.get('exclusions') != expected_exclusions
                    or manifest.get('primary_quality_case_count') != len(selected) - len(expected_exclusions)):
                raise ValueError('WCXB full-test source qualification drifted')
        elif manifest.get('size') != len(selected) or manifest.get('exclusions') or manifest.get('primary_quality_case_count') != len(selected):
            raise ValueError('WCXB qualified-test selection drifted')
    else:
        selected, distribution = choose(records, 200)
        if manifest.get('seed') != SEED or manifest.get('size') != 200:
            raise ValueError('WCXB sample selection drifted')
    expected_cases = [{'file_id': r['track_id'], 'page_type': r['meta']['page_type']} for r in selected]
    actual_cases = manifest.get('cases')
    if manifest.get('schema_version') == 2:
        if manifest.get('source_count') != len(records):
            raise ValueError('WCXB qualified edition count differs from its inputs')
        actual_cases = [{'file_id': c['file_id'], 'page_type': c['page_type']} for c in actual_cases]
        by_id = {r['track_id']: r for r in selected}
        for case in manifest['cases']:
            row = by_id.get(case['file_id'])
            if row is None or any(case.get(field) != value for field, value in (
                    ('url', row['url']), ('reference', row['groundtruth_content']),
                    ('with', row['ground_truth'].get('with') or []),
                    ('without', row['ground_truth'].get('without') or []))):
                raise ValueError('WCXB task fields differ from frozen ground truth')
    if (manifest.get('source_sha256') != source_hash or manifest.get('split') != 'test'
            or manifest.get('distribution') != distribution or actual_cases != expected_cases):
        raise ValueError('WCXB manifest differs from source-only selection')
    return selected, distribution


def choose(records: list[dict], size: int) -> tuple[list[dict], dict]:
    groups = defaultdict(list)
    for record in records:
        groups[record["meta"]["page_type"]].append(record)
    counts = {kind: len(items) for kind, items in sorted(groups.items())}
    ideal = {kind: size * count / len(records) for kind, count in counts.items()}
    quotas = {kind: int(value) for kind, value in ideal.items()}
    remaining = size - sum(quotas.values())
    for kind in sorted(counts, key=lambda k: (-(ideal[k] - quotas[k]), k))[:remaining]:
        quotas[kind] += 1
    selected = []
    for kind in sorted(groups):
        ordered = sorted(groups[kind], key=lambda r: hashlib.sha256(
            f"{SEED}:{r['track_id']}".encode()).hexdigest())
        selected.extend(ordered[:quotas[kind]])
    selected.sort(key=lambda r: r["track_id"])
    return selected, {"parent": counts, "sample": dict(Counter(r["meta"]["page_type"] for r in selected))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--full-test", action="store_true")
    parser.add_argument("--qualified-test", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Sample manifest already exists")
    edition_path = None
    if args.source is None:
        args.source, edition_path = selected_wcxb_input()
    elif (args.source.parent / 'tasks.json').is_file():
        edition_path = args.source.parent / 'tasks.json'
    records, source_hash = load_wcxb_source(args.source, manifest=edition_path)
    edition = json.loads(edition_path.read_bytes()) if edition_path is not None else {}
    if edition.get('schema_version') == 2:
        validate_manifest(records, source_hash, edition)
    if args.full_test and args.qualified_test:
        parser.error('Choose one test scope')
    if args.full_test or args.qualified_test:
        invalid = {row['file_id'] for row in full_test_exclusions(records)} if args.qualified_test else set()
        selected = sorted((r for r in records if r['track_id'] not in invalid), key=lambda r: r['track_id'])
        distribution = {'parent': dict(Counter(r['meta']['page_type'] for r in records)),
                        'sample': dict(Counter(r['meta']['page_type'] for r in selected))}
    else:
        selected, distribution = choose(records, 200)
    payload = {"schema_version": 1, "dataset_id": "wcxb-v1-test-qualified" if args.qualified_test else "wcxb-v1-test-full" if args.full_test else "wcxb-v1-test-stratified-200",
               "source_commit": "c039d5ee9f5a3a984a0e167e63aacd04e76e78a9",
               "source_sha256": source_hash, "split": "test", "scope": "qualified_test" if args.qualified_test else "full_test" if args.full_test else "stratified_sample",
               "seed": None if (args.full_test or args.qualified_test) else SEED,
               "algorithm": "all source-qualified test cases by file ID" if args.qualified_test else "all test cases by file ID" if args.full_test else "Hamilton proportional quotas by page type; within each type sort by SHA-256(seed:case_id)",
               "size": len(selected), "distribution": distribution,
               "cases": [{"file_id": r["track_id"], "page_type": r["meta"]["page_type"]} for r in selected]}
    if edition.get('schema_version') == 2:
        payload.update(schema_version=2, source_count=len(records),
                       cases=[{'file_id': r['track_id'], 'page_type': r['meta']['page_type'],
                               'url': r['url'], 'reference': r['groundtruth_content'],
                               'with': r['ground_truth'].get('with') or [],
                               'without': r['ground_truth'].get('without') or []} for r in selected])
        if payload['scope'] == edition.get('scope'):
            payload['dataset_id'] = edition['dataset_id']
        else:
            payload['parent_dataset_id'] = edition['dataset_id']
        if 'source_commit' in edition:
            payload['source_commit'] = edition['source_commit']
        else:
            payload.pop('source_commit')
    if args.full_test:
        exclusions = full_test_exclusions(selected)
        payload['exclusions'] = exclusions
        payload['primary_quality_case_count'] = len(selected) - len(exclusions)
    if args.qualified_test:
        payload['primary_quality_case_count'] = len(selected)
    validate_manifest(records, source_hash, payload)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
    print(json.dumps({"source_sha256": source_hash, "sample_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                      "distribution": distribution}, ensure_ascii=False))
