"""Evidence-first report: measured tracks, canonical cases, no review workflow."""
from __future__ import annotations


from harness.web_fetch.report_i18n import saved_matrix_text

from harness.distribution import repository_root

import hashlib
import html
import json
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import replace
from functools import wraps
from math import ceil
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from lexbench_webfetch.body_coverage import (
 _compact_with_positions,
 _global_region_allocation,
 compact_ngrams,
 evaluate_body_requirements,
 native_markup_body_text,
 tokens,
)
from lexbench_webfetch.dynamic_lists import visible_html_text
from lexbench_webfetch.metrics import normalize_for_match, percentile, task_measured, task_passed
from lexbench_webfetch.models import TRACKS
from lexbench_webfetch.paths import contained_path
from lexbench_webfetch.reference_quality import is_html_document_or_fragment
from lexbench_webfetch.response_input import answer_input
from lexbench_webfetch.stable_tasks import SCHEMA_FIELDS, _parse_records, _visible

from harness.numbers import nonnegative_number
from harness.report_languages import (
 check_language_contract,
 english_notice,
 generated_languages,
 primary_report_language,
 select_report_language,
 write_language_contract,
)
from harness.reporting.mermaid import mermaid_script_attributes
from harness.reporting.presentation import esc
from harness.web_fetch.adjudication_evidence import adjudication_reason
from harness.web_fetch.report_model import (
 ReportModel,
 RenderContext,
 render_context,
 rendering,
 reset_render_context,
 set_render_context,
)
from harness.web_fetch.template import role_attribute
from harness.web_fetch.failure_diagnosis import diagnose
from harness.web_fetch.methods import (
 ROUTE_LABELS,
 browser_method_label as _catalog_browser_method_label,
 is_browser_comparison,
 method_access,
 method_page_model,
 method_version,
 method_versions,
 report_method_catalog,
 with_measured_method_labels,
)
from harness.web_fetch.models import REPORT_FAMILIES, execution_id, report_family as task_report_family
from harness.reporting.publication_privacy import (
 project_evidence_view,
 public_render_views,
 read_evidence_binding,
 require_public_method_options,
)
from harness.web_fetch.reliability import assess_reliability
from harness.web_fetch.report_changes import (
 ROOT,
 localized_release_history,
 render_changes,
 render_localized_release_history,
 require_review_rounds,
)
from harness.web_fetch.report_components import (
 basis_label,
 comparable_body_reference,
 examples,
 list_rule_description,
 measurement_presentation,
 metric_guide,
 metric_legend,
 reference_label,
 reference_status,
 reference_text,
 render_acceptance_scope,
 sources_panel,
 stable_contract_details,
 tabs,
 task_contract_guide,
 task_instruction,
 task_pass_guide,
)
from harness.web_fetch.report_diagnostic import (
 FAILURE_TYPES,
 diagnostic_call_summary,
 diagnostic_cohort_summary,
 diagnostic_failure_counts,
 diagnostic_findings,
 diagnostic_specimen_excerpt,
)
from harness.web_fetch.report_explainer import (
 _article_speed_population,
 load_browser_failure_review,
 render_explainer,
)
from harness.web_fetch.report_i18n import pair, report_text, t, write_english_report
from harness.web_fetch.report_interfaces import method_provenance
from harness.web_fetch.report_methods import render_methods
from harness.web_fetch.report_quality import render_quality_observations
from harness.web_fetch.report_reading import render_archived_reading
from harness.web_fetch.report_resources import render_resource_comparison
from harness.reporting.style import STYLE
from harness.web_fetch.report_style import asset_text
from harness.web_fetch.result_ledger import validate_ledger
from harness.safety import safe_href, validate_report_ids


def browser_method_label(name, *, language="zh"):
 label = render_context().labels.get(name)
 return label if label is not None else _catalog_browser_method_label(name, language=language)

def provider_label(name):
 return browser_method_label(name)

def report_family(case):
 return case.get('report_family') or task_report_family(case)

def case_track_label(case):
 if case.get('report_family_label'):return esc(case['report_family_label'])
 family=report_family(case)
 if family in {'web_media','media_file'}:return REPORT_FAMILIES[family]
 if case.get('track','web')=='web':
  return {'file':t('file_delivery', 'File delivery'),'resource':t('resource_discovery', 'Resource discovery'),'structure':t('structured_content', 'Structured content')}.get(case.get('task_kind','body'),TRACKS['web'])
 return TRACKS[case['track']]
REPORT_TRACKS = {key: TRACKS[key] for key in ('web', 'dynamic', 'challenge', 'risk_control', 'document', 'image', 'video')}

def active_tracks(cases):
 if render_context().suites:
  return [(s.id,s.label) for s in render_context().suites if set(s.case_ids) & set(cases)]
 rows=[]
 for key,label in REPORT_FAMILIES.items():
  selected=[c for c in cases.values() if report_family(c)==key]
  if not selected:continue
  if key=='web' and any(c.get('task_kind','body')!='body' for c in selected):
   labels={case_track_label(c) for c in selected}
   label=next(iter(labels)) if len(labels)==1 else t('web_tasks', 'Web tasks')
  rows.append((key,label))
 return rows

def track_count_label(count):return str(count)

def is_headless_cohort(records):
 return is_browser_comparison(records)

def provider_configuration(name,records,info):
 """Compatibility accessor for the generic method-page model."""
 return method_page_model(name,provider_label(name),records)['configuration']

def provider_page_body(name,records,cases,headers,tracks,*,native_default=False):
 """Render one method page from generic result records and method metadata."""
 model=method_page_model(name,provider_label(name),records)
 english_model=method_page_model(name,browser_method_label(name,language='en'),records,language='en')
 def field(key):return model_text(model[key],english_model[key])
 controls=filter_controls([('track',t('family', 'Family'),tracks),('result',t('result', 'Result'),[('pass',t('pass', 'Pass')),('fail',t('fail', 'Fail'))])]+failure_filters(records,cases))
 configuration=f'<p><code>{field("endpoint")}</code></p>'
 if native_default:
  configuration+='<p>'+model_text(*pair('call_counts_retries_and_deadlines_come_from', 'Call counts, retries and deadlines come from run records; internal parameters come from saved requests.'))+'</p>'
  configuration+='<p>'+link('../methodology/index.html',t('view_run_parameters', 'View run parameters'),'View execution parameters')+' · '+link('../reproduce/index.html',t('view_frozen_configuration_and_evidence', 'View frozen configuration and evidence'),'View frozen configuration and evidence')+'</p>'
 else:
  configuration+=f'<p>{field("freshness")}</p><p>{field("configuration")}</p>'
 body=hero(field('title'),field('contract'))+section(t('measured_configuration', 'Measured configuration'),configuration)
 body+=section(t('method_resources', 'Method resources'),'<p class="actions">'+link('../adoption/index.html#'+model['pricing_anchor'],t('pricing_billing', 'Pricing & billing →'))+link('../adoption/index.html#interface-'+name,t('interfaces_examples', 'Interfaces & examples →'))+'</p>')
 return body+section(t('execution_records', '{count} execution records', count=len(records)),controls+table(headers,[attempt_row(r,cases[r['case_id']]) for r in records],'filtered-table'))
JS=asset_text('report.js').replace('/*mermaid-script-attributes*/',mermaid_script_attributes('script'))
EXTRA=asset_text('report-extra.css')

def readable_output(s):
 return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',lambda m:'\\x'+format(ord(m[0]),'02x'),s)

_PRIVATE_LOG_KEYS={'raw_response_path','diagnostic_log_path','private_artifacts','private_artifact','private_path'}
def public_provider_metadata(value):
 """Remove pointers to private envelopes while keeping public adapter evidence."""
 if isinstance(value,dict):
  return {key:public_provider_metadata(item) for key,item in value.items()
          if key not in _PRIVATE_LOG_KEYS and not key.startswith('private_')}
 if isinstance(value,list):return [public_provider_metadata(item) for item in value]
 return value

def call_log_bindings(record):
 metadata=record.get('provider_metadata') or {}
 candidates=[]
 retry=metadata.get('retry') or {}
 if isinstance(retry,dict):
  for index,attempt in enumerate(retry.get('attempts',[]),1):
   if isinstance(attempt,dict):candidates.append((t('physical_call', 'Physical call {get}', get=attempt.get('physical_attempt', index)),attempt.get('call_log')))
 candidates.extend([(t('final_physical_call', 'Final physical call'),record.get('call_log')),(t('final_physical_call', 'Final physical call'),metadata.get('call_log')),
                    (t('final_physical_call', 'Final physical call'),{'path':record.get('call_log_path'),'sha256':record.get('call_log_sha256')}),
                    (t('final_physical_call', 'Final physical call'),{'path':metadata.get('call_log_path'),'sha256':metadata.get('call_log_sha256')})])
 result=[];seen=set()
 for label,binding in candidates:
  if not isinstance(binding,dict):continue
  path=binding.get('path')
  if not path or str(path).startswith('private_artifacts/') or str(path) in seen:continue
  seen.add(str(path));result.append((label,dict(binding)))
 return result

def call_log_path(record):
 bindings=call_log_bindings(record)
 return bindings[-1][1]['path'] if bindings else None

def _error_locator(message):
 """Use a retained error line, keeping complete traces in the log section."""
 text=str(message or '')
 if len(text)<=300:return text
 lines=text.splitlines()
 for line in reversed(lines):
  if re.search(r'^(?:Reason:|Error:|[\w.]*Error:)|\b(?:timed out|timeout_phase=)',line,re.I):
   return line[:300]
 return t('error_details_are_in_this_run_s', "Error details are in this run's log.")


def empty_output_reason(record,case,output):
 if output:return ''
 metadata=record.get('provider_metadata') or {}
 delivered=metadata.get('file_evaluation',{}).get('files',[])
 if case.get('task_kind')=='file' and delivered:
  return t('this_task_delivered_original_files_empty_text', 'This task delivered {count} original files. Empty text does not mean nothing was delivered; the file bytes are listed separately below.', count=len(delivered))
 if record.get('error_message'):
  return t('the_final_output_is_genuinely_empty_the', 'The final output is genuinely empty. The adapter recorded this reason: {error_locator}', error_locator=_error_locator(record['error_message']))
 if not record.get('ok'):
  error_type=record.get('error_type') or t('error_type_not_recorded', 'Error type not recorded')
  return t('the_final_output_is_genuinely_empty_the_2', 'The final output is genuinely empty. The request failed with error type {error_type}; the historical record retained no further error text.', error_type=error_type)
 if metadata.get('empty_content'):
  return t('the_final_output_is_genuinely_empty_the_3', 'The final output is genuinely empty. The upstream request returned a response, but the adapter obtained no extractable body text.')
 return t('the_final_output_is_genuinely_empty_this', 'The final output is genuinely empty. This historical record retained no more specific reason, so it cannot establish whether the cause was the server, network, or adapter.')

def _reader_log_excerpt(text):
 """Select real log lines for the reader while preserving original line numbers."""
 lines=text.splitlines();selected=set()
 if lines:selected.add(0)
 diagnostic=re.compile(r'error|warn|fail|timeout|timed out|panic|denied|challenge',re.I)
 lifecycle=re.compile(r'fetch request complete|final_url=|document_to_markdown|extract|completed.*fetch|finished.*fetch',re.I)
 for index,line in enumerate(lines):
  if diagnostic.search(line):selected.update(range(max(0,index-1),min(len(lines),index+2)))
  elif lifecycle.search(line) and len(selected)<12:selected.add(index)
  if len(selected)>=18:break
 if not selected:selected.update(range(min(12,len(lines))))
 return '\n'.join(f'{index+1:>6}  {lines[index]}' for index in sorted(selected)[:18])

def _worker_response_stream(row):
 """Recognize saved worker protocol responses, which belong to output evidence."""
 if not isinstance(row,dict) or row.get('source')!='native_process_stdout':return False
 try:payload=json.loads(row.get('text',''))
 except (ValueError,TypeError):return False
 return isinstance(payload,dict) and isinstance(payload.get('ok'),bool) and any(key in payload for key in ('content','raw_tool_response','error_type'))

def runtime_log_text(text):
 """Exclude protocol responses from the public log; retain private capture unchanged."""
 lines=[]
 for line in text.splitlines(keepends=True):
  try:row=json.loads(line)
  except ValueError:row=None
  if not _worker_response_stream(row):lines.append(line)
 return ''.join(lines)

def _separate_observed_logs(text):
 """Separate captured product streams from caller events without inventing steps."""
 markers=list(re.finditer(r'^--- native [^\n]+ ---\s*$',text,re.M))
 caller=text[:markers[0].start()] if markers else text
 native='\n'.join(text[m.end():markers[i+1].start() if i+1<len(markers) else len(text)].lstrip('\n') for i,m in enumerate(markers))
 events=[];library=[]
 for line in caller.splitlines():
  try:row=json.loads(line)
  except json.JSONDecodeError:
   events.append(line);continue
  if _worker_response_stream(row):continue
  if isinstance(row,dict) and row.get('source') in ('native_process_stderr','native_process_stdout'):
   stream=str(row.get('text',''))
   try:payload=json.loads(stream)
   except ValueError:payload=None
   if isinstance(payload,dict) and payload.get('source')=='caller_worker':events.append(stream.rstrip('\n'))
   else:library.append(stream)
  elif isinstance(row,dict) and row.get('source')=='native_library':
   library.append(str(row.get('message',''))+(('\n'+str(row['exception'])) if row.get('exception') else ''))
  else:events.append(line)
 if library:native+='\n'+'\n'.join(library)
 return native.strip('\n'), '\n'.join(events)


def _log_evidence_dialog(relative, label):
    """Use the shared evidence dialog, backed by one existing public file."""
    target='saved-log-'+hashlib.sha256(relative.encode()).hexdigest()[:16]
    return ((f'''<button type="button" class="call-log-button" data-call-log-open="{target}" aria-haspopup="dialog">{model_text(*pair('open_complete_record', 'Open complete record'))}</button><dialog class="call-log-dialog wmb-detail-dialog" id="{target}" aria-labelledby="{target}-title"><div class="call-log-shell"><header><h2 id="{target}-title">{label}</h2><button type="button" data-call-log-close aria-label="''' + t('close', 'Close') + '">' + t('close', 'Close') + f'</button></header><div class="wmb-dialog-content"><pre data-file="{esc(relative)}" data-readable="1" data-evidence-kind="log"></pre></div></div></dialog>'))


def diagnostic_view_page(out, relative, label, note):
    """Show the real sanitized text in HTML; retain its original download bytes."""
    source=contained_path(out,relative)
    raw=source.read_bytes()
    view=Path(relative).with_suffix('.html').as_posix()
    body=(hero(label,t('actual_saved_records_formatted_for_browsing_without', 'Actual saved records, formatted for browsing without additional product calls.'))+
          section(t('record_source', 'Record source'),f"""<p>{esc(note)}</p><p>{link('../' + relative, t('download_original_public_file', 'Download original public file'))} · {str(len(raw))} 字节 · SHA-256：<code class="break">{hashlib.sha256(raw).hexdigest()}</code></p>""")+
          section(model_text(*pair('record_excerpt', 'Record excerpt')),'<pre>'+esc(_diagnostic_log_excerpt(raw.decode('utf-8','replace'))[:6000])+'</pre>')+
          section(model_text(*pair('complete_record', 'Complete record')),_log_evidence_dialog(relative,esc(label))))
    write(out,view,label,body)
    return view


def run_log_section(out,record,output):
 """Render the approved summary → fields → real-log drill-down."""
 metadata=record.get('provider_metadata') or {}
 bindings=call_log_bindings(record);logs=[]
 for label,binding in bindings:
  path=contained_path(out,binding['path'])
  if not path.is_file():continue
  raw=path.read_bytes();observed=hashlib.sha256(raw).hexdigest()
  if render_context().score_audit:
   from harness.web_fetch.published_snapshot import read_snapshot_public_file
   read_snapshot_public_file(out,binding)
  else:
   if binding.get('sha256') and binding['sha256']!=observed:raise ValueError(f'Call log hash mismatch: {record["attempt_id"]}')
   if binding.get('bytes') is not None and binding['bytes']!=len(raw):raise ValueError(f'Call log size mismatch: {record["attempt_id"]}')
  try:payload=json.loads(raw)
  except (UnicodeDecodeError,json.JSONDecodeError):payload={}
  logs.append({'label':label,'path':binding['path'],'sha256':observed,
               'bytes':len(raw),'payload':payload})
 payload=logs[-1]['payload'] if logs else {}
 process=payload.get('process') if isinstance(payload.get('process'),dict) else {}
 timing=payload.get('timing') if isinstance(payload.get('timing'),dict) else {}
 native_public=metadata.get('publication_views',{}).get('native_response')
 native_public_link=('<br>'+link('../'+native_public['path'],t('download_the_complete_native_response_public_copy', 'Download the complete native response (public copy)'),'Download complete native response (public view)')+' · '+link('../'+native_public['receipt'],t('original_and_public_copy_verification_records', 'Original and public-copy verification records'),'Original/public identity record')) if native_public else ''
 artifact_relative=record.get('artifact_path')
 artifact=contained_path(out,artifact_relative) if artifact_relative else None
 output_data=artifact.read_bytes() if artifact and artifact.is_file() else output.encode('utf-8')
 output_bytes=len(output_data)
 stderr_total=0;any_stderr=False;any_truncated=False;diagnostics=[]
 for index,log in enumerate(logs,1):
  log_process=(log['payload'].get('process')
               if isinstance(log['payload'].get('process'),dict) else {})
  stderr_text=readable_output(str(log_process.get('stderr') or ''))
  stderr_data=stderr_text.encode('utf-8');stderr_total+=len(stderr_data)
  truncated=bool(log_process.get('stderr_truncated') or metadata.get('stderr_truncated'))
  any_stderr=any_stderr or bool(stderr_data);any_truncated=any_truncated or truncated
  raw_download=((f"""<a href="../{esc(log['path'])}" download>""" + t('download_call_log_json', 'Download call-log JSON') + f"""</a> · <code class="break">JSON SHA-256：{esc(log['sha256'])}</code>"""))
  if stderr_data:
   rows=''.join(f'<li value="{int(match.group(1))}"><code>{esc(match.group(2))}</code></li>'
                for line in _reader_log_excerpt(stderr_text)[:6000].splitlines()
                if (match:=re.match(r'\s*(\d+)\s{2}(.*)',line)))
   completeness=(t('this_public_log_contains_the_leading_portion', 'This public log contains the leading portion retained in the call record; that record marks it as truncated.') if truncated else
                 t('the_lines_below_are_quoted_from_the', 'The lines below are quoted from the complete stderr / debug log saved for this physical call.'))
   body=(('<p class="source-note">' + t('line_numbers_refer_to_the_process_stderr', '{completeness} Line numbers refer to the process.stderr field in the call log JSON; human notes are not written into the log box.', completeness=completeness) + f"""</p>{(f'<ol class="log-lines">{rows}</ol>' if rows else '')}<p>{raw_download}</p>{_log_evidence_dialog(log['path'], esc(log['label']))}"""))
  else:
   body=(('<p class="source-note">' + t('no_displayable_stderr_or_debug_log_was', 'No displayable stderr or debug log was saved for this physical call.') + f"</p><p>{raw_download}</p>{_log_evidence_dialog(log['path'], esc(log['label']))}"))
  diagnostics.append(f'<article><h3>{esc(log["label"])}</h3>{body}</article>')
 complete_label=(t('yes', 'Yes') if any_stderr and not any_truncated else
                 t('no_at_least_one_public_log_is', 'No (at least one public log is truncated)') if any_stderr else t('not_applicable_no_debug_log_retained', 'Not applicable (no debug log retained)'))
 public_debug=metadata.get('public_debug_log_path')
 if public_debug:
  debug_path=contained_path(out,public_debug)
  debug_bytes=debug_path.read_bytes()
  if hashlib.sha256(debug_bytes).hexdigest()!=metadata.get('public_debug_log_sha256'):
   raise ValueError('Public diagnostic log hash mismatch: '+record['attempt_id'])
  metadata['public_debug_view_path']=diagnostic_view_page(out,public_debug,t('saved_diagnostic_logs', 'Saved diagnostic logs'),metadata.get('public_log_note') or t('contains_only_observed_logs', 'Contains only observed logs.'))
  stderr_total=len(debug_bytes);any_stderr=bool(debug_bytes)
  any_truncated=bool(metadata.get('diagnostic_log_truncated'))
  complete_label=t('saved_file_truncated_see_native_log_coverage', 'Saved file truncated; see native log coverage notes') if any_truncated else t('saved_file_not_truncated_see_native_log', 'Saved file not truncated; see native log coverage notes')
  native_text,caller_text=_separate_observed_logs(debug_bytes.decode("utf-8", "replace"))
  native_view=('<pre data-log-source="product">'+esc(_reader_log_excerpt(native_text)[:6000])+'</pre>' if native_text else ('<p data-log-unavailable>' + t('no_native_product_or_dependency_logs_were', 'No native product or dependency logs were obtained for this call. Saved caller records appear below.') + '</p>'))
  diagnostics=[('<article><h3>' + t('observed_diagnostic_logs_products_and_dependencies', 'Observed diagnostic logs · products and dependencies') + f"</h3><p>{esc(metadata.get('public_log_note') or t('only_observed_records_are_shown_unavailable_internal', 'Only observed records are shown; unavailable internal steps are not inferred.'))}</p><p>" + t('returned_json_is_shown_separately_under_raw', 'Returned JSON is shown separately under raw responses. Adapter records appear below under caller events.') + f'</p>{native_view}<details><summary>' + t('caller_events_test_program_records', 'Caller events (test program records)') + f"""</summary><pre data-log-source="caller">{esc(_reader_log_excerpt(caller_text)[:6000])}</pre></details>{_log_evidence_dialog(public_debug, model_text(*pair('complete_diagnostic_log', 'Complete diagnostic log')))}<p><a href="../{esc(public_debug)}" download>""" + t('download_runtime_logs_returned_content_separated', 'Download runtime logs (returned content separated)') + '</a> ' + t('public_sha_256', '· Public SHA-256:') + f"""<code class="break">{esc(metadata['public_debug_log_sha256'])}</code> """ + t('original_capture_bytes_may_include_protocol_responses', '· Original capture: {value:,} bytes (may include protocol responses) · Original SHA-256:', value=int(metadata.get('diagnostic_log_bytes') or 0)) + f"""<code class="break">{esc(metadata.get('diagnostic_log_sha256') or '—')}</code></p></article>""")]
 version=method_version(record,method_provenance(out)) or '—'
 command=metadata.get('command_args') or metadata.get('command') or t('the_call_record_did_not_retain_a', '— (the call record did not retain a complete displayable command)')
 if isinstance(command,list):command=' '.join(str(item) for item in command)
 status=t('succeeded', 'Succeeded') if record.get('ok') else t('failed', 'Failed')
 result_note=(t('this_call_produced_output_that_could_be', 'This call produced output that could be scored.') if record.get('ok') else
              t('this_call_did_not_produce_output_that', 'This call did not produce output that could pass scoring.'))
 top_log_size=(f"{stderr_total:,} 字节{(t('at_least_one_is_truncated', ' (at least one is truncated)') if any_truncated else '')}"
               if any_stderr else t('0_bytes', '0 bytes'))
 fields=(('<dt>' + t('call_status', 'Call status') + f'</dt><dd>{status}</dd><dt>' + t('elapsed_time', 'Elapsed time') + f"</dt><dd>{ms(record.get('elapsed_ms'))}</dd><dt>" + t('exit_code', 'Exit code') + f"</dt><dd>{esc(process.get('exit_code', metadata.get('exit_code', '—')))}</dd><dt>" + t('raw_output', 'Raw output') + '</dt><dd>' + t('bytes', '{output_bytes:,} bytes', output_bytes=output_bytes) + '</dd><dt>' + t('run_logs', 'Run logs') + f'</dt><dd>{top_log_size}</dd><dt>' + t('complete_log_retained', 'Complete log retained') + f'</dt><dd>{complete_label}</dd><dt>' + t('run_id', 'Run ID') + f"</dt><dd>{esc(record.get('run_id') or execution_id(record) or '—')}</dd><dt>" + t('tool_version', 'Tool version') + f'</dt><dd>{esc(version)}</dd>'))
 full_fields=(('<dt>' + t('started_at', 'Started at') + f"</dt><dd>{esc(record.get('started_at') or timing.get('started_at') or '—')}</dd><dt>" + t('finished_at', 'Finished at') + f"</dt><dd>{esc(record.get('completed_at') or timing.get('completed_at') or '—')}</dd><dt>" + t('log_level', 'Log level') + f"</dt><dd>{esc(metadata.get('log_level') or (t('observed_diagnostics_saved_see_native_log_coverage', 'Observed diagnostics saved (see native log coverage notes)') if public_debug else t('stderr_retained_level_not_recorded', 'stderr retained (level not recorded)') if any_stderr else t('debug_logging_disabled', 'Debug logging disabled')))}</dd><dt>" + t('public_log', 'Public log') + f'</dt><dd>{top_log_size}</dd><dt>' + t('public_log_redacted', 'Public log redacted') + f"</dt><dd>{(t('credentials_and_local_paths_hidden', 'Credentials and local paths hidden') if public_debug else t('no_2', 'No'))}</dd><dt>" + t('method_sha_256', 'Method SHA-256') + f"</dt><dd>{esc(metadata.get('binary_sha256') or '—')}</dd><dt>" + t('official_command', 'Official command') + f'</dt><dd>{esc(command)}</dd>'))
 runtime_error=''
 if not record.get('ok') or record.get('error_type'):
  runtime_error=('<div class="runtime-error"><h3>' + t('program_error', 'Program error') + '</h3><p>' + t('classification', 'Classification: ') + f"<code>{esc(record.get('error_type') or t('not_recorded', 'Not recorded'))}</code></p><pre>{esc(record.get('error_message') or t('the_call_record_provides_no_error_text', 'The call record provides no error text.'))}</pre></div>")
 diagnostic=''.join(diagnostics) if diagnostics else ('<p class="source-note">' + t('this_historical_call_retained_no_displayable_stderr', 'This historical call retained no displayable stderr/debug log; no missing log is reconstructed.') + '</p>')
 raw_record=' · '.join(f'<a href="../{esc(log["path"])}" download>下载{esc(log["label"])}记录</a>' for log in logs)
 if artifact_relative:
  if raw_record:raw_record+=' · '
  _answer_view=metadata.get('publication_views',{}).get('scored_answer')
  if _answer_view and _answer_view['original_sha256']!=_answer_view['published_sha256']:
   raw_record+=f'<a href="../{esc(_answer_view["path"])}" download>'+model_text(*pair('download_public_output_view', 'Download public output view'))+'</a>'
  else:
   raw_record+=(f'<a href="../{esc(artifact_relative)}" download>' + t('download_the_raw_output_retained_for_this', 'Download the raw output retained for this call') + '</a>')
 raw_record+=native_public_link
 public_response=metadata.get('public_response_path')
 if public_response:
  response_view=diagnostic_view_page(out,public_response,t('complete_original_response', 'Complete original response'),t('actual_product_response_credentials_and_local_paths', 'Actual product response; credentials and local paths are hidden in this public copy.'))
  if raw_record:raw_record+=' · '
  raw_record+=(f'<a href="../{esc(response_view)}" target="_blank">' + t('view_complete_api_response_credentials_and_local', 'View complete API response (credentials and local paths hidden)') + '</a>')
 if metadata.get("public_log_note"):
  raw_record+='<p class="source-note">'+esc(metadata["public_log_note"])+'</p>'
 public_debug=metadata.get('public_debug_log_path')
 if public_debug:
  if raw_record:raw_record+=' · '
  raw_record+=(f"""<a href="../{esc(metadata.get('public_debug_view_path') or public_debug)}" target="_blank">""" + t('view_saved_diagnostic_logs_credentials_and_local', 'View saved diagnostic logs (credentials and local paths hidden)') + '</a>')
 diagnostic_path=metadata.get('diagnostic_log_path')
 if metadata.get('local_report_complete_diagnostic_links') and diagnostic_path:
  if raw_record:raw_record+=' · '
  diagnostic_size=int(metadata.get('diagnostic_log_bytes') or 0)
  diagnostic_sha=metadata.get('diagnostic_log_sha256') or t('not_registered', 'Not registered')
  diagnostic_truncated=t('yes', 'Yes') if metadata.get('diagnostic_log_truncated') else t('no_2', 'No')
  raw_record+=((f'<a href="../../{esc(diagnostic_path)}" download>' + t('download_the_complete_raw_debug_log', 'Download the complete raw debug log') + f'</a>（{diagnostic_size:,} 字节 · SHA-256：<code class="break">{esc(diagnostic_sha)}</code> · 截断：{diagnostic_truncated}）'))
 if not raw_record:raw_record=t('this_historical_call_has_no_downloadable_raw', 'This historical call has no downloadable raw record')
 evidence_identity_note=(model_text(*pair('scoring_originals_remain_private_public_downloads_hide', 'Scoring originals remain private; public downloads hide paths or sensitive values and bind original/public identities separately.')) if any(view.get('redactions') for view in metadata.get('publication_views',{}).values()) else t('raw_output_keeps_the_bytes_saved_after', 'Raw output keeps the bytes saved after the call.'))
 if record.get('scoring',{}).get('mode')=='published_snapshot_rescore':
  evidence_identity_note=t('this_run_only_rescored_call_logs_keep', 'This run only rescored. Call logs keep the original call details; this scoring input and its raw and public hashes are listed under the actual output.')
 return (('<section class="section" id="run-log"><div class="wrap"><h2>' + t('run_logs_and_evidence', 'Run logs and evidence') + f'</h2><dl class="diagnostic-fields">{fields}</dl>{runtime_error}<details class="technical-details"><summary>' + t('view_complete_run_fields', 'View complete run fields') + f'</summary><dl class="diagnostic-fields">{full_fields}</dl></details><p class="log-summary"><b>' + t('result_note', 'Result note: ') + f'</b>{result_note}</p><details class="diagnostic-log"><summary>' + t('view_this_run_s_real_log', "View this run's real log") + f'</summary><div class="diagnostic-log-body">{diagnostic}</div></details></div></section><section class="section"><div class="wrap"><h2>' + t('original_run_evidence', 'Original run evidence') + '</h2><p>' + t('the_run_record_keeps_request_status_latency', '{raw_record}. The run record keeps request status, latency and evidence paths; {evidence_identity_note}', raw_record=raw_record, evidence_identity_note=evidence_identity_note) + '</p></div></section>'))
def pct(n):return '—' if n is None else f'{n*100:.1f}%'
def ms(n):return '—' if n is None else f'{n:,.0f} ms'
def link(url,label,english=None):return f'<a href="{esc(safe_href(url))}">{model_text(label,english)}</a>'
def cell(value,sort=None):return '<td'+(f' data-sort="{sort}"' if sort is not None else '')+f'>{value}</td>'

def model_text(value,english=None):
 return esc(value) if english is None else '<span data-report-en="'+esc(english)+'">'+esc(value)+'</span>'
def rate(a,b):return pct(a/b) if b else '—'
def section(title,body,id=''):return '<section class="section"'+(f' id="{id}"' if id else '')+f'><div class="wrap"><h2>{title}</h2>{body}</div></section>'
def hero(title,desc):
 banner=render_context().banner
 label=t('synthetic_example', 'Synthetic example') if t('synthetic_example_not_a_formal_benchmark', 'Synthetic example · not a formal benchmark') in banner else t('diagnostic_run', 'Diagnostic run') if t('diagnostic_run_not_a_formal_benchmark', 'Diagnostic run · not a formal benchmark') in banner else t('measured_report', 'Measured report')
 return f'<section class="hero"><div class="wrap"><div class="eyebrow">Web Fetch · {label}</div><h1>{title}</h1><p class="lead">{desc}</p></div></section>'
def comparative_rows(rows, directions):
 """Mark raw-value column extrema without ranking unavailable values or ties."""
 matches=[list(re.finditer(r'<td\b[^>]*>.*?</td>',values,re.S)) for _,values in rows]
 numbers={}
 for index,(metric,direction) in directions.items():
  values=[]
  for cells in matches:
   match=re.search(r'data-sort="([^"]+)"',cells[index].group())
   value=float(match.group(1)) if match else None
   values.append(value if value is not None and value>=0 else None)
  valid=[v for v in values if v is not None]
  extreme=(max(valid) if direction=='max' else min(valid)) if valid else None
  numbers[index]=(values,extreme,valid.count(extreme)==1)
 result=[]
 for row_number,((attrs,values),cells) in enumerate(zip(rows,matches,strict=True)):
  for index in sorted(directions,reverse=True):
   metric,direction=directions[index];column,extreme,unique=numbers[index]
   fragment=cells[index].group();opening,body=fragment.split('>',1)
   if column[row_number] is None:opening=re.sub(r' data-sort="[^"]+"','',opening)
   opening+=' data-metric="'+metric+'" data-metric-direction="'+direction+'"'
   content=body[:-5]
   if (unique or render_context().descriptive_ranks) and column[row_number] is not None and column[row_number]==extreme:
    content='<strong class="comparison-winner good">'+content+'</strong>'
   values=values[:cells[index].start()]+opening+'>'+content+'</td>'+values[cells[index].end():]
  result.append((attrs,values))
 return result


def table(headers,rows,id='',sortable=False,cls='',metrics=None,index_header=t('no', 'No.'),default_sort=None,include_index=True,rank_values=None):
 head=(f'<th>{esc(index_header)}</th>' if include_index else '')+''.join(f'<th><button class="sort-button" onclick="sortTable(\'{id}\',{i+int(include_index)})">{h} ↕</button></th>' if sortable else f'<th>{h}</th>' for i,h in enumerate(headers))
 body=''.join(f'<tr{attrs}>'+ ((f'<td data-rank="{rank_values[i]}">{rank_values[i]}</td>' if rank_values is not None else f'<td>{i+1}</td>') if include_index else '')+f'{values}</tr>' for i,(attrs,values) in enumerate(rows))
 sort_attrs=(f' data-col="{default_sort[0]}" data-dir="{default_sort[1]}"' if default_sort else '')
 if not include_index:sort_attrs+=' data-row-index="false"'
 return metric_legend(headers if metrics is None else metrics)+f'<div class="table-wrap"><table class="{cls}"'+(f' id="{id}"' if id else '')+sort_attrs+f'><thead><tr>{head}</tr></thead><tbody'+(' id="rows"' if id=='filtered-table' else '')+f'>{body}</tbody></table></div>'
def _scoped_render(function):
 @wraps(function)
 def render(*args,**kwargs):
  with rendering():return function(*args,**kwargs)
 return render

def write(out,path,title,body,*,root_links=False,show_language=True):
 depth=len(Path(path).parts)-1;base='../'*depth
 document_base='<base href="'+(base or './')+'">' if root_links else ''
 if root_links:base=''
 nav=[('datasets/index.html',t('dataset', 'Dataset')),('methodology/index.html',t('methodology', 'Methodology')),('analysis/index.html',t('analysis', 'Analysis')),('review/index.html',t('review_record', 'Review record')),('guide/index.html',t('interpretation', 'Interpretation')),('adoption/index.html',t('methods', 'Methods')),('reproduce/index.html',t('reproduction', 'Reproduction')),('changes/index.html',t('changelog', 'Changelog')),('accounts/index.html',t('access_setup', 'Access & setup'))]
 brand=link(base+'index.html','Web Fetch')
 context=render_context()
 if context.score_audit and path.startswith('attempts/'):
  identifier=Path(path).stem
  reviewed=next((item for item in context.score_audit['changes'] if item['attempt_id']==identifier),None)
  if reviewed:
   body+=section(t('acceptance_basis', 'Acceptance basis'),('<div data-score-audit><dl class="diagnostic-fields"><dt>' + t('acceptance_requirement', 'Acceptance requirement') + f"</dt><dd>{esc(reviewed['requirement'])}</dd><dt>" + t('actual_output', 'Actual output') + f"</dt><dd>{esc(reviewed['actual'])}</dd></dl><p>{link('../audit/index.html#' + identifier, t('view_sources_and_judgment_basis', 'View sources and judgment basis'))}</p></div>"))
 counterpart=(base+'en/'+path if context.english_generated else base+'en/index.html')
 language=f'<a href="{safe_href(counterpart)}" hreflang="en">English</a>' if show_language else ''
 footer='' if context.external_scope_notes else t('all_scores_are_computed_from_the_recorded', 'All scores are computed from the recorded calls. Web pages and services may change; conclusions apply only to this sample and configuration.')
 evidence_attr=' data-evidence-state="published_snapshot_score_audit"' if context.score_audit else ''
 if context.score_audit:footer=link(base+'methodology/index.html',t('methodology', 'Methodology'))+' · '+link(base+'changes/index.html',t('version_records', 'Version records'))
 banner=context.banner.replace('href="audit/index.html"','href="'+base+'audit/index.html"')
 if context.external_scope_notes:
  banner=''
  evidence_attr+=' data-scope-notes="external"'
 scope_note=''
 if context.scope_presentation:
  from harness.web_fetch.report_version import SCOPE_TEXT, scope_retry_text
  state=context.scope_presentation
  scope_note='<p class="wrap source-note" data-report-content-scope="'+esc(state['scope'])+'"><strong>'+esc(t(*SCOPE_TEXT[state['scope']]))+'</strong>'
  retry=scope_retry_text(state)
  if retry:
   scope_note+=' <span data-retry-acceptance-scopes="'+esc(','.join(state['retry_scopes']))+'">'+esc(t(*retry))+'</span>'
  scope_note+='</p>'
 page=(f'''<!doctype html><html lang="zh-CN"><head><meta name="webstone-report" content="1"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{document_base}<title>{esc(title)} · Web Fetch</title><link rel="stylesheet" href="{base}assets/style.css?v={hashlib.sha256((STYLE + EXTRA).encode()).hexdigest()[:16]}"><script src="{base}assets/report.js" defer></script></head><body><header class="top"><nav class="wrap">{brand}{''.join((link(base + url, label) for url, label in nav))}{language}<button class="theme-toggle" onclick="theme()">''' + t('toggle_theme', 'Toggle theme') + f'</button></nav></header><main{role_attribute(path)}{evidence_attr}>{banner}{scope_note}{body}</main><footer class="wrap source-note"><p>{footer}</p></footer></body></html>')
 target=contained_path(out,path);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(page,encoding='utf-8')

_RESEARCH_ROUTES=[
 ('datasets/index.html',t('dataset', 'Dataset'),'Datasets'),
 ('methodology/index.html',t('methodology', 'Methodology'),'Methodology'),
 ('analysis/index.html',t('research_findings', 'Research findings'),'Research findings'),
 ('review/index.html',t('review_record', 'Review record'),'Verification'),
 ('guide/index.html',t('interpretation', 'Interpretation'),'Guide'),
 ('adoption/index.html',t('adoption', 'Adoption'),'Adoption'),
 ('reproduce/index.html',t('reproduction', 'Reproduction'),'Reproduction'),
 ('changes/index.html',t('changelog', 'Changelog'),'Changes'),
 ('accounts/index.html',t('access_setup', 'Access & setup'),'Accounts and setup'),
]

@_scoped_render
def _diagnostic_pages(out, model):
 """Render live cohort diagnostics through the maintained WebFetch components."""
 token=set_render_context(RenderContext(methods=render_context().methods,banner=('<div class="wrap callout"><strong>' + esc(model.get('diagnostic_label') or t('live_page_diagnostic', 'Live page diagnostic')) + f"</strong><p>{esc(model['scope_note'])}</p></div>"),scope_presentation={'scope':model['content_scope'],'retry_scopes':[]} if model.get('content_scope') in {'main','full'} else None))
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA+asset_text('cohort-diagnostic.css'),encoding='utf-8')
 (out/'assets/report.js').write_text(JS+asset_text('cohort-diagnostic.js'),encoding='utf-8')
 groups={g['id']:g for g in model['cohorts']};rows=model['rows'];labels=model['cause_labels']
 files={r[field]['path']:(out/r[field]['path']).read_bytes() for r in rows for field in ('log','reference')}
 findings={r['id']:diagnostic_findings(r,files) for r in rows}
 failure_types=diagnostic_failure_counts(model,files)
 def page(route,title,body):write(out,route,title,body,root_links=True,show_language=False)
 def detail_link(row):return 'attempts/'+row['id']+'.html'
 def blocks(name):
  body=''
  by_id={row['id']:row for row in rows}
  def value_html(value):
   if not isinstance(value,dict):return esc(str(value))
   if value.get('url'):return link(value['url'],value['label'])
   if value.get('case_id'):return link(detail_link(by_id[value['case_id']]),value['label'])
   return link('guide/index.html#'+value['issue_id'],value['label'])
  for index,block in enumerate(model.get('pages',{}).get(name,[])):
   text=''
   if block.get('figure'):
    figure=block['figure']
    png=(out/figure['path']).read_bytes()
    width,height=int.from_bytes(png[16:20],'big'),int.from_bytes(png[20:24],'big')
    text+='<figure class="diagnosis-figure"><a href="'+esc(figure['path'])+'"><img loading="lazy" width="'+str(width)+'" height="'+str(height)+'" src="'+esc(figure['path'])+'" alt="'+esc(figure['alt'])+'"></a><figcaption>'+esc(figure['scope'])+'</figcaption></figure>'
   text+=''.join('<p>'+esc(p)+'</p>' for p in block['paragraphs'])
   if block.get('table'):
    spec=block['table']
    text+=table(spec['headers'],[('', ''.join(cell(value_html(value)) for value in row)) for row in spec['rows']],f'diagnostic-table-{name}-{index}')
   for example in block.get('examples',[]):
    row=by_id[example['case_id']]
    text+='<article class="diagnosis-example" data-diagnosis-case="'+esc(row['id'])+'"><h3>'+link(detail_link(row),example.get('title')or row.get('name')or row['id'])+'</h3><p>'+esc(groups[row['cohort']]['label'])+' · '+link(row['url'],row['url'])+'</p><div class="diagnosis-contrast">'
    for key,label in [('expected',t('diagnosis_expected','Task requirement')),('actual',t('diagnosis_actual','Observed result'))]:
     text+='<div data-diagnosis-field="'+key+'"><h4>'+label+'</h4><p>'+esc(example[key])+'</p></div>'
    text+='</div><dl class="diagnosis-repair">'
    for key,label in [('cause',t('diagnosis_cause','Cause and evidence')),('fix',t('diagnosis_fix','Recommended fix')),('verify',t('diagnosis_verify','Acceptance check'))]:
     text+='<dt>'+label+'</dt><dd data-diagnosis-field="'+key+'">'+esc(example[key])+'</dd>'
    text+='</dl><p>'+link(detail_link(row),t('diagnosis_evidence','Open the exact checks and original evidence →'))+'</p></article>'
   if block.get('case_ids'):
    text+='<p class="actions">'+''.join(link('attempts/'+cid+'.html',(by_id[cid].get('name')or cid)+' →') for cid in block['case_ids'])+'</p>'
   body+=section(esc(block['title']),text,block.get('id',''))
  return body
 def distribution(counts):
  return '<figure class="editorial-figure">'+''.join('<div class="editorial-bar-row"><span>'+esc(label)+'</span><div class="editorial-bar-track"><i style="width:'+str(100*n/population)+'%"></i></div><b>'+str(n)+' / '+str(population)+'</b></div>' for label,n,population in counts)+'</figure>'
 try:
  home=hero(esc(model['title']),esc(model['subtitle']))
  overall=diagnostic_call_summary(rows,files);n=overall['total']
  versions={g.get('version') or t('see_the_call_record', 'See the call record') for g in groups.values()}
  values=cell(t('all_tasks', 'All tasks'))+cell(esc(next(iter(versions)) if len(versions)==1 else t('several_versions_see_groups', 'Several versions; see groups')))+cell(str(n),n)
  for key in ('delivered','accepted'):
   value=overall[key]
   values+=cell('—' if value is None else f'{100*value/n:.1f}% <small>({value} / {n})</small>',-1 if value is None else value)
  values+=''.join(cell('—' if overall[k] is None else f'{overall[k]/1000:.2f}',-1 if overall[k] is None else overall[k]) for k in ('mean_ms','p50_ms','p95_ms'))
  overall_table=table([t('dataset', 'Dataset'),t('implementation_version', 'Implementation version'),t('tasks', 'Tasks'),t('call_success', 'Call success'),t('pass', 'Pass'),t('mean_latency_s', 'Mean latency (s)'),t('p50_latency_s', 'P50 latency (s)'),t('p95_latency_s', 'P95 latency (s)')],[(' data-summary-overall="true"',values)],'overall-summary')
  overall_section=section(t('overall_evaluation_results', 'Overall evaluation results'),(f'{overall_table}<p>' + t('all_planned_tasks_form_one_evaluation_set', 'All planned tasks form one evaluation set. A successful call does not mean a passed task; latency merges all calls including failures and does not average per-group percentiles. Latency merged across execution routes only describes this evaluation set and does not rank routes.') + '</p>'),'overall-results')
  comparison_mode=model.get('comparison_mode',False)
  if not comparison_mode:home+=overall_section
  summary_rows=[]
  for s in diagnostic_cohort_summary(model,files):
   g=groups[s['id']];n=s['total']
   values=cell(esc(g['label']))+cell(esc(g.get('version') or t('see_the_call_record', 'See the call record')))+cell(str(n),n)+cell('—' if s['delivered'] is None else f"{100*s['delivered']/n:.1f}% <small>({s['delivered']} / {n})</small>",-1 if s['delivered'] is None else s['delivered']/n)+cell(f"{100*s['accepted']/n:.1f}% <small>({s['accepted']} / {n})</small>",s['accepted']/n)
   values+=''.join(cell('—' if s[k] is None else f'{s[k]/1000:.2f}',-1 if s[k] is None else s[k]) for k in ('mean_ms','p50_ms','p95_ms'))
   summary_rows.append((' data-summary-group="'+esc(s['id'])+'"',values))
  if comparison_mode:
   summary_rows=comparative_rows(summary_rows,{3:('delivery','max'),4:('acceptance','max')})
  home+=section(t('results_by_task_family', 'Results by task family'),(f"{table([t('task_group', 'Task group'), t('implementation_version', 'Implementation version'), t('tasks', 'Tasks'), t('call_success', 'Call success'), t('pass', 'Pass'), t('mean_latency_s', 'Mean latency (s)'), t('p50_latency_s', 'P50 latency (s)'), t('p95_latency_s', 'P95 latency (s)')], summary_rows, 'cohort-summary', sortable=True)}{distribution([(groups[g]['label'], *model['metrics'][g]) for g in groups])}<p>" + t('a_successful_call_only_means_the_request', 'A successful call only means the request delivered; a task pass also requires the body, text and structure rules. All three latency figures cover every call in the group; P50 is the median and P95 the 95th percentile. Groups without complete timing show —.') + f"</p><p>{esc(model['metric_note'])}</p>"),'cohort-results')
  failures=Counter(r['cause'] for r in rows if not r['initial_pass'])
  home+=blocks('overview')
  if comparison_mode:home+='<details class="wrap"><summary>'+t('call_inventory', 'Call inventory')+'</summary>'+overall_section+'</details>'
  home+=section(t('what_failed_outputs_mostly_miss', 'What failed outputs mostly miss'),(f'{distribution([(FAILURE_TYPES[k], n, sum(failures.values())) for k, n in failure_types.most_common(3)])}<p>' + t('the_denominator_is_all_first_round_failed', 'The denominator is all {count} first-round failed tasks. One task can miss both text and records, so the counts do not add up. {link}', count=str(sum(failures.values())), link=link('analysis/index.html', t('view_failure_types_and_specific_samples', 'View failure types and specific samples →'))) + '</p>'))
  audit=model['success_audit'];counts=audit['source_state_counts']
  home+=section(t('passes_are_also_checked_against_the_source', 'Passes are also checked against the source page'),(f"{distribution([(model['source_labels'][k], n, audit['audited_passes']) for k, n in counts.items()])}<p>" + t('passing_results_have_three_way_check_records', '{audited_passes} passing results have three-way check records; comparable content and access barriers on the current page are recorded separately. {link}', audited_passes=str(audit['audited_passes']), link=link('review/index.html', t('check_scope_and_conclusions', 'Check scope and conclusions →'))) + '</p>'))
  home+=section(t('where_to_read_next', 'Where to read next'),'<div class="guide-grid">'+''.join('<article class="guide-card"><h3>'+link(route,title)+'</h3><p>'+text+'</p></article>' for route,title,text in [('datasets/index.html',t('dataset', 'Dataset'),t('view_samples_tasks_and_independent_reference_requirements', 'View samples, tasks and independent reference requirements.')),('analysis/index.html',t('analysis', 'Analysis'),t('filter_passes_failures_and_attribution_and_open', 'Filter passes, failures and attribution, and open complete evidence.')),('guide/index.html',t('interpretation', 'Interpretation'),t('understand_access_challenges_extraction_omissions_and_evaluation', 'Understand access challenges, extraction omissions and evaluation revisions.'))])+'</div>')
  page('index.html',model['title'],home)
  controls=('<div class="filters"><label>' + t('group', 'Group') + '<select data-filter="cohort"><option value="">' + t('all', 'All') + f"""</option>{''.join(('<option value="' + esc(k) + '">' + esc(g['label']) + '</option>' for k, g in groups.items()))}</select></label><label>""" + t('result', 'Result') + '<select data-filter="result"><option value="fail">' + t('fail_2', 'Fail') + '</option><option value="pass">' + t('pass_2', 'Pass') + '</option><option value="">' + t('all', 'All') + '</option></select></label><label>' + t('failure_type', 'Failure type') + '<select data-filter="issue"><option value="">' + t('all', 'All') + f"""</option>{''.join(('<option value="' + esc(k) + '">' + esc(FAILURE_TYPES[k]) + '</option>' for k in failure_types))}</select></label><label>""" + t('check_leads', 'Check leads') + '<select data-filter="cause"><option value="">' + t('all', 'All') + f"""</option>{''.join(('<option value="' + esc(k) + '">' + esc(v) + '</option>' for k, v in labels.items()))}</select></label><label>""" + t('search', 'Search') + '<input data-filter="query" aria-label="搜索样本" placeholder="编号或网址"></label></div><p id="count" role="status"></p>')
  inventory=[]
  for r in rows:
   kinds=sorted({f['kind'] for f in findings[r['id']]})
   attrs=' data-cohort="'+esc(r['cohort'])+'" data-result="'+('pass' if r['initial_pass'] else 'fail')+'" data-cause="'+esc(r['cause'])+'" data-issue="'+' '.join(kinds)+'" data-search="'+esc(r['url'])+'"'
   values=cell(link(detail_link(r),r.get('name')or urlsplit(r['url']).hostname or r['id'])+'<br><small>'+esc(r['id'])+'</small>')+cell(esc(groups[r['cohort']]['label']))+cell(t('pass_2', 'Pass') if r['initial_pass'] else t('fail_2', 'Fail'))+cell(esc('；'.join(FAILURE_TYPES[k] for k in kinds) or '—'))+cell(esc(labels[r['cause']]))+cell(link(detail_link(r),t('requirement_actual_and_checks', 'Requirement, actual and checks →')))
   inventory.append((attrs,values))
  type_rows=[]
  for kind,n in failure_types.most_common():
   example=next(r for r in rows if any(f['kind']==kind for f in findings[r['id']]))
   type_rows.append((' data-failure-type="'+kind+'" data-count="'+str(n)+'"',cell(link('analysis/index.html#issue='+kind,FAILURE_TYPES[kind]))+cell(str(n)+' / '+str(sum(failures.values())))+cell(link(detail_link(example),urlsplit(example['url']).hostname or example['id']))))
  analysis=hero(str(len(rows))+' 例结果分析',t('start_with_the_requirements_outputs_violated_then', 'Start with the requirements outputs violated, then see how far independent checks can explain them.'))
  analysis+=blocks('analysis')
  analysis+=section(t('failure_types_and_counts', 'Failure types and counts'),(f"{table([t('requirements_unmet_in_the_first_round', 'Requirements unmet in the first round'), t('affected_tasks', 'Affected tasks'), t('sample_evidence', 'Sample evidence')], type_rows)}<p>" + t('counted_from_the_saved_first_round_scores', 'Counted from the saved first-round scores; one task can involve several failures, so counts do not add up. Select a type to filter all related tasks.') + '</p>'),'failure-types')
  analysis+=section(t('what_independent_checks_found', 'What independent checks found'),(f"{table([t('check_leads', 'Check leads'), t('failed_tasks', 'Failed tasks')], [('', cell(esc(labels[k])) + cell(str(n))) for k, n in failures.items()])}<p>" + t('these_leads_are_counted_separately_from_the', 'These leads are counted separately from the output defects above. An independent browser also being blocked only proves this check hit an access barrier; a later fetch passing does not prove the internal cause of the first-round failure.') + '</p>'))
  analysis+=section('逐项结果',controls+table([t('site_and_sample', 'Site and sample'),t('group', 'Group'),t('first_round_result', 'First-round result'),t('failure_type', 'Failure type'),t('check_leads', 'Check leads'),'证据'],inventory,'filtered-table',sortable=True),'failure-inventory')
  page('analysis/index.html',t('analysis', 'Analysis'),analysis)
  interpretation_lead=esc(model['interpretation_lead']) if model.get('interpretation_lead') else (f"{str(model['metrics']['overall'][0])} / {str(len(rows))} " + t('first_calls_met_the_full_acceptance_requirements', 'first calls met the full acceptance requirements of the time; the diagnostics below cover the designated sample.'))
  guide=hero(esc(model.get('guide_title') or t('extraction_results_for_these_pages', 'Extraction results for these pages')),interpretation_lead)
  guide+=('' if comparison_mode else overall_section)+section(t('results_and_measurement_scope', 'Results and measurement scope'),(f"{table([t('task_group', 'Task group'), t('implementation_version', 'Implementation version'), t('tasks', 'Tasks'), t('call_success', 'Call success'), t('pass', 'Pass'), t('mean_latency_s', 'Mean latency (s)'), t('p50_latency_s', 'P50 latency (s)'), t('p95_latency_s', 'P95 latency (s)')], summary_rows, 'guide-summary', sortable=True)}<p>" + t('latency_covers_all_calls_in_each_group', '{metric_note} Latency covers all calls in each group including failures; P50 is the median. Groups without complete timing are not computed.', metric_note=esc(model['metric_note'])) + '</p>'),'guide-results')
  if comparison_mode:
   guide+='<details class="wrap"><summary>'+t('call_inventory', 'Call inventory')+'</summary>'+overall_section+'</details>'
  guide+=blocks('guide')
  guide_defect_rows=[(f' data-failure-type="{k}" data-count="{n}"',cell(link('analysis/index.html#issue='+k,FAILURE_TYPES[k]))+cell(str(n)+' / '+str(sum(failures.values())))) for k,n in failure_types.most_common()]
  guide+=section(t('requirements_failures_violate_most_often','Requirements failures violate most often'),table([t('output_defects','Output defects'),t('affected_failed_tasks','Affected failed tasks')],guide_defect_rows)+'<p>'+t('one_task_can_involve_several_defects_so',"One task can involve several defects, so counts do not add up. Missing text is a fact about output against acceptance, not a finding about the tested tool's internal fault.")+'</p>','guide-output-defects')
  guide+=section(t('failure_classes_after_checking', 'Failure classes after checking'),(f"{table([t('check_conclusion', 'Check conclusion'), t('failed_tasks', 'Failed tasks'), t('sample_evidence', 'Sample evidence')], [('', cell(esc(labels[k])) + cell(str(n) + ' / ' + str(sum(failures.values()))) + cell(link(detail_link(next((r for r in rows if not r['initial_pass'] and r['cause'] == k))), t('view_specific_requirements_output_and_source_page', 'View specific requirements, output and source page')))) for k, n in failures.most_common()])}<p>" + t('each_failed_task_belongs_to_exactly_one', 'Each failed task belongs to exactly one class, and the classes sum to all failed tasks. Access restrictions, reference-scope mismatches and output omissions are handled separately; an inaccessible current source page is not evidence of an engine fault.') + '</p>'),'guide-reviewed-causes')
  examples=model.get('interpretation_case_ids')
  if not examples:
   examples=[next(r['id'] for r in rows if not r['initial_pass'])] if failures else []
   if any(r['initial_pass'] for r in rows):examples.append(next(r['id'] for r in rows if r['initial_pass']))
  for cid in examples:
   r=next(r for r in rows if r['id']==cid)
   panels=''
   for title,field in [(t('acceptance_requirement', 'Acceptance requirement'),'reference'),(t('first_round_actual_output', 'First-round actual output'),'first_answer'),(t('independent_source_page_observation', 'Independent source-page observation'),'source')]:
    binding=r.get(field)
    specimen_files={r[f]['path']:(out/r[f]['path']).read_bytes() for f in ('reference','first_answer','source') if r.get(f)}
    excerpt=diagnostic_specimen_excerpt(r,field,specimen_files,model)
    empty_note=('<p>' + t('output_is_empty_0_bytes', 'Output is empty (0 bytes).') + '</p>') if field=='first_answer' and not excerpt else ''
    panels+='<div data-specimen-field="'+field+'"'+(' data-sha256="'+binding['sha256']+'"' if binding else '')+'><h3>'+title+'</h3>'+empty_note+'<pre>'+esc(excerpt)+'</pre><p>'+esc(binding.get('scope','') if binding else t('independent_observation_did_not_obtain_target_body', 'Independent observation did not obtain target body text'))+'</p></div>'
   guide+=section((urlsplit(r['url']).hostname or cid)+'：'+(t('pass_2', 'Pass') if r['initial_pass'] else t('fail_2', 'Fail')),'<div data-interpretation-case="'+esc(cid)+'"><p>'+esc(r['task'])+'</p><div class="diagnostic-specimen">'+panels+'</div><p>'+esc(r['cause_detail'])+'</p><p>'+link(detail_link(r),t('view_the_complete_reference_output_live_page', 'View the complete reference, output, live page and logs →'))+'</p></div>')
  guide+=section(t('conclusions_and_check_scope', 'Conclusions and check scope'),(''.join('<p>'+esc(value)+'</p>' for value in model.get('guide_conclusions',[model['scope_note']]))+'<p>' + t('source_page_observations_and_output_checks_are', 'Source-page observations and output checks are kept separately. When an independent browser hits a verification page or access problem, this neither counts as a pass for the tested tool nor establishes that the tool is wrong. {link}', link=link('review/index.html', t('view_check_scope_and_handling', 'View check scope and handling →'))) + '</p>'),'guide-conclusions')
  page('guide/index.html',t('interpretation', 'Interpretation'),guide)
  dataset=[]
  for r in rows:
   dataset.append((' data-search="'+esc(r['url'])+'"',cell(link('datasets/'+r['id']+'.html',r['id']))+cell(esc(groups[r['cohort']]['label']))+cell(link(r['url'],t('actual_page', 'Actual page ↗')))+cell(esc(r['task']))))
   reference=(out/r['reference']['path']).read_text(encoding='utf-8')
   page('datasets/'+r['id']+'.html',(f"{r['id']} " + t('task_and_reference', ' · task and reference')),hero(esc(r['id']),esc(r['task']))+section(t('page_and_scope', 'Page and scope'),'<p>'+link(r['url'],r['url'])+'</p><p>'+esc(r['reference']['scope'])+'</p>')+section(t('independent_acceptance_requirements', 'Independent acceptance requirements'),('<details open><summary>' + t('view_the_reference_contract', 'View the reference contract') + f'</summary><pre>{esc(reference)}</pre></details>'))+section(t('result_entry', 'Result entry'),'<p>'+link(detail_link(r),t('view_this_task_s_output_and_three', "View this task's output and three-way check →"))+'</p>'))
  dataset_search=('<div class="filters"><label>' + t('search_tasks', 'Search tasks') + '<input data-filter="query" aria-label="' + t('search_tasks', 'Search tasks') + '" placeholder="编号、网址或任务"></label></div><p id="count" role="status"></p>')
  page('datasets/index.html',t('dataset', 'Dataset'),hero(t('web_task_set', 'Web task set'),t('each_group_keeps_its_own_sampling_and', 'Each group keeps its own sampling and acceptance scope; task pages show no measured scores.'))+blocks('datasets')+section(t('all_tasks', 'All tasks'),dataset_search+table([t('sample', 'Sample'),t('group', 'Group'),t('source_page', 'Source page'),t('task', 'Task')],dataset,'filtered-table',sortable=True)))
  for r in rows:
   dialogs=[]
   def evidence(target,title,body,kind,dialogs=dialogs):
    dialogs.append((f'<dialog class="call-log-dialog wmb-detail-dialog" id="{target}" aria-labelledby="{target}-title"><div class="call-log-shell"><header><h2 id="{target}-title">{esc(title)}</h2><button type="button" data-call-log-close aria-label="' + t('close', 'Close') + '">' + t('close', 'Close') + f'</button></header><div class="wmb-dialog-content" data-evidence-kind="{kind}">{body}</div></div></dialog>'))
    return '<button type="button" class="call-log-button" data-call-log-open="'+target+'" aria-haspopup="dialog">'+esc(title)+'</button>'
   actions=''
   for field,title in [('reference',t('reference_and_acceptance_contract', 'Reference and acceptance contract')),('first_answer',model.get('output_label') or t('first_round_actual_output', 'First-round actual output')),('answer',t('diagnostic_output', 'Diagnostic output')),('source',t('actual_browser_page', 'Actual browser page')),('log',t('errors_and_run_records', 'Errors and run records'))]:
    if field=='answer' and r[field]==r['first_answer']:continue
    binding=r.get(field)
    if binding is None:continue
    raw=(out/binding['path']).read_text(encoding='utf-8')
    kind_attr=' data-kind="error"' if field=='log' else ''
    lazy=' data-evidence-file="'+esc(binding['path'])+'"' if len(raw)>32768 else ''
    contents='<p>'+link(binding['path'],t('download_complete_evidence', 'Download complete evidence'))+'</p><p>'+esc(binding.get('scope',t('saved_call_evidence', 'Saved call evidence')))+'</p><pre'+kind_attr+lazy+'>'+esc(t('reading_complete_evidence', 'Reading complete evidence…') if lazy else raw)+'</pre>'
    actions+=evidence('result-logs' if field=='log' else 'evidence-'+field,title,contents,field)
   summary=(f"""<div data-result-summary><h2>{((esc(model['pass_label']) if model.get('pass_label') else t('first_round_pass', 'First-round pass')) if r['initial_pass'] else (esc(model['fail_label']) if model.get('fail_label') else t('first_round_failure', 'First-round failure')))}</h2><p>{esc(labels[r['cause']])}</p><dl class="diagnostic-fields"><dt>""" + t('task_id', 'Task ID') + f"</dt><dd>{esc(r['id'])}</dd><dt>" + t('run_id', 'Run ID') + f"</dt><dd>{esc(r['run_id'])}</dd><dt>" + t('source_page_observation_time', 'Source-page observation time') + f"</dt><dd>{esc(r.get('source_checked_at') or r.get('success_audit', {}).get('source_checked_at') or t('see_the_independent_observation_record', 'See the independent observation record'))}</dd></dl></div>")
   body=hero(esc(r.get('name')or r['id']),esc(r['task']))+section(t('result', 'Result'),summary)
   if findings[r['id']]:
    check_rows=[(' data-finding-kind="'+f['kind']+'"',cell(esc(f['label']))+cell(esc(f['expected']))+cell(esc(f['actual']))) for f in findings[r['id']]]
    body+=section(t('what_the_first_round_failed_to_meet', 'What the first round failed to meet'),(f"{table([t('failed_item', 'Failed item'), t('requirement', 'Requirement'), t('actual', 'Actual')], check_rows)}<p>" + t('this_table_explains_the_saved_first_round', 'This table explains the saved first-round score and does not substitute later fetched output. The complete source text and scoring record are in the evidence below.') + '</p>'),'failed-requirements')
   body+=section(t('independent_checks_and_handling', 'Independent checks and handling'),(f"<div data-failure-diagnosis><p>{esc(r['cause_detail'])}</p><p>" + t('handling_status', 'Handling status:') + f"{esc(r['followup_status'])}</p></div>"))
   if r.get('success_audit'):
    a=r['success_audit'];audit_rows=[]
    for title,status_key,actual_key,required_key in [(t('named_links', 'Named links'),'source_named_links_result','source_visible_named_links','source_required_named_links'),('表格行','source_table_result','source_scoped_table_rows','source_required_table_rows')]:
     status=a.get(status_key,'unavailable');status_label={'not_applicable':t('no_such_task_requirement', 'No such task requirement'),'meets':t('meets_the_count_requirement', 'Meets the count requirement'),'below':t('below_the_count_requirement', 'Below the count requirement'),'unavailable':t('check_count_not_obtained', 'Check count not obtained')}.get(status,status)
     audit_rows.append(('',cell(title)+cell(esc(status_label))+cell(str(a.get(actual_key)) if a.get(actual_key) is not None else '—')+cell(str(a.get(required_key)) if a.get(required_key) is not None else '—')))
    body+=section(t('three_way_check', 'Three-way check'),(f"<p>{esc(model['source_labels'][a['source_state']])}</p>{table([t('check', 'Check'), t('current_source_page_status', 'Current source-page status'), t('actual', 'Actual'), t('requirement', 'Requirement')], audit_rows)}<p>" + t('means_the_task_has_no_such_requirement', '— means the task has no such requirement or the count was not obtained; it does not count as a pass.') + '</p>'))
   if not r.get('source'):body+=section(t('source_page_observation', 'Source-page observation'),'<p>'+esc(r.get('source_error') or t('independent_browser_observation_obtained_no_downloadable_target', "Independent browser observation obtained no downloadable target body text; access status is in this task's check record."))+'</p>')
   body+=section(t('reference_output_and_live_page_evidence', 'Reference, output and live-page evidence'),'<div class="actions wmb-evidence-actions">'+actions+'</div><p>'+link('datasets/'+r['id']+'.html',t('task_and_reference_2', 'Task and reference →'))+' · '+link('analysis/index.html',t('back_to_result_analysis', 'Back to result analysis →'))+'</p>')+''.join(dialogs)
   page(detail_link(r),(f"{r['id']} " + t('result_2', ' · result')),'<div data-report-detail="'+('success' if r['initial_pass'] else 'failed')+'" data-task-id="'+esc(r['id'])+'">'+body+'</div>')
  for name,title in [('methodology',t('methodology', 'Methodology')),('review',t('review_record', 'Review record')),('reproduce',t('reproduction', 'Reproduction')),('changes',t('changelog', 'Changelog')),('adoption',t('methods', 'Methods')),('accounts',t('access_setup', 'Access & setup'))]:
   page(name+'/index.html',title,hero(title,esc(model.get('page_intros',{}).get(name,'')))+blocks(name))
  page('en/index.html','English edition','<div class="wrap"><h1>English edition not generated</h1><p>'+link('index.html','返回中文报告')+'</p></div>')
 finally:reset_render_context(token)


def _research_links(urls):
 return '<br>'.join(link(url,url.replace('https://','')) for url in urls)

def _research_hero(title,desc,english=False):
 eyebrow=report_text('en' if english else 'zh')('web_fetch_research_report', 'Web Fetch · Research report')
 return f'<section class="hero"><div class="wrap"><div class="eyebrow">{eyebrow}</div><h1>{title}</h1><p class="lead">{desc}</p></div></section>'

def _research_write(out,path,title,body,english=False):
 local_path=('en/'+path) if english else path
 depth=len(Path(local_path).parts)-1;base='../'*depth
 nav=''.join(link(base+('en/' if english else '')+url,en if english else zh) for url,zh,en in _RESEARCH_ROUTES)
 brand=link(base+('en/index.html' if english else 'index.html'),'Web Fetch')
 counterpart=base+path if english else base+'en/'+path
 language=f'''<a href="{safe_href(counterpart)}" hreflang="{('zh-CN' if english else 'en')}">{('中文' if english else 'English')}</a>'''
 status=(report_text('en' if english else 'zh')('research_edition_0_measured_results', 'Research edition · 0 measured results'))
 footer=(report_text('en' if english else 'zh')('research_was_verified_from_public_papers_repositories', 'Research was verified from public papers, repositories, datasets and scorers. No benchmark result is claimed in this edition.'))
 page=f'''<!doctype html><html lang="{('en' if english else 'zh-CN')}"><head><meta name="webstone-report" content="1"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Web Fetch</title><link rel="stylesheet" href="{base}assets/style.css"><script src="{base}assets/report.js" defer></script></head><body><header class="top"><nav class="wrap">{brand}{nav}{language}<button class="theme-toggle" onclick="theme()">{report_text('en' if english else 'zh')('toggle_theme', 'Theme')}</button></nav></header><main{role_attribute(path)}><div class="wrap reliability" role="status"><strong>{status}</strong></div>{body}</main><footer class="wrap source-note"><p>{footer}</p></footer></body></html>'''
 target=contained_path(out,local_path);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(page,encoding='utf-8')

def _research_pages(out:Path,research:dict,*,include_english:bool=False):
 """Render the research-only public benchmark report with no measured-result paths."""
 if research.get('status')!='research_only' or research.get('measured_results')!=0:
  raise ValueError('Public benchmark research must explicitly contain zero measured results')
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA,encoding='utf-8')
 (out/'assets/report.js').write_text(JS,encoding='utf-8')
 release_history=localized_release_history(
  json.loads(((ROOT / 'research/report-changes.json')).read_text(encoding='utf-8')),
  'public_benchmark_research')
 (out/'report-changes.json').write_text(
  json.dumps({'schema_version':2,'releases':release_history},ensure_ascii=False,indent=2)+'\n',
  encoding='utf-8')
 direct=research['direct_benchmarks'];adjacent=research['adjacent_browser_benchmarks']
 for english in ((False,True) if include_english else (False,)):
  h=report_text('en' if english else 'zh')
  sample_size=research['sampling_contract']['size']
  parent_pages=next(x for x in direct if x['id']=='webmainbench-545')['pages']
  current_cases=sum(research['current_benchmark']['tracks'].values())
  def field(row,key,english=english):return row.get(key) if english else row.get(key+'_zh',row.get(key))
  def research_table(headers,rows,index_header=h('no', 'No.'),**kwargs):return table(headers,rows,index_header=index_header,**kwargs)
  artifact_prefix='../../' if english else '../'
  home=_research_hero(h('public_web_fetch_benchmark_research', 'Public Web Fetch benchmark research'),h('public_data_official_scorers_and_adoption_evidence', 'Public data, official scorers and adoption evidence are verified; this edition only defines the next measurement boundary.'),english)
  cards=''.join(f"""<article class="card"><h3>{esc(x['name'])}</h3><p><b>{x['pages']:,}</b> {h('pages_2', 'pages')}</p><p>{esc(field(x, 'scope'))}</p></article>""" for x in direct)
  qualification=research['decision']['qualification_en' if english else 'qualification_zh']
  home+=section(h('decision', 'Decision'),f"""<p class="callout success"><b>{h('selected', 'Selected: ')}</b>{esc(research['decision']['summary_en' if english else 'summary_zh'])}</p><p>{esc(qualification)}</p>""")
  home+=section(h('candidate_benchmarks', 'Candidate benchmarks'),'<div class="grid">'+cards+'</div>')
  home+=section(h('current_status', 'Current status'),f"""<div class="review-summary"><article class="card"><h3>0</h3><p>{h('measured_results', 'measured results')}</p></article><article class="card"><h3>{len(direct)}</h3><p>{h('direct_tracks', 'direct tracks')}</p></article><article class="card"><h3>{len(adjacent)}</h3><p>{h('adjacent_browser_benchmarks', 'adjacent browser benchmarks')}</p></article></div>""")
  _research_write(out,'index.html',h('public_benchmark_research', 'Public benchmark research'),home,english)

  rows=[]
  for x in direct:
   rows.append(('',cell(esc(x['name']))+cell(f'{x["pages"]:,}')+cell(esc(field(x,'metric')))+cell(esc(field(x,'aggregation')))+cell(esc(field(x,'maturity')))+cell(_research_links(x['sources']))))
  datasets=_research_hero(h('dataset', 'Datasets'),h('only_public_tracks_that_directly_measure_page', 'Only public tracks that directly measure page main-content or Markdown conversion are included.'),english)
  datasets+=section(h('primary_tracks', 'Primary tracks'),research_table([h('name', 'Name'),h('pages_3', 'Pages'),h('official_metric', 'Official metric'),h('aggregation', 'Aggregation'),h('maturity', 'Maturity'),h('sources', 'Sources')],rows,metrics=[]))
  decision_rows=[('',cell(esc(x['name']))+cell(esc(field(x,'disposition')))+cell(esc(x['license']))) for x in direct]
  datasets+=section(h('selection_result', 'Selection result'),research_table([h('candidate', 'Candidate'),h('disposition', 'Disposition'),h('license', 'License')],decision_rows,metrics=[]))
  subsets=next(x for x in direct if x['id']=='wceb')['subsets']
  datasets+=section('WCEB '+h('composition', 'composition'),research_table([h('subset', 'Subset'),h('pages_3', 'Pages')],[('',cell(esc(name))+cell(f'{count:,}')) for name,count in subsets],metrics=[])+f"<p>{h('total', 'Total')}: <b>{sum((x[1] for x in subsets)):,}</b></p>")
  wcxb=next(x for x in direct if x['id']=='wcxb')
  development_pages,test_pages=wcxb['repository_audit']['development_html'],wcxb['repository_audit']['test_html']
  datasets+=section('WCXB '+h('stratification', 'stratification'),f"<p>{h('the_development_split_has_pages_and_the', 'The development split has {development_pages:,} pages and the test split {test_pages:,}; HTML and ground truth were verified present for both.', development_pages=development_pages, test_pages=test_pages)}</p><p>{h('page_types', 'Page types: ')}"+esc(', '.join(wcxb['page_types']))+'</p>')
  frozen=research.get('frozen_sample')
  if frozen:
   distribution_rows=[]
   for axis in frozen['sampling']['official_axes']:
    parent_dist=frozen['parent_distribution'][axis];sample_dist=frozen['sample_distribution'][axis]
    for category in sorted(set(parent_dist)|set(sample_dist)):
     distribution_rows.append(('',cell(esc(axis))+cell(esc(category))+cell(str(parent_dist.get(category,0)))+cell(str(sample_dist.get(category,0)))))
   datasets+=section(h('frozen_case_distribution', 'Frozen {sample_size}-case distribution', sample_size=sample_size),f"<p>{h('parent_and_sample_counts_follow_rare_languages', 'Parent and sample counts follow. Rare languages have only one or two cases and do not support language-capability claims.')} {link(artifact_prefix + 'sample-manifest.json', h('full_manifest_json', 'Full manifest JSON →'))}</p>"+research_table([h('axis', 'Axis'),h('category', 'Category'),h('parent', 'Parent {parent_pages}', parent_pages=parent_pages),h('sample_2', 'Sample {sample_size}', sample_size=sample_size)],distribution_rows,metrics=[]))
  embedded=''.join(f'<article class="guide-card"><h3>{esc(x["name"])}</h3><p>{esc(field(x,"disposition"))}</p><p>{_research_links(x["sources"])}</p></article>' for x in research['embedded_or_supplementary'])
  datasets+=section(h('embedded_and_supplementary_sets', 'Embedded and supplementary sets'),'<div class="guide-grid">'+embedded+'</div>')
  _research_write(out,'datasets/index.html',h('dataset', 'Datasets'),datasets,english)

  method=_research_hero(h('methodology', 'Methodology'),h('preserve_each_benchmark_s_original_data_preprocessing', "Preserve each benchmark's original data, preprocessing, scorer and denominator."),english)
  contract=research['next_run_contract' if english else 'next_run_contract_zh']
  rules=''.join(f'<li><b>{i+1}</b><span>{esc(rule)}</span></li>' for i,rule in enumerate(contract))
  method+=section(h('frozen_contract', 'Frozen contract'),'<ol class="quality-steps">'+rules+'</ol>')
  sampling=research['sampling_contract']
  sample_text=(f'{sampling["size"]} · {esc(sampling["seed"])} · '+esc(sampling['reason' if english else 'reason_zh']))
  method+=section(h('case_sampling_contract', '{sample_size}-case sampling contract', sample_size=sample_size),f"<p><b>{sample_text}</b></p><p>{h('official_axes', 'Official axes: ')}<code>{esc(', '.join(sampling['official_axes']))}</code></p><p>{h('algorithm', 'Algorithm: ')}{esc(sampling['algorithm'])}</p>")
  no_combined=h('the_public_cases_use_webmainbench_545_s', "The public {sample_size} cases use WebMainBench-545's official components; the current {current_cases} cases continue to measure live access, resources, dynamic rendering and latency. Show both without a rescaled combined score.", sample_size=sample_size, current_cases=current_cases)
  method+=section(h('no_synthetic_combined_score', 'No synthetic combined score'),'<p>'+no_combined+'</p>')
  _research_write(out,'methodology/index.html',h('methodology', 'Methodology'),method,english)

  analysis=_research_hero(h('research_findings', 'Research findings'),h('direct_public_benchmarks_exist_but_none_jointly', 'Direct public benchmarks exist, but none jointly isolate live access success and extraction quality.'),english)
  analysis+=section(h('smallest_complete_design', 'Smallest complete design'),f"<p>{esc(research['decision']['summary_en' if english else 'summary_zh'])}</p><p>{h('the_public_set_freezes_html_and_answers', 'The public set freezes HTML and answers extraction/Markdown fidelity on identical input; the current {current_cases} cases continue to answer end-to-end URL-to-output capability.', current_cases=current_cases)}</p>")
  limitations='<ul>'+''.join('<li>'+esc(x)+'</li>' for x in wcxb['limitations' if english else 'limitations_zh'])+'</ul>'
  analysis+=section('WCXB '+h('audit_and_disposition', 'audit and disposition'),f'<p>{esc(field(wcxb,"disposition"))}</p>'+limitations)
  current=research['current_benchmark']
  track_text=' · '.join(f'{name} {count}' for name,count in current['tracks'].items())
  analysis+=section(h('relationship_to_the_current_cases', 'Relationship to the current {current_cases} cases', current_cases=current_cases),f'<p><b>{esc(track_text)}</b></p><p>{esc(field(current,"measures"))}</p><p>{esc(field(current,"difference"))}</p>')
  fusion_rows=[('',cell(esc(field(x,'layer')))+cell(esc(field(x,'keep')))) for x in research['fusion_plan']]
  analysis+=section(h('fuse_process_and_diagnostics_not_scores', 'Fuse process and diagnostics, not scores'),research_table([h('layer', 'Layer'),h('decision_2', 'Decision')],fusion_rows,metrics=[]))
  analysis+=section(h('what_stays_outside_the_main_leaderboard', 'What stays outside the main leaderboard'),f"<p>{h('webarena_mind2web_webvoyager_and_browsecomp_matter_for', 'WebArena, Mind2Web, WebVoyager and BrowseComp matter for browser/search systems, but score actions, task completion or research QA and cannot attribute differences to the fetch layer.')}</p>")
  _research_write(out,'analysis/index.html',h('research_findings', 'Research findings'),analysis,english)

  review=_research_hero(h('review_record', 'Verification'),h('this_verifies_sources_not_benchmark_results', 'This verifies sources, not benchmark results.'),english)
  audit=next(x for x in direct if x['id']=='wceb')['download_audit']
  review+=section(h('verified', 'Verified'),f"""<p>{h('checked', 'Checked')}: {esc(research['verified_at'])}</p><p>WCEB <code>{esc(audit['artifact'])}</code>: {audit['bytes']:,} bytes · SHA-256 <code class="break">{audit['sha256']}</code> · {audit['observed_pages']:,} {h('pages_2', 'pages')}</p><p>BrowseComp: 1,266 {h('data_rows_excluding_the_header', 'data rows excluding the header')}</p>""")
  repo_audit=wcxb['repository_audit']
  review+=section('WCXB '+h('full_repository_audit', 'full repository audit'),f"<p>dev: {repo_audit['development_html']:,} HTML / {repo_audit['development_ground_truth']:,} ground truth · test: {repo_audit['test_html']:,} HTML / {repo_audit['test_ground_truth']:,} ground truth</p><p>{h('empty_main_content', 'Empty main content')}: {repo_audit['empty_main_content']} · with mismatch: {esc(repo_audit['with_snippets_not_in_main_content'])} · without mismatch: {esc(repo_audit['without_snippets_present_in_main_content'])} · test result rows: {repo_audit['published_test_result_rows']}/{repo_audit['official_test_rows']}</p>")
  selected=next(x for x in direct if x['id']=='webmainbench-545')
  scorer_items=''.join('<li>'+esc(item)+'</li>' for item in selected['scorer_audit' if english else 'scorer_audit_zh'])
  review+=section('WebMainBench-545 '+h('scorer_audit', 'scorer audit'),'<ul>'+scorer_items+'</ul>')
  if frozen:
   qa=frozen['quality_audit']
   review+=section('WebMainBench-545 '+h('released_file_full_audit', 'released-file full audit'),f"""<p>{frozen['parent']['bytes']:,} bytes · SHA-256 <code class="break">{esc(frozen['parent']['sha256'])}</code> · {frozen['case_count']} {h('cases_frozen', 'cases frozen')}</p><p>groundtruth_content empty: {qa['empty_groundtruth_content']}/{parent_pages} · HTML empty: {qa['empty_html']}/{parent_pages} · main_html empty: {qa['empty_main_html']}/{parent_pages} · convert_main_content empty: {qa['empty_convert_main_content']}/{parent_pages} · style non-null: {qa['non_null_style']}/{parent_pages} · language non-null: {qa['non_null_language']}/{parent_pages}</p>""")
  review+=section(h('not_started', 'Not started'),f"""<p class="callout">{h('measured_results_rankings_scores_and_independent_result', 'Measured results, rankings, scores and independent result reviews are all zero.')}</p>""")
  _research_write(out,'review/index.html',h('review_record', 'Verification'),review,english)

  guide=_research_hero(h('interpretation', 'Guide'),h('this_edition_answers_what_to_test_not', 'This edition answers what to test, not which method wins.'),english)
  guide+=section(h('reading_order', 'Reading order'),f"""<div class="guide-grid"><article class="guide-card"><h3>1 · {h('selection', 'Selection')}</h3><p>{h('see_why_webmainbench_545_is_the_sole', 'See why WebMainBench-545 is the sole selection.')}</p></article><article class="guide-card"><h3>2 · {h('sampling', 'Sampling')}</h3><p>{h('see_how_cases_cover_official_categories', 'See how {sample_size} cases cover official categories.', sample_size=sample_size)}</p></article><article class="guide-card"><h3>3 · {h('adoption_2', 'Adoption')}</h3><p>{h('separate_execution_integration_and_mere_mentions', 'Separate execution, integration and mere mentions.')}</p></article><article class="guide-card"><h3>4 · {h('measurement', 'Measurement')}</h3><p>{h('rank_only_after_complete_denominators_and_review', 'Rank only after complete denominators and review.')}</p></article></div>""")
  _research_write(out,'guide/index.html',h('interpretation', 'Guide'),guide,english)

  adoption=_research_hero(h('adoption', 'Adoption'),h('adoption_is_evidenced_by_papers_executable_benchmark', 'Adoption is evidenced by papers, executable benchmark repositories and framework integrations; stars are context only.'),english)
  adoption+=section(h('evidence_boundary', 'Evidence boundary'),f"<p>{h('verified_evidence_covers_paper_runs_open_source', 'Verified evidence covers paper runs, open-source adapters and executable benchmark repositories. No independently auditable commercial production-deployment count was found, so extractor support and search hits are not presented as commercial adoption.')}</p>")
  adoption_rows=[]
  for x in direct:
   adoption_rows.append(('',cell(esc(x['name']))+cell('<br>'.join(esc(u) for u in x['users']))+cell(esc(field(x,'maturity')))+cell(_research_links(x['sources']))))
  adoption+=section(h('direct_benchmarks', 'Direct benchmarks'),research_table([h('benchmark', 'Benchmark'),h('verified_users', 'Verified users'),h('assessment', 'Assessment'),h('evidence', 'Evidence')],adoption_rows,metrics=[]))
  browser_rows=[('',cell(esc(x['name']))+cell(esc(field(x,'size')))+cell(esc(field(x,'measures')))+cell(esc(field(x,'adoption')))+cell(esc(field(x,'why_separate')))+cell(_research_links(x['sources']))) for x in adjacent]
  adoption+=section(h('browser_and_search_benchmarks', 'Browser and search benchmarks'),research_table([h('name', 'Name'),h('scale', 'Scale'),h('measures', 'Measures'),h('adoption_2', 'Adoption'),h('why_separate', 'Why separate'),h('sources_2', 'Sources')],browser_rows,metrics=[]))
  stars=[('',cell(esc(name))+cell(str(stars))+cell(str(forks))) for name,stars,forks in research['adoption_snapshot']['repositories']]
  warning=research['adoption_snapshot']['warning' if english else 'warning_zh']
  adoption+=section(h('github_snapshot', 'GitHub snapshot'),f'<p class="source-note">{esc(warning)}</p>'+research_table([h('repository', 'Repository'),'Stars','Forks'],stars,metrics=[]))
  _research_write(out,'adoption/index.html',h('adoption', 'Adoption'),adoption,english)

  reproduce=_research_hero(h('reproduction', 'Reproduction'),h('this_edition_only_reproduces_the_research_report', 'This edition only reproduces the research report and makes no provider calls.'),english)
  reproduce+=section(h('build', 'Build'),'<pre>make public-benchmark-research</pre>')
  reproduce+=section(h('freeze_the_case_manifest', 'Freeze the {sample_size}-case manifest', sample_size=sample_size),'<pre>make public-benchmark-sample SOURCE=/absolute/path/to/WebMainBench_545.jsonl</pre>')
  if frozen: reproduce+=section(h('frozen_artifact', 'Frozen artifact'),f"<p>{link(artifact_prefix + 'sample-manifest.json', h('download_the_case_manifest_json', 'Download the {sample_size}-case manifest JSON →', sample_size=sample_size))}</p>")
  reproduce+=section(h('freeze_before_measurement', 'Freeze before measurement'),f"<p>{h('record_dataset_and_scorer_revisions_environment_licenses', 'Record dataset and scorer revisions, environment, licenses and the complete denominator for each track; provider outputs remain outside Git.')}</p>")
  _research_write(out,'reproduce/index.html',h('reproduction', 'Reproduction'),reproduce,english)

  changes=render_localized_release_history(
   {'releases':release_history},english,kind='public_benchmark_research')
  _research_write(out,'changes/index.html',h('changelog', 'Changes'),changes,english)

  accounts=_research_hero(h('access_setup', 'Accounts and setup'),h('the_research_edition_needs_no_provider_account', 'The research edition needs no provider account.'),english)
  accounts+=section(h('current_requirement', 'Current requirement'),f"""<p class="callout success">{h('only_the_committed_research_and_sample_manifest', 'Only the committed research and sample-manifest JSON files are read; no .env, token, cookie or personal login state is used.')}</p>""")
  accounts+=section(h('measurement_phase', 'Measurement phase'),f"<p>{h('the_measured_edition_will_list_official_installation', 'The measured edition will list official installation sources, versions and required environment-variable names per method, never their values.')}</p>")
  _research_write(out,'accounts/index.html',h('access_setup', 'Accounts and setup'),accounts,english)
 (out/'research.json').write_text(json.dumps(research,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 write_language_contract(out,include_english=include_english)
 return out/'index.html'

def result_view(record):
 views=render_context().result_views
 return views.get((record['provider'],record['case_id']),{}) if views else {}

def result_case(record,fallback):
 """Keep each saved result's own rules separate from its comparison identity."""
 return result_view(record).get('case',fallback)

def result_href(record,kind,base=''):
 """Resolve a retained evidence route relative to the report root."""
 if kind=='task':default='datasets/'+record['case_id']+'.html'
 elif kind=='attempt':default='attempts/'+record['attempt_id']+'.html'
 else:raise ValueError('Unknown result evidence route: '+kind)
 return base+result_view(record).get(kind+'_href',default)

def succeeded(r,c):
 c=result_case(r,c)
 if r.get('report_invocation_status') == 'adapter_rejected':return False
 return task_passed(r['ok'],r['metrics'],c.get('task_kind','body'))

def measured(r,c):
 c=result_case(r,c)
 if r.get('report_invocation_status') == 'adapter_rejected':return False
 return task_measured(r['metrics'],c.get('task_kind','body'))

def result_label(r,c):
 c=result_case(r,c)
 if r.get('report_invocation_status') == 'adapter_rejected':
  return saved_matrix_text('adapter_rejected')
 if r['metrics'].get('evaluation_excluded'):return t('excluded_from_comparison', 'Excluded from comparison')
 if not measured(r,c):return t('undecidable', 'Undecidable')
 state=t('pass_2', 'Pass') if succeeded(r,c) else t('fail_2', 'Fail')
 return (t('list_tasks', 'List tasks:') + f'{state}') if c.get('task_kind')=='structure' else state

def case_rate(rs,c):
 rs=[r for r in rs if measured(r,c)]
 value=rate(sum(succeeded(r,c) for r in rs),len(rs))
 return value

def comparison_population(records,cases):
 """Remove evaluation exclusions from every method's comparison population."""
 excluded={r['case_id'] for r in records if r['metrics'].get('evaluation_excluded')}
 return ([r for r in records if r['case_id'] not in excluded],
         {k:c for k,c in cases.items() if k not in excluded})


def overview_card(records,cases,track,label,names,*,diagnostic=False):
 records,cases=comparison_population(records,cases)
 selected={k:c for k,c in cases.items() if report_family(c)==track}
 if not selected:return ''
 eligible=[r for r in records if r['case_id'] in selected and measured(r,selected[r['case_id']])]
 if diagnostic or not ranking_readiness(records,selected,participants=family_participants(records,selected))[0]:
  return (f"""<div class="card"><div class="eyebrow">{len(selected)}{t('report_tasks_e5ff3d', ' tasks')}</div><h3>{link('#home-rankings-' + track, label)}</h3><p>{t('report_these_saved_results_are_diagnostic_and_uncertified_543b73', 'These saved results are diagnostic and uncertified.')}</p></div>""")
 rates={n:sum(succeeded(r,cases[r['case_id']]) for r in eligible if r['provider']==n)/sum(r['provider']==n for r in eligible) for n in names if any(r['provider']==n for r in eligible)}
 maximum=max(rates.values(),default=None)
 winners='、'.join(provider_label(n) for n in names if rates.get(n)==maximum) if maximum else t('no_passing_tasks_in_this_run', 'No passing tasks in this run') if rates else t('no_call_records_yet', 'No call records yet')
 return ('<div class="card"><div class="eyebrow">' + t('tasks_2', '{count} tasks', count=len(selected)) + f"</div><h3>{link('#home-rankings-' + track, label)}</h3><p>" + t('best_in_family', 'Best in family:') + f'<b>{pct(maximum)}</b></p><p>{esc(winners)}</p></div>')

def review_summary(out, prefix='', *, highlight=True):
 path=out/'result-review.json'
 if not path.is_file():return ''
 audit=json.loads(path.read_text(encoding='utf-8'))
 if audit.get('review_protocol',{}).get('machine_judgments_withheld') is False:
  return '<p class="callout">'+t('known_outcome_audit_scope', 'Evidence audit with known machine outcomes: {reviewed}/{total} results; {unreviewed} machine passes were not individually reviewed.', reviewed=audit.get('reviewed_attempts',0),total=audit.get('total_attempts',0),unreviewed=audit.get('accepted_unreviewed_passes',0))+' '+link(prefix+'review/index.html',t('view_review_rounds', 'View review rounds →'))+'</p>'
 if audit.get('retained_reviews'):
  mode_note = (model_text(*pair('this_round_checked_evidence_with_known_machine', ' This round checked evidence with known machine outcomes; it was not an independent blind review.')) if audit.get('review_protocol',{}).get('blind_initial_verdict') is False else '')
  if audit.get('review_protocol',{}).get('fresh_blind_initial_verdict') is True:
   mode_note=model_text(*pair('fresh_reviews_judged_answers_with_machine_outcomes', ' Fresh reviews judged answers with machine outcomes hidden, then investigated disagreements and anomalies; retained reviews preserve their original protocol.'))
  if audit.get('renewed_context_reviewed_attempts',0):
   blind_count=audit.get('fresh_blind_reviewed_attempts',0);renewed_count=audit['renewed_context_reviewed_attempts']
   mode_note=model_text(*pair('of_these_received_initial_judgments_with_machine', ' Of these, {blind_count} received initial judgments with machine outcomes hidden; {renewed_count} received supplemental adjudication of corrected contracts or request context, preserving prior opinions and inspecting current evidence and machine outcomes.', blind_count=blind_count, renewed_count=renewed_count))
  return ('<p'+(' class="callout"' if highlight else '')+'>'+model_text(*pair('current_review_coverage_freshly_reviewed_prior_reviews', 'Current review coverage: {get}/{get2}: {get3} freshly reviewed; {get4} prior reviews retained after verifying unchanged decision inputs.', get=audit.get('reviewed_attempts', 0), get2=audit.get('expected_attempts', 0), get3=audit.get('fresh_reviewed_attempts', 0), get4=audit.get('retained_reviewed_attempts', 0)))+mode_note+' '+link(prefix+'review/index.html',t('view_review_rounds', 'View review rounds →'))+'</p>')
 if audit.get('adjudication_revision'):
  count=len(audit['adjudication_revision']['adjudicated_attempt_ids'])
  return ('<p'+(' class="callout"' if highlight else '')+'>'+model_text(*pair('current_review_coverage_case_specific_adjudications_supplemented', 'Current review coverage: {get}/{get2}; {count} case-specific adjudications supplemented. No new full blind review or certification is claimed.', get=audit.get('reviewed_attempts', 0), get2=audit.get('expected_attempts', 0), count=count))+' '+link(prefix+'review/index.html',t('view_review_rounds', 'View review rounds →'))+'</p>')
 scope=audit.get('review_protocol',{}).get('scope','all')
 if scope=='supplement-25-local-audit':
  return (f"""<p class="callout success"><b>新增25例结果审核 {audit.get('reviewed_attempts', 0):,}/{audit.get('expected_attempts', 0):,}；未决 {audit.get('unresolved', 0)}；75例沿用既有审核记录</b>。 {link(prefix + 'review/index.html', t('view_review_rounds', 'View review rounds →'))}</p>""")
 subject='机器失败' if scope=='machine-failures' else '全量'
 return (f"""<p class="callout success"><b>独立核验：{subject} {audit.get('reviewed_attempts', 0):,}/{audit.get('expected_attempts', 0):,}；未决 {audit.get('unresolved', 0)}</b>。 {link(prefix + 'review/index.html', t('view_review_rounds', 'View review rounds →'))}</p>""")

def review_index(audit):
 return {x['attempt_id']:x for x in (audit or {}).get('attempts',[])}

def review_cell(audit, rows, base=''):
 if not audit:return ''
 indexed=review_index(audit);items=[indexed[r['attempt_id']] for r in rows if r['attempt_id'] in indexed]
 unresolved=sum(not x.get('final_verdict') for x in items)
 label=t('review_records', '{count}/{count2} · review records', count=len(items), count2=len(rows))
 if unresolved:label=saved_matrix_text('review_coverage_unresolved',reviewed=len(items),total=len(rows),unresolved=unresolved)
 return cell(link(base+'review/index.html',label),len(items)/len(rows) if rows else -1)

def _evidence_chunks(text, width=620, overlap=100):
 chunks=[]
 for raw in text.splitlines():
  line=raw.strip()
  if not line:continue
  if len(line)<=width:chunks.append(line)
  else:
   for start in range(0,len(line),width-overlap):chunks.append(line[start:start+width])
 return chunks

def _best_evidence_excerpt(expected, output):
 wanted=Counter(tokens(expected))
 if not wanted:return ''
 best='';best_score=0
 for chunk in _evidence_chunks(output):
  found=Counter(tokens(chunk));score=sum((wanted & found).values())/sum(wanted.values())
  if score>best_score:best,best_score=chunk,score
 return best if best_score>=min(.25,3/max(1,sum(wanted.values()))) else ''

def _normalized_evidence_lines(output):
 return [[line,normalize_for_match(line),None] for raw in output.splitlines() if (line:=raw.strip())]

def _exact_evidence_lines(expected, output, *, normalized_lines=None):
 """Locate one bounded, verbatim fragment; never repeat a whole long HTML line."""
 normalized_expected=normalize_for_match(expected)
 lines=_normalized_evidence_lines(output) if normalized_lines is None else normalized_lines
 for entry in lines:
  line,normalized_line=entry[:2]
  if not normalized_expected or normalized_expected not in normalized_line:continue
  if len(line)<=1200:return line
  # Retain the full-line match requirement: cutting Markdown syntax must not
  # turn an otherwise excluded destination into a spurious visible-text hit.
  if entry[2] is None:
   entry[2]=[(chunk,normalize_for_match(chunk))
             for chunk in _evidence_chunks(line,width=1200,overlap=600)]
  for chunk,normalized_chunk in entry[2]:
   if normalized_expected in normalized_chunk:return chunk
 return ''

def _diagnostic_log_excerpt(text):
 """Keep exact retained log lines around reader-relevant diagnostics."""
 pattern=re.compile(r'error|warn|fail|timeout|timed out|panic|denied|challenge',re.I)
 lines=text.splitlines();selected=set()
 for index,line in enumerate(lines):
  if pattern.search(line):selected.update(range(max(0,index-1),min(len(lines),index+2)))
  if len(selected)>=36:break
 if not selected:selected.update(range(min(12,len(lines))))
 return '\n'.join(f'{index+1:>6}  {lines[index]}' for index in sorted(selected))

def _call_log_excerpt(item):
 """Prefer exact structured diagnostics while retaining plain legacy logs."""
 payload=item['payload']
 if not payload:return _diagnostic_log_excerpt(item['text'])
 parts=[]
 error=payload.get('error') if isinstance(payload.get('error'),dict) else {}
 process=payload.get('process') if isinstance(payload.get('process'),dict) else {}
 events=payload.get('events') if isinstance(payload.get('events'),list) else []
 if error.get('type') or error.get('message'):
  parts.append('error: '+str(error.get('type') or '—')+' · '+str(error.get('message') or '—'))
 if process.get('stderr'):
  parts.append('stderr:\n'+str(process['stderr']))
 elif process.get('stdout'):
  parts.append('stdout:\n'+str(process['stdout']))
 for event in events:
  if isinstance(event,dict) and (event.get('error_type') or event.get('stage')):
   parts.append('event: '+json.dumps(event,ensure_ascii=False,sort_keys=True))
 return _diagnostic_log_excerpt('\n'.join(parts) if parts else item['text'])

def _body_region_evidence(content,contract):
 """Map canonical scorer allocations to complete lines without changing scoring output."""

 version=contract['version'];size=6 if version==3 else 3
 units=tokens(content) if version==1 else compact_ngrams(content,size)
 predicted=Counter(units);expected={}
 for region in contract['regions']:
  values=(region['text'].split() if version==1 else compact_ngrams(region['text'],size))
  expected[region['id']]=Counter(values)
 if version==3:
  floor=contract['minimum_floor'];target=contract['minimum_coverage']
  floors={identifier:ceil(floor*sum(row.values())-1e-12) for identifier,row in expected.items()}
  targets={identifier:ceil(target*sum(row.values())-1e-12) for identifier,row in expected.items()}
  assigned,_=_global_region_allocation(
   predicted,expected,floors,targets,contract['minimum_region_passes'])
 else:
  assigned={identifier:predicted & row for identifier,row in expected.items()}
 lines=[raw.strip() for raw in content.splitlines() if raw.strip()]
 evidence_lines={identifier:set() for identifier in assigned}
 if version==1:
  occurrences=defaultdict(list);flattened=[]
  for line_index,line in enumerate(lines):
   for unit in tokens(line):
    flattened.append(unit);occurrences[unit].append((line_index,))
  if flattened!=tokens(content):
   raise ValueError('Body token evidence differs from scorer normalization')
  for identifier,row in assigned.items():
   for unit,count in row.items():
    for location in occurrences[unit][:count]:evidence_lines[identifier].update(location)
 else:
  compact_lines=[compact_ngrams(line,1) for line in lines]
  flattened=[character for line in compact_lines for character in line]
  if flattened!=compact_ngrams(content,1):
   raise ValueError('Body fragment evidence differs from scorer normalization')
  character_lines=[]
  for line_index,line in enumerate(compact_lines):character_lines.extend([line_index]*len(line))
  occurrences=defaultdict(list)
  for index,unit in enumerate(compact_ngrams(content,size)):
   occurrences[unit].append(tuple(dict.fromkeys(character_lines[index:index+size])))
  offsets=Counter()
  for identifier in sorted(assigned):
   for unit,count in assigned[identifier].items():
    start=offsets[unit] if version==3 else 0
    selected=occurrences[unit][start:start+count]
    if len(selected)!=count:raise ValueError('Body evidence allocation exceeds scorer units')
    for location in selected:evidence_lines[identifier].update(location)
    if version==3:offsets[unit]+=count
 evidence={identifier:[lines[index] for index in sorted(indices)]
           for identifier,indices in evidence_lines.items()}
 return evidence,{identifier:sum(row.values()) for identifier,row in assigned.items()}

def _position_label(index,total):
 names=[t('opening', 'Opening'),t('early_section', 'Early section'),t('middle', 'Middle'),t('late_section', 'Late section'),t('ending', 'Ending')] if total==5 else [f'位置 {i+1}' for i in range(total)]
 start=round(index*100/total);end=round((index+1)*100/total)
 return f'{names[index]}（标准答案 {start}–{end}%）'

def _body_scoring_text(record, output):
 """Use the scorer's body input; titles belong only to answer diagnostics."""
 content=answer_input(output.encode('utf-8'),record.get('provider_metadata'),
                      ok=record.get('ok',False),status_code=record.get('status_code'))[0]
 if is_html_document_or_fragment(content):
  content=visible_html_text(content)
 else:
  fallback=html.unescape(re.sub(r"<(script|style|template)\b[^>]*>.*?</\1\s*>","",content,flags=re.I|re.S))
  content=native_markup_body_text(content,fallback)
 return content

def body_region_rows(case, metrics, output, *, frozen_scores=False):
 contract=case.get('body_requirements') or {}
 if not contract:return [],''
 saved_regions=metrics.get('body_region_results',[])
 recalculated={}
 if frozen_scores:
  # The frozen-build boundary validates the saved metric and answer hashes.
  # A newer normalizer or a redacted public answer cannot reproduce the old
  # matching-text identity. Render the saved allocation without rescoring it.
  evidence={}
  for item in saved_regions:
   passages=[]
   for passage in item.get('matched_output_passages',[]):
    if passage.get('text'):
     public,_=project_evidence_view('public-evidence/excerpt.txt',passage['text'].encode(),role='scored_answer',content_format='text')
     passages.append(public.decode())
   evidence[item['id']]=passages
  evidence_counts={item['id']:item.get('matched_units',item.get('matched_tokens',0)) for item in saved_regions}
  recalculated={item['id']:item for item in saved_regions}
 elif saved_regions and all('matched_output_passages' in item for item in saved_regions):
  matching_text=_compact_with_positions(output)[0]
  digest=hashlib.sha256(matching_text.encode()).hexdigest()
  if any(item.get('matching_text_sha256')!=digest for item in saved_regions):
   raise ValueError('Saved body evidence differs from the scorer input')
  evidence={item['id']:[passage.get('text','') for passage in item.get('matched_output_passages',[]) if passage.get('text')]
            for item in saved_regions}
  evidence_counts={item['id']:item.get('matched_units',item.get('matched_tokens',0)) for item in saved_regions}
  recalculated={item['id']:item for item in saved_regions}
 else:
  recalculated={x['id']:x for x in evaluate_body_requirements(output,contract)['body_region_results']}
  evidence,evidence_counts=_body_region_evidence(output,contract)
 regions={x['id']:x for x in contract['regions']}
 rows=[];cards=[];total=len(regions)
 for index,item in enumerate(metrics.get('body_region_results',[]),1):
  actual={**recalculated.get(item['id'],{}),**item}
  if recalculated and evidence_counts.get(item['id'])!=recalculated.get(item['id'],{}).get('matched_units'):
   raise ValueError('Body evidence allocation differs from scorer: '+item['id'])
  expected=regions.get(item['id'],{}).get('text','')
  expected_count=actual.get('expected_units',actual.get('expected_tokens',len(expected.split())))
  matched=actual.get('matched_units',actual.get('matched_tokens',round(actual['coverage']*expected_count)))
  passed=actual.get('target_pass',actual['pass'])
  label=_position_label(index-1,total)
  judgement=(t('meets_target', 'Meets target') if passed else t('below_target_above_hard_floor', 'Below target; above hard floor') if actual.get('floor_pass') else t('below_hard_floor', 'Below hard floor'))
  css='pass' if passed else 'fail'
  rows.append(('',cell(esc(label))+cell(f'{expected_count:,}')+cell(f'{matched:,}')+
               cell(pct(actual['coverage']))+cell(esc(judgement))))
  passage='\n\n'.join(evidence.get(item['id'],[]))
  missing=(model_text(*pair('the_historical_score_has_no_saved_matching', 'The historical score has no saved matching passage for this region; inspect the complete output.'))
           if frozen_scores and 'matched_output_passages' not in item else t('the_scorer_assigned_no_displayable_actual_output', 'The scorer assigned no displayable actual-output text to this region.'))
  actual_html=(f'<pre data-kind="region-output">{esc(passage)}</pre>' if passage else
               '<p class="empty">'+missing+'</p>')
  cards.append((f'<article class="region-card {css}" id="region-{index}"><div class="region-head"><div><h3>{index}. {esc(label)}</h3><p>' + t('this_region_has_coverage_every_region_must', 'This region has {coverage} coverage. Every region must reach at least {get}; reaching {minimum} counts as a substantially complete region.', coverage=pct(actual['coverage']), get=pct(actual.get('floor', actual['minimum'])), minimum=pct(actual['minimum'])) + f'</p></div><span class="verdict-badge">{esc(judgement)}</span></div><div class="region-pair"><div><b>' + t('complete_reference_text_for_this_region', 'Complete reference text for this region') + f'</b><pre data-kind="reference">{esc(expected)}</pre></div><div><b>' + t('complete_actual_output_lines_used_to_score', 'Complete actual-output lines used to score this region') + f'</b>{actual_html}</div></div></article>'))
 return rows,cards

def stable_answer_panel(contract):
 anchors=contract.get('stable_text_anchors',[])
 anchors_required=contract.get('stable_text_role','required')!='diagnostic'
 anchor_label=t('stable_text_that_must_appear', 'Stable text that must appear') if anchors_required else t('stable_text_for_scope_checks', 'Stable text for scope checks')
 items=[]
 for index,anchor in enumerate(anchors,1):
  alternatives=anchor.get('alternatives',[])
  text=('\n<span>' + t('or', 'or') + '</span>\n').join(f'<span translate="no">{esc(value)}</span>' for value in alternatives)
  items.append(f'<div class="answer-item"><b>{anchor_label} {index}（任一写法即可）</b><blockquote>{text}</blockquote></div>')
 minimum=contract.get('minimum_records')
 maximum=contract.get('maximum_records')
 count=(t('at_least_complete_unique_records', 'At least {minimum} complete, unique records', minimum=minimum) if maximum is None else
        f'{minimum}–{maximum} 条完整且不重复的记录')
 scroll=' data-scroll-after="5"' if len(anchors)>5 else ''
 role_note=t('all_belong_to_the_reference_answer', 'All belong to the reference answer') if anchors_required else t('used_only_for_scope_checks_does_not', 'Used only for scope checks; does not affect the result')
 note=(f'<p class="source-note">共 {len(anchors)} 组稳定文字，{role_note}；当前区域显示约 5 组高度，向下滚动查看全部。</p>'
       if len(anchors)>5 else '')
 return (f'{note}<div class="answer-list"{scroll}><div class="answer-item"><b>' + t('structure_requirements', 'Structure requirements') + f"</b><blockquote>{esc(count)}</blockquote></div>{''.join(items)}</div>")

def required_text_answer_panel(case):
 items=[('<div class="answer-item"><b>' + t('body_length_requirement', 'Body length requirement') + '</b><blockquote>' + t('at_least_characters', 'At least {get:,} characters', get=case.get('min_content_chars', 1)) + '</blockquote></div>')]
 for index,fact in enumerate(case.get('required_facts',[]),1):
  text=('\n<span>' + t('or', 'or') + '</span>\n').join(f'<span translate="no">{esc(value)}</span>' for value in fact.get('alternatives',[]))
  items.append(('<div class="answer-item"><b>' + t('required_text_group_any_alternative_may_match', 'Required text group {index} (any alternative may match)', index=index) + f'</b><blockquote>{text}</blockquote></div>'))
 threshold=case.get('fact_coverage_threshold',1.0)
 note=(('<p class="source-note">' + t('every_text_group_must_match', 'Every text group must match.') + '</p>') if threshold==1 else
       ('<p class="source-note">' + t('text_group_coverage_must_reach_at_least', 'Text-group coverage must reach at least {threshold}.', threshold=pct(threshold)) + '</p>'))
 return note+'<div class="answer-list">'+''.join(items)+'</div>'


def machine_contract(case):
 contract={key:case[key] for key in ('acceptance_contract','composite_acceptance','body_requirements') if case.get(key)}
 return contract

def composite_contract_details(contract):
 labels={'named_link':t('name_and_real_link', 'Name and real link'),'name_description':t('name_and_description', 'Name and description'),'table':t('table_fields', 'Table fields'),
         'linked_table':t('same_row_fields_and_real_link', 'Same-row fields and real link'),'literals':t('fixed_text_groups', 'Fixed text groups'),'field_report':t('same_report_fields', 'Same report fields')}
 rows=[]
 for group in contract.get('groups',[]):
  scope=[]
  if group.get('start_pattern'):scope.append((t('start', 'Start:') + f"{group['start_pattern']}"))
  if group.get('end_pattern'):scope.append((t('end', 'End:') + f"{group['end_pattern']}"))
  requirements=[]
  if group.get('minimum_records') is not None:requirements.append(f'至少 {group["minimum_records"]} 条')
  if group.get('hosts'):requirements.append((t('hosts', 'Hosts: ') + f"{'、'.join(group['hosts'])}"))
  if group.get('entry_path'):requirements.append((t('path', 'Path:') + f"{group['entry_path']}"))
  if group.get('required_fields'):requirements.append((t('same_entry_fields', 'Same-entry fields:') + f"{'、'.join((field['id'] for field in group['required_fields']))}"))
  if group.get('required_columns'):requirements.append((t('same_row_columns', 'Same-row columns:') + f"{'、'.join((field['id'] for field in group['required_columns']))}"))
  if group.get('required_literals'):requirements.append((t('all_text', 'All text:') + f"{'、'.join(group['required_literals'])}"))
  if group.get('fixed_values'):requirements.append((t('fixed_value', 'Fixed value:') + f"{'、'.join((f'{key}={value}' for key, value in group['fixed_values'].items()))}"))
  rows.append(('',cell(esc(group['id']))+cell(esc(labels.get(group['kind'],group['kind'])))+
               cell(esc('；'.join(scope) or t('whole_response', 'Whole response')))+cell(esc('；'.join(requirements)))))
 return (('<div class="answer-item"><b>' + t('combined_rule', 'Combined rule') + '</b><p>' + t('each_group_is_computed_separately_and_every', 'Each group is computed separately and every group must pass; counts, links and extra fields cannot be stitched across lists or entries.') + f"</p>{table([t('rule_group', 'Rule group'), t('check_type', 'Check type'), t('scope', 'Scope'), t('must_satisfy', 'Must satisfy')], rows, metrics=[])}</div>"))

def composite_result_evidence(metrics):
 results=metrics.get('composite_group_results',[])
 if not results:return ''
 labels={'named_link':t('name_and_real_link', 'Name and real link'),'name_description':t('name_and_description', 'Name and description'),'table':t('table_fields', 'Table fields'),
         'linked_table':t('same_row_fields_and_real_link', 'Same-row fields and real link'),'literals':t('fixed_text_groups', 'Fixed text groups'),'field_report':t('same_report_fields', 'Same report fields')}
 rows=[];details=[]
 for result in results:
  count='—' if result.get('count') is None else str(result['count'])
  minimum='—' if result.get('minimum') is None else str(result['minimum'])
  rows.append(('',cell(esc(result['id']))+cell(esc(labels.get(result['kind'],result['kind'])))+
               cell(count)+cell(minimum)+cell(t('pass_2', 'Pass') if result.get('pass') else t('fail_2', 'Fail'))))
  payload={key:result[key] for key in ('records','literal_results','fixed_value_results') if result.get(key)}
  if payload:
   details.append((f"<details><summary>{esc(result['id'])} " + t('parsing_result', ' parsing result') + f'</summary><pre data-kind="metadata">{esc(json.dumps(payload, ensure_ascii=False, indent=2))}</pre></details>'))
 return (('<div class="decision-lead"><b>' + t('combined_rule_result', 'Combined-rule result:') + '</b>' + t('each_group_is_checked_independently_entries_from', 'Each group is checked independently; entries from another list cannot fill a gap, and fields from different entries cannot be joined.') + f"</div>{table([t('rule_group', 'Rule group'), t('check_type', 'Check type'), t('valid_entries', 'Valid entries'), t('at_least', 'At least'), t('verdict', 'Verdict')], rows, metrics=[])}{''.join(details)}"))

def _stable_field_comparison(metrics,item):
 """Find the closest parsed record for a structured anchor, for display only."""
 schema=metrics.get('stable_task_schema')
 if schema!='quote':return None
 candidates=[]
 for alternative in item.get('alternatives',[]):
  expected=_parse_records(_visible(alternative,schema),schema)
  candidates.extend(row['fields'] for row in expected if row.get('valid'))
 actual=[row['fields'] for row in metrics.get('stable_records',[]) if row.get('valid')]
 if not candidates or not actual:return None
 def norm(value):return normalize_for_match(value).strip('“”"‘’ ')
 best=None
 for expected in candidates:
  for returned in actual:
   exact={field:norm(returned.get(field,''))==norm(expected.get(field,'')) for field in SCHEMA_FIELDS[schema]}
   score=sum(exact.values())+2*exact.get('quote',False)+exact.get('author',False)
   if best is None or score>best[0]:best=(score,expected,returned,exact)
 return best[1:] if best else None

def _quote_field_issue(metrics):
 comparisons=[]
 for item in metrics.get('stable_anchor_results',[]):
  if item.get('pass'):continue
  comparison=_stable_field_comparison(metrics,item)
  if comparison and comparison[2].get('quote') and comparison[2].get('author'):
   comparisons.append(comparison)
 if not comparisons:return ''
 compact=0
 for expected,returned,_ in comparisons:
  left=normalize_for_match(expected.get('tags','')).replace(' ','')
  right=normalize_for_match(returned.get('tags','')).replace(' ','')
  compact+=bool(left and left==right)
 contaminated=len(comparisons)-compact
 parts=[t('quotes_and_authors_of_failed_checks_match', 'Quotes and authors of {count} failed checks match the reference', count=len(comparisons))]
 if compact:parts.append(t('records_have_complete_tag_text_but_no', '{compact} records have complete tag text but no readable separator between tags', compact=compact))
 if contaminated:parts.append(t('records_have_tag_fields_mixed_with_extra', '{contaminated} records have tag fields mixed with extra text or missing text', contaminated=contaminated))
 return (f"{'；'.join(parts)}" + t('the_task_requires_the_tag_list_to', '. The task requires the tag list to keep field boundaries, so it still fails'))

def _returned_answer_fields(metadata):
 return {key:metadata[key] for key in ('links','images','videos','media','extras','page_information','page_structure','records')
         if metadata.get(key)}

def _stable_passage_excerpt(text, alternatives, schema, representation):
 from lexbench_webfetch.stable_tasks import _stable_literal_match
 lines = [line for line in text.splitlines() if line.strip()]
 visible_lines = lines if representation == 'plain_text' else [
     re.sub(r'^\s*(?:#{1,6}|[-*+])[ 	]+', '', line) for line in lines]
 normalized = [normalize_for_match(line, representation=representation) for line in visible_lines]
 expected = [normalize_for_match(value, representation=representation) for value in alternatives]
 for index in range(len(lines)):
  for width in range(1, min(5, len(lines)-index)+1):
   passage = '\n'.join(lines[index:index+width])
   if len(passage)>1200:
    break
   joined = ' '.join(normalized[index:index+width])
   if any(value and value in joined for value in expected) or any(
       _stable_literal_match(passage, value, representation=representation,
                                rendered=schema in {'body_text','page_identity'})
          for value in alternatives):
    return passage
 return ''

def stable_anchor_evidence(metrics,output,metadata=None,base_url='',contract=None):
 from lexbench_webfetch.response_input import NON_TEXT_PAGE_INFORMATION_FIELDS, unscoped_page_information
 from lexbench_webfetch.stable_tasks import _stable_literal_match
 fields=_returned_answer_fields(metadata or {})
 # Only fields the scorer accepts for anchors can be shown as their evidence;
 # returned link labels never count as page text.
 candidates=[]
 for key,value in (fields.get('page_information',{}) if unscoped_page_information(contract) else {}).items():
  if key not in NON_TEXT_PAGE_INFORMATION_FIELDS:
   candidates.extend(('page_information.'+key,text) for text in (value if isinstance(value,list) else [value])
                     if isinstance(text,str))
 cards=[];normalized_lines=None;visible_lines=None;passage_text=None;context_text=None
 anchors_required=metrics.get('stable_anchors_required',True)
 expected_label=t('reference_answer', 'Reference answer') if anchors_required else t('scope_check_text', 'Scope-check text')
 for index,item in enumerate(metrics.get('stable_anchor_results',[]),1):
  alternatives=item.get('alternatives',[]);expected=('\n<span>' + t('or', 'or') + '</span>\n').join(f'<span translate="no">{esc(value)}</span>' for value in alternatives)
  excerpt=''
  for alternative in (item.get('matched') or alternatives):
   if normalized_lines is None:normalized_lines=_normalized_evidence_lines(output)
   candidate=_exact_evidence_lines(alternative,output,normalized_lines=normalized_lines)
   if candidate:excerpt=candidate;break
  passed=bool(item.get('pass'));css='pass' if passed else 'fail'
  comparison=_stable_field_comparison(metrics,item) if not passed else None
  field_detail='';badge=t('matched', 'Matched') if passed else t('not_matched', 'Not matched')
  if comparison:
   expected_fields,returned_fields,exact=comparison
   labels={'quote':t('quote', 'Quote'),'author':t('author', 'Author'),'tags':t('tags', 'Tags')};rows=[]
   for field in ('quote','author','tags'):
    wanted=expected_fields.get(field,'');got=returned_fields.get(field,'')
    compact=(normalize_for_match(wanted).replace(' ','')==normalize_for_match(got).replace(' ',''))
    verdict=t('matched', 'Matched') if exact[field] else t('text_complete_but_separators_lost', 'Text complete, but separators lost') if field=='tags' and compact else t('different', 'Different')
    rows.append(('',cell(esc(labels[field]))+cell(f'<span translate="no">{esc(wanted)}</span>')+cell(f'<span translate="no">{esc(got)}</span>')+cell(esc(verdict))))
   field_detail=('<p class="source-note">' + t('whole_records_are_judged_by_field_text', 'Whole records are judged by field; text appearing does not mean list boundaries are intact.') + f"</p>{table([t('field', 'Field'), t('reference_answer', 'Reference answer'), t('actually_parsed', 'Actually parsed'), t('verdict', 'Verdict')], rows, metrics=[])}")
   badge=t('invalid_field_boundaries', 'Invalid field boundaries')
  actual=f'<p translate="no">{esc(excerpt)}</p>' if excerpt else ''
  if passed and not actual:
   if visible_lines is None:
    visible = _visible(output, metrics.get('stable_task_schema'),
                       representation=(metadata or {}).get('answer_format'))
    visible_lines = _normalized_evidence_lines(visible)
   for alternative in item.get('matched',[]):
    visible_excerpt = _exact_evidence_lines(alternative, '', normalized_lines=visible_lines)
    if visible_excerpt:
     actual=('<p class="source-note">'+model_text('从返回内容读取的文字','Text read from the response')
             +f'</p><p translate="no">{esc(visible_excerpt)}</p>')
     break
  if passed and not actual and not item.get('match_scope') and not item.get('visible_control'):
   field_match=next(((field,text) for field,text in candidates if any(
       _stable_literal_match(text,value,representation='plain_text') for value in item.get('matched',[]))),None)
   if field_match:
    field,text=field_match
    actual=(f'<p><a href="#returned-fields"><code>{esc(field)}</code></a></p>'
            f'<p translate="no">{esc(text)}</p>')
  if passed and not actual:
   from lexbench_webfetch.reference_quality import markdown_body_content, visible_mixed_html_text
   from lexbench_webfetch.stable_tasks import _non_record_visible
   schema=metrics.get('stable_task_schema')
   representation=(metadata or {}).get('answer_format')
   if item.get('match_scope')=='non_record_lines' and contract:
    if context_text is None:
     context_text=_non_record_visible(output,contract,metrics.get('stable_records',[]),
                                      base_url,representation=representation) or ''
    text=context_text
   elif not item.get('match_scope') and not item.get('visible_control'):
    if passage_text is None:
     source=output if representation in {'plain_text','html'} else markdown_body_content(output)
     source=source if representation=='plain_text' else visible_mixed_html_text(source)
     passage_text=_visible(source,schema,representation=representation)
    text=passage_text
   else:
    text=''
   passage=_stable_passage_excerpt(text,item.get('matched',[]),schema,representation)
   if passage:
    actual=('<p class="source-note">'+model_text('从返回内容读取的文字','Text read from the response')
            +f'</p><pre translate="no">{esc(passage)}</pre>')
  if passed and not actual:
   actual=model_text('保存的判定为命中。未找到可供定位的连续原文，请核对下方解析记录与返回字段。',
                     'The saved check passed. This record has no locatable continuous excerpt. See the parsed records and returned fields.')
  if not actual:
   actual=('<p class="empty">' + t('no_continuous_source_excerpt_could_be_located', 'No continuous source excerpt could be located in the actual output.') + '</p>')
  cards.append((f'<article class="anchor-card {css}"><div class="anchor-head"><h3>' + t('stable_text', 'Stable text {index}', index=index) + f'</h3><span class="verdict-badge">{badge}</span></div><div class="evidence-pair"><div class="evidence-sample"><b>{expected_label}</b><p>{expected}</p></div><div class="evidence-sample"><b>' + t('actual_output_evidence', 'Actual-output evidence') + f'</b>{actual}</div></div>{field_detail}</article>'))
 if not cards:return ''
 role_note=(t('all_count_toward_scoring', 'All count toward scoring') if anchors_required else
            t('used_only_for_scope_checks_does_not', 'Used only for scope checks; does not affect the result'))
 note=(f'<p class="source-note">共 {len(cards)} 项稳定文字判定，{role_note}；当前区域显示约 5 项高度，向下滚动查看全部。</p>'
       if len(cards)>5 else '')
 scroll=' data-scroll-after="5"' if len(cards)>5 else ''
 return note+f'<div class="anchor-grid"{scroll}>'+''.join(cards)+'</div>'

def failure_category(r,c):
 c=result_case(r,c)
 if r.get('report_invocation_status') == 'adapter_rejected':return result_label(r,c)
 if r['metrics'].get('evaluation_excluded'):return t('evaluation_environment_defect_excluded_across_all_methods', 'Evaluation environment defect: excluded across all methods')
 if not measured(r,c):return t('undecidable_evidence_of_the_final_page_identity', 'Undecidable: evidence of the final page identity is missing') if r['metrics'].get('page_identity_required') and r['metrics'].get('page_identity_status')=='unavailable' else t('unmeasured_acceptance_evidence_is_missing', 'Unmeasured: acceptance evidence is missing')
 if succeeded(r,c):return '—'
 # The evidence diagnosis owns the cause; downstream rule symptoms must not
 # replace it in inventories, filters or same-task comparisons.
 if r.get('diagnosis'):
  return r['diagnosis']['category']
 if r['metrics'].get('page_identity_required') and r['metrics'].get('page_identity_status') != 'verified':
  return t('final_page_identity_is_not_verified', 'Final page identity is not verified')
 # Classification describes an already failed result; hints never veto success.
 metrics=r['metrics']
 if (r.get('ok',False) and metrics.get('request_ok',True)
     and (metrics.get('blocked_page') or metrics.get('stable_blocked_page'))):
  return t('verification_or_block_page_returned', 'Verification or block page returned')
 if c.get('track') == 'risk_control':
  metrics=r['metrics']
  if not metrics.get('request_ok',r.get('ok',False)):
   return t('request_or_program_execution_failed', 'Request or program execution failed')
  content_chars=metrics.get('content_chars')
  if content_chars == 0:
   return t('completely_empty_response', 'Completely empty response')
  if (
   isinstance(content_chars,int) and content_chars <= 100
   and not metrics.get('markdown_headings')
   and not metrics.get('markdown_links')
   and not metrics.get('stable_anchor_hits')
   and not metrics.get('content_check_hits')
  ):
   return t('waiting_message_or_page_shell_only', 'Waiting message or page shell only')
  return t('acceptance_rules_not_met', 'Acceptance rules not met')
 if r['metrics'].get('composite_acceptance_pass') is False:return t('combined_rule_not_met', 'Combined rule not met')
 if _quote_field_issue(r['metrics']):return t('field_boundaries_lost', 'Field boundaries lost')
 return (r.get('diagnosis') or diagnose(r,c,'') or {'category':t('task_requirements_not_met', 'Task requirements not met')})['category']

def stable_failure_summary(metrics):
 parts=[]
 minimum=metrics.get('stable_minimum_records')
 complete=metrics.get('stable_unique_record_count')
 if isinstance(minimum,int) and isinstance(complete,int) and complete < minimum:
  parts.append(t('only_records_are_both_complete_and_non', 'Only {complete} records are both complete and non-duplicate, fewer than the required {minimum}', complete=complete, minimum=minimum))
 quote_issue=_quote_field_issue(metrics)
 if quote_issue:return quote_issue
 missing=[]
 for item in metrics.get('stable_anchor_results',[]):
  if not item.get('pass'):
   alternatives=item.get('alternatives',[])
   if alternatives:missing.append(' / '.join(alternatives))
 if missing:parts.append((t('stable_text_missing', 'Stable text missing: "') + f"{'”、“'.join(missing)}”"))
 if metrics.get('text_encoding_corruption'):
  parts.append(t('output_is_visibly_garbled_replacement_characters_of', 'Output is visibly garbled: {get:,} replacement characters, {get2} of it', get=metrics.get('replacement_character_count', 0), get2=pct(metrics.get('replacement_character_ratio'))))
 errors=set(metrics.get('stable_task_errors') or [])
 labels=[
  ('declared_truncation',t('the_method_explicitly_declares_the_body_truncated', 'The method explicitly declares the body truncated')),
  ('script_payload_shell',t('returned_script_or_serialized_state_not_readable', 'Returned script or serialized state, not readable body text')),
  ('content_too_short',t('body_shorter_than_the_task_s_declared', "Body shorter than the task's declared minimum")),
  ('duplicate_records',t('returned_records_contain_duplicates', 'Returned records contain duplicates')),('incomplete_records',t('some_records_miss_required_fields', 'Some records miss required fields')),
  ('too_many_complete_records',t('complete_records_exceed_the_declared_task_range', 'Complete records exceed the declared task range')),
 ]
 for key,label in labels:
  if key in errors and label not in parts:parts.append(label)
 return '；'.join(parts) or t('stable_text_or_record_structure_did_not', 'Stable text or record structure did not meet the task requirements')

def reason(r,c):
 c=result_case(r,c)
 if r.get('report_invocation_status') == 'adapter_rejected':
  return saved_matrix_text('adapter_rejected_note')
 if r['metrics'].get('evaluation_excluded'):
  return r['metrics']['evaluation_exclusion_reason']
 if not measured(r,c):
  if r['metrics'].get('page_identity_required') and r['metrics'].get('page_identity_status')=='unavailable':
   return t('undecidable_the_actual_response_is_saved_but', 'Undecidable: the actual response is saved, but the final page identity cannot be verified; it counts toward neither passes, failures nor the pass-rate denominator.')
  return t('unmeasured_the_historical_call_has_no_original', 'Unmeasured: the historical call has no original-file byte acceptance evidence and counts toward neither the task pass rate nor failures.')
 if r['metrics'].get('deadline_overrun'):return t('the_call_exceeded_its_registered_end_to', 'The call exceeded its registered end-to-end deadline. Actual latency and returned content are retained, but the result does not count as on-time delivery.')
 if r['metrics'].get('forbidden_requirement_hits'):
  return (t('matched_content_the_task_explicitly_forbids', 'Matched content the task explicitly forbids:') + f"{'、'.join(r['metrics']['forbidden_requirement_hits'])}。")
 if r['ok'] and 'stable_task_pass' in r['metrics']:
  m=r['metrics']
  if m.get('composite_acceptance_pass') is False:
   failed=[row['id'] for row in m.get('composite_group_results',[]) if not row.get('pass')]
   return t('combined_rule_not_met_2', 'Combined rule not met: {value}.', value='、'.join(failed) if failed else t('failed_rule_group_not_registered', 'Failed rule group not registered'))
  success=t('stable_content_and_structure_checks_passed', 'Stable content and structure checks passed.') if m.get('stable_anchor_results') or m.get('stable_page_pattern_results') else t('record_structure_passed_this_task_has_no', 'Record structure passed; this task has no stable-text checks.')
  if m.get('stable_task_schema') == 'body_text':
   return success if succeeded(r,c) else stable_failure_summary(m)+'。'
  counts=f"检出 {m.get('stable_record_count','—')} 条，字段完整 {m.get('stable_complete_record_count','—')} 条，完整且唯一 {m.get('stable_unique_record_count','—')} 条，要求至少 {m.get('stable_minimum_records','—')} 条。"
  return counts+(success if succeeded(r,c) else stable_failure_summary(m)+'。')
 if not r['ok']:
  if r.get('diagnosis'):return r['diagnosis']['evidence']
  names={'dns':t('a_domain_on_the_request_path_could', 'A domain on the request path could not be resolved'),'timeout':t('request_timed_out', 'Request timed out'),'network':t('network_failure_on_the_request_path', 'Network failure on the request path'),'api_error':t('provider_interface_error', 'Provider interface error'),'WEB_BLOCKED_URL':t('the_method_s_url_safety_rule_rejected', "The method's URL safety rule rejected the request")}
  return names.get(r.get('error_type'),r.get('error_type') or t('request_failure', 'Request failure'))+': '+_error_locator(r.get('error_message'))
 m=r['metrics']
 if m.get('text_encoding_corruption'):
  return t('the_returned_output_is_visibly_garbled_replacement', 'The returned output is visibly garbled: {get:,} replacement characters, {get2} of it; damaged text is not valid body text.', get=m.get('replacement_character_count', 0), get2=pct(m.get('replacement_character_ratio')))
 if m.get('body_region_results') and not m.get('body_coverage_pass'):
  regions=m['body_region_results'];failed=[]
  for index,item in enumerate(regions):
   if not item.get('target_pass',item.get('pass')):
    failed.append(f'{_position_label(index,len(regions)).split("（")[0]} {pct(item.get("coverage"))}')
  return '正文没有抓全；低于目标的位置为：'+'、'.join(failed)+'。下方会把完整标准答案与实际返回并排展示。'
 if r.get('diagnosis'):return r['diagnosis']['evidence']
 if c.get('task_kind')=='structure':
  return t('found_named_detail_entries', 'Found {get} named detail entries.', get=r['metrics'].get('named_entry_count', 0))+list_rule_description(c)
 if r['metrics'].get('page_identity_required') and r['metrics'].get('page_identity_status') != 'verified':
  return t('failed_final_page_identity_lacks_evidence_this', 'Failed: final page identity lacks evidence; this result remains in the planned task denominator.')
 if c.get('task_kind')=='file':return t('original_file_bytes_were_returned_and_passed', 'Original-file bytes were returned and passed format signature, length, and target SHA-256 validation.') if succeeded(r,c) else t('no_verifiable_original_file_was_returned_text', 'No verifiable original file was returned; text or repeating the input URL does not count as a file.')
 if c.get('task_kind')=='resource':
  missing=[x['id'] for x in r['metrics'].get('resource_results',[]) if not x['pass']]
  unexpected=r['metrics'].get('unexpected_resource_urls',[])
  if missing and unexpected:
   return ((t('target_resource_not_found', 'Target resource not found:') + f"{', '.join(missing)}" + t('also_returned_unexpected_resources', '; also returned unexpected resources: ') + f"{', '.join(unexpected)}"))
  if unexpected:return t('the_target_resource_was_found_but_answer_2', 'The target resource was found, but answer-bearing fields also returned unexpected resources: {join}', join=', '.join(unexpected))
  return t('the_required_resource_was_found', 'The required resource was found.') if not missing else t('target_resource_not_found_2', 'Target resource not found: {join}', join=', '.join(missing))
 m=r['metrics'];parts=[]
 if m.get('declared_truncation_hits'):parts.append(t('the_provider_declares_that_the_body_was', 'The provider declares that the body was truncated'))
 if not m.get('length_pass'):parts.append(t('body_too_short', 'Body too short'))
 missing=[x['id'] for x in m.get('fact_results',[]) if not x['pass']]
 if missing:parts.append((t('entry_fields_not_fully_matched', 'Entry fields not fully matched:') if any(x.get('match_scope')=='entry' for x in m.get('fact_results',[])) else t('missing_checks', 'Missing checks:'))+', '.join(missing))
 for x in m.get('pattern_results',[]):
  if not x['pass']:parts.append(t('only_items_required', '{id} only {count} items, {minimum} required', id=x['id'], count=x['count'], minimum=x['minimum']))
 return '；'.join(parts) or t('required_body_text_checks_passed_full_text', 'Required body-text checks passed; full-text coverage is reported separately.')

def stable_checks(m):
 checks=[]
 for x in m.get('stable_anchor_results',[]):
  checks.append(('',cell(esc(x['id']))+cell(esc(' / '.join(x.get('alternatives',[]))))+cell(t('pass_2', 'Pass') if x['pass'] else t('fail_2', 'Fail'))+cell(esc(' / '.join(x.get('matched',[]))))))
 for i,x in enumerate(m.get('stable_page_pattern_results',[]),1):
  checks.append(('',cell(t('page_pattern', 'Page pattern {i}', i=i))+cell(esc(x['pattern']))+cell(t('pass_2', 'Pass') if x['pass'] else t('fail_2', 'Fail'))+cell('—')))
 content=table([t('stable_content_checks', 'Stable-content checks'),t('requirement_2', 'Requirement'),t('verdict', 'Verdict'),t('observed_match', 'Observed match')],checks) if checks else ('<p>' + t('no_stable_text_checks_are_registered_for', 'No stable text checks are registered for this task, so no claim is made that its text content was validated.') + '</p>')
 records=[('',cell(esc(json.dumps(x.get('fields',{}),ensure_ascii=False)))+cell(t('pass_2', 'Pass') if x.get('valid') else t('fail_2', 'Fail'))+cell(esc('；'.join(x.get('errors',[])) or '—'))) for x in m.get('stable_records',[])]
 record_table=table([t('record_field', 'Record field'),t('structure_verdict', 'Structure verdict'),t('reason', 'Reason')],records)
 if len(records)>5:record_table=f'<div class="record-scroll" data-scroll-after="5">{record_table}</div>'
 return content+record_table+(('<p>' + t('failure_reason', 'Failure reason:') + f'{esc(stable_failure_summary(m))}。</p>') if m.get('stable_task_errors') else '')

def family_participants(records,cases):
 """Use the declared family population, retaining saved-matrix participants."""
 plan=render_context().coverage_plan or {}
 names={r['provider'] for r in records if r['case_id'] in cases}
 for c in cases.values():names.update(plan.get(c.get('track','web'),[]))
 if render_context().suites:names.update(render_context().labels)
 return names


def ranking_readiness(records,cases,*,participants=None):
 """Require a complete shared task population and one recorded scorer."""
 groups={name:[] for name in (participants if participants is not None else family_participants(records,cases)|set(render_context().labels))}
 for r in records:
  if r['case_id'] in cases:groups[r['provider']].append(r)
 populations=[Counter(r['case_id'] for r in rs) for rs in groups.values()]
 complete=bool(cases) and bool(groups) and all(p==Counter({case_id:1 for case_id in cases}) for p in populations) and all(
  {r['case_id'] for r in rs}==set(cases)
  and all(measured(r,cases[r['case_id']]) and r.get('report_invocation_status') != 'adapter_rejected' for r in rs)
  for rs in groups.values())
 identities=[r['metrics'].get('scoring_source_sha256') for rs in groups.values() for r in rs]
 same_scorer=bool(identities) and all(isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) for value in identities) and len(set(identities))==1
 notes=[]
 if not complete:notes.append(model_text(*pair('report_results_do_not_cover_the_same_complete_task_1be98a', 'Results do not cover the same complete task population; scores are shown without ranks.')))
 if not same_scorer and not render_context().descriptive_ranks:notes.append(model_text(*pair('report_scorer_identities_differ_or_are_missing_scores_are_ceb67f', 'Scorer identities differ or are missing; scores are shown without ranks.')))
 return complete and (same_scorer or render_context().descriptive_ranks), ''.join('<p class="callout">'+n+'</p>' for n in notes)


def shared_ranks(scores):
 """Competition ranks preserve exact ties independently of display ordering."""
 return [1+sum(other>score for other in scores) for score in scores]


def ranking(records,cases,track,base='',id=None,compact=False,audit=None,*,diagnostic=False):
 if render_context().suites:return saved_suite_ranking(records,cases,track,base=base,id=id,diagnostic=diagnostic)
 records,cases=comparison_population(records,cases)
 title_scope=track=='challenge' and any('title_quality' in r for r in records if report_family(cases[r['case_id']])==track)
 selected={k:c for k,c in cases.items() if report_family(c)==track}
 text_track=track in {'web','dynamic','document','challenge','risk_control'} and any(c.get('task_kind','body') in {'body','structure'} for c in selected.values())
 groups=defaultdict(list)
 for r in records:
  if report_family(cases[r['case_id']])==track:groups[r['provider']].append(r)
 def sort_key(item):
  name,rs=item
  n=sum(measured(r,cases[r['case_id']]) for r in rs)
  return (-sum(succeeded(r,cases[r['case_id']]) for r in rs)/n if n else 1,name)
 rows=[];text_scopes={}
 for name,rs in sorted(groups.items(),key=sort_key):
  n=sum(measured(r,cases[r['case_id']]) for r in rs);ok=sum(bool(r['ok']) for r in rs);passed=sum(succeeded(r,cases[r['case_id']]) for r in rs)
  body=[r for r in rs if cases[r['case_id']].get('task_kind','body')=='body'];bodypass=sum(succeeded(r,cases[r['case_id']]) for r in body)
  full=[r for r in rs if comparable_body_reference(cases[r['case_id']]) and r['metrics'].get('word_f1') is not None]
  def avg(key,full=full):
   per_case=defaultdict(list)
   for r in full:
    if r['metrics'].get(key) is not None:per_case[r['case_id']].append(r['metrics'][key] if r['ok'] else 0)
   return statistics.mean(statistics.mean(values) for values in per_case.values()) if per_case else None
  times=[r['elapsed_ms'] for r in rs if succeeded(r,cases[r['case_id']])]
  values=cell(link(base+f'providers/{name}.html',provider_label(name),browser_method_label(name,language='en')))+cell(rate(ok,len(rs)),ok/len(rs))
  if text_track:
   values+=cell(rate(passed,n),passed/n if n else -1)
   values+=''.join(cell(pct(avg(k)),avg(k) if full else -1) for k in ['word_recall','word_precision','word_f1'])
   per_case=defaultdict(list)
   for r in rs:
    m=r['metrics']; checks=m.get('stable_anchor_results',[]) if cases[r['case_id']].get('acceptance_contract') else m.get('fact_results',[])
    if checks:per_case[r['case_id']].append(sum(bool(x.get('pass')) for x in checks)/len(checks) if r['ok'] else 0)
   coverage=statistics.mean(statistics.mean(v) for v in per_case.values()) if per_case else None
   text_scopes[name]=frozenset(per_case)
   values+=cell(pct(coverage),coverage if coverage is not None else -1)
  else:
   resources=[r for r in rs if cases[r['case_id']].get('task_kind')=='resource'];files=[r for r in rs if cases[r['case_id']].get('task_kind')=='file']
   values+=cell(rate(passed,n),passed/n if n else -1)
   for subset,total in [(resources,sum(succeeded(r,cases[r['case_id']]) for r in resources)),(files,sum(succeeded(r,cases[r['case_id']]) for r in files)),(body,bodypass)]:
    subset=[r for r in subset if measured(r,cases[r['case_id']])]
    values+=cell(rate(total,len(subset)),total/len(subset) if subset else -1)
  values+=cell(ms(statistics.mean(times)) if times else '—',statistics.mean(times) if times else -1)+cell(ms(percentile(times,.5)),percentile(times,.5) or -1)+cell(ms(percentile(times,.95)),percentile(times,.95) or -1)
  values+=cell(f'{n}/{len(selected)}',n/len(selected) if selected else -1)
  if audit:values+=review_cell(audit,rs,base)
  rows.append(('',values))
 heads=[t('method', 'Method'),t('request_success_rate', 'Request success rate')]
 if text_track:
  heads+=[t('task_pass_rate', 'Task pass rate'),t('recall', 'Recall'),t('precision', 'Precision'),'F1',t('required_text_coverage', 'Required-text coverage')]
 else:heads+=[t('task_pass_rate', 'Task pass rate'),t('resource_match_rate', 'Resource match rate'),t('file_delivery_rate', 'File delivery rate'),t('metadata_pass_rate', 'Metadata pass rate')]
 heads+=[t('mean', 'Mean'),'P50','P95',t('judgement_coverage', 'Judgement coverage')]
 if audit:heads+=[t('review_coverage', 'Review coverage')]
 note=t('the_request_success_rate_uses_all_calls', 'The request success rate uses all calls as its denominator; the task pass rate uses decidable results, and missing acceptance evidence counts as neither pass nor failure.')
 if text_track:
  covered=sum(comparable_body_reference(c) for c in selected.values())
  note=f'完整参考覆盖 {covered}/{len(selected)}；缺少参考只影响全文指标，不影响任务结果。有参考时，请求失败计 0；请求成功但任务未通过仍按实际正文评分。'
  identity_rules=list(dict.fromkeys(list_rule_description(c) for c in selected.values()
   if c.get('task_kind')=='structure' and not c.get('acceptance_contract')))
  if identity_rules:note+=' '+esc(' '.join(identity_rules))
  note+=(' ' + t('required_text_coverage_measures_only_registered_required', 'Required-text coverage measures only registered required-text groups; it is not full-text recall. Tasks without text evidence are excluded from that average.'))
  if text_scopes and len(set(text_scopes.values()))==1:note+=(' ' + t('required_text_coverage_tasks_for_every_method', 'Required-text coverage: {count}/{count2} tasks for every method.', count=len(next(iter(text_scopes.values()))), count2=len(selected)))
  elif text_scopes:note+=(' ' + t('text_check_coverage_differs_by_method_see', 'Text-check coverage differs by method; see the coverage below.'))
  if track=='challenge':note+=(' ' + t('lists_stable_field_labels_and_descriptions_are', 'Lists, stable field labels, and descriptions are judged together; title-text quality is only a supporting diagnostic below.'))
  if track=='dynamic':note+=(' ' + t('legacy_snapshot_text_scores_containing_excluded_volatile', "Legacy snapshot-text scores containing excluded volatile fields remain in attempt details and are excluded from this table's full-text averages."))
 else:note=t('direct_file_tasks_require_byte_validation_any', 'Direct-file tasks require byte validation. Any missing evidence makes the report unreliable. Resource, file, and metadata columns are calculated on their corresponding task subsets.')
 note+=(' ' + t('judgement_coverage_is_judged_calls_divided_by', "Judgement coverage is judged calls divided by planned calls, not a pass rate. If any method is incomplete, the entire report is incomplete and publication is blocked.Mean, P50, and P95 include only each method's own passing calls and are not a matched-task speed ranking."))
 unmeasured=sorted({r['case_id'] for rs in groups.values() for r in rs if not measured(r,cases[r['case_id']])})
 if unmeasured:
  note+=(' ' + t('tasks_missing_acceptance_evidence', 'Tasks missing acceptance evidence:') + f"{'、'.join((link(base + 'datasets/' + key + '.html', cases[key]['name']) for key in unmeasured))}" + t('counts_as_neither_pass_nor_failure_the', '. Counts as neither pass nor failure; the pass-rate denominator includes only decidable results.'))
 if track=='dynamic':
  missing=sorted({r['provider'] for r in records}-set(groups))
  note+=(' ' + t('each_family_is_compared_within_its_own', 'Each family is compared within its own task scope and is not combined into a cross-family weighted score.'))
  if missing:note+=(' ' + t('unmeasured_methods_not_counted_as_failures', 'Unmeasured methods (not counted as failures):') + f"{esc('、'.join(missing))}。")
 directions={i:('quality_'+str(i),'max') for i in range(1,len(heads)) if heads[i] not in {t('mean', 'Mean'),'P50','P95',t('judgement_coverage', 'Judgement coverage'),t('review_coverage', 'Review coverage')}}
 directions.update({i:('latency_'+str(i),'min') for i,h in enumerate(heads) if h in {t('mean', 'Mean'),'P50','P95'}})
 eligible,rank_note=ranking_readiness(records,selected,participants=family_participants(records,selected))
 ranked=not diagnostic and eligible
 if ranked:rows=comparative_rows(rows,directions)
 scores=[-sort_key(item)[0] for item in sorted(groups.items(),key=sort_key)]
 result=rank_note+table(heads,rows,id or 'ranking-'+track,True,'ranking',metrics=[] if compact else None,include_index=ranked,rank_values=shared_ranks(scores) if ranked else None,default_sort=(2+int(ranked),'desc'))
 if text_scopes and len(set(text_scopes.values()))>1:
  result=('<details><summary>' + t('text_check_coverage', 'Text-check coverage') + f"""</summary><ul>{''.join((f"<li>{esc(name)}：{len(scope)}/{len(selected)} 例；{esc('、'.join((selected[k]['name'] for k in sorted(scope))) or t('no_text_check_record', 'No text-check record'))}</li>" for name, scope in sorted(text_scopes.items())))}</ul></details>{result}""")
 if compact:
  result=('<details class="ranking-metric-help"><summary>' + t('metric_definitions_pass_rate_quality_and_latency', 'Metric definitions · pass rate, quality, and latency') + f'</summary>{metric_legend(heads)}</details>{result}')
 if title_scope:result+=title_diagnostics(groups,selected,base,id or 'ranking-'+track)
 result+=matched_latency(groups,cases,base)
 return task_pass_guide(track,base,selected)+'<p class="source-note">'+note+'</p>'+result

def matched_latency(groups,cases,base):
 scopes=[{r['case_id'] for r in rows if succeeded(r,cases[r['case_id']])} for rows in groups.values()]
 common=set.intersection(*scopes) if scopes else set()
 note=t('the_evaluated_methods_jointly_pass_tasks_latency', 'The evaluated methods jointly pass {count} tasks. Latency is compared only on this identical subset; it does not represent the cost of failed tasks or a production SLA.', count=len(common))
 rows=[]
 if common:
  for provider, records in sorted(groups.items()):
   bycase=defaultdict(list)
   for r in records:
    if r['case_id'] in common and succeeded(r,cases[r['case_id']]):bycase[r['case_id']].append(r['elapsed_ms'])
   times=[statistics.mean(v) for v in bycase.values()]
   rows.append(('',cell(esc(provider))+cell(ms(statistics.mean(times)))+cell(ms(percentile(times,.5)))))
 links='、'.join(link(base+'datasets/'+key+'.html',cases[key].get('name',key)) for key in sorted(common))
 return ('<details><summary>' + t('matched_task_latency_tasks_passed_by_every', 'Matched-task latency · tasks passed by every method') + f"</summary><p>{note}</p>{(table([t('method', 'Method'), t('mean_on_shared_tasks', 'Mean on shared tasks'), t('p50_on_shared_tasks', 'P50 on shared tasks')], rows, metrics=[]) + '<p>' + links + '</p>' if rows else '')}</details>")

def overview_all_call_latency(records,cases):
 excluded={r['case_id'] for r in records if r['metrics'].get('evaluation_excluded')}
 records=[r for r in records if r['case_id'] not in excluded]
 cases={key:case for key,case in cases.items() if key not in excluded}
 names=sorted({r['provider'] for r in records})
 keys={(r['provider'],r['case_id']) for r in records}
 if len(keys)!=len(records) or any((name,cid) not in keys for name in names for cid in cases):
  raise ValueError('All-call latency requires the full common task matrix')
 rows=[]
 for name in names:
  times=[r['elapsed_ms'] for r in records if r['provider']==name]
  rows.append(('',cell(link('providers/'+name+'.html',provider_label(name),browser_method_label(name,language='en')))+cell(str(len(times)))+
               cell(ms(statistics.mean(times)))+cell(ms(percentile(times,.5)))+cell(ms(percentile(times,.95)))))
 return ('<p>'+model_text(*pair('each_method_uses_the_same_tasks_including', 'Each method uses the same {count} tasks, including passes, failures, errors and timeouts. Latency covers one call from input to output, excluding cooldown intervals between calls. Each method executes cases sequentially; method concurrency and limits are documented in the call-chain audit. Local/remote execution, network conditions and native product limits still affect latency.', count=len(cases)))+'</p>'+
         table([t('method', 'Method'),t('calls', 'Calls'),t('all_call_mean', 'All-call mean'),t('all_call_p50', 'All-call P50'),t('all_call_p95', 'All-call P95')],rows,'all-call-latency-table',metrics=[]))


def result_ledger_section(out, records, prefix=''):
 path=out/'local-evidence/result-ledger.json'
 if not path.is_file():return ''
 ledger=validate_ledger(json.loads(path.read_text(encoding='utf-8')),records,source=out)
 rows=[('',cell(esc(row['id']))+cell(str(row['before_passes']))+cell(str(row['added_passes']))+
           cell(str(row['removed_passes']))+cell(str(row['totals']['pass']))+
           cell(str(row['totals']['excluded']))) for row in ledger['rounds']]
 return section(t('scoring_revision_ledger', 'Scoring revision ledger'),('<p>' + t('additions_removals_and_final_passes_are_derived', 'Additions, removals and final passes are derived from individual result states. Excluded results retain their original calls and stay outside comparison denominators.') + f"</p>{table([t('stage', 'Stage'), t('passes_before', 'Passes before'), t('passes_added', 'Passes added'), t('passes_removed', 'Passes removed'), t('passes_after', 'Passes after'), t('excluded_from_comparison', 'Excluded from comparison')], rows, metrics=[])}{link(prefix + 'local-evidence/result-ledger.json', t('download_per_result_revision_ledger', 'Download per-result revision ledger'))}"),'result-ledger')

def overview_matched_latency(records,cases):
 names=sorted(set(render_context().labels) | {r['provider'] for r in records})
 keys={(r['provider'],r['case_id']) for r in records}
 if not names or len(keys)!=len(records) or any((name,cid) not in keys for name in names for cid in cases):
  return ('<p>' + t('the_call_matrix_is_incomplete_so_latency', 'The call matrix is incomplete, so latency on tasks passed by every method is not yet summarized.') + '</p>')
 common=set.intersection(*({r['case_id'] for r in records if r['provider']==name and succeeded(r,cases[r['case_id']])} for name in names))
 if not common:return ('<p>' + t('no_task_is_yet_passed_by_every', 'No task is yet passed by every method, so shared-task latency is not calculated.') + '</p>')
 population=_article_speed_population(cases,records,names,execution_scope='Main-evaluation historical calls across batches; all registered methods and configurations, not a same-window controlled experiment.')
 metrics=['mean_ms','p50_ms','p95_ms'];rows=[]
 for row in population['rows']:
  values=cell(link('providers/'+row['provider']+'.html',provider_label(row['provider']),browser_method_label(row['provider'],language='en')))
  for key in metrics:
   value=ms(row[key]);values+=cell(value,row[key])
  rows.append(('',values))
 count=population['common_pass_count']
 note=(('<p>' + t('all_tested_configurations_pass', 'All {count} tested configurations pass', count=len(names)) + f' <strong>{count}/{len(cases)}</strong> ' + t('tasks_in_common_every_row_below_uses', 'tasks in common. Every row below uses this same task set.') + '</p><p class="source-note">' + t('this_includes_all_methods_and_versions_registered', 'This includes all methods and versions registered in the report. Statistics use recorded primary calls and disclosed latency corrections across dates and batches. These values are not a controlled speed comparison within one time window and exclude failed-task latency.') + '</p>'))
 if render_context().external_scope_notes:note=note.split('<p class="source-note">',1)[0]
 dates=[]
 for row in population['rows']:
  observed=row['recorded_start_dates']
  if observed:
   dates.append(model_text(provider_label(row['provider']),browser_method_label(row['provider'],language="en"))+model_text('：'+'、'.join(f'{date}（{number} 题）' for date,number in observed.items()),': '+', '.join(f'{date} ({number} tasks)' for date,number in observed.items())))
 links='、'.join(link('datasets/'+cid+'.html',cases[cid].get('name',cid)) for cid in population['common_pass_cases'])
 content=((f"{note}{table([t('method', 'Method'), t('mean_on_shared_tasks', 'Mean on shared tasks'), t('p50_on_shared_tasks', 'P50 on shared tasks'), t('p95_on_shared_tasks', 'P95 on shared tasks')], rows, 'overall-matched-latency-table', metrics=[], include_index=False)}<details><summary>" + t('shared_tasks_and_call_dates', 'Shared tasks and call dates') + f"</summary><p>{links}</p><p>{'<br>'.join(dates)}</p></details>"))
 return ('<details class="ranking-metric-help"><summary>' + t('view_shared_pass_latency_on_demand', 'View shared-pass latency on demand') + f'</summary>{content}</details>')

def overview_resources(out):
 path=out/'resource-cohort/resource-comparison.json'
 if not path.is_file():
  return ('<p>' + t('the_current_evidence_has_no_independent_client_2', 'The current evidence has no independent client-side resource measurement; CPU and memory are not inferred from latency or process size.') + '</p>')
 summary=json.loads(path.read_text(encoding='utf-8'))
 rendered,_=render_resource_comparison(summary,summary['providers'])
 return rendered

def title_diagnostics(groups,selected,base,identifier):
 rows=[]
 for name,rs in sorted(groups.items()):
  measured=[r for r in rs if r.get('title_quality',{}).get('f1') is not None]
  values=cell(link(base+f'providers/{name}.html',name))
  for key in ['recall','precision','f1']:
   per_case=defaultdict(list)
   for r in measured:per_case[r['case_id']].append(r['title_quality'][key])
   value=statistics.mean(statistics.mean(v) for v in per_case.values()) if per_case else None
   values+=cell(pct(value),value if value is not None else -1)
  count=sum(r.get('title_quality',{}).get('scored_titles',0) for r in rs)
  total=sum(r.get('title_quality',{}).get('returned_titles',0) for r in rs)
  coverage=t('tasks_3', '{count}/{total} · {count2}/{count3} tasks', count=count, total=total, count2=len({r['case_id'] for r in measured}), count3=len(selected))
  if not measured:coverage+=t('no_scorable_titles', ' (no scorable titles)')
  rows.append(('',values+cell(coverage)))
 returned=sum(r.get('title_quality',{}).get('returned_titles',0) for rs in groups.values() for r in rs)
 scored=sum(r.get('title_quality',{}).get('scored_titles',0) for rs in groups.values() for r in rs)
 cases_scored=len({r['case_id'] for rs in groups.values() for r in rs if r.get('title_quality',{}).get('f1') is not None})
 steps=(('<ol class="quality-steps"><li><b>' + t('identify_returned_records', 'Identify returned records') + '</b><span>' + t('read_titles_and_detail_urls_from_structured', 'Read titles and detail URLs from structured results: {returned} returned titles.', returned=returned) + '</span></li><li><b>' + t('join_references_by_detail_url', 'Join references by detail URL') + '</b><span>' + t('titles_join_to_independent_name_evidence_titles', '{scored}/{returned} titles join to independent name evidence; titles without matching evidence are not scored.', scored=scored, returned=returned) + '</span></li><li><b>' + t('compare_title_text', 'Compare title text') + '</b><span>' + t('calculate_title_recall_precision_and_f1_separately', 'Calculate title recall, precision, and F1 separately; formatting marks are not title content.') + '</span></li><li><b>' + t('aggregate_comparable_results', 'Aggregate comparable results') + '</b><span>' + t('coverage_tasks_this_is_a_title_diagnostic', 'Coverage: {cases_scored}/{count} tasks. This is a title diagnostic and does not determine task acceptance.', cases_scored=cases_scored, count=len(selected)) + '</span></li></ol>'))
 return (('<details class="ranking-metric-help"><summary>' + t('title_text_quality_method_and_results', 'Title-text quality · method and results') + '</summary><p>' + t('this_diagnostic_asks_whether_titles_that_were', 'This diagnostic asks whether titles that were returned are complete and clean. It does not measure list recall, freshness, or whole-page body quality.') + f"</p>{steps}{table([t('method', 'Method'), t('title_recall', 'Title recall'), t('title_precision', 'Title precision'), t('title_f1', 'Title F1'), t('title_reference_coverage_scorable_returned', 'Title reference coverage (scorable / returned)')], rows, identifier + '-titles', True, metrics=[])}</details>"))

def input_suite_comparison(dataset, cases, records):
 """Compare the selected input groups declared by the frozen source inventory."""
 sources=dataset.get('comparison',{}).get('input_sources',[])
 if not sources or not any('case_ids' in source for source in sources):return ''
 labels=dataset.get('comparison',{}).get('report_suite_labels',{})
 selected=set(cases);seen=set();groups=[]
 for index,source in enumerate(sources,1):
  ids=set(source.get('case_ids',[])) & selected
  if seen & ids:raise ValueError('Input suite comparison contains overlapping tasks')
  seen |= ids
  if ids:
   label=labels.get(source.get('path'),{})
   title=model_text(label.get('zh',t('input_group', 'Input group {index}', index=index)),label.get('en',f'Input group {index}'))
   groups.append((ids,title))
 if seen != selected:raise ValueError('Input suite comparison omits selected tasks')
 providers=sorted({row['provider'] for row in records});rows=[]
 for provider in providers:
  values=cell(link('providers/'+provider+'.html',provider_label(provider)))
  for ids,_ in groups:
   subset=[row for row in records if row['provider']==provider and row['case_id'] in ids]
   if {row['case_id'] for row in subset} != ids or len(subset)!=len(ids):
    raise ValueError('Input suite comparison requires one result per method and task')
   available=[row for row in subset if measured(row,cases[row['case_id']])]
   passed=sum(succeeded(row,cases[row['case_id']]) for row in available)
   value=f'{passed}/{len(available)} · '+rate(passed,len(available))
   if len(available)!=len(ids):value+=' · '+model_text(*pair('unscored_tasks', '{value} unscored tasks', value=len(ids) - len(available)))
   values+=cell(value,passed/len(available) if available else -1)
  rows.append(('',values))
 directions={index+1:(f'input-suite-{index+1}','max') for index in range(len(groups))}
 rows=comparative_rows(rows,directions)
 return section(model_text(*pair('comparison_by_input_group', 'Comparison by input group')),
  '<p>'+model_text(*pair('groups_follow_the_frozen_input_inventory_denominators', 'Groups follow the frozen input inventory; denominators count selected, scored tasks. These samples test particular scenarios and do not represent randomly sampled webpages.'))+'</p>'+
  table([model_text(*pair('method', 'Method'))]+[title+f' · n={len(ids)}' for ids,title in groups],rows,'input-suite-comparison',metrics=[]),
  'input-suite-comparison-section')


def logical_execution_cost_ms(record):
 """Return the saved complete-call cost, never a partial retry estimate."""
 continuation=record.get('report_retry_continuation')
 if continuation is not None:return continuation.get('execution_cost_ms')
 metadata=record.get('provider_metadata') or {}
 retry=metadata.get('retry')
 if retry is not None:return retry.get('execution_cost_ms')
 if metadata.get('retry_pending') and 'call_sequence_elapsed_ms' not in metadata:return None
 if 'call_sequence_elapsed_ms' in metadata:return metadata['call_sequence_elapsed_ms']
 return record['elapsed_ms']+metadata.get('retry_cleanup_wait_ms',0)+metadata.get('retry_throttle_wait_ms',0)


def successful_final_call_latency(records,cases,*,prefix=""):
 """Show conditional task-passing latency separately from all-result costs."""
 groups=defaultdict(list)
 for record in records:groups[record['provider']].append(record)
 rows=[]
 for name,group in sorted(groups.items()):
  times=[r['elapsed_ms'] for r in group if succeeded(r,cases[r['case_id']])]
  values=cell(link(f'{prefix}providers/{name}.html',provider_label(name)))+cell(f'{len(times)}/{len(group)}')
  for value in (statistics.mean(times) if times else None,percentile(times,.5),percentile(times,.95)):
   values+=cell(ms(value),value)
  rows.append(('',values))
 rows=comparative_rows(rows,{i:('successful_final_latency_'+str(i),'min') for i in (2,3,4)})
 return ('<h3>'+model_text(*pair('report_final_call_latency_of_task_passing_results_c5387b', 'Final-call latency of task-passing results'))+'</h3><p>'+
  ('' if render_context().external_scope_notes else model_text(*pair('report_only_each_method_s_task_passing_results_are_a81e48', 'Only each method’s task-passing results are included; failures cannot win latency highlights. Populations differ, so highlights identify column minima, not a same-task speed ranking.')))+'</p>'+
  table([model_text(*pair('report_method_59e05c', 'Method')),model_text(*pair('report_passed_saved_ecdf33', 'Passed / saved')),model_text(*pair('report_mean_456d46', 'Mean')),'P50','P95'],rows,'successful-final-call-latency',metrics=[]))


def token_usage_comparison(records):
 from harness.web_fetch.report_usage import token_usage
 usage=token_usage(records)
 if not usage:return ''
 fields=('total_tokens','uncached_input_tokens','cached_input_tokens','output_tokens')
 rows=[]
 for item in usage:
  values=cell(link('providers/'+item['provider']+'.html',provider_label(item['provider'])))+cell(f"{item['observed']}/{item['count']}")
  for key in fields:
   value=item[key]
   values+=cell('—' if value is None else f'{value:,}',value)
  rows.append((' data-provider="'+esc(item['provider'])+'"',values))
 comparable=len({item['count'] for item in usage})==1 and all(item['observed']==item['count'] for item in usage)
 rows=comparative_rows(rows,{2:('total_tokens','min')} if comparable else {})
 heads=[t('method','Method'),t('token_usage_coverage','Recorded / tasks'),t('token_usage_total','Total tokens'),t('token_usage_uncached','Uncached input'),t('token_usage_cached','Cached input'),t('token_usage_output','Output')]
 return section(t('token_usage_title','Token usage'),'<p>'+t('token_usage_definition','Total = uncached input + cached input + output. Each row sums the recorded task subagents; cached input is included once. Figures are token counts.')+'</p>'+('<p class="source-note">'+t('token_usage_minimum','Green bold marks the lower total for the same number of tasks.')+'</p>' if comparable else '')+table(heads,rows,'token-usage',True,metrics=[],include_index=False,default_sort=(2,'asc')),'token-consumption')


def all_final_call_latency(records):
 groups=defaultdict(list)
 for record in records:
  if record.get('report_invocation_status') != 'adapter_rejected':
   groups[record['provider']].append(record['elapsed_ms'])
 rows=[]
 for name,times in sorted(groups.items()):
  values=cell(link(f'../providers/{name}.html',provider_label(name)))+cell(str(len(times)))
  for value in (statistics.mean(times),percentile(times,.5),percentile(times,.95)):
   values+=cell(ms(value),value)
  rows.append(('',values))
 return section(saved_matrix_text('all_final_call_timing_title'),
  '<p>'+saved_matrix_text('final_call_timing_help')+'</p>' +
  table([t('method', 'Method'),saved_matrix_text('final_call_count'),*saved_matrix_text('final_call_timing_headers')],
   rows,'all-final-call-latency',metrics=[]),'all-final-call-timing')


def overall_ranking(records,cases,audit=None,*,diagnostic=False,call_budget_seconds=None,combined=True,passing_latency=False):
 records,cases=comparison_population(records,cases)
 has_unknown=any(not measured(r,cases[r["case_id"]]) for r in records)
 has_not_called=any(r.get('report_invocation_status') == 'adapter_rejected' for r in records)
 groups=defaultdict(list)
 for r in records:groups[r['provider']].append(r)
 if render_context().suites:
  for provider in render_context().labels:groups.setdefault(provider,[])
 rows=[]
 for name,rs in groups.items():
  n=sum(measured(r,cases[r['case_id']]) for r in rs)
  passed=sum(succeeded(r,cases[r['case_id']]) for r in rs)
  called=[r for r in rs if r.get('report_invocation_status') != 'adapter_rejected']
  request_success=sum(bool(r['ok']) for r in called)
  values=cell(link(f'providers/{name}.html',provider_label(name),browser_method_label(name,language='en')))+cell(rate(request_success,len(called)),request_success/len(called) if called else -1)
  if combined:values+=cell(rate(passed,n)+(f'<br><small>{passed}/{n}</small>' if has_unknown else ''),passed/n if n else -1)
  if render_context().suites and len(called) < len(cases):
   values=values.replace('</a>', '</a><br><small>'+saved_matrix_text('incomplete')+'</small>',1)
  if has_unknown:values+=cell(str(len(rs)-n))
  for track,_ in active_tracks(cases):
   subset=[r for r in rs if report_family(cases[r['case_id']])==track and measured(r,cases[r['case_id']])]
   values+=cell(f'{sum(succeeded(r,cases[r["case_id"]]) for r in subset)}/{len(subset)}',sum(succeeded(r,cases[r['case_id']]) for r in subset)/len(subset) if subset else -1)
  times=[r['elapsed_ms'] for r in called if not passing_latency or succeeded(r,cases[r['case_id']])]
  mean_time=statistics.mean(times) if times else None
  values+=cell(ms(mean_time),mean_time)
  values+=cell(ms(percentile(times,.5)),percentile(times,.5))
  values+=cell(ms(percentile(times,.95)),percentile(times,.95))
  if audit:values+=review_cell(audit,rs)
  rows.append((passed/n if n else -1,name,('',values)))
 rows=[row for _,_,row in sorted(rows,key=lambda x:(-x[0],x[1]) if combined else (x[1],))]
 tracks=active_tracks(cases)
 timing_heads=saved_matrix_text('passing_final_call_headers' if passing_latency else 'final_call_timing_headers')
 heads=[(t('report_method_22b9f0', 'Method')),(t('report_request_success_rate_413aeb', 'Request success rate'))]+([(t('report_overall_task_pass_rate_71785d', 'Overall task pass rate'))] if combined else [])+([(t('report_undecidable_including_uncalled_273298', 'Undecidable (including uncalled)')) if has_not_called else (t('report_undecidable_4c6165', 'Undecidable'))] if has_unknown else [])+[esc(label) if render_context().suites else label for _,label in tracks]+timing_heads
 if audit:heads+=[(t('report_review_coverage_9466e9', 'Review coverage'))]
 case_count=len(cases);track_count=len(tracks);track_label=track_count_label(track_count)
 if case_count==150 and track_count==6:
  intro=('<p class="decision-lead"><b>' + t('overall_ranking_basis', 'Overall-ranking basis:') + '</b>' + t('every_method_runs_the_same_150_tasks', 'Every method runs the same 150 tasks. Each task contributes one pass or fail and the results are summed directly; latency is shown separately and does not affect pass-rate ranking. The overall ranking shows delivery coverage, while specific capabilities belong in the six family rankings.') + '</p>')
 else:
  intro=(('<p class="decision-lead"><b>' + t('overall_ranking_basis', 'Overall-ranking basis:') + '</b>' + t('every_method_runs_the_current_dataset_s', "Every method runs the current dataset's {case_count} planned tasks. Each task contributes one pass or fail and the results are summed directly; latency is shown separately and does not affect pass-rate ranking. The overall ranking shows delivery coverage, while specific capabilities belong in the {track_label} family rankings.", case_count=case_count, track_label=track_label) + '</p>'))
 if has_unknown:
  intro='<p class="decision-lead"><b>'+t('overall_ranking_basis', 'Overall-ranking basis:')+'</b>'+model_text(*pair('report_each_method_calls_the_same_value_tasks_pass_9cda7e', 'Each method calls the same {value0} tasks. Pass rate is passed / decidable results; undecidable results are listed separately and count as neither passes nor failures. Decidable denominators differ, so these rates describe only judged results, not a definite pass rate for all tasks. Latency does not affect pass-rate ordering.', value0=case_count))+'</p>'
 if any(len(rs)!=len(cases) for rs in groups.values()):
  intro='<p class="decision-lead"><b>'+t('overall_ranking_basis', 'Overall-ranking basis:')+'</b>'+model_text(*pair('report_some_results_are_missing_or_tasks_were_not_c085cf', 'Some results are missing or tasks were not called. Rates use each method’s saved decidable results; missing items are not failures. See completion details for denominators, calls and missing counts. Adapter rejections retain their original failure scores without implying a product call.'))+'</p>'
 if has_not_called:
  intro='<p class="decision-lead">'+saved_matrix_text('suite_denominator_note')+'</p>'
 help=((f"""<details class="ranking-metric-help"><summary>{t('report_overall_ranking_metrics_977f67', 'Overall-ranking metrics')}</summary><div class="metric-help"><dl><div><dt>{t('report_overall_task_pass_rate_71785d', 'Overall task pass rate')}</dt><dd>{case_count}{t('report_planned_tasks_passed_decidable_each_task_has_equal_weig_513a18', ' planned tasks: passed / decidable; each task has equal weight and undecidable results are excluded from the denominator.')}</dd></div><div><dt>{track_label}{t('report_families_passed_ae37c3', ' families passed')}</dt><dd>{t('report_passes_divided_by_tasks_in_each_family_showing_which_ca_c7ed30', 'Passes divided by tasks in each family, showing which capabilities contribute to the overall result.')}</dd></div>""")
       + '<div><dt>'+saved_matrix_text('passing_final_call_title' if passing_latency else 'final_call_timing_title')+'</dt><dd>'+saved_matrix_text('passing_final_call_help' if passing_latency else 'final_call_timing_help')+'</dd></div>' +
       ('<div>' + '<dt>' + t('report_review_coverage_9466e9', 'Review coverage') + '</dt>' + '<dd>' + t('report_coverage_of_the_independent_blind_review_its_protocol_a_329b7a', 'Coverage of recorded result reviews, their protocols, and unresolved count.') + '</dd>' + '</div>' + '</dl>' + '</div>' + '</details>'))
 offset=2+int(combined)
 directions={i:('acceptance_'+str(i),'max') for i in range(1,offset+len(tracks)+int(has_unknown))}
 if has_unknown:directions.pop(offset,None)
 # Timing columns compare recorded consumption over their declared population.
 directions.update({i:('final_call_latency_'+str(i),'min') for i in range(len(heads)-len(timing_heads)-int(bool(audit)),len(heads)-int(bool(audit)))})
 eligible,rank_note=ranking_readiness(records,cases)
 ranked=combined and not diagnostic and eligible
 if ranked:rows=comparative_rows(rows,directions)
 timing_note='<p>'+saved_matrix_text('final_call_timing_note')
 if passing_latency:
  timing_note+=' '+saved_matrix_text('passing_final_call_help')+' '+link('analysis/index.html#all-final-call-timing',saved_matrix_text('all_final_call_timing_title'))
 if call_budget_seconds is not None:
  timing_note+=model_text(*pair('report_each_physical_call_has_a_value_g_second_053250', 'Each physical call has a {value0:g}-second budget.', value0=call_budget_seconds))
 timing_note+='</p>'
 if diagnostic:
  intro+='<p>'+model_text(*pair('report_diagnostic_results_show_scores_without_ranks_d759cd', 'Diagnostic results show scores without ranks.'))+'</p>'
  help=help.replace((t('report_overall_ranking_metrics_977f67', 'Overall-ranking metrics')),(t('report_diagnostic_metric_definitions_9fb150', 'Diagnostic metric definitions')))
  if render_context().score_audit:
   help=help.replace((t('report_diagnostic_metric_definitions_9fb150', 'Diagnostic metric definitions')),(t('report_metric_definitions_eeb1ae', 'Metric definitions')))
 if not combined:
  intro='<p class="decision-lead">'+model_text(*pair('report_compare_each_frozen_task_group_separately_without_a_f6add0', 'Compare each frozen task group separately, without a combined pass-rate ranking. Cross-group cases retain their original identities; record counts are not unique-site counts.'))+'</p>'
  help=''
 scores=[sum(succeeded(r,cases[r['case_id']]) for r in rs) for _,rs in sorted(groups.items(),key=lambda item:(-sum(succeeded(r,cases[r['case_id']]) for r in item[1]),item[0]))]
 rendered=table(heads,rows,'ranking-overall',True,'ranking',metrics=[],include_index=ranked,rank_values=shared_ranks(scores) if ranked else None,default_sort=(2+int(ranked),'desc') if combined else (0,'asc'))
 return intro+rank_note+timing_note+help+('<p class="source-note">'+t('latency_column_minimum','Lower values in latency columns are green and bold.')+'</p>' if ranked else '')+rendered

def analysis_body(records,cases,headers=None,tracks=None):
 """Render every participant through the shared comparison and result controls."""
 names=sorted(set(render_context().labels) | {r['provider'] for r in records})
 bycase=defaultdict(list)
 for record in records:bycase[record['case_id']].append(record)
 tracks=active_tracks(cases) if tracks is None else tracks
 if headers is None:headers=[t('task_2', 'Task'),t('family', 'Family'),t('result', 'Result'),t('failure_class', 'Failure class'),t('elapsed_time', 'Elapsed time'),t('run_id', 'Run ID')]
 controls=filter_controls([('provider',t('method', 'Method'),[(n,provider_label(n)) for n in names]),('track',t('family', 'Family'),tracks),('result',t('result', 'Result'),[('pass',t('pass', 'Pass')),('fail',t('fail', 'Fail'))])]+failure_filters(records,cases))
 comparison=section(t('method_comparison', 'Method comparison'),('<p class="source-note">' + t('methods_are_compared_task_by_task_conclusions', 'Methods are compared task by task; conclusions never splice together different success samples. Pass and fail states use the primary program verdicts. Open a method to inspect its reference, output, and rationale.') + f'</p>{method_comparisons(cases, bycase, names)}'),'method-comparison')
 return hero(t('analysis', 'Analysis'),t('compare_methods_on_the_same_tasks_first', 'Compare methods on the same tasks first, then filter the complete execution record.'))+comparison+section(t('all_execution_records', 'All execution records'),controls+table([t('method', 'Method')]+headers,[(case_attrs(r,cases[r['case_id']]),cell(link('../providers/'+r['provider']+'.html',provider_label(r['provider']),browser_method_label(r['provider'],language='en')))+attempt_row(r,cases[r['case_id']])[1]) for r in records],'filtered-table',cls='all-method-results'))

def filter_controls(options,all_first=()):
 controls=''
 for key,label,choices in options:
  ordered=([('',t('all', 'All'))]+choices) if key in {'provider','track','category','failureStage','failureCategory',*all_first} else (choices+[('',t('all', 'All'))])
  controls+=f'<label>{label} <select data-filter="{key}" aria-label="{label}">'+''.join(f'<option value="{esc(v)}"'+(' selected' if key=='result' and v=='fail' else '')+(f' data-report-en="{esc(browser_method_label(v,language="en"))}"' if key=='provider' and v else '')+f'>{esc(t)}</option>' for v,t in ordered)+'</select></label>'
 return (f'<div class="filters">{controls}<input data-filter="query" aria-label="' + t('search', 'Search') + '" placeholder="' + t('search_cases_methods_or_reasons', 'Search cases, methods, or reasons') + '"><span id="count" role="status" aria-live="polite"></span></div>')

def failure_filters(records,cases):
 categories=sorted({failure_category(r,cases[r['case_id']]) for r in records if measured(r,cases[r['case_id']]) and not succeeded(r,cases[r['case_id']])})
 return [('failureStage',t('failure_stage', 'Failure stage'),[('request',t('request_failure', 'Request failure')),('task',t('request_succeeded_task_failed', 'Request succeeded; task failed'))]),('failureCategory',t('failure_category', 'Failure category'),[(x,x) for x in categories])]

def comparison_rows(cases,bycase,names):
 rows=[]
 def definition(case):return {key:value for key,value in case.items() if key!='id'}
 for case_id,c in cases.items():
  attempts={r['provider']:r for r in bycase.get(case_id,[])}
  passed=[name for name in names if name in attempts and measured(attempts[name],c) and succeeded(attempts[name],c)]
  failed=[name for name in names if name in attempts and measured(attempts[name],c) and not succeeded(attempts[name],c)]
  method_links=lambda selected,state,attempts=attempts:('<span class="outcome-list">'+''.join(f'<a class="outcome-pill {state}" href="{esc(safe_href(result_href(attempts[name],"attempt","../")))}">{model_text(provider_label(name),browser_method_label(name,language="en"))}</a>' for name in selected)+'</span>') if selected else '—'
  attrs=(dataset_attrs(c)+f' data-pass="{esc(" ".join(passed))}" data-fail="{esc(" ".join(failed))}"')
  task=(f'<div class="task-cell">{link("../datasets/"+case_id+".html",c["name"])}<div class="task-meta"><code class="tiny">{esc(case_id)}</code>'
        f'<span class="track-badge">{esc(case_track_label(c))}</span></div></div>')
  # Different editions may retain different requirements for the same task.
  # Expose each original definition without changing scores or comparison IDs.
  if any(definition(result_case(r,c))!=definition(c) for r in attempts.values()):
   sources=' · '.join(link(result_href(attempts[name],'task','../'),model_text(provider_label(name),browser_method_label(name,language='en'))) for name in names if name in attempts)
   task+='<details class="task-contracts"><summary>'+saved_matrix_text('task_rules_by_method')+'</summary>'+sources+'</details>'
  values=cell(task)+cell(method_links(passed,'pass'))+cell(method_links(failed,'fail'))
  rows.append((attrs,values))
 return rows

def comparison_table(rows,body_id):
 return table([t('task', 'Task'),t('passing_methods', 'Passing methods'),t('failing_methods', 'Failing methods')],rows,cls='comparison-table',metrics=[]).replace('<tbody>',f'<tbody id="{body_id}">',1)

def method_comparisons(cases,bycase,names):
 all_attempts=[attempt for attempts in bycase.values() for attempt in attempts]
 ordered=sorted(names,key=lambda name:(-sum(succeeded(attempt,cases[attempt['case_id']]) for attempt in all_attempts if attempt['provider']==name),provider_label(name)))
 if len(ordered)<2:return ('<p class="decision-lead">' + t('at_least_two_methods_are_needed_to', 'At least two methods are needed to compare shared tasks.') + '</p>')
 a=ordered[0];b=ordered[1]
 options=lambda selected:''.join(f'<option value="{esc(name)}"'+(' selected' if name==selected else '')+f' data-report-en="{esc(browser_method_label(name,language="en"))}">{esc(provider_label(name))}</option>' for name in ordered)
 tracks=('<option value="">' + t('all_families', 'All families') + f"""</option>{''.join((f'<option value="{esc(key)}">{esc(label)}</option>' for key, label in REPORT_TRACKS.items()))}""")
 rows=comparison_rows(cases,bycase,names)
 checks=''.join(f'<label><input type="checkbox" value="{esc(name)}" data-compare-method'+(' checked' if name==b else '')+f'> {model_text(provider_label(name),browser_method_label(name,language="en"))}</label>' for name in ordered)
 return (('<article class="method-comparison"><header class="comparison-heading"><div><h3>' + t('same_task_outcome_comparison', 'Same-task outcome comparison') + '</h3><p>' + t('use_method_a_as_the_baseline_and', 'Use Method A as the baseline and select one or more methods for a joint comparison. Each method name opens its complete evidence.') + '</p></div><b class="comparison-count" id="compare-count" role="status" aria-live="polite"></b></header><div class="comparison-controls"><label>' + t('method_a', 'Method A') + f'<select id="compare-a" data-compare-control>{options(a)}</select></label><label>' + t('comparison_condition', 'Comparison condition') + '<select id="compare-relation" data-compare-control><option value="mixed">' + t('outcomes_differ', 'Outcomes differ') + '</option><option value="a_pass_others_fail">' + t('a_passes_all_others_fail', 'A passes; all others fail') + '</option><option value="a_fail_others_pass">' + t('a_fails_all_others_pass', 'A fails; all others pass') + '</option><option value="all_pass">' + t('all_selected_methods_pass', 'All selected methods pass') + '</option><option value="all_fail">' + t('all_selected_methods_fail', 'All selected methods fail') + '</option><option value="exact_pass">' + t('only_the_selected_methods_pass', 'Only the selected methods pass') + '</option></select></label><label>' + t('task_family', 'Task family') + f'<select id="compare-track" data-compare-control>{tracks}</select></label></div><fieldset class="comparison-methods"><legend>' + t('methods_to_compare_multiple_selection', 'Methods to compare (multiple selection)') + f"""</legend><div class="method-checks">{checks}</div></fieldset><div class="comparison-result">{comparison_table(rows, 'compare-rows')}</div></article>"""))

def dataset_attrs(c):
 return f' data-track="{esc(report_family(c))}" data-category="{esc(c["category"])}"'

def case_attrs(r,c):
 comparison=c
 c=result_case(r,c)
 not_called=r.get('report_invocation_status') == 'adapter_rejected'
 content='na' if c.get('task_kind','body')!='body' else 'pass' if r['ok'] and r['metrics']['content_pass'] else 'fail'
 resources='na' if not c.get('expected_resources') else 'pass' if r['ok'] and r['metrics'].get('resource_pass') else 'fail'
 if not_called:content=resources='na'
 transport='na' if not_called else 'pass' if r['ok'] else 'fail'
 stage='' if succeeded(r,c) or not measured(r,c) else 'task' if r['ok'] else 'request'
 category='' if not stage else failure_category(r,c)
 result='excluded' if r['metrics'].get('evaluation_excluded') else 'unmeasured' if not measured(r,c) else 'pass' if succeeded(r,c) else 'fail'
 identifier=str(c.get('id',r.get('case_id','')))
 if r.get('case_id') and r['case_id']!=identifier:identifier+=' '+r['case_id']
 return dataset_attrs(comparison)+f' data-provider="{esc(r["provider"])}" data-transport="{transport}" data-result="{result}" data-content="{content}" data-resources="{resources}" data-failure-stage="{stage}" data-failure-category="{esc(category)}" data-search="{esc(identifier+" "+str(c.get("name",""))+" "+str(c.get("url",""))+" "+reason(r,c))}"'

def attempt_row(r,c,base='../',*,label=None):
 comparison=c
 c=result_case(r,c)
 state=result_label(r,c)
 values=cell(link(result_href(r,'attempt',base),c['name'] if label is None else label))+cell(case_track_label(comparison))+cell(f'<span class="{"muted" if not measured(r,c) else "good" if succeeded(r,c) else "bad"}">{state}</span>')+cell(esc(failure_category(r,c)))+cell(ms(r['elapsed_ms']))
 values+=cell(f'<code class="tiny break">{esc(r.get("run_id") or execution_id(r))}</code>')
 return case_attrs(r,comparison),values

def local_call_chain_section(dataset, prefix="../"):
 audit=dataset.get('comparison',{}).get('call_chain_audit')
 if not audit:return ''
 rows=[]
 for item in audit.get('methods',[]):
  note=item['native_log_note']
  rows.append(('',cell(link(prefix+'providers/'+item['provider']+'.html',provider_label(item['provider']),browser_method_label(item['provider'],language='en')))+cell(esc(note))))
 intro=('<p>' + t('every_call_retains_harness_events_start_time', 'Every call retains harness events: start time, input parameters, return time, and errors. These appear under Caller events in each case detail.') + '</p><p>' + t('product_and_dependency_logs_contain_runtime_information', 'Product and dependency logs contain runtime information actually emitted by the product or underlying libraries. Harness events do not substitute for unavailable cloud-internal logs. The table lists what was retained for each method.') + '</p>')
 return section(t('logs_retained_in_this_run', 'Logs retained in this run'),(f"{intro}{table([t('method', 'Method'), t('product_and_dependency_log_coverage', 'Product and dependency log coverage')], rows, metrics=[])}<p>" + t('full_retained_logs_are_available_in_each', 'Full retained logs are available in each case detail. They help locate problems, but unrecorded internal processes cannot be inferred; a log file does not mean every internal process was recorded.') + '</p>'),'call-chain-audit')


def local_pass_rate_ranking(dataset):
 return dataset.get('comparison',{}).get('presentation',{}).get('ranking') == 'saved_task_pass_rate'


def local_audit_banner(dataset):
 if dataset.get('comparison',{}).get('presentation',{}).get('scope_notes')=='external':return ''
 if dataset.get('saved_matrix_scope') and local_pass_rate_ranking(dataset):
  return '<div class="wrap source-note" data-evidence-state="saved_results" data-ranking-scope="saved-task-pass-rate"><strong>'+model_text(*pair('report_saved_result_rankings_not_benchmark_certification_93b684', 'Saved-result rankings · not benchmark certification'))+'</strong><p>'+model_text(*pair('report_ranks_describe_the_listed_results_and_the_acceptance_14faa5', 'Ranks describe the listed results and the acceptance rules recorded with them. Source and execution-setting verification is not benchmark certification; see methodology for configurations and collection dates.'))+'</p></div>'
 if dataset.get('comparison',{}).get('publication_state')!='local_audit_only_not_for_release':
  return ''
 if dataset.get('saved_matrix_scope'):
  return '<div class="wrap callout" data-evidence-state="saved_diagnostic"><strong>'+model_text(*pair('report_saved_result_diagnostics_uncertified_b7909f', 'Saved-result diagnostics · uncertified'))+'</strong><p>'+model_text(*pair('report_scores_use_the_acceptance_rules_recorded_with_the_611ca8', 'Scores use the acceptance rules recorded with the saved results; ranks are not shown.'))+'</p></div>'
 if measurement_presentation(dataset).local_matrix:
  if local_pass_rate_ranking(dataset):
   return '<p class="wrap source-note" data-evidence-state="local_measurement" data-ranking-scope="saved-task-pass-rate">'+model_text(*pair('report_ordered_by_pass_rate_on_the_listed_tasks_3f246f', 'Ordered by pass rate on the listed tasks; repository certification is incomplete. See details for test dates and verification scope.'))+'</p>'
  note=(t('report_review_records_are_available_see_the_review_page_for_co_7eabe7', 'Review records are available; see the review page for coverage and adjudication evidence.')) if dataset['comparison'].get('independent_review') else (t('report_results_are_machine_judgments_without_independent_human_5c8742', 'Results are machine judgments without independent human review.'))
  return ('<div class="wrap callout" role="alert" data-evidence-state="local_measurement">' + '<strong>' + t('report_local_uncertified_diagnostic_results_22b53d', 'Local uncertified diagnostic results') + '</strong>' + '<p>' + t('report_saved_results_are_shown_without_ranks_sources_were_not__475548', 'Saved results are shown without ranks. Sources were not fully verified before and after calls; these results are not a certified release.') + '</p>' + '<p>')+note+'</p></div>'
 return '<div class="wrap callout"><strong>'+model_text(*pair('report_local_audit_report_5d8f02', 'Local audit report'))+'</strong><p>'+model_text(*pair('report_this_report_is_not_a_formal_release_consult_cf9dcc', 'This report is not a formal release. Consult the saved records for tool versions, call times and verification scope.'))+'</p></div>'


def render_report(out,*inputs,variant=None,**options):
 """The one renderer entry: write the page set of a registered template variant.

 Variants without their own page set (the default) render one validated
 ``ReportModel``; their builds record the variant. A benchmark contributes its
 variants' page sets through ``views()`` (``PageSetHook``) and the track owns the
 diagnostic and research sets; this entry records those variants in the manifest.
 """
 if variant is None:return _model_pages(Path(out),*inputs,**options)
 entry=_page_set(variant)(Path(out),*inputs,**options)
 _record_template(Path(out),variant)
 return entry


def _record_template(out,variant):
 """Record the rendered variant in the report manifest, beside what the page set recorded itself.

 The record names the base, track and variant versions only, so an installed package records it too.
 """
 from harness.web_fetch.template import TEMPLATE
 path=out/'report-manifest.json'
 manifest=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'schema_version':1}
 manifest['template']=TEMPLATE.record(variant)
 path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def _page_set(variant):
 from harness.reporting.templates import PageSetHook
 from harness.web_fetch.benchmarks import BENCHMARKS
 from harness.web_fetch.template import TEMPLATE
 TEMPLATE.variant(variant)
 found=[hook.render for _,benchmark in BENCHMARKS.items() for hook in benchmark.views()
        if isinstance(hook,PageSetHook) and hook.key==variant]
 found+=[_TRACK_PAGE_SETS[variant]] if variant in _TRACK_PAGE_SETS else []
 if len(found)>1:raise ValueError('Report variant has more than one page set: '+variant)
 return found[0] if found else _model_pages


def _model_pages(out,model):
 if not isinstance(model,ReportModel):raise TypeError('render_report() takes a validated ReportModel')
 # The model keeps its inputs by reference; admit them again at the render boundary.
 model.require_admission()
 with rendering(model):
  return _render_model(Path(out),model.dataset,model.records,model.manifests,model=model)


def _write_uncalled_participants(out,model):
 """A declared participant without results keeps its route, showing the declared scope."""
 called={row['provider'] for row in model.records}
 for participant in model.participants:
  if participant.id not in called:
   write(out,'providers/'+participant.id+'.html',participant.label,
         local_audit_banner(model.dataset)+hero(esc(participant.label),'')+saved_matrix_scope_section(model.dataset.get('saved_matrix_scope') or {}),root_links=True)


@with_measured_method_labels
def _render_model(out,dataset,records,manifests,*,model):
 options=model.options
 include_english,authored_interpretation,presentation_only=options.include_english,options.authored_interpretation,options.presentation_only
 show_local_status_banners,detail_attempt_ids,dataset_case_ids=options.show_local_status_banners,options.detail_attempt_ids,options.dataset_case_ids
 frozen_scores,english_routes,diagnostic_view_paths=options.frozen_scores,options.english_routes,options.diagnostic_view_paths
 _public_views=public_render_views(out,records)
 for _record in records:
  if _record['attempt_id'] in _public_views:
   _record.setdefault('provider_metadata',{})['publication_views']=_public_views[_record['attempt_id']]
 local_snapshot=dataset.get('comparison',{}).get('publication_state')=='local_audit_only_not_for_release'
 score_binding=dataset.get('comparison',{}).get('score_audit')
 score_audit=json.loads(contained_path(out,score_binding['path']).read_text(encoding='utf-8')) if score_binding else None
 diagnostic=bool(score_audit) or (local_snapshot and not local_pass_rate_ranking(dataset))
 if local_snapshot and not show_local_status_banners:
  raise ValueError('Uncertified local diagnostic status cannot be hidden')
 reliability=assess_reliability(dataset,records)
 final_saved=dataset.get('comparison',{}).get('publication_state')=='final_saved_results'
 if final_saved and (not dataset.get('saved_matrix_scope') or not reliability['complete']):
  raise ValueError('Final saved report requires complete judged call evidence')
 if dataset.get('saved_matrix_scope') and not final_saved and not any(reliability[key] for key in ('extra','duplicates')):
  saved_scope=dataset['saved_matrix_scope']
  counts={key:sum(row[key] for row in saved_scope['providers']) for key in ('planned','saved','uncommitted','not_called','rejected')}
  reliability_banner=('<div class="wrap reliability failure" role="status" data-saved-completion>'
      '<strong>'+saved_matrix_text('coverage_banner_title')+'</strong><span>'
      +esc(saved_matrix_text('coverage_banner_counts', **counts))+'</span><span>'
      +esc(saved_matrix_text('coverage_banner_note'))+'</span></div>')
  if saved_scope.get('parameter_reruns'):
   reliability_banner+='<div class="wrap callout" data-parameter-reruns>'+esc(saved_matrix_text('parameter_family_banner' if saved_scope['parameter_reruns'].get('family_selection') else 'parameter_rerun_banner', **saved_scope['parameter_reruns']))+'</div>'
 elif reliability['complete']:
  reliability_banner=''
 elif (measurement_presentation(dataset).explain_unjudged
       and dataset.get('comparison',{}).get('publication_state')=='local_audit_only_not_for_release'
       and not any(reliability[key] for key in ('missing','extra','duplicates'))):
  excluded=[r for r in records if r['metrics'].get('evaluation_excluded')]
  if len(excluded)==len(reliability['unjudged']):
   reliability_banner='<div class="wrap callout">'+model_text(*pair('results_are_excluded_across_all_methods_for', '{count} results are excluded across all methods for a verified evaluation-environment defect; original calls and outputs are retained.', count=len(excluded)))+'</div>'
  else:
   reliability_banner=('<div class="wrap reliability failure" role="alert"><strong>' + t('calls_are_complete_but_some_results_are', 'Calls are complete, but some results are undecidable') + f"</strong><span>{esc(t('all_calls_have_records_lack_sufficient_acceptance', 'All {present_calls} calls have records; {count} lack sufficient acceptance evidence. The pass rate uses decidable results, with the denominator and undecidable count shown separately. A local report is not full acceptance or formal publication.', present_calls=reliability['present_calls'], count=len(reliability['unjudged'])))}</span></div>")
 else:
  reliability_banner=('<div class="wrap reliability failure" role="alert"><strong>' + t('report_unreliable_evaluation_incomplete', 'Report unreliable: evaluation incomplete') + f"</strong><span>{esc('；'.join(reliability['errors']))}。本页排名不得用于结论或发布。</span></div>")
 if not show_local_status_banners and dataset.get('comparison',{}).get('publication_state')=='local_audit_only_not_for_release' and not any(reliability[key] for key in ('missing','extra','duplicates')):
  reliability_banner=''
 if show_local_status_banners:
  reliability_banner+=local_audit_banner(dataset)
 known=dataset.get('comparison',{}).get('call_chain_audit',{}).get('known_scoring_false_negatives',[])
 if known:
  reliability_banner+=('<div class="wrap callout" role="alert"><strong>' + t('confirmed_scoring_issue', 'Confirmed scoring issue') + f"</strong><p>{esc(t('table_results_were_misjudged_as_failures_by', '{count} table results were misjudged as failures by the scorer. Statistics keep the original verdict, and the current pass rate must not be used to rank products; see the call-chain checks.', count=len(known)))}</p></div>")
 if records and all(row.get('provider_type') == 'fixture' for row in records):
  reliability_banner += ('<div class="wrap callout" role="status"><strong>' + t('synthetic_example_not_a_formal_benchmark', 'Synthetic example · not a formal benchmark') + '</strong><p>' + t('content_and_failures_are_predefined_scenarios_for', 'Content and failures are predefined scenarios for checking installation and report functionality, not browser measurements.') + '</p></div>')
 elif dataset.get('comparison', {}).get('publication_state') == 'diagnostic_not_for_release':
  reliability_banner += ('<div class="wrap callout" role="status"><strong>' + t('diagnostic_run_not_a_formal_benchmark', 'Diagnostic run · not a formal benchmark') + '</strong><p>' + t('these_calls_check_the_selected_tasks_and', 'These calls check the selected tasks and methods without the cohort certification required for a formal benchmark.') + '</p></div>')
 from harness.web_fetch.report_version import content_scope_presentation
 set_render_context(replace(render_context(),banner=reliability_banner,english_generated=include_english,score_audit=score_audit,coverage_plan=dataset.get('coverage_plan'),scope_presentation=content_scope_presentation(dataset,records,manifests),external_scope_notes=dataset.get('comparison',{}).get('presentation',{}).get('scope_notes')=='external'))
 validate_report_ids(dataset,records)
 for path in ['assets/style.css','assets/report.js','references']:
  contained_path(out,path)
 for c in dataset['cases']:
  contained_path(out,'references/'+c['id']+'.txt')
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA, encoding='utf-8');(out/'assets/report.js').write_text(JS, encoding='utf-8')
 cases={c['id']:c for c in dataset['cases']};names=sorted(set(render_context().labels) | {r['provider'] for r in records})
 tracks=active_tracks(cases);track_count=len(tracks);track_label=track_count_label(track_count)
 audit=json.loads((out/'result-review.json').read_text(encoding='utf-8')) if (out/'result-review.json').is_file() else None
 audited_attempts=review_index(audit)
 failure_reviews={}
 content_binding=dataset.get('comparison',{}).get('report_interpretation')
 if content_binding:
  article_content=json.loads(contained_path(out,content_binding['path']).read_text(encoding='utf-8'))
  failure_reviews=load_browser_failure_review(out,cases,records,article_content)
 bycase=defaultdict(list);byprovider=defaultdict(list)
 for r in records:bycase[r['case_id']].append(r);byprovider[r['provider']].append(r)
 cards=''
 for track,label in tracks:
  cards+=overview_card(records,cases,track,label,names,diagnostic=diagnostic)
 description=(t('tasks_methods_local_uncertified_diagnostic_snapshot', '{count} tasks · {count2} methods · local uncertified diagnostic snapshot.', count=len(cases), count2=len(names)) if diagnostic else t('tasks_methods_family_rankings_and_one_overall', '{count} tasks · {count2} methods · {track_label} family rankings and one overall coverage ranking.', count=len(cases), count2=len(names), track_label=track_label))
 if score_audit:
  description=model_text(*pair('report_value_cases_value_methods_value_task_families_056945', '{value0} cases · {value1} methods · {value3} task families.', value0=len(cases), value1=len(names), value2=track_label, value3=len(tracks)))
 if local_pass_rate_ranking(dataset):
  description=model_text(*pair('report_value_cases_value_methods_ordered_by_task_pass_07cb44', '{value0} cases · {value1} methods · Ordered by task pass rate for this sample.', value0=len(cases), value1=len(names)))
 if not dataset.get('comparison',{}).get('presentation',{}).get('combined_ranking',True):
  description=model_text(*pair('report_value_task_entries_value_methods_compared_within_each_953b5c', '{value0} task entries · {value1} methods · Compared within each task category.', value0=len(cases), value1=len(names)))
 release_path=out/'benchmark-release.json'
 if release_path.is_file():
  release=json.loads(release_path.read_text(encoding='utf-8'))
  description='<span>'+description+'</span> · <a data-benchmark-release="'+esc(release['version'])+'" href="benchmark-release.json">'+esc('bench v'+release['version'])+'</a>'
 home='<div class="home-page">'+hero(t('web_fetch_benchmark_report', 'Web Fetch benchmark report'),description)
 TITLE=t('call_chain_checks_and_log_scope_for', 'Call-chain checks and log scope for this run') if dataset.get('comparison',{}).get('call_chain_audit') else t('task_scope_and_acceptance_rules', 'Task scope and acceptance rules')
 if authored_interpretation and dataset.get('comparison',{}).get('report_interpretation'):
  binding=dataset['comparison']['report_interpretation']
  TITLE=json.loads(contained_path(out,binding['path']).read_text(encoding='utf-8'))['title']
 reading_note=t('use_score_differences_failure_evidence_and_implementation', 'Use score differences, failure evidence, and implementation boundaries to choose the right retrieval method.') if authored_interpretation else t('see_the_tasks_result_counts_and_acceptance', 'See the tasks, result counts and acceptance rules used in this report.')
 if dataset.get('comparison',{}).get('call_chain_audit'):
  reading_url='guide/index.html'
  home+=('<aside class="wrap home-reading" aria-label="' + t('interpretation', 'Interpretation') + '"><div><strong>' + t('interpretation', 'Interpretation') + f"</strong><p>{link(reading_url, t('after_a_web_fetch_succeeds_how_far', 'After a web fetch succeeds, how far is the content from usable?'))}</p></div><p>" + t('use_score_differences_failure_evidence_and_implementation', 'Use score differences, failure evidence, and implementation boundaries to choose the right retrieval method.') + f"</p>{link(reading_url, t('read_the_interpretation', 'Read the interpretation →'))}</aside>")
 if not dataset.get('comparison',{}).get('call_chain_audit'):home+=('<aside class="wrap home-reading" aria-label="' + t('interpretation', 'Interpretation') + '"><div><strong>' + t('interpretation', 'Interpretation') + f"</strong><p>{link('guide/index.html', TITLE)}</p></div><p>{reading_note}</p>{link('guide/index.html', t('read_the_interpretation', 'Read the interpretation →'))}</aside>")
 if dataset.get('comparison',{}).get('call_chain_audit'):
  TITLE=t('call_chain_checks_and_log_scope_for', 'Call-chain checks and log scope for this run')
  guide=section(t('how_to_read_these_results', 'How to read these results'),('<p>' + t('rates_apply_only_to_the_cases_and', 'Rates apply only to the cases and configurations in this report. Start with the overall and category rankings, then open individual cases to compare requirements with actual output. Request success means the method returned normally; task success means the returned content met every requirement for that case.') + '</p><p>' + t('see_methodology_for_scoring_and_timing_review', 'See Methodology for scoring and timing, Review history for checks and corrections, and Reproduce for method versions, fixed options and log coverage.') + '</p>'))
 else:
  guide=render_explainer(out,cases,records,table=table,cell=cell,link=link,authored=authored_interpretation)
 guide=render_archived_reading(out) or guide
 write(out,'guide/index.html',t('interpretation', 'Interpretation'),guide)
 panels=[]
 for track,label in tracks:
  selected=[c for c in cases.values() if report_family(c)==track]
  method_count=len({r['provider'] for r in records if report_family(cases[r['case_id']])==track})
  coverage=model_text(*pair('report_value_cases_value_measured_methods_885407', '{value0} cases · {value1} measured methods', value0=len(selected), value1=method_count))
  panels.append((track,label,f'<div class="track-heading"><h3>{esc(label) if render_context().suites else label}</h3><span>{coverage}</span></div>'+ranking(records,cases,track,compact=True,audit=audit,diagnostic=diagnostic)+(f"<p>{link('datasets/index.html#track-' + track, t('report_tasks_and_references_2fa3ec', 'Tasks and references →'))}</p>")))
 overall_title=model_text(*pair('report_task_pass_rate_ranking_for_this_sample_5d9edf', 'Task pass-rate ranking for this sample')) if local_pass_rate_ranking(dataset) else ((t('report_task_results_b4e30d', 'Task results')) if diagnostic else (t('report_overall_coverage_ranking_47274b', 'Overall coverage ranking')))
 combined=dataset.get('comparison',{}).get('presentation',{}).get('combined_ranking',True)
 if not combined:overall_title=(t('report_task_results_b4e30d', 'Task results'))
 budgets={m['timeout_seconds'] for m in manifests if m.get('timeout_seconds') is not None}
 home+=section(overall_title,overall_ranking(records,cases,audit,diagnostic=diagnostic,call_budget_seconds=next(iter(budgets)) if len(budgets)==1 else None,combined=combined),'overall-ranking')
 home+=token_usage_comparison(records)
 home+=input_suite_comparison(dataset,cases,records)
 if diagnostic and not score_audit:
  home+=result_ledger_section(out,records)
  low=[r for r in records if not r['metrics'].get('evaluation_excluded') and succeeded(r,cases[r['case_id']]) and cases[r['case_id']].get('ground_truth_main_content') and r['metrics'].get('word_precision') is not None and r['metrics']['word_precision']<.3]
  home+=section(t('content_diagnostics_for_passing_results', 'Content diagnostics for passing results'),('<p>' + t('passing_results_have_reference_word_precision_below', '{count} passing results have reference word precision below 30%. This locates inspection candidates, not a universal failure threshold. Passing does not establish clean body text; reference scope and time also affect this metric.', count=len(low)) + '</p>'),'coverage-quality')
 if measurement_presentation(dataset).all_call_latency:
  home+=section((t('report_all_call_latency_on_the_same_task_set_45ffcf', 'All-call latency on the same task set')),overview_all_call_latency(records,cases),'all-call-latency')
 completeness=(('<p class="callout">' + '<b>' + t('report_result_storage_5f0229', 'Result storage:') + '</b>')+str(reliability['present_calls'])+'/'+str(reliability['expected_calls'])+(t('report_groups_have_saved_results_no_scores_were_invented_for_m_448a36', ' groups have saved results; no scores were invented for missing entries.') + '</p>'))
 if final_saved or render_context().external_scope_notes:
  completeness=''
 elif not dataset.get('saved_matrix_scope'):
  completeness=('<p class="callout success">' + '<b>' + t('report_result_coverage_403d3c', 'Result coverage:') + '</b>')+str(reliability['expected_calls'])+'/'+str(reliability['expected_calls'])+(t('report_calls_all_have_saved_results_0daa25', ' calls all have saved results.') + '</p>')
 home+=section(model_text(*pair('report_value_task_family_overview_8b3e73', '{value1} task-family overview', value0=track_label, value1=len(tracks))),review_summary(out)+completeness+('<p class="source-note">' + t('report_compare_results_only_within_the_same_task_family_9a6239', 'Compare results only within the same task family.') + '</p>' + '<div class="grid home-overview-grid">')+cards+'</div>','overview')
 if dataset.get('saved_matrix_scope') and not final_saved:
  home+=saved_matrix_scope_section(dataset['saved_matrix_scope'])

 home+=section((t('report_latency_on_tasks_passed_by_every_method_4f37d4', 'Latency on tasks passed by every method')),overview_matched_latency(records,cases),'overall-matched-latency')
 if (out/'resource-cohort/resource-comparison.json').is_file():
  home+=section((t('report_cpu_memory_and_network_6ef0eb', 'CPU, memory, and network')),overview_resources(out),'overall-resources')
 home+=section((t('report_results_by_task_family_fc8ac0', 'Results by task family')) if score_audit else (t('report_diagnostic_results_by_task_family_d020c9', 'Diagnostic results by task family')) if diagnostic else model_text(*pair('report_value_family_rankings_886366', '{value1} family rankings', value0=track_label, value1=len(tracks))),(('<p>' + t('report_task_pass_rate_content_quality_and_latency_are_shown_se_2f9439', 'Task pass rate, content quality and latency are shown separately.') + '</p>') if score_audit else ('<p class="ranking-orientation">' + t('report_diagnostic_rows_are_sorted_by_task_pass_rate_for_inspec_6aadb0', 'Diagnostic rows are sorted by task pass rate for inspection; no ranks are assigned. Task judgments, content quality and latency are shown separately.') + '</p>') if diagnostic else ('<p class="ranking-orientation">' + t('report_family_rankings_show_specific_capabilities_they_default_500d5c', 'Family rankings show specific capabilities. They default to task pass rate; select a column heading to sort. Text quality and pass rate are calculated separately.') + '</p>'))+tabs('home-rankings',panels),'results')
 write(out,'index.html',(t('report_benchmark_report_838269', 'Benchmark report')),home+'</div>')
 controls=filter_controls([('track',(t('report_family_ba4001', 'Family')),tracks),('category',(t('report_family_0d12cb', 'Family')),[(x,x) for x in dict.fromkeys(c['category'] for c in cases.values())])])
 rows=[]
 for c in cases.values():
  verified=c.get('source_verification',{});state=t('access_restricted', 'Access restricted') if verified.get('state')=='restricted' else t('valid', 'Valid')
  rows.append((dataset_attrs(c),cell(link(c['id']+'.html',c['name']))+cell(esc(c['category']))+cell(link(c['url'],t('source_page_2', 'Source page ↗')))+cell(link(c['id']+'.html#facts',basis_label(c)))+cell(reference_label(c))+cell(state)))
 anchors=''.join(f'<span id="track-{t}"></span>' for t,_ in tracks)
 dataset_panels=[('cases',t('task_2', 'Task'),(f'{anchors}{controls}<p class="source-note">' + t('open_any_evidence_basis_for_details_restricted', "Open any evidence basis for details. Restricted access does not mean a site is down. Stable content and record structure are checked against registered contracts rather than frequently changing lists. Rendered-web body text is judged within each task's declared complete scope.") + f"</p>{table([t('task_2', 'Task'), t('family_2', 'Family'), 'URL', t('reference_basis', 'Reference basis'), t('reference_status', 'Reference status'), t('original_access', 'Original access')], rows, 'filtered-table')}")),('references',t('reference_status', 'Reference status'),'<span id="references"></span>'+reference_status(list(cases.values()))),('sources',t('sources_2', 'Source'),'<span id="sources"></span>'+sources_panel())]
 write(out,'datasets/index.html',t('dataset', 'Dataset'),hero(t('dataset', 'Dataset'),t('tasks_grouped_by_task_family', '{count} tasks, grouped by task family.', count=len(cases)))+section(t('tasks_and_evidence', 'Tasks and evidence'),tabs('dataset-hub',dataset_panels)))
 headers=[t('task_2', 'Task'),t('family', 'Family'),t('result', 'Result'),t('failure_class', 'Failure class'),t('elapsed_time', 'Elapsed time'),t('run_id', 'Run ID')]
 for c in ([case for case in cases.values() if case['id'] in (dataset_case_ids or set())] if presentation_only else cases.values()):
  v=c.get('source_verification',{});ref=reference_text(c)
  (out/'references').mkdir(exist_ok=True);(out/'references'/f'{c["id"]}.txt').write_text(ref, encoding='utf-8')
  if c.get('acceptance_contract'):
   target=contained_path(out,'contracts/'+c['id']+'.json');target.parent.mkdir(exist_ok=True)
   target.write_text(json.dumps(machine_contract(c),ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
  facts=[('',cell(esc(f['id']))+cell(esc(' / '.join(f['alternatives'])))) for f in c.get('required_facts',[])]
  resources=[('',cell(esc(x['id']))+cell('<br>'.join(link(u,u) for u in x.get('urls',[]))+(('<br>' + t('entry_url_rule', 'Entry URL rule:') + f"<code>{esc(' / '.join(x['url_patterns']))}</code>") if x.get('url_patterns') else ''))) for x in c.get('expected_resources',[])]
  rules=''.join(f'<p>{esc(x["id"])}：至少 {x.get("minimum",1)} 项；<code>{esc(x["pattern"])}</code></p>' for x in c.get('required_patterns',[]))
  status_note=f'<p class="callout">参考状态：{reference_label(c)}。</p>'
  if c.get('resource_output_contract',{}).get('unexpected')=='reject':
   status_note+=('<p class="callout">' + t('resource_output_contract_answer_bearing_image_fields', 'Resource output contract: answer-bearing image fields may contain only registered targets, aliases, or matching patterns; additional images fail the task.') + '</p>')
  if c.get('task_kind')=='structure':
   status_note=(f'<p class="callout">{esc(list_rule_description(c))}</p><p>' + t('task_verdict_and_latency_enter_the_primary', 'Task verdict and latency enter the primary ranking; full-text metrics are shown as — without a complete reference.') + '</p>')
   if (out/'list-references'/f'{c["id"]}.json').exists():status_note+='<p>'+link('../list-references/'+c['id']+'.json',t('name_and_url_validation_basis', 'Name and URL validation basis'))+'</p>'
   rules=('<div class="guide-grid"><article class="guide-card"><h3>' + t('registered_task_rules', 'Registered task rules') + f'</h3><p>{esc(list_rule_description(c))}</p></article><article class="guide-card"><h3>' + t('stable_content_and_changing_fields', 'Stable content and changing fields') + '</h3><p>' + t('stable_titles_sections_and_descriptions_are_checked', 'Stable titles, sections and descriptions are checked against registered content; frequently changing lists, entry titles, order, prices or IDs are not pinned to old snapshot values and are checked by declared field structure, type, count and uniqueness. Login pages, duplicate entries and empty links do not count.') + '</p></article></div>')
  if c.get('acceptance_contract'):
   facts=[]
   rules=stable_contract_details(c,evidence_root=out)
   if c.get('composite_acceptance'):
    rules+=composite_contract_details(c['composite_acceptance'])
  source_record=link('../sources/'+c['id']+'/evidence.json',t('retrieval_record', 'Retrieval record')) if (out/'sources'/c['id']/'evidence.json').exists() else t('no_source_page_snapshot_saved', 'No source-page snapshot saved')
  if c.get('track')=='dynamic':
   projected=c.get('stable_text_evidence') or {}
   if projected:
    source_record+=' · '+link('../'+projected['path'],t('independent_visible_text', 'Independent visible text'))+' · '+link('../'+projected['manifest_path'],t('source_verification_record', 'Source verification record'))
   elif (out/'sources'/c['id']/'mixed-session-stable-text.json').is_file():
    source_record+=' · '+link('../sources/'+c['id']+'/mixed-session-stable-text.json',t('independent_visible_text_and_source_verification_records', 'Independent visible text and source verification records'))
   elif all((out/'sources'/c['id']/name).is_file() for name in ('initial.html.txt','rendered.txt','render-audit.json')):
    source_record+=' · '+link('../sources/'+c['id']+'/initial.html.txt',t('initial_html', 'Initial HTML'))+' · '+link('../sources/'+c['id']+'/rendered.txt',t('rendered_body_text', 'Rendered body text'))+' · '+link('../sources/'+c['id']+'/render-audit.json',t('paired_evidence_validation', 'Paired-evidence validation'))
   observed=sorted({r['provider'] for r in bycase[c['id']]})
   status_note+=('<p class="source-note">' + t('methods_measured_on_dynamic_web_text', 'Methods measured on dynamic web text:') + f"{esc('、'.join(observed))}" + t('unmeasured_methods_do_not_count_as_failures', '. Unmeasured methods do not count as failures.') + '</p>')
  body=hero(esc(c['name']),('<strong>' + t('task_id_2', 'Task ID: ') + f"<code>{esc(c['id'])}</code></strong><br>{esc(task_instruction(c))}"))+section(t('reference_and_source', 'Reference and source'),(f"""{status_note}<p>{link(c['url'], t('open_task_source', 'Open task source ↗'))} · {link('../references/' + c['id'] + '.txt', t('download_reference', 'Download reference'))} · {source_record}</p><div class="evidence-pane"><header><h3>""" + t('reference_answer_required_deliverable', 'Reference answer / required deliverable') + '</h3><p>' + t('the_complete_material_used_for_the_verdict', 'The complete material used for the verdict appears below; long content scrolls within its pane.') + f'</p></header><div class="pane-body"><pre data-kind="reference">{esc(ref)}</pre></div></div><details class="technical-details"><summary>' + t('view_source_record', 'View source record') + f"</summary><p>{esc(v.get('method'))} · {esc(v.get('checked_at'))}</p><p>{esc(v.get('note'))}</p></details>"))
  body+=section(t('scoring_checks', 'Scoring checks'),(table([t('check_2', 'Check'),t('accepted_variants', 'Accepted variants')],facts) if facts else '')+rules+(table([t('resource', 'Resource'),t('accepted_urls', 'Accepted URLs')],resources) if resources else '')+(('<p>' + t('checks_that_a_verifiable_original_file_was', 'Checks that a verifiable original file was returned.') + '</p>') if c.get('task_kind')=='file' else ''),'facts')
  if c.get('task_kind')=='structure':
   example=out/'sources'/c['id']/'items.json'
   if example.exists():
    items=json.loads(example.read_text(encoding='utf-8'))
    sample=[('',cell(esc(x.get('title') or x.get('text','')))+cell(link(x['url'],t('details', 'Details ↗')) if x.get('url') else '—')) for x in items]
    body+=section(t('public_entry_snapshot', 'Public entry snapshot'),('<p class="source-note">' + t('public_fields_at_capture_time_are_kept', 'Public fields at capture time are kept as source evidence; the same list or order need not be returned, and stable tasks are judged by the content anchors and field requirements above.') + f"</p><details><summary>展开 {str(len(items))} 条</summary>{table([t('name_content', 'Name / content'), t('entry', 'Entry')], sample)}</details>"))
  if c.get('body_requirements'):
   rules=c['body_requirements']
   region_cards=[];total=len(rules['regions'])
   for index,region in enumerate(rules['regions']):
    label=_position_label(index,total)
    region_cards.append((f'<article class="region-card" id="reference-region-{index + 1}"><div class="region-head"><div><h3>{index + 1}. {esc(label)}</h3><p>' + t('this_is_the_text_from_the_same', 'This is the text from the same reference at this position, not a separate task.') + f"""</p></div></div><pre data-kind="reference">{esc(region['text'])}</pre></article>"""))
   if rules.get('version')==3:
    policy=(t('hard_floor_per_region_at_least_regions', 'Hard floor per region {minimum_floor}; at least {minimum_region_passes}/{count} regions reach {minimum_coverage}.', minimum_floor=pct(rules['minimum_floor']), minimum_region_passes=rules['minimum_region_passes'], count=len(rules['regions']), minimum_coverage=pct(rules['minimum_coverage'])))
   else:policy=f'每段至少达到 {pct(rules["minimum_coverage"])}。'
   body+=section(t('how_body_coverage_is_checked', 'How body coverage is checked'),('<p class="decision-lead"><b>' + t('this_is_one_complete_answer_not_tasks', 'This is one complete answer, not {total} tasks.', total=total) + '</b> ' + t('checking_only_the_title_and_a_few', "Checking only the title and a few keywords could let an opening-only result pass, so the same reference is checked by position. {policy} comparison ignores whitespace, layout symbols and Markdown link targets; each execution page shows each position's reference text and actual evidence side by side.", policy=policy) + f"</p>{''.join(region_cards)}"))
  body+='<p class="actions">'+link('../analysis/index.html#inspect?query='+c['id'],t('view_execution_results_for_all_methods', 'View execution results for all methods →'))+'</p>'
  write(out,'datasets/'+c['id']+'.html',c['name'],body)
 for name,rs in byprovider.items():
  write(out,'providers/'+name+'.html',name,provider_page_body(name,rs,cases,headers,tracks,native_default=measurement_presentation(dataset).native_outputs))
 detail_records=([r for r in records if r['attempt_id'] in (detail_attempt_ids or set())] if presentation_only else records)
 for r in detail_records:
  c=cases[r['case_id']];m=r['metrics'];ref=reference_text(c)
  output=contained_path(out,r['artifact_path']).read_text(encoding='utf-8',errors='replace')
  rows=[('',cell(k)+cell(v)) for k,v in [(t('task_id', 'Task ID'),f'<code>{esc(c["id"])}</code>'),(t('method', 'Method'),link('../providers/'+r['provider']+'.html',provider_label(r['provider']),browser_method_label(r['provider'],language='en'))),(t('request', 'Request'),t('succeeded', 'Succeeded') if r['ok'] else t('failed', 'Failed')),(t('task_result', 'Task result'),result_label(r,c)),(t('http_status', 'HTTP status'),esc(r.get('status_code'))),(t('elapsed_time', 'Elapsed time'),ms(r['elapsed_ms'])),(t('full_text_recall_precision_f1', 'Full-text recall / precision / F1'),f'{pct(m.get("word_recall"))} / {pct(m.get("word_precision"))} / {pct(m.get("word_f1"))}'),(t('output_characters', 'Output characters'),f'{len(output):,}'),(t('output_checksum', 'Output checksum'),f'<code class="break">{esc(m.get("content_sha256"))}</code>')]]
  if not c.get('ground_truth_main_content'):
   rows[6]=('',cell(t('full_text_recall_precision_f1', 'Full-text recall / precision / F1'))+cell(t('not_measured_no_complete_full_text_reference', '— / — / — (not measured; no complete full-text reference)')))
  else:
   from harness.web_fetch.report_components import comparable_body_reference
   if not comparable_body_reference(c):
    rows[6]=('',cell(t('snapshot_text_diagnostic_recall_precision_f1', 'Snapshot-text diagnostic · recall / precision / F1'))+cell((f"{' / '.join((pct(m.get(k)) for k in ['word_recall', 'word_precision', 'word_f1']))}" + t('the_reference_includes_fields_without_fixed_values', '; the reference includes fields without fixed values, so it is only a historical snapshot diagnostic and stays out of the main-table full-text mean.'))))
  if r.get('status_code') is None:
   rows[4]=('',cell(t('http_status', 'HTTP status'))+cell(t('not_provided_by_the_interface', '— (not provided by the interface)')))
  if r.get('report_invocation_status') == 'adapter_rejected':
   rows[2]=('',cell((t('report_request_393e12', 'Request')))+cell(result_label(r,c)))
  if c.get('task_kind')=='file':
   rows[7]=('',cell(t('text_output_characters', 'Text-output characters'))+cell(t('file_bytes_listed_separately', '{count:,} (file bytes listed separately)', count=len(output))))
   rows[8]=('',cell(t('text_output_checksum', 'Text-output checksum'))+cell(f'<code class="break">{esc(m.get("content_sha256"))}</code>'))
  _answer_view=r.get('provider_metadata',{}).get('publication_views',{}).get('scored_answer')
  if _answer_view and _answer_view['original_sha256']!=_answer_view['published_sha256']:
   rows[7]=('',cell(model_text(*pair('public_view_characters', 'Public-view characters')))+cell(f'{len(output):,}'))
   rows[8]=('',cell(model_text(*pair('original_scored_output_hash', 'Original scored output hash')))+cell(f'<code class="break">{esc(m.get("content_sha256"))}</code>'))
   rows.append(('',cell(model_text(*pair('public_view_hash', 'Public-view hash')))+cell(f'<code class="break">{esc(_answer_view["published_sha256"])}</code>')))
  if 'content_check_total' in m:
   rows.append(('',cell(t('required_text_coverage', 'Required-text coverage'))+cell(pct(m.get('content_check_coverage'))+f' · {m["content_check_hits"]}/{m["content_check_total"]} 组')))
  if c.get('task_kind')=='resource':
   rows.append(('',cell(t('resource_recall_precision', 'Resource recall / precision'))+cell(
       f'{pct(m.get("resource_recall"))} / {pct(m.get("resource_precision"))}')))
   if m.get('unexpected_resource_urls'):
    rows.append(('',cell(t('unexpected_resources', 'Unexpected resources'))+cell('<br>'.join(
        f'<code class="break">{esc(url)}</code>' for url in m['unexpected_resource_urls']))))
  rows[3]=('',cell(t('task_result', 'Task result'))+cell(result_label(r,c)))
  if r.get('raw_request_status'):
   rows.append(('',cell(t('raw_adapter_status', 'Raw adapter status'))+cell(esc(json.dumps(r['raw_request_status'],ensure_ascii=False)))))
  rows.append(('',cell(t('scorer_sha_256', 'Scorer SHA-256'))+cell(esc(m.get('scoring_source_sha256')))))
  title_details=''
  if 'title_quality' in r:
   q=r['title_quality']
   rows.append(('',cell(t('title_recall_title_precision_title_f1', 'Title recall / title precision / title F1'))+cell(' / '.join(pct(q[k]) for k in ['recall','precision','f1']))))
   rows.append(('',cell(t('title_reference_coverage', 'Title reference coverage'))+cell(t('only_returned_titles_with_independent_references_are', '{scored_titles}/{returned_titles}; only returned titles with independent references are measured', scored_titles=q['scored_titles'], returned_titles=q['returned_titles']))))
   checks=[('',cell(esc(x['title']))+cell(esc(x['reference_used'] or t('no_independent_name_reference_not_scored', 'No independent name reference; not scored')))+cell(link(x['url'],t('details_2', 'Details')))+cell(' / '.join(pct(x[k]) for k in ['recall','precision','f1']))) for x in q['checks']]
   steps=(('<ol class="quality-steps"><li><b>' + t('parse_records', 'Parse records') + '</b><span>' + t('the_output_contains_titles_with_detail_urls', 'The output contains {returned_titles} titles with detail URLs.', returned_titles=q['returned_titles']) + '</span></li><li><b>' + t('join_independent_references', 'Join independent references') + '</b><span>' + t('titles_have_independent_name_references_joined_by', '{scored_titles}/{returned_titles} titles have independent name references joined by detail URL.', scored_titles=q['scored_titles'], returned_titles=q['returned_titles']) + '</span></li><li><b>' + t('compare_title_text_2', 'Compare title text') + '</b><span>' + t('recall_precision_f1', 'Recall / precision / F1: {join}.', join=' / '.join((pct(q[k]) for k in ['recall', 'precision', 'f1']))) + '</span></li><li><b>' + t('interpret_the_result', 'Interpret the result') + '</b><span>' + t('this_score_describes_only_the_text_quality', 'This score describes only the text quality of returned titles and does not determine task acceptance.') + '</span></li></ol>'))
   comparison=(table([t('returned_title', 'Returned title'),t('reference_title', 'Reference title'),t('record_identity', 'Record identity'),t('recall_precision_f1_2', 'Recall / precision / F1')],checks,metrics=[])
               if checks else ('<p class="empty">' + t('no_returned_titles_can_be_compared_item', 'No returned titles can be compared item by item, so an empty table is not shown.') + '</p>'))
   title_details=(f'{steps}<details><summary>' + t('per_title_comparison_and_source', 'Per-title comparison and source') + f"""</summary><p>{link('../' + q['reference_path'], t('independent_reference', 'Independent reference'))} · 采集时间 {esc(q.get('reference_checked_at'))}</p>{comparison}<details class="technical-details"><summary>""" + t('technical_checksums', 'Technical checksums') + '</summary><p>' + t('reference_sha_256', 'Reference SHA-256:') + f"""<code class="break">{esc(q['reference_sha256'])}</code></p><p>""" + t('scorer_sha_256_2', 'Scorer SHA-256:') + f"""<code class="break">{esc(q['scorer_sha256'])}</code></p></details></details>""")
  if c.get('task_kind')=='structure' and 'stable_task_pass' not in m:
   rows[3]=('',cell(t('task_result', 'Task result'))+cell(result_label(r,c)))
   if c.get('source_benchmark',{}).get('verification_scope')=='first_three_name_detail_pairs':
    rows.append(('',cell(t('name_and_link_identity_check', 'Name-and-link identity check'))+cell(t('confirmed_this_does_not_establish_factual_accuracy', '{get} confirmed; this does not establish factual accuracy or currency.', get=m.get('confirmed_entries', 0)))))
   rows.append(('',cell(t('named_detail_records', 'Named detail records'))+cell(str(m.get('named_entry_count',0)))))
  if 'stable_task_pass' in m:
   for label,key in [(t('stable_content_check', 'Stable-content check'),'stable_content_pass'),(t('record_structure_check', 'Record-structure check'),'stable_structure_pass')]:
    no_checks=key=='stable_content_pass' and not (m.get('stable_anchor_results') or m.get('stable_page_pattern_results'))
    rows.append(('',cell(label)+cell(t('not_configured', '— (not configured)') if no_checks else '—' if m.get(key) is None else t('pass_2', 'Pass') if m[key] else t('fail_2', 'Fail'))))
   for label,key in [(t('structure_type', 'Structure type'),'stable_task_schema'),(t('records_found', 'Records found'),'stable_record_count'),(t('complete_records', 'Complete records'),'stable_complete_record_count'),(t('complete_unique_records', 'Complete unique records'),'stable_unique_record_count'),(t('declared_minimum_records', 'Declared minimum records'),'stable_minimum_records'),(t('declared_maximum_records', 'Declared maximum records'),'stable_maximum_records')]:
    rows.append(('',cell(label)+cell(esc(m.get(key) if m.get(key) is not None else '—'))))
  elif 'effective_entries' in m:
   minimum=m.get('minimum_entries',m.get('minimum'))
   rows.append(('',cell(t('valid_entries_declared_minimum', 'Valid entries / declared minimum'))+cell(esc(m['effective_entries'])+' / '+esc(minimum if minimum is not None else t('not_provided', 'Not provided')))))
  rows.extend(('',cell(k)+cell(f'<code class="break">{esc(v)}</code>')) for k,v in [(t('run_id', 'Run ID'),r.get('run_id') or execution_id(r)),(t('execution_record', 'Execution record'),r['attempt_id']),(t('started_utc', 'Started (UTC)'),r.get('started_at','—')),(t('provider_request_id', 'Provider request ID'),r.get('provider_metadata',{}).get('request_id') or t('not_returned', 'Not returned'))])
  scoring=r.get('scoring') or {}
  for label,key in [(t('scoring_method', 'Scoring method'),'mode'),(t('scored_at', 'Scored at'),'scored_at'),(t('rule_checksum', 'Rule checksum'),'rule_sha256')]:
   if scoring.get(key) is not None:
    value=t('offline_scoring_of_the_saved_output', 'Offline scoring of the saved output') if key=='mode' and scoring[key]=='offline_rescore' else scoring[key]
    rows.append(('',cell(label)+cell(esc(value))))
  checks=[('',cell(esc(x['id']))+cell(esc(' / '.join(x.get('alternatives',x.get('expected_urls',[])))))+cell(t('matched', 'Matched') if x['pass'] else t('entry_not_fully_matched', 'Entry not fully matched') if x.get('match_scope')=='entry' else t('missing', 'Missing'))+cell(esc(' / '.join(x.get('matched',x.get('matched_urls',[])))))) for x in m.get('fact_results',[])+m.get('resource_results',[])]
  if c.get('task_kind')=='structure' and 'stable_task_pass' not in m:
   checks=[('',cell(link(x['url'],x['title']))+cell(esc(' / '.join(x['reference_titles']) or t('no_matching_independent_name_evidence', 'No matching independent name evidence')))+cell(t('confirmed', 'Confirmed') if x['verdict']=='confirmed' else t('access_restricted_unconfirmed', 'Access restricted · unconfirmed') if x.get('access_restricted') else t('unconfirmed', 'Unconfirmed'))+cell(esc(' · '.join(filter(None,[x.get('evidence_checked_at'),x.get('access_note')]))))) for x in m.get('list_checks',[])]
  caveat=''
  if c.get('track')=='dynamic':caveat=('<p class="source-note">' + t('judged_by_the_rules_actually_registered_for', 'Judged by the rules actually registered for this task. "Entry not fully matched" means no complete field combination was found, not that every field is absent. Stable titles, sections and descriptions are checked against registered content; frequently changing lists and prices are checked by field structure, type, count and uniqueness, not pinned to old snapshot values. Full-text metrics are computed separately only with a complete, comparable reference and do not prove changing values accurate or current.') + '</p>')
  if c.get('task_kind')=='structure':
   caveat='<p class="source-note">'+esc(list_rule_description(c))+link('../datasets/'+c['id']+'.html',t('rules_and_independent_reference', 'Rules and independent reference'))+'</p>'
  if 'stable_task_pass' in m:
   caveat=('<p class="source-note">' + t('this_task_is_judged_jointly_on_stable', 'This task is judged jointly on stable content and record structure. Full-text metrics require a complete comparable reference and cannot be inferred from record counts.') + '</p>')
  detail_metrics=[t('request', 'Request'),t('result', 'Result'),t('elapsed_time', 'Elapsed time'),t('http_status', 'HTTP status'),t('output_characters', 'Output characters'),t('output_checksum', 'Output checksum')]+([t('named_detail_records', 'Named detail records')] if c.get('task_kind')=='structure' else [t('recall', 'Recall'),t('precision', 'Precision'),'F1'] if c.get('ground_truth_main_content') else [])
  state=result_label(r,c);passed=succeeded(r,c)
  provider_meta=r.get('provider_metadata',{})
  route_english=None
  if provider_meta.get('document_extractor'):
   route_label=t('route_the_method_obtains_the_original_document', 'Route: the method obtains the original document directly → {document_extractor} → readable body text', document_extractor=provider_meta['document_extractor'])
  elif c.get('task_kind')=='file':
   route_label=t('retrieval_path_method_obtains_original_file_format', 'Retrieval path: method obtains original file → format, length, and SHA-256 validation')
  elif provider_meta.get('output_contract') in report_method_catalog()['output_contract_labels']:
   contract=provider_meta['output_contract']
   route_label=report_method_catalog()['output_contract_labels'][contract].format(method=provider_label(r['provider']))
   route_english=report_method_catalog('en')['output_contract_labels'][contract].format(method=browser_method_label(r['provider'],language='en'))
  else:
   extractor=str(provider_meta.get('extractor') or '')
   route_label=(t('retrieval_path', 'Retrieval path: ') + f"{ROUTE_LABELS.get(extractor, extractor or t('tested_method_returns_body_text_directly', 'Tested method returns body text directly'))}")
   route_english='Retrieval path: '+report_method_catalog('en')['route_labels'].get(extractor,extractor or 'Text returned directly by the method')
  delivered_summary=provider_meta.get('file_evaluation',{}).get('files',[])
  actual_summary=(f'实际交付：{len(delivered_summary)} 个文件、{sum(x.get("bytes",0) for x in delivered_summary):,} 字节'
                  if c.get('task_kind')=='file' else f'实际输出：{len(output):,} 字符')
  hero_meta=(('<strong>' + t('task_id_2', 'Task ID: ') + f"""<code>{esc(c['id'])}</code></strong><br>{esc(task_instruction(c))}<span class="meta"><span class="pill">""" + t('method_2', 'Method: {model_text}', model_text=model_text(provider_label(r['provider']), browser_method_label(r['provider'], language='en'))) + '</span><span class="pill">' + t('run_id_2', 'Run ID: {value}', value=esc(r.get('run_id') or execution_id(r))) + '</span></span>'))
  summary=((f"""<div class="attempt-verdict {('' if passed or not measured(r, c) else 'failure')}"><span class="status-line">{esc(state)}</span><p>{esc(reason(r, c) if passed or not measured(r, c) else (r.get('diagnosis') or {}).get('category') or t('task_requirements_not_met', 'Task requirements not met'))}</p><p class="source-note">""" + t('method_3', 'Method: ') + f"{link('../providers/' + r['provider'] + '.html', provider_label(r['provider']), browser_method_label(r['provider'], language='en'))} · 请求：{(t('succeeded', 'Succeeded') if r['ok'] else t('failed', 'Failed'))} · 耗时：{ms(r['elapsed_ms'])} · {actual_summary}</p></div>"))
  summary+=f'<p class="source-note">{model_text(route_label,route_english)}</p>'
  summary+=(('<p class="actions"><a href="#evidence-reference">' + t('view_the_reference_used_for_scoring', 'View the reference used for scoring') + '</a><a href="#evidence-output">' + t('view_the_complete_saved_output', 'View the complete saved output') + '</a><a href="#decision">' + t('view_item_level_verdicts', 'View item-level verdicts') + '</a><a href="#run-log">' + t('view_run_logs_and_evidence', 'View run logs and evidence') + '</a></p>'))
  if r.get('diagnosis') and not passed and measured(r,c):
   d=r['diagnosis'];s=d.get('independent_source_check') or {}
   quote_issue=_quote_field_issue(m)
   facts=[((t('report_failure_class_af3c34', 'Failure class')),(t('report_field_boundaries_lost_6378fd', 'Field boundaries lost')) if quote_issue else d['category']),
          ((t('report_stage_79f15e', 'Stage')),(t('report_field_parsing_71fbcc', 'Field parsing')) if quote_issue else d['stage']),
          ((t('report_decision_basis_8ad25d', 'Decision basis')),reason(r,c)),((t('report_attribution_boundary_970403', 'Attribution boundary')),d['certainty'])]
   if s:facts.extend([((t('report_independent_source_page_check_4a36d6', 'Independent source-page check')),(f"{s.get('checked_at', '')} · HTTP {s.get('status', t('report_unavailable_e7954a', 'Unavailable'))} · {s.get('title') or s.get('error', '')}")),((t('report_recheck_boundary_a5810d', 'Recheck boundary')),d['source_check_boundary'])])
   summary+=('<details class="technical-details">' + '<summary>' + t('report_view_failure_class_and_attribution_boundary_b4f55e', 'View failure class and attribution boundary') + '</summary>')+table([(t('report_field_79f326', 'Field')),(t('report_evidence_2e9f01', 'Evidence'))],[('',cell(esc(k))+cell(esc(v))) for k,v in facts],metrics=[])+"</details>"
  body=hero(esc(c['name']),hero_meta)+section((t('report_conclusion_14163a', 'Conclusion')),summary,'overview')
  if r.get('report_superseded_history'):
   history_rows=[]
   for old in r['report_superseded_history']:
    history_rows.append(('',cell(esc(old.get('started_at') or '—'))+cell(link('../'+old['record_path'],saved_matrix_text('record_link')))+cell(link('../'+old['answer_path'],saved_matrix_text('answer_link')))+cell(link('../'+old['scope_path'],saved_matrix_text('history_scope')))))
   body+=section(saved_matrix_text('history_title'),'<p>'+saved_matrix_text('history_note')+'</p>'+table([(t('report_time_8b6ff4', 'Time')),(t('report_record_9fad24', 'Record')),(t('report_return_572cf4', 'Return')),(t('report_scope_ae72b5', 'Scope'))],history_rows,metrics=[]),'superseded-history')
  if r.get('report_completion_note'):
   body+=section(model_text(*pair('report_completion_note_02b01a', 'Completion note')),'<p>'+esc(r['report_completion_note'])+'</p>','completion-note')
  if r.get('report_parameter_rerun'):
   proof=r['report_parameter_rerun']
   evidence=[]
   for label in ('before','candidate'):
    evidence.append(esc(saved_matrix_text('parameter_rerun_'+label))+': '+link('../'+proof[label+'_record_path'],saved_matrix_text('record_link'))+' · '+link('../'+proof[label+'_answer_path'],saved_matrix_text('answer_link')))
   if proof.get('candidate_prior_record_path'):
    evidence.append(link('../'+proof['candidate_prior_record_path'],saved_matrix_text('continuation_prior'))+' · '+link('../'+proof['candidate_prior_answer_path'],saved_matrix_text('answer_link')))
   outcome=saved_matrix_text('parameter_rerun_selected' if proof['selected_new'] else 'parameter_rerun_retained')
   outcome+=' · '+saved_matrix_text('parameter_rerun_group', **proof['group'])
   family_selection=proof['selection']=='replace_family_if_pass_count_improves'
   if family_selection:
    outcome+=' · '+saved_matrix_text('parameter_family_eligibility', **{label:saved_matrix_text('parameter_eligible' if proof['group'][label+'_uniform'] else 'parameter_ineligible') for label in ('before','candidate')})
    retry_valid=proof['group'].get('before_retry_policy_valid')
    retry_label='parameter_eligible' if retry_valid is True else 'parameter_ineligible' if retry_valid is False else 'parameter_unverified'
    outcome+=' · '+saved_matrix_text('parameter_baseline_retry_eligibility', valid=saved_matrix_text(retry_label))
   body+=section(saved_matrix_text('parameter_rerun_title'),'<p>'+saved_matrix_text('parameter_family_note' if family_selection else 'parameter_rerun_note')+'</p><p>'+outcome+'</p><p>'+'<br>'.join(evidence)+'</p>','parameter-rerun')
  if r.get('report_retry_continuation'):
   continuation=r['report_retry_continuation']
   cost=ms(continuation['execution_cost_ms']) if continuation['execution_cost_ms'] is not None else saved_matrix_text('continuation_unknown_cost')
   evidence=link('../'+continuation.get('prior_record_path',continuation.get('prior_uncommitted_path')),saved_matrix_text('continuation_prior'))
   if continuation.get('prior_answer_path'):evidence+=' · '+link('../'+continuation['prior_answer_path'],saved_matrix_text('answer_link'))
   body+=section(saved_matrix_text('continuation_title'),'<p>'+saved_matrix_text('continuation_note')+'</p><p>'+saved_matrix_text('continuation_cost')+esc(cost)+'</p>'+evidence,'retry-continuation')
  body+=section((t('report_target_coverage_and_additional_content_e66c3d', 'Target coverage and additional content')),render_quality_observations(r,c,output,esc=esc),'quality-diagnostics')
  diagnostic_view=r.get('provider_metadata',{}).get('public_result_diagnostics_receipt')
  if diagnostic_view:
   body+='<p class="source-note">'+t('result_diagnostics_redacted_note', 'Quoted output and resource-URL diagnostics are redacted in this public copy. Scores, counts, timings and verdicts are unchanged; original records remain private.')+' '+link('../'+diagnostic_view,t('result_diagnostics_view_receipt', 'Original/public diagnostic verification record'))+'</p>'
  reviewed=audited_attempts.get(r['attempt_id'])
  if reviewed:
   independent={'pass':t('pass_2', 'Pass'),'fail':t('fail_2', 'Fail'),'insufficient':t('insufficient_evidence', 'Insufficient evidence')}.get(reviewed.get('agent_verdict'),t('not_recorded', 'Not recorded'))
   final={'pass':t('pass_2', 'Pass'),'fail':t('fail_2', 'Fail'),'unavailable':t('undecidable', 'Undecidable')}.get(reviewed.get('final_verdict'),t('not_adjudicated_the_current_score_stands', 'Not adjudicated; the current score stands'))
   review_rows=[
    (t('evidence_audit_verdict', 'Evidence audit verdict') if audit.get('review_protocol',{}).get('machine_judgments_withheld') is False else t('independent_review_verdict', 'Independent review verdict'),independent),
    (t('direct_evidence', 'Direct evidence'),reviewed.get('agent_evidence') or '—'),
    (t('final_verdict', 'Final verdict'),final),
   ]
   adjudication_en=None
   if reviewed.get('adjudication_evidence'):
    proof=reviewed['adjudication_evidence']
    adjudication_en=proof.get('reason_en')
    if not adjudication_en and proof.get('rule_assertions'):
     adjudication_en=adjudication_reason(proof['rule_assertions'],language='en')
    review_rows.append((t('disagreement_adjudication', 'Disagreement adjudication'),proof.get('summary_zh',reviewed['adjudication'])))
   body+=section(t('known_outcome_evidence_audit', 'Evidence audit with known machine outcomes') if audit.get('review_protocol',{}).get('machine_judgments_withheld') is False else t('independent_result_review', 'Independent result review'),table(
       [t('field_2', 'Field'),t('review_record', 'Review record')],
       [('',cell(esc(label))+cell(
           model_text(value,adjudication_en) if label==t('disagreement_adjudication', 'Disagreement adjudication')
           else '<span translate="no">'+esc(value)+'</span>' if label==t('direct_evidence', 'Direct evidence') else esc(value))) for label,value in review_rows],
       metrics=[]),'independent-review')
   if reviewed.get('adjudication_evidence',{}).get('summary_zh'):
    body+='<details><summary>'+model_text(*pair('full_adjudication_record_original', 'Full adjudication record (original)'))+'</summary><pre translate="no">'+esc(reviewed['adjudication'])+'</pre></details>'
  failure_review=failure_reviews.get(r['attempt_id'])
  if failure_review:
   cause_body='<p><strong>'+esc(failure_review['category_label'])+'</strong></p><p>'+esc(failure_review['reason'])+'</p>'
   cause_body+=('<p>' + t('resolution', 'Resolution: {remediation_status}', remediation_status=esc(failure_review['remediation_status'])) + f"</p><p>{link('../guide/index.html#browser-' + r['provider'], t('similar_failures_and_topic_analysis', 'Similar failures and topic analysis'))}</p>")
   cause_body+=('<details><summary>' + t('topic_review_basis', 'Topic review basis') + f"</summary><ul>{''.join(('<li>' + link('../' + item['path'], '证据 ' + str(index)) + '</li>' for index, item in enumerate(failure_review['evidence_paths'], 1)))}</ul></details>")
   body+=section(t('topic_check_of_failure_causes', 'Topic check of failure causes'),cause_body,'failure-review')
  reference_heading=t('complete_reference_answer', 'Complete reference answer') if c.get('task_kind')!='structure' else t('reference_and_acceptance_contract', 'Reference and acceptance contract')
  reference_download='../references/'+c['id']+'.txt'
  source_body=f'<pre data-kind="reference">{esc(ref)}</pre>'
  source_meta=f"{len(ref):,} 字符 · {link(reference_download, t('download_complete_reference', 'Download complete reference'))}"
  if not (c.get('ground_truth_main_content') or c.get('reference_answer')) and c.get('task_kind','body')=='body':
   reference_heading=t('acceptance_rules_no_full_text_reference', 'Acceptance rules (no full-text reference)')
   source_meta=t('full_text_recall_precision_and_f1_do', '{link}; full-text recall, precision and F1 do not apply.', link=link(reference_download, t('download_acceptance_rules', 'Download acceptance rules')))
  if (c.get('track','web')=='web' and c.get('task_kind','body')=='body'
      and c.get('required_facts') and not c.get('acceptance_contract')):
   rules={key:c[key] for key in ('min_content_chars','expected_any','required_facts',
          'fact_coverage_threshold','required_patterns','body_requirements') if key in c}
   source_body=required_text_answer_panel(c)
   if c.get('ground_truth_main_content') or c.get('reference_answer'):
    source_body+=('<h4>' + t('complete_reference_body', 'Complete reference body') + f'</h4><pre data-kind="reference">{esc(ref)}</pre>')
   source_body+=(('<details class="technical-details"><summary>' + t('view_raw_acceptance_rules', 'View raw acceptance rules') + f'</summary><pre data-kind="contract">{esc(json.dumps(rules, ensure_ascii=False, indent=2))}</pre></details>'))
  delivered=r.get('provider_metadata',{}).get('file_evaluation',{}).get('files',[])
  if 'stable_task_pass' in m and c.get('acceptance_contract'):
   reference_download='../contracts/'+c['id']+'.json'
   contract_text=json.dumps(machine_contract(c),ensure_ascii=False,indent=2)
   source_body=((f"""{stable_answer_panel(c['acceptance_contract'])}{(composite_contract_details(c['composite_acceptance']) if c.get('composite_acceptance') else '')}<details class="technical-details"><summary>""" + t('view_machine_readable_contract', 'View machine-readable contract') + f'</summary><pre data-kind="contract">{esc(contract_text)}</pre></details>'))
   source_meta=(t('the_required_text_and_record_counts_are', 'The required text and record counts are shown directly; readers do not need to inspect JSON.')
                if c['acceptance_contract'].get('stable_text_role','required')!='diagnostic' else
                t('this_section_lists_the_body_acceptance_rules', 'This section lists the body acceptance rules and text used to check retrieval scope; scope-check text does not affect acceptance.'))
  corruption=''
  if m.get('text_encoding_corruption'):
   corruption=(('<p class="callout failure encoding-warning"><b>' + t('the_returned_text_contains_decoding_corruption', 'The returned text contains decoding corruption:') + '</b>' + t('contains_characters_of_it_these_characters_were', 'Contains {get:,} � characters, {get2} of it. These characters were lost before saving and cannot be restored from the current Markdown.', get=m.get('replacement_character_count', 0), get2=pct(m.get('replacement_character_ratio'))) + '</p>'))
  answer_view=r.get('provider_metadata',{}).get('publication_views',{}).get('scored_answer')
  output_note=(f"{len(output):,} 字符 · {link('../' + r['artifact_path'], t('download_raw_output', 'Download raw output'))}")
  if answer_view and answer_view['original_sha256']!=answer_view['published_sha256']:
   output_note=(f"{len(output):,} 字符 · {link('../' + answer_view['path'], t('download_public_copy', 'Download public copy'), 'Download public view')} · {link('../' + answer_view['receipt'], t('view_redacted_record', 'View redacted record'), 'View redaction record')}<br>{model_text(*pair('paths_or_sensitive_values_are_hidden_scores', 'Paths or sensitive values are hidden; scores come from the original. Original/public hashes are listed in the run fields.'))}")
  if r.get('scoring',{}).get('mode')=='published_snapshot_rescore':
   view=r['scoring']['artifact_view']
   view_label={'original':t('saved_output_identical_to_the_original_hash', 'Saved output identical to the original hash'),'recovered_original':t('output_restored_by_the_original_hash_this', 'Output restored by the original hash (this page shows the public copy)'),'published_projection':t('public_redacted_copies', 'Public redacted copies')}[view]
   output_note=t('characters_this_scoring_input', '{count:,} characters · this scoring input: {view_label} · {link}', count=len(output), view_label=esc(view_label), link=link('../' + r['artifact_path'], t('download_public_output', 'Download public output')))
   output_note+=('<br>' + t('original_call_output_sha_256', 'Original call output SHA-256:') + f"""<code class="break">{esc(r['scoring']['original_content_sha256'])}</code><br>""" + t('this_scoring_input_sha_256', 'This scoring input SHA-256:') + f"""<code class="break">{esc(r['scoring']['scored_content_sha256'])}</code>""")
  if readable_output(output)!=output:output_note+=' · 控制字节在页面中显示为 \\xNN'
  actual_body=(corruption+f'<pre data-kind="output">{esc(readable_output(output))}</pre>' if output else
               f'<div class="call-log-empty">{esc(empty_output_reason(r,c,output))}</div>')
  if c.get('task_kind')=='file':
   reference_heading=t('target_file', 'Target file')
   expected_hash=c.get('source_verification',{}).get('file_sha256') or t('not_registered', 'Not registered')
   source_meta=t('this_task_validates_original_file_bytes_markdown', 'This task validates original-file bytes; Markdown is not required.')
   source_body=(('<div class="answer-list"><div class="answer-item"><b>' + t('file_requirements', 'File requirements') + f'</b><blockquote translate="no">{esc(ref)}</blockquote></div><div class="answer-item"><b>' + t('target_sha_256', 'Target SHA-256') + f'</b><code class="break">{esc(expected_hash)}</code></div></div>'))
   if delivered:
    actual_rows=[('',cell(esc(f.get('mime') or t('unknown_format', 'Unknown format')))+cell(f'{f.get("bytes",0):,}')+
                  cell(f'<code class="break">{esc(f.get("sha256"))}</code>')+cell(t('match', 'Match') if f.get('pass') else t('not_matched_2', 'Not matched'))) for f in delivered]
    actual_body=table([t('detected_format', 'Detected format'),t('bytes_2', 'Bytes'),'SHA-256',t('target_check', 'Target check')],actual_rows,metrics=[])
    output_note=f"实际交付 {len(delivered)} 个文件 · {link('../' + delivered[0]['artifact_path'], t('download_delivered_file', 'Download delivered file'))}"
   else:
    actual_body=('<p class="empty">' + t('this_call_did_not_deliver_verifiable_original', 'This call did not deliver verifiable original-file bytes.') + '</p>')
    output_note=t('0_files_delivered', '0 files delivered')
  returned_fields=_returned_answer_fields(provider_meta)
  if returned_fields:
   actual_body+=('<details id="returned-fields"><summary>'+model_text('查看随正文返回的字段','View fields returned with the text')
                 +'</summary><pre translate="no">'+esc(json.dumps(returned_fields,ensure_ascii=False,indent=2))+'</pre></details>')
  reference_scope=('' if not (c.get('track')=='document' and 0 < len(ref) < 100) else
                   ('<p class="callout"><b>' + t('reference_scope', 'Reference scope:') + '</b>' + t('this_is_a_minimal_document_parsing_sample', 'This is a minimal document-parsing sample. All extractable body text of the frozen source is {count:,} characters, and the reference omits nothing. It only verifies short document text extraction, not multi-page, table or appendix completeness.', count=len(ref)) + '</p>'))
  evidence_intro=t('the_left_side_is_the_reference_or', 'The left side is the reference or explicit acceptance contract used for the verdict. The right side is the complete output saved from this call. Neither side is a summary; long content scrolls within its pane.')
  if answer_view and answer_view['original_sha256']!=answer_view['published_sha256']:
   evidence_intro=model_text(*pair('the_left_pane_contains_the_scoring_reference', 'The left pane contains the scoring reference or acceptance rules. The right pane contains the public output view with paths or sensitive values hidden. Scores retain the original verdict; original and public hashes are identified separately. Long content scrolls within each pane.'))
  evidence=((f'<p class="evidence-intro">{evidence_intro}</p>{reference_scope}<div class="evidence-compare"><article class="evidence-pane" id="evidence-reference"><header><h3>{esc(reference_heading)}</h3><p>{source_meta}</p></header><div class="pane-body">{source_body}</div></article><article class="evidence-pane" id="evidence-output"><header><h3>' + t('actual_output', 'Actual output') + f'</h3><p>{output_note}</p></header><div class="pane-body">{actual_body}</div></article></div>'))
  body+=section(t('reference_and_actual_output', 'Reference and actual output'),evidence,'evidence')
  checks_html=''
  if 'stable_task_pass' in m:
   checks_html+=stable_anchor_evidence(m,output,provider_meta,r.get("final_url") or c["url"],c.get("acceptance_contract"))
   checks_html+=(('<p class="decision-lead"><b>' + t('structure_result', 'Structure result:') + f"</b>实际解析出 {m.get('stable_unique_record_count', 0)} 条完整且不重复的记录，要求至少 {m.get('stable_minimum_records', '—')} 条；结构检查{(t('pass_2', 'Pass') if m.get('stable_structure_pass') else t('fail_2', 'Fail'))}，稳定文字检查{(t('pass_2', 'Pass') if m.get('stable_content_pass') else t('fail_2', 'Fail'))}。</p>"))
   checks_html+=('<details class="technical-details"><summary>' + t('view_all_parsed_records_and_machine_check', 'View all parsed records and machine-check fields') + f'</summary>{stable_checks(m)}</details>')
   checks_html+=composite_result_evidence(m)
  else:
   checks_html+=(table([t('check_2', 'Check'),t('reference_answer', 'Reference answer'),t('verdict', 'Verdict'),t('observed_match', 'Observed match')],checks) if checks else '<p class="decision-lead">'+(t('this_case_is_judged_against_the_text', 'This case is judged against the text acceptance rules on the left.') if c.get('task_kind','body')=='body' else t('this_task_is_judged_by_its_record', 'This task is judged by its record structure or original-file output.'))+'</p>')
   checks_html+=''.join(f'<p>{esc(x["id"])}：实际 {x["count"]}，要求至少 {x["minimum"]}。</p>' for x in m.get('pattern_results',[]))
  if c.get('task_kind')=='resource' and m.get('resource_candidates'):
   unexpected=set(m.get('unexpected_resource_urls',[]))
   candidate_rows=[('',cell(f'<code class="break">{esc(item["url"])}</code>')
                    +cell(esc('、'.join(item.get('sources',[]))))
                    +cell(t('unexpected_resources', 'Unexpected resources') if item['url'] in unexpected else t('target_or_allowed_variant', 'Target or allowed variant')))
                   for item in m['resource_candidates']]
   checks_html+=table([t('candidate_resource_url', 'Candidate resource URL'),t('answer_field_source', 'Answer-field source'),t('scope_decision', 'Scope decision')],candidate_rows,metrics=[])
  if m.get('stable_field_results'):
   checks_html+=table([t('record_fields', 'Record fields'),t('valid_records', 'Valid records'),t('at_least', 'At least'),t('verdict', 'Verdict')],[('',cell(esc(x['field']))+cell(str(x['count']))+cell(str(x['minimum']))+cell(t('pass_2', 'Pass') if x['pass'] else t('fail_2', 'Fail'))) for x in m['stable_field_results']],metrics=[])
  body+=section(t('why_this_verdict', 'Why this verdict'),caveat+checks_html,'decision')
  if m.get('body_region_results'):
   scoring_text=_body_scoring_text(r,output)
   region_rows,region_cards=body_region_rows(c,m,scoring_text,frozen_scores=frozen_scores);rules=c.get('body_requirements',{})
   required=rules.get('minimum_region_passes',len(rules.get('regions',[])))
   region_count=len(rules.get('regions',[]))
   region_intro=(('<div class="decision-lead"><b>' + t('body_text_rule', 'Body-text rule: ') + f"</b>本例分为 {region_count} 段。每段至少达到 {pct(rules.get('minimum_floor', rules.get('minimum_coverage')))}，其中至少 {required} 段达到 {pct(rules.get('minimum_coverage'))}。先看下表；需要核对原文时，再展开下面的左右对照。</div>"))
   if frozen_scores:
    region_intro+='<p class="source-note">'+model_text(*pair('this_section_retains_the_values_and_passages', 'This section retains the values and passages saved with the score. Sensitive values are hidden in public passages. Missing historical passages are not reconstructed with current rules, and public copies are not used to recalculate original metrics.'))+'</p>'
   body+=section(t('body_coverage', 'Body coverage'),(f"""{region_intro}{table([t('reference_region', 'Reference region'), t('reference_shingles', 'Reference shingles'), t('matched_shingles', 'Matched shingles'), t('coverage', 'Coverage'), t('verdict', 'Verdict')], region_rows, metrics=[])}<details class="technical-details"><summary>""" + t('view_complete_reference_text_and_scoring_lines', 'View complete reference text and scoring lines for each region') + f"</summary>{''.join(region_cards)}</details>"),'body-coverage')
  if title_details:body+=section(t('title_text_quality', 'Title-text quality'),title_details)
  runtime=(('<details class="technical-details"><summary>' + t('view_complete_run_hashes_and_scoring_fields', 'View complete run, hashes, and scoring fields') + f"</summary>{metric_legend(detail_metrics)}{table([t('field_2', 'Field'), t('value', 'Value')], rows, metrics=[])}<details><summary>" + t('public_fields_returned_by_the_adapter', 'Public fields returned by the adapter') + f"""</summary><pre data-kind="metadata">{esc(json.dumps(public_provider_metadata(r.get('provider_metadata', {})), ensure_ascii=False, indent=2))}</pre></details></details><p class="actions">{link(c['url'], t('open_source_page', 'Open source page ↗'))}{link('../datasets/' + c['id'] + '.html', t('view_all_methods_for_this_task', 'View all methods for this task'))}</p>"""))
  body+=section(t('run_and_scoring_details', 'Run and scoring details'),runtime,'run-info')
  body+=run_log_section(out,r,output)
  if r.get('provider_metadata',{}).get('rendered_html_path'):
   body+=section(t('browser_html_evidence', 'Browser HTML evidence'),'<p>'+link('../'+r['provider_metadata']['rendered_html_path'],t('download_the_complete_pre_conversion_dom_html', 'Download the complete pre-conversion DOM HTML'))+'</p>')
  if r.get('provider_metadata',{}).get('raw_stdout_path'):
   metadata=r['provider_metadata'];extractor=metadata.get('document_extractor')
   if extractor:
    body+=section(t('original_document_evidence', 'Original-document evidence'),('<p>' + t('the_body_text_was_extracted_by', '{link}. The body text was extracted by', link=link('../' + metadata['raw_stdout_path'], t('download_the_original_document_obtained_by_the', 'Download the original document obtained by the method'))) + f' <code>{esc(extractor)}</code> ' + t('within_the_same_call_s_timing_budget', ' within the same call\'s timing budget; the "Actual response" above is the text sent to the shared scorer.') + '</p>'))
   else:
    body+=section(t('original_response_evidence', 'Original-response evidence'),('<p>' + t('if_the_task_requires_body_text_and', '{link}. If the task requires body text and the response forms no readable text, it fails on the actual result.', link=link('../' + metadata['raw_stdout_path'], t('download_the_raw_response_bytes_the_method', 'Download the raw response bytes the method obtained directly'))) + '</p>'))
  if delivered:
   file_rows=[('',cell(link('../'+f['artifact_path'],t('download_delivered_file', 'Download delivered file')))+cell(esc(f.get('mime')))+cell(str(f['bytes']))+cell('<code class="break">'+esc(f['sha256'])+'</code>')+cell(t('match', 'Match') if f.get('pass') else t('not_matched_2', 'Not matched'))) for f in delivered]
   body+=section(t('original_file_byte_evidence', 'Original-file byte evidence'),table([t('file', 'File'),t('format_signature', 'Format signature'),t('bytes_2', 'Bytes'),'SHA-256',t('target_check', 'Target check')],file_rows,metrics=[]))
  write(out,'attempts/'+r['attempt_id']+'.html',c['name']+' · '+r['provider'],body)
 write(out,'analysis/index.html',t('analysis', 'Analysis'),analysis_body(records,cases,headers,tracks)+section(t('passing_latency_detail', 'Task-passing latency'),successful_final_call_latency(records,cases,prefix='../'),'passing-latency'))
 write_support(out,records,manifests,authored_interpretation=authored_interpretation)
 if score_audit:
  render_score_audit(out,score_audit,score_binding)
 if records and all(row.get('provider_type') == 'fixture' for row in records):
  write(out,'changes/index.html',t('about_this_example', 'About this example'),hero(t('synthetic_example', 'Synthetic example'),t('this_report_was_generated_by_a_local', 'This report was generated by a local test adapter and has no formal benchmark release history.')))
 elif not authored_interpretation:
  write_saved_changes(out)
 else:
  render_changes(out,cases,records,dataset)
 for relative in diagnostic_view_paths:
  with (out/relative).open(encoding='utf-8') as source_header:
   previous=BeautifulSoup(source_header.read(8192),'html.parser')
  heading=previous.find('h1');source_heading=previous.find('h2',string=t('record_source', 'Record source'))
  if heading is None or source_heading is None:raise ValueError('Saved diagnostic viewer header is unavailable: '+relative)
  note=source_heading.find_next('p')
  diagnostic_view_page(out,str(Path(relative).with_suffix('.txt')),heading.get_text(),note.get_text() if note else '')
 # Every Chinese route, including declared participants without calls, exists before translation.
 _write_uncalled_participants(out,model)
 if include_english:
  write_english_report(out,source_routes=english_routes)
 if presentation_only and (out/'report-languages.json').is_file():
  if ('en' in generated_languages(out)) != include_english:
   raise ValueError('Presentation refresh cannot change generated languages')
  if not include_english:
   (out/'en').mkdir(exist_ok=True)
   (out/'en/index.html').write_text(english_notice(), encoding='utf-8')
  if primary_report_language(out) == 'en':
   select_report_language(out, 'en')
  check_language_contract(out)
 else:
  write_language_contract(out,include_english=include_english)
 return out/'index.html'


def current_local_review_findings(out,dataset,records,audit):
 """Describe the current snapshot from bound calls, never cached release prose."""
 receipt=json.loads(read_evidence_binding(out,dataset['comparison']['task_replacement']).read_text(encoding='utf-8'))
 added=receipt['provider_calls_added']
 cases={c['id']:c for c in dataset['cases']}
 eligible=[r for r in records if measured(r,cases[r['case_id']])]
 passed=sum(succeeded(r,cases[r['case_id']]) for r in eligible)
 adjudicated=len(audit.get('adjudication_revision',{}).get('adjudicated_attempt_ids',[]))
 unreviewed=len(audit.get('unreviewed_attempt_ids',[]))
 rows=[
  (t('the_current_report_contains_results_reuse_historical', 'The current report contains {count} results: {inherited_results} reuse historical calls and {added} come from new calls. Original responses, call times and latencies are kept.', count=len(records), inherited_results=receipt['inherited_results'], added=added),
   f"The current report contains {len(records)} results: {receipt['inherited_results']} inherited calls and {added} new calls. Original answers, timestamps and latencies are retained."),
  (t('currently_pass_are_comparable_and_are_excluded', 'Currently {passed} pass, {count} are comparable and {value} are excluded. Pages and downloadable summaries use the same saved results.', passed=passed, count=len(eligible), value=len(records) - len(eligible)),
   f"Currently {passed} pass among {len(eligible)} eligible results; {len(records)-len(eligible)} are excluded. Pages and downloadable summaries use the same saved results."),
  (t('independent_review_records_retained_another_are_not', 'Independent review records retained: {get}/{count}; another {unreviewed} are not independently reviewed, {get2} of them machine passes. {adjudicated} supplemental adjudications are not called a new full blind review.', get=audit.get('reviewed_attempts', 0), count=len(records), unreviewed=unreviewed, get2=audit.get('accepted_unreviewed_passes', 0), adjudicated=adjudicated),
   f"Retained independent review covers {audit.get('reviewed_attempts',0)}/{len(records)} results. Another {unreviewed} are not independently reviewed, including {audit.get('accepted_unreviewed_passes',0)} machine passes. There are {adjudicated} supplemental adjudications; this is not a new full blind review."),
  (t('target_content_requirements_body_quality_and_diagnostic', 'Target-content requirements, body quality and diagnostic text are shown separately; scoring implementation hashes are on each result page, and diagnostic words alone do not decide a task.'),
   "Target requirements, body quality and diagnostic text are reported separately. Each result binds its scorer hash; diagnostic words alone do not determine acceptance."),
 ]
 if audit.get('retained_reviews'):
  rows[2] = (
   t('current_review_coverage_rechecked_under_current_requirements', 'Current review coverage {reviewed_attempts}/{count}: {fresh_reviewed_attempts} rechecked under current requirements this time; {retained_reviewed_attempts} reuse the original review. Before reuse, each positive requirement, saved response and conclusion was confirmed unchanged, and no old judgment relied on a removed restriction. Reuse does not count as a new blind review.', reviewed_attempts=audit['reviewed_attempts'], count=len(records), fresh_reviewed_attempts=audit['fresh_reviewed_attempts'], retained_reviewed_attempts=audit['retained_reviewed_attempts']),
   f"Current review covers {audit['reviewed_attempts']}/{len(records)} results: {audit['fresh_reviewed_attempts']} newly checked under current requirements; {audit['retained_reviewed_attempts']} original reviews retained. Retention verifies unchanged positive requirements, saved answers and verdicts, with no dependency on removed restrictions. Retention is not a fresh blind review.")
 supplements = audit.get('supplemental_reviews', [])
 if supplements and audit.get('retained_reviews'):
  fresh = [row for row in audit.get('attempts', []) if row.get('review_origin') == 'fresh']
  conflicts = sum(row.get('agent_verdict') != row.get('final_verdict') for row in fresh)
  if audit.get('review_protocol',{}).get('blind_initial_verdict') is False:
   rows.append((t('this_review_knew_the_program_verdict_and', 'This review knew the program verdict and checked each saved response, task requirement and native field; it is not an independent blind review and added no calls to tested pages. The original opinions of the earlier review are kept.'),
                'This round inspected saved answers, task requirements and native fields with known machine outcomes. It was not an independent blind review and added no benchmark webpage calls. Original opinions from retained reviews are preserved.'))
  else:
   rows.append((t('this_new_review_first_hid_the_program', 'This new review first hid the program verdict; the initial judgment disagreed with the program on {conflicts} items, adjudicated against original responses and task requirements. The original independent opinions are kept, and no web calls were added.', conflicts=conflicts),
                f'Fresh review withheld machine verdicts. {conflicts} initial disagreements were adjudicated against the original answers and task requirements. Initial independent opinions are preserved; no new webpage calls were added.'))
 elif supplements:
  supplement_rows = [json.loads(read_evidence_binding(out, item).read_text(encoding='utf-8')) for item in supplements]
  reviewed = sum(item['reviewed_attempts'] for item in supplement_rows)
  rows.append((t('new_calls_received_a_supplemental_item_by', '{reviewed} new calls received a supplemental item-by-item review matching the program; the machine summary was known beforehand, so this is not a blind review. A content pass does not guarantee runnable code; specific quality issues are in the review records.', reviewed=reviewed),
               f'{reviewed} new-call results received supplemental per-answer review and agree with the scorer. The machine summary was already known; this was not blind review. A content pass does not guarantee executable code; quality issues are retained in the review records.'))
 if local_pass_rate_ranking(dataset):
  rows.append((t('rank_follows_the_current_task_pass_rate', 'Rank follows the current task pass rate; latency is shown separately and does not affect rank. Historical calls did not complete full before-and-after source verification, so actual check dates and scope are kept and no source certification is claimed.'),
               'Ranks use current task pass rates. Latency is separate and does not determine rank. Historical calls lack complete before/after source verification; actual check dates and scope are retained without claiming source certification.'))
 return [{'text':zh,'text_en':en} for zh,en in rows]


def saved_adopted_revisions(out,matrix):
 """Collect visible row-bound revisions even without a root revision."""
 groups={}
 for row in matrix.get('rows',[]):
  path=row.get('score_revision',{}).get('receipt_path')
  if not path or path=='score-revision.json':continue
  receipt=json.loads(contained_path(out,path).read_text(encoding='utf-8'))
  key=json.dumps(receipt,sort_keys=True)
  group=groups.setdefault(key,{'path':path,'receipt':receipt,'rows':[]})
  group['rows'].append(row)
 return list(groups.values())


def saved_current_revision_scope(out,matrix):
 """Count only retained rows, including adopted revisions and excluding replaced calls."""
 changed=set()
 rescored=0
 for row in matrix.get('rows',[]):
  proof=row.get('score_revision')
  if not proof:continue
  rescored+=1
  before=json.loads(contained_path(out,proof['before_case_path']).read_text(encoding='utf-8'))
  current=json.loads(contained_path(out,row['case_path']).read_text(encoding='utf-8'))
  if before!=current:changed.add((row['suite'],row['case_id']))
 return len(changed),rescored


def write_saved_changes(out):
 """Render saved-result change history from its versioned revision evidence."""
 review_path=out/'result-review.json'
 review=json.loads(review_path.read_text(encoding='utf-8')) if review_path.is_file() else {}
 updates=''.join('<article class="review-round"><div class="round-label">'+esc(row.get('date',''))+'</div><div><h3>'+model_text(row.get('title',''),row.get('title_en'))+'</h3><p>'+model_text(row.get('fix',''),row.get('fix_en'))+'</p><p class="source-note">'+model_text(row.get('scope',''),row.get('scope_en'))+'</p></div></article>' for row in review.get('review_rounds',[]))
 matrix_path=out/'matrix.json'
 matrix=json.loads(matrix_path.read_text(encoding='utf-8')) if matrix_path.is_file() else {}
 adopted=saved_adopted_revisions(out,matrix)
 if adopted:
  count=sum(len(group['rows']) for group in adopted)
  updates+='<article class="review-round" data-adopted-revision-history><div><h3>'+model_text(*pair('report_scoring_revisions_of_adopted_results_1738a7', 'Scoring revisions of adopted results'))+'</h3><p>'+model_text(*pair('report_value_adopted_results_have_offline_scoring_revisions_original_06489c', '{value0} adopted results have offline scoring revisions; original scores and revision evidence are retained.', value0=count))+'</p><p>'+link('../review/index.html',(t('report_revision_basis_and_verdict_changes_419378', 'Revision basis and verdict changes')))+'</p></div></article>'
 reruns=[row['parameter_rerun'] for row in matrix.get('rows',[]) if row.get('parameter_rerun')]
 if reruns:
  note=saved_matrix_text('parameter_family_banner' if any(row['selection']=='replace_family_if_pass_count_improves' for row in reruns) else 'parameter_rerun_banner', evaluated=len(reruns),selected=sum(row['selected_new'] for row in reruns))
  updates+='<article class="review-round" data-parameter-history><div class="round-label">'+esc(saved_matrix_text('parameter_rerun_title'))+'</div><div><h3>'+esc(saved_matrix_text('parameter_rerun_title'))+'</h3><p>'+esc(note)+'</p><p>'+link('../analysis/index.html',(t('report_per_task_results_and_evidence_ddad92', 'Per-task results and evidence')))+'</p></div></article>'
 revision_path=out/'score-revision.json'
 if revision_path.is_file():
  count,results=saved_current_revision_scope(out,matrix)
  updates+='<article class="review-round"><div class="round-label">'+model_text(*pair('report_offline_revision_d6fff2', 'Offline revision'))+'</div><div><h3>'+model_text(*pair('report_rule_and_scorer_revisions_9bfdf8', 'Rule and scorer revisions'))+'</h3><p>'+model_text(*pair('report_value_cases_have_revised_rules_and_value_saved_5b3e4a', '{value0} cases have revised rules and {value1} saved results were rescored. Original rules, results and call evidence are retained; no provider calls were added.', value0=count, value1=results))+'</p><p>'+link('../review/index.html',(t('report_revision_basis_and_verdict_changes_419378', 'Revision basis and verdict changes')))+'</p></div></article>'
 if not updates:
  updates='<p>'+model_text(*pair('report_no_result_revisions_are_registered_for_this_report_87a325', 'No result revisions are registered for this report.'))+'</p>'
 write(out,'changes/index.html',(t('report_changelog_6a4481', 'Changelog')),hero((t('report_changelog_6a4481', 'Changelog')),saved_matrix_text('changes_scope'))+section((t('report_corrections_to_this_run_ff4ddd', 'Corrections to this run')),'<div class="review-rounds">'+updates+'</div>')+section((t('report_supporting_evidence_f9035c', 'Supporting evidence')),'<p>'+link('../review/index.html',(t('report_review_scope_and_corrections_e82445', 'Review scope and corrections')))+' · '+link('../analysis/index.html',(t('report_per_task_results_and_evidence_ddad92', 'Per-task results and evidence')))+' · '+link('../reproduce/index.html',(t('report_run_parameters_and_versions_be2f2a', 'Run parameters and versions')))+'</p>'))


def review_rounds_html(rounds):
 return ''.join(
  '<article class="review-round"><div class="round-label">'+t('round','Round {i}',i=i)+
  '<br><small>'+esc(row.get('date',''))+'</small></div><div><h3>'+model_text(row.get('title',''),row.get('title_en'))+'</h3>'+''.join(
   '<p><b>'+label+'</b>'+model_text(row.get(key,''),row.get(key+'_en'))+'</p>'
   for key,label in [('scope',t('scope_2','Scope:')),('finding',t('finding','Finding:')),
                     ('fix',t('correction','Correction:')),('pending',t('outstanding','Outstanding:'))])+ '</div></article>'
  for i,row in enumerate(rounds,1))


def write_review_page(out,records):
 revision_path=out/'score-revision.json'
 matrix=json.loads((out/'matrix.json').read_text(encoding='utf-8')) if (out/'matrix.json').is_file() else {}
 adopted=saved_adopted_revisions(out,matrix)
 if revision_path.is_file() or adopted:
  revision=json.loads(revision_path.read_text(encoding='utf-8')) if revision_path.is_file() else {
   'changed_cases':sorted({case for group in adopted for case in group['receipt'].get('changed_cases',[])}),
   'rescored_results':sum(len(group['rows']) for group in adopted)}
  revision_link='../score-revision.json' if revision_path.is_file() else '../'+adopted[0]['path']
  page=hero((t('report_review_record_efa815', 'Review record')),saved_matrix_text('offline_review_scope'))
  count,results=saved_current_revision_scope(out,matrix)
  page+=section((t('report_revision_scope_0a5c51', 'Revision scope')),'<p>'+model_text(*pair('report_in_the_current_report_value_cases_revised_and_adefb1', 'In the current report, {value0} cases revised and {value1} saved results rescored. Original rules, results, answers and call timings are retained.', value0=count, value1=results))+'</p><p>'+link(revision_link,(t('report_revision_basis_and_verdict_changes_419378', 'Revision basis and verdict changes')))+'</p>','current-revision-scope')
  audit_path=out/'result-review.json'
  if audit_path.is_file():
   audit=json.loads(audit_path.read_text(encoding='utf-8'))
   note=review_summary(out,'../')
   if audit.get('review_protocol',{}).get('machine_judgments_withheld') is False:
    note+='<p>'+t('known_outcome_audit_protocol', 'This evidence audit knew the machine outcomes. It checked saved responses, frozen requirements, native field selection and call evidence; it is not an independent blind review and added no provider calls.')+'</p>'
   note+='<p>'+link('../result-review.json',t('review_records', '{count}/{count2} · review records',count=audit.get('reviewed_attempts',0),count2=audit.get('total_attempts',0)))+'</p>'
   page+=section(t('current_status', 'Current status'),note,'current-result-review')
   if audit.get('review_rounds'):
    page+=section(t('review_rounds','Review rounds'),'<div class="review-rounds">'+review_rounds_html(audit['review_rounds'])+'</div>')
  if adopted:
   sections=[]
   for group in adopted:
    labels=', '.join(sorted({row['provider'] for row in group['rows']}))
    links=[link('../'+group['path'],(t('report_revision_rationale_10587d', 'Revision rationale')))]+[link('../'+path,name) for name,path in group['receipt'].get('review_files',{}).items()]
    sections.append('<p>'+esc(labels)+' · '+str(len(group['rows']))+' '+model_text(*pair('report_adopted_results_57e115', 'adopted results'))+' · '+' · '.join(links)+'</p>')
   page+=section((t('report_scoring_revisions_of_retained_results_16ea0e', 'Scoring revisions of retained results')),''.join(sections),'adopted-score-revisions')
  if revision.get('historical_scope_path'):
   page+='<p>'+model_text(*pair('report_these_counts_cover_only_the_current_report_population_401030', 'These counts cover only the current report population.'))+' '+link('../'+revision['historical_scope_path'],model_text(*pair('report_revision_evidence_for_the_full_historical_population_089216', 'Revision evidence for the full historical population')))+'</p>'
  steps=[]
  for step in revision.get('steps',[]):
   changes=step.get('task_verdict_changes',[])
   gained=sum(x['before'] is False and x['after'] is True for x in changes)
   lost=sum(x['before'] is True and x['after'] is False for x in changes)
   kind=model_text(*pair('report_rule_revision_4bfad0', 'Rule revision')) if step.get('rule_revision') else model_text(*pair('report_scorer_revision_7af741', 'Scorer revision'))
   steps.append(('', cell(kind)+cell(str(gained))+cell(str(lost))))
  if steps:
   page+=section(model_text(*pair('report_historical_scoring_transitions_c14734', 'Historical scoring transitions')),'<p>'+model_text(*pair('report_these_statistics_belong_to_the_historical_revision_records_6cea45', 'These statistics belong to the historical revision records and may include calls later replaced by whole-group selection. They are not net changes in the current report. Each step compares against its immediate predecessor; scorer revisions do not remove rules. The revision records retain the complete scope and evidence.'))+'</p>'+table([(t('report_revision_type_1a9815', 'Revision type')),(t('report_fail_pass_72c8c7', 'Fail → pass')),(t('report_pass_fail_3e61be', 'Pass → fail'))],steps,'score-revision-steps'))
  attachments=revision.get('review_files',{})
  if attachments:
   page+=section((t('report_complete_inventories_9d0567', 'Complete inventories')),'<p>'+' · '.join(link('../'+path,{'decisions':(t('report_per_case_retained_and_removed_rules_with_evidence_16177f', 'Per-case retained and removed rules with evidence')),'failures':(t('report_original_failed_checks_aa4b48', 'Original failed checks')),'parameters':(t('report_parameter_configuration_and_scope_c9b869', 'Parameter configuration and scope'))}.get(name,name)) for name,path in attachments.items())+'</p>')
  if attachments.get('decisions'):
   decisions=json.loads(contained_path(out,attachments['decisions']).read_text(encoding='utf-8'))
   rows=[]
   for decision in decisions:
    removed=[x for x in decision.get('rule_decisions',[]) if x.get('action',x.get('decision'))=='remove']
    labels=[' / '.join(x.get('alternatives',x.get('text',[]))) for x in removed]
    rows.append(('', ''.join(cell(esc(value)) for value in [decision['suite'],decision['case_id'],str(len(removed)),
                 '；'.join(labels) if labels else (t('report_original_rules_retained_aaf8ed', 'Original rules retained')),decision['decision_basis']])))
   page+=section((t('report_per_case_decisions_b7f78d', 'Per-case decisions')),table([(t('report_task_suite_863437', 'Task suite')),(t('report_task_6603e1', 'Task')),(t('report_rules_removed_eb1112', 'Rules removed')),(t('report_removed_requirements_ae3ca3', 'Removed requirements')),(t('report_evidence_2e9f01', 'Evidence'))],rows,'score-revision-decisions'))
  matrix=json.loads((out/'matrix.json').read_text(encoding='utf-8')) if (out/'matrix.json').is_file() else {}
  rows=[]
  revisions=[revision,*[group['receipt'] for group in adopted]]
  changed_cases={case for item in revisions for case in item.get('changed_cases',[])}
  changed_attempts={change['attempt_id'] for item in revisions for step in item.get('steps',[])
                    for change in step.get('task_verdict_changes',[])}
  for row in matrix.get('rows',[]):
   proof=row.get('score_revision')
   if proof:
    current=json.loads(contained_path(out,row['record_path']).read_text(encoding='utf-8'))
    if (row['suite']+'--'+row['case_id'] not in changed_cases
        and row['suite']+'--'+current['attempt_id'] not in changed_attempts):
     continue
    rows.append(('', ''.join([cell(esc(row['suite'])),cell(esc(row['case_id'])),cell(esc(row['provider'])),
                 cell(link('../'+proof['before_case_path'],(t('report_original_rules_3a92e4', 'Original rules')))),cell(link('../'+(proof.get('before_summary_path') or proof['before_record_path']),
                     t('historical_score_summary', 'Historical score summary') if proof.get('before_summary_path') else t('report_original_result_b302a6', 'Original result'))),
                 cell(link('../'+row['case_path'],(t('report_revised_rules_36d7de', 'Revised rules')))),cell(link('../'+row['record_path'],(t('report_rescored_result_f1f6a8', 'Rescored result'))))])))
  if rows:page+=section((t('report_before_and_after_evidence_de3fc6', 'Before and after evidence')),table([(t('report_task_suite_863437', 'Task suite')),(t('report_task_6603e1', 'Task')),(t('report_method_22b9f0', 'Method')),(t('report_original_rules_3a92e4', 'Original rules')),(t('report_original_result_b302a6', 'Original result')),(t('report_revised_rules_36d7de', 'Revised rules')),(t('report_rescored_result_f1f6a8', 'Rescored result'))],rows,'score-revision-evidence'))
  if matrix:
   page+='<p>'+link('../matrix.json',t('complete_revision_inventory','Complete revision sources, including historical score summaries'))+'</p>'
  write(out,'review/index.html',(t('report_review_record_efa815', 'Review record')),page)
  return
 path=out/'result-review.json'
 if not path.is_file():
  dataset_path=out/'dataset.json'
  cases={c['id']:c for c in json.loads(dataset_path.read_text(encoding='utf-8')).get('cases',[])} if dataset_path.is_file() else {}
  accepted=sum(task_passed(r['ok'],r['metrics'],cases.get(r['case_id'],{}).get('task_kind','body')) for r in records)
  coverage='<p data-review-scope="none">'+t('unreviewed_result_coverage', 'Independent review: 0/{total} results. {accepted} machine passes remain independently unreviewed.',total=len(records),accepted=accepted)+'</p>'
  write(out,'review/index.html',t('review_record', 'Review record'),hero(t('review_record', 'Review record'),t('no_independent_review_record_is_bound_to', 'No independent review record is bound to this build.'))+section(t('review_scope', 'Review scope'),coverage))
  return
 audit=json.loads(path.read_text(encoding='utf-8'));reviewer=audit.get('reviewer',{})
 dataset_path=out/'dataset.json'
 dataset=json.loads(dataset_path.read_text(encoding='utf-8')) if dataset_path.is_file() else {}
 if 'review_rounds' in audit:
  rounds=audit['review_rounds']
 else:
  metadata_path=repository_root()/'research/report-changes.json'
  if not metadata_path.is_file():
   raise ValueError('A reviewed report requires bound review rounds or legacy research/report-changes.json')
  rounds=require_review_rounds(json.loads(metadata_path.read_text(encoding='utf-8')))
 rounds_html=review_rounds_html(rounds)
 page=hero(t('review_record', 'Review record'),t('each_review_round_records_its_scope_findings', 'Each review round records its scope, findings, corrections, and unresolved items.'))
 reviewed_attempts=audit.get('attempts',[])
 disagreements=[row for row in reviewed_attempts
                if row.get('agent_verdict') != row.get('machine_verdict')]
 adjudicated=[row for row in disagreements if row.get('final_verdict') in {'pass','fail','unavailable'}]
 unresolved=audit.get('unresolved',0)
 def coverage(label,item):
  scope=item.get('review_protocol',{}).get('scope','all')
  if scope=='machine-failures':
   subject=t('review_failures_and_selected_passes', 'Machine failures and selected passes') if item.get('review_protocol',{}).get('additional_attempt_ids') else '机器失败'
   return (f'{label}{subject} {item.get("reviewed_attempts",0):,}/{item.get("expected_attempts",0):,}；'
           f'机器通过未逐条审查 {item.get("accepted_unreviewed_passes",0):,}')
  return f'{label}全量 {item.get("reviewed_attempts",0):,}/{item.get("total_attempts",item.get("expected_attempts",0)):,}'
 conclusion=(f"""<p class="callout success">{coverage('主结果', audit)}；代理分歧 {len(disagreements)}；已裁定 {len(adjudicated)}；未决 {unresolved}。</p>""")
 if audit.get('adjudication_revision'):
  conclusion=review_summary(out,'../',highlight=not local_pass_rate_ranking(dataset))
 page+=section(t('current_status', 'Current status'),conclusion)
 page+=result_ledger_section(out,records,'../')
 page+=section(t('review_rounds', 'Review rounds'),'<p>'+model_text(*pair('historical_rounds_retain_the_treatment_and_limits', 'Historical rounds retain the treatment and limits recorded at the time. Current scores, task scope and ranking state are described by the current summary on this page.'))+'</p><div class="review-rounds">'+rounds_html+'</div>')
 method=(('<details class="technical-details"><summary>' + t('review_protocol_and_reviewer', 'Review protocol and reviewer') + '</summary><p>' + t('reviewer', 'Reviewer:') + f"{esc(reviewer.get('agent', t('independent_agent', 'Independent agent')))}" + t('model', '; model: ') + f"{esc(reviewer.get('model', '—'))}" + t('reasoning_effort', '; reasoning effort: ') + f"{esc(reviewer.get('effort', '—'))}" + t('machine_verdicts_and_scores_are_hidden_during', '. Machine verdicts and scores are hidden during review; only the task contract, reference answer, and complete actual output are provided. Disagreements are adjudicated from original evidence, and any unresolved item blocks publication. Existing reviews are retained when task and output are unchanged; changed calls require a new blind review.') + '</p></details>'))
 if audit.get('adjudication_revision'):
  method='<p>'+model_text(*pair('original_independent_and_supplemental_opinions_remain_in', 'Original independent and supplemental opinions remain in the per-result records. This supplemental adjudication inspected saved scores, contracts and original answers; it is not a new full blind review. Each adjudication records specific rules and original excerpts. Completing adjudication does not certify source validity.'))+'</p>'
 if audit.get('review_protocol',{}).get('machine_judgments_withheld') is False:
  method='<p>'+t('known_outcome_audit_protocol', 'This evidence audit knew the machine outcomes. It checked saved responses, frozen requirements, native field selection and call evidence; it is not an independent blind review and added no provider calls.')+'</p>'
 if audit.get('review_protocol',{}).get('blind_initial_verdict') is False and audit.get('retained_reviews'):
  method='<p>'+model_text(*pair('the_newly_checked_records_received_evidence_review', 'The newly checked records received evidence review with known machine outcomes, checking complete saved answers, task requirements and native fields. Other records retain prior opinions after dependency checks. The two scopes are reported separately; this evidence review is not a new independent blind review and does not complete historical source certification.'))+'</p>'
 if audit.get('review_protocol',{}).get('fresh_blind_initial_verdict') is True and audit.get('retained_reviews'):
  method='<p>'+model_text(*pair('fresh_reviews_received_task_requirements_frozen_references', 'Fresh reviews received task requirements, frozen references and saved answers, with machine outcomes hidden for the initial judgment. Disagreements and anomalies were then investigated through raw responses, field selection, call logs and scoring rules. Initial opinions remain immutable. Prior reviews with unchanged decision inputs are counted separately and retain their original protocol. Review completion does not provide historical source certification.'))+'</p>'
 if audit.get('renewed_context_reviewed_attempts',0):
  blind_count=audit.get('fresh_blind_reviewed_attempts',0);renewed_count=audit['renewed_context_reviewed_attempts']
  method='<p>'+model_text(*pair('saved_answers_received_initial_independent_judgments_with', '{blind_count} saved answers received initial independent judgments with machine outcomes hidden, followed by disagreement and anomaly investigation. Another {renewed_count} received supplemental adjudication after task-contract or same-call request-context corrections, checking current requirements, original answers and final outcomes. This supplemental adjudication inspected machine outcomes and preserves original opinions. Remaining records retain prior reviews after dependency checks. The scopes are counted separately; review completion does not provide historical source certification.', blind_count=blind_count, renewed_count=renewed_count))+'</p>'
 page+=section(t('review_protocol', 'Review protocol'),method)
 dataset_path=out/'dataset.json'
 if dataset_path.is_file():
  dataset=json.loads(dataset_path.read_text(encoding='utf-8'))
  findings=dataset.get('comparison',{}).get('call_chain_audit',{}).get('findings',[])
  if dataset.get('comparison',{}).get('task_replacement'):
   findings=current_local_review_findings(out,dataset,records,audit)
  if findings:
   page+=section(t('corrections_and_review_limits', 'Corrections and review limits'),''.join('<p>'+model_text(item['text'],item.get('text_en'))+'</p>' for item in findings))
 write(out,'review/index.html',t('review_record', 'Review record'),page)

NATIVE_OPTION_LABELS={'wait_until':t('page_completion_condition', 'Page completion condition'),'block_private_networks':t('block_private_network_access', 'Block private-network access'),'max_bytes':t('returned_content_limit_bytes', 'Returned content limit (bytes)'),'max_age':t('maximum_cache_age', 'Maximum cache age'),'proxy':t('proxy_selection', 'Proxy selection'),'formats':t('requested_output_format', 'Requested output format'),'only_main_content':t('main_content_only', 'Main content only'),'extract_depth':t('extraction_depth', 'Extraction depth'),'include_images':t('include_images', 'Include images'),'text_verbosity':t('body_detail_level', 'Body detail level'),'max_age_hours':t('maximum_cache_age_hours', 'Maximum cache age (hours)'),'include_resource_links':t('include_resource_links', 'Include resource links'),'rich_links_limit':t('named_link_limit', 'Named-link limit'),'document_max_bytes':t('document_size_limit_bytes', 'Document size limit (bytes)'),'min_interval_seconds':t('call_interval_seconds', 'Call interval (seconds)'),'log_level':t('log_level', 'Log level'),'retain_complete_diagnostic_log':t('keep_complete_diagnostic_logs', 'Keep complete diagnostic logs'),'max_diagnostic_bytes':t('log_retention_limit_bytes', 'Log retention limit (bytes)'),'uniform_native_parameters':t('use_uniform_fixed_native_options', 'Use uniform fixed native options'),'dns_mode':t('name_resolution_mode', 'Name resolution mode')}

def load_frozen_method_options(out,records):
 """Bind displayed controls to their actual configuration and call scope."""
 document=json.loads((out/'frozen-method-options.json').read_text(encoding='utf-8'))
 if document.get('schema_version')!=2 or not document.get('source_config'):
  raise ValueError('Frozen option display requires a bound configuration and call scope')
 binding=document['source_config']
 source_config=json.loads(read_evidence_binding(out,binding).read_text(encoding='utf-8'))
 configuration=source_config['providers']
 config_hash=binding['sha256']
 if source_config.get('projection'):
  require_public_method_options(configuration)
  if source_config['projection']!='allowlisted_method_options':
   raise ValueError('Unknown frozen option configuration projection')
  config_hash=source_config.get('original_provider_config_sha256')
  contract=json.loads((out/'local-run-contract.json').read_text(encoding='utf-8'))
  if (not config_hash or config_hash!=contract.get('provider_config_sha256')
      or any(key not in NATIVE_OPTION_LABELS for options in configuration.values() for key in options)):
   raise ValueError('Public option configuration differs from its original call binding')
 identifiers=document.get('case_ids',[])
 selected=[row for row in records if row['case_id'] in identifiers]
 providers=document.get('providers',{})
 if (not identifiers or len(identifiers)!=len(set(identifiers)) or not providers
     or document.get('provider_config_sha256')!=config_hash
     or document.get('result_count')!=len(selected)
     or len(selected)!=len(identifiers)*len(providers)
     or {(r['provider'],r['case_id']) for r in selected}
        !={(p,c) for p in providers for c in identifiers}
     or any(not document.get(key) for key in
            ('scope_title','scope_title_en','scope_description','scope_description_en'))):
  raise ValueError('Frozen option display scope differs from saved calls')
 for name,options in providers.items():
  if (name not in configuration or not isinstance(options,dict)
      or any(key not in NATIVE_OPTION_LABELS or key not in configuration[name]
             or type(configuration[name][key]) is not type(value)
             or configuration[name][key]!=value for key,value in options.items())):
   raise ValueError('Displayed option differs from the frozen configuration')
 return document


def render_account_requirements(records):
 names=sorted({row['provider'] for row in records},key=lambda name:(provider_label(name).lower(),name))
 accesses={name:method_access(name,records) for name in names}
 english_accesses={name:method_access(name,records,language='en') for name in names}
 hosted=[name for name in names if accesses[name].get('type')=='hosted']
 intro=model_text(*pair('account_and_installation_requirements_come_from_the', 'Account and installation requirements come from the participating method registrations; missing requirements are marked.'))
 if hosted:
  intro+=' '+model_text(*pair('methods_requiring_a_provider_account', 'Methods requiring a provider account: '))+model_text('、'.join(provider_label(name) for name in hosted),', '.join(browser_method_label(name,language='en') for name in hosted))
 rows=[]
 for name,access in accesses.items():
  english=english_accesses[name]
  official='<br>'.join(link(url,label,english['links'][index][0])
                      for index,(label,url) in enumerate(access['links']))
  variables='<br>'.join('<code>'+esc(value)+'</code>' for value in access['env_vars'])
  rows.append(('',cell(model_text(provider_label(name),browser_method_label(name,language='en')))+cell(model_text(access['account'],english['account']))+cell(official)+cell(variables)+cell(model_text(access['configuration'],english['configuration']))))
 body=hero(t('access_setup', 'Access & setup'),intro)
 body+=section(t('methods_in_this_benchmark', 'Methods in this benchmark'),('<p>' + t('this_is_the_complete_external_setup_required', 'This is the complete external setup required for the benchmark: accounts, official sources, and variable names. Values, local paths, login sessions, cookies, and tokens are never included.') + f"</p>{table([t('method', 'Method'), t('account_requirement', 'Account requirement'), t('official_source_2', 'Official source'), t('variable_names', 'Variable names'), t('measured_configuration', 'Measured configuration')], rows, cls='accounts-table', metrics=[])}"))
 body+=section(t('public_data_boundary', 'Public-data boundary'),('<p class="callout success"><b>' + t('the_report_publishes_only_the_names_and', 'The report publishes only the names and official entry points needed for reproduction, never credential values.') + '</b> ' + t('the_publication_gate_scans_html_json_downloads', 'The publication gate scans HTML, JSON, downloads, and per-call outputs for common token forms, private keys, Authorization values, and user-directory paths. Secret-shaped fragments in third-party raw HTML are redacted separately while scored output remains byte-for-byte unchanged. Scan results and redacted-file hashes are retained with the release bundle.') + '</p>'))
 return body


def render_execution_contract(dataset,records,manifests):
 """Render recorded budgets and scheduling without inferring missing values."""
 if not manifests and dataset.get('saved_matrix_scope',{}).get('execution_settings'):
  return '<p><a href="#execution-settings">'+t('saved_settings_budget_link', 'Verified call budgets, retry limits and native parameters are listed below by method and task family.')+'</a></p>'
 def seconds(value):
  if not nonnegative_number(value):
   return model_text(*pair('not_recorded', 'Not recorded'))
  return model_text(*pair('seconds', '{value:g} seconds', value=value))
 def count(value):
  return str(value) if isinstance(value,int) and not isinstance(value,bool) and value>=0 else model_text(*pair('not_recorded', 'Not recorded'))
 declared=dataset.get('comparison',{}).get('timeout_seconds')
 intro=''
 if declared is not None:
  intro='<p>'+model_text(*pair('predeclared_outer_budget', 'Predeclared outer budget: '))+seconds(declared)+'</p>'
 names={row['provider'] for row in records}
 rows=[]
 represented=set()
 for index,manifest in enumerate(manifests,1):
  parameters=manifest.get('parameters',{})
  for provider in manifest.get('providers',[]):
   name=provider.get('name')
   if name not in names:continue
   represented.add(name)
   values=[model_text(provider_label(name),browser_method_label(name,language="en")),str(index),seconds(parameters.get('timeout')),
           seconds(provider.get('min_interval_seconds')),seconds(parameters.get('delay')),
           count(parameters.get('runs')),count(parameters.get('max_failure_retries'))]
   rows.append(('', ''.join(cell(value) for value in values)))
 for name in sorted(names-represented):
  rows.append(('',cell(model_text(provider_label(name),browser_method_label(name,language="en")))+cell('—')+
               ''.join(cell(model_text(*pair('not_recorded', 'Not recorded'))) for _ in range(5))))
 body=intro+table([model_text(*pair('method', 'Method')),model_text(*pair('batch', 'Batch')),
     model_text(*pair('outer_timeout', 'Outer timeout')),model_text(*pair('method_minimum_interval', 'Method minimum interval')),
     model_text(*pair('runner_delay_between_calls', 'Runner delay between calls')),model_text(*pair('planned_runs', 'Planned runs')),
     model_text(*pair('failure_retry_limit', 'Failure retry limit'))],rows,metrics=[])
 body+='<p>'+model_text(*pair('the_table_reads_saved_execution_batches_missing', 'The table reads saved execution batches; missing values are not replaced with current defaults. Method intervals and runner delays are separate settings, not measured waiting time. Inter-method concurrency and actual scheduling follow batch records and call timestamps. Native loading, connection and processing limits come from frozen configuration and actual requests, not from the outer budget.'))+' '+link('../reproduce/index.html',t('reproduction', 'Reproduction'),'Environment and reproduction')+'</p>'
 return body


def write_support(out,records,manifests,*,authored_interpretation=True):
 dataset=json.loads((out/'dataset.json').read_text(encoding='utf-8')) if (out/'dataset.json').is_file() else {}
 local_matrix=measurement_presentation(dataset).local_matrix
 headless=is_headless_cohort(records)
 methodology=hero(t('methodology', 'Methodology'),t('acceptance_rules_are_defined_by_task_measured', 'Acceptance rules are defined by task. Measured methods within a family use the same frozen tasks and references, and each ranking states its coverage.'))
 if headless:
  methodology+=section(t('from_reference_capture_to_publication', 'From reference capture to publication'),('<div class="guide-grid"><article class="guide-card"><h3>' + t('1_freeze_references', '1 · Freeze references') + '</h3><p>' + t('locks_frozen_tasks_references_and_acceptance_rules', 'Locks frozen tasks, references and acceptance rules; source pages are recaptured only for tasks explicitly declaring a live reference.') + '</p></article><article class="guide-card"><h3>' + t('2_run_provider_calls', '2 · Run provider calls') + '</h3><p>' + t('use_the_same_url_task_and_deadline', 'Use the same URL, task, and deadline. Each method uses its fastest official task-native output path, while version, output, and hashes are retained.') + '</p></article><article class="guide-card"><h3>' + t('3_post_run_validation', '3 · Post-run validation') + '</h3><p>' + t('checks_frozen_tasks_references_rules_and_call', 'Checks frozen tasks, references, rules and call records; tasks that need a live reference are captured before and after to confirm it is unchanged.') + '</p></article><article class="guide-card"><h3>' + t('4_independent_result_review', '4 · Independent result review') + '</h3><p>' + t('independent_review_follows_an_explicitly_specified_scope', "Independent review follows an explicitly specified scope and checks output, references and acceptance rules; this round's coverage is below, and program scoring is not treated as independent review.") + f"</p></article></div>{review_summary(out, '../')}"),t('evaluation_workflow', 'Evaluation workflow'))
 examples_html=re.sub(r'<a href="../datasets/([^"#]+).html">([^<]*)</a>',lambda m:m[0] if (out/'datasets'/(m[1]+'.html')).exists() else m[2],examples({c['id']:c for c in dataset.get('cases',[])}))
 methodology+=section(t('how_each_task_family_is_evaluated', 'How each task family is evaluated'),examples_html)+section(t('metric_definitions', 'Metric definitions'),metric_guide())
 methodology+=section(t('shared_inputs_timing_boundary_and_call_budget', 'Shared inputs, timing boundary, and call budget'),('<p>' + t('every_method_enters_the_same_scoring_pipeline', "Every method enters the same scoring pipeline, while acceptance follows each task's required deliverable. Web and document tasks require readable body text; web-media tasks require the specified resource URL or registered text; direct-media tasks require image or video bytes; structure tasks require the declared fields and stable text. HTML and Markdown links follow the same checks as explicitly paired structured name–URL fields. Request echoes and diagnostic logs do not count as answers. Raw output and adapter status are preserved, and the report layer does not alter results by method name.") + '</p><div class="guide-grid"><article class="guide-card"><h3>' + t('included_in_latency', 'Included in latency') + '</h3><p>' + t('timing_starts_when_the_provider_call_is', 'Timing starts when the provider call is prepared and ends when the final deliverable is ready for the shared scorer. It includes thread scheduling, SDK/CLI/HTTP execution, remote processing and network transfer, browser rendering and body extraction, local parsing, normalized input conversion, file-byte reading, format detection, and SHA-256 calculation.') + '</p></article><article class="guide-card"><h3>' + t('excluded_from_latency', 'Excluded from latency') + '</h3><p>' + t('dataset_and_reference_loading_provider_configuration_validation', 'Dataset and reference loading, provider configuration validation, inter-call rate-limit waits, evidence persistence after the deliverable is ready, shared scoring, report aggregation, and page generation are excluded. Provider-reported server timing is supplementary evidence; rankings use client-observed end-to-end latency.') + '</p></article></div><p>' + t('this_boundary_measures_the_time_needed_to', "This boundary measures the time needed to obtain the task's final deliverable. A service that returns a finished result includes the full round trip; a local method stops only after any required download, rendering, or parsing. The evaluated method must deliver document body text in readable form. Direct-media tasks stop after byte validation and do not perform an unrelated Markdown conversion. Every method uses the same outer timer and deadline. Late answers are rejected. Calls that cannot be cancelled are isolated, which leaves the batch incomplete and blocks publication.") + '</p><p>' + t('alongside_the_existing_checks_complete_body_tasks', 'Alongside the existing checks, complete-body tasks measure six-character-shingle coverage across up to five consecutive reference regions. Every region must reach 60%, and normally at least four must reach 80%. A global capacity assignment ensures that each returned fragment instance counts toward only one region, regardless of region order. A task may declare different regional thresholds when independent evidence establishes legitimate reordering. Matching ignores whitespace and presentation marks while preserving text order; full-text F1 remains a separate measurement. Direct-media tasks require byte validation, and a resource URL does not count as a file.') + '</p>'))
 if (out/'resource-cohort/resource-comparison.json').is_file():
  resource_summary=json.loads((out/'resource-cohort/resource-comparison.json').read_text(encoding='utf-8'))
  resource_environment=resource_summary.get('environment',{})
  resource_parameters=[
   ((t('tasks', 'Tasks'),'Task count'),len(resource_summary.get('planned_cases',[]))),
   ((t('methods_2', 'Methods'),'Method count'),len(resource_summary.get('providers',[]))),
   ((t('cpu_cores', 'CPU cores'),'CPU cores'),resource_environment.get('cpus','—')),
   ((t('memory_limit', 'Memory limit'),'Memory limit'),resource_environment.get('memory','—')),
   ((t('process_limit', 'Process limit'),'Process limit'),resource_environment.get('pids_limit','—')),
   ((t('sampling_interval_ms', 'Sampling interval (ms)'),'Sampling interval (ms)'),resource_environment.get('sample_interval_ms','—')),
  ]
  methodology+=section(t('resource_measurement_boundary', 'Resource measurement boundary'),'<p>'+model_text(*pair('cpu_memory_and_network_measurements_come_from', 'CPU, memory and network measurements come from a separate resource cohort. The primary table includes all predeclared calls, including failures; successful-call distributions are conditional and the common-pass subset is diagnostic only.'))+'</p>'
   +table([model_text(*pair('parameter', 'Parameter')),model_text(*pair('value', 'Value'))],[('',cell(model_text(*key))+cell(esc(value))) for key,value in resource_parameters],metrics=[])
   +'<p>'+model_text(*pair('cumulative_counters_provide_cpu_and_network_totals', 'Cumulative counters provide CPU and network totals. Memory peaks and rate percentiles depend on the recorded sampling interval and may miss shorter peaks. The resource evidence identifies the environment, scope and exclusions.'))+' '
   +link('../resource-cohort/resource-comparison.json',t('resource_measurement_evidence', 'Resource measurement evidence'))+'</p>')
 if (out/'scoring-revisions.json').exists():
  revision_links=[link('../scoring-revisions.json',t('scoring_rule_revision_receipt', 'Scoring-rule revision receipt'))]
  if (out/'reference-revisions.json').exists():revision_links.append(link('../reference-revisions.json',t('reference_answer_revision_receipt', 'Reference-answer revision receipt')))
  if (out/'latency-corrections.json').exists():revision_links.append(link('../latency-corrections.json',t('latency_correction_receipt', 'Latency-correction receipt')))
  if (out/'request-status-corrections.json').exists():revision_links.append(link('../request-status-corrections.json',t('request_status_correction_receipt', 'Request-status correction receipt')))
  detail=t('current_scores_include_offline_rescoring_of_saved', 'Current scores include offline rescoring of saved outputs. Receipts record input and output hashes, verdict changes, and provider-call counts without presenting new rules as the original run configuration.')
  if headless and (out/'latency-corrections.json').exists():
   latency_receipt=json.loads((out/'latency-corrections.json').read_text(encoding='utf-8'))
   changes=latency_receipt.get('changes',[])
   if changes:
    affected='、'.join(provider_label(name) for name in sorted({change['provider'] for change in changes}))
    detail+=((' ' + t('the_latency_correction_in_the_current_report', 'The {affected} latency correction in the current report replaces only {count} timings, all from round {get} of the paired experiment; old timings and original physical calls stay in offline evidence, and a cross-batch view must not be read as a simultaneous rerun.', affected=affected, count=len(changes), get=latency_receipt.get('selected_round', '—'))))
  methodology+=section(t('scoring_provenance_and_revisions', 'Scoring provenance and revisions'),'<p>'+detail+' '+' · '.join(revision_links)+'</p>')
 methodology+=section(t('stable_task_contracts_and_full_text_diagnostics', 'Stable task contracts and full-text diagnostics'),task_contract_guide(),'task-contracts')
 figures=''
 for name,label in [('annotation',t('reference_annotations', 'Reference annotations')),('validation',t('task_acceptance', 'Task acceptance')),('scoring',t('scoring_scope', 'Scoring scope'))]:
  if not (out/'assets/methodology'/f'{name}.png').is_file():
   continue
  english_asset=(f' data-en-src="../assets/methodology/{name}.en.png"'
                 if (out/'assets/methodology'/f'{name}.en.png').is_file() else '')
  figures+=f'<figure><img src="../assets/methodology/{name}.png"{english_asset} alt="{label}" style="max-width:100%;height:auto" loading="lazy"><figcaption>{label}</figcaption></figure>'
 if figures:methodology+=section(t('method_diagrams', 'Method diagrams'),figures)
 methodology+=section(t('reference_normalization_and_matching', 'Reference normalization and matching'),('<p>' + t('original_snapshots_remain_unchanged_scoring_references_remove', 'Original snapshots remain unchanged. Scoring references remove zero-width spaces, BOMs, and soft hyphens, and are checked for empty bodies, decoding corruption, duplicate checks, and whether each check exists in the reference. Passing validation does not prove semantic completeness; source records still define body scope.') + '</p><p>' + t('key_facts_are_matched_mechanically_against_normalized', 'Key facts are matched mechanically against normalized visible text: Unicode, letter case, and layout whitespace are normalized, so menu paths such as Settings &gt; AI and Settings&gt;AI are equivalent; Markdown link destinations, inline-code markers, and emphasis markers are ignored. A field whose complete record boundary is known may omit whitespace between inline elements, such as the tag list') + ' <code>change deep-thoughts</code> ' + t('and', 'and') + ' <code>changedeep-thoughts</code> ' + t('is_content_equivalent_adjacent_independent_links_are', 'is content-equivalent. Adjacent independent links are still checked as separate token segments, with no guessed segmentation across records. Numbers, signs, negation, punctuation, and English word boundaries are preserved. Acceptance never relies on an LLM or fuzzy similarity.') + '</p>'))
 methodology+=section(t('call_timing', 'Call timing'),('<p>' + t('all_methods_use_the_same_frozen_tasks', 'All methods use the same frozen tasks and references. Latencies from different batches are not ranked directly; the same-window cohort is identified separately. Live content, login state, and site policy may change, so results represent only the recorded calls. See Reproduction for environment, versions, and commands.') + '</p>'))
 methodology+=section(t('rerun_scope_after_a_change', 'Rerun scope after a change'),('<p>' + t('a_code_change_does_not_automatically_require', 'A code change does not automatically require a full rerun. Presentation-only changes rebuild the report; judge-only changes are rescored offline when saved output is sufficient; a changed reference reruns every method for that task; a new or upgraded implementation reruns that method across all tasks; a timing-boundary change invalidates and reruns the same-window cohort. A full rerun is reserved for changes to global task inputs, call boundaries, or dependencies shared by every method.') + '</p>'))
 methodology+=section(t('calculations_and_limits', 'Calculations and limits'),('<details><summary>' + t('full_text_scoring_formula', 'Full-text scoring formula') + '</summary><p>' + t('recall_equals_recovered_reference_units_divided_by', 'Recall equals recovered reference units divided by reference units; precision equals matched body units divided by returned units; F1 equals 2 × recall × precision ÷ their sum. English is tokenized by word and Chinese by character, with case and Unicode normalized; only Markdown link text is counted. Values are averaged across tasks with complete references. Request failures score 0, successful requests that fail acceptance retain their observed text score, and tasks without references are excluded.') + '</p></details><details><summary>' + t('reference_scope_and_fairness', 'Reference scope and fairness') + '</summary><p>' + t('web_and_document_tasks_use_independently_retained', 'Web and document tasks use independently retained body references. Repository tasks target the README; list tasks cover only the declared loaded range and do not require infinite scrolling or pagination. Login and CAPTCHA pages cannot replace the target body. Terms such as “404 Not Found” inside the reference itself are not treated as error-page signals.') + '</p><p>' + t('a_media_url_proves_only_that_an', "A media URL proves only that an endpoint was found, not that the file was obtained. File tasks do not use a separate downloader to complete the evaluated method's work.") + '</p></details>'))
 methodology+=section(model_text(*pair('run_parameters_and_call_boundaries', 'Run parameters and call boundaries')),
                      render_execution_contract(dataset,records,manifests))
 if measurement_presentation(dataset).native_outputs:
  methodology+='<p>'+model_text(*pair('text_acceptance_uses_content_delivered_by_the', 'Text acceptance uses content delivered by the native interface, without an additional parser completing the answer. Original-file tasks use their byte-acceptance rules; requests, outputs and errors remain in per-call evidence.'))+'</p>'
 if dataset.get('cases'):
  methodology+=render_acceptance_scope({case['id']:case for case in dataset['cases']},records)
 score_binding=dataset.get('comparison',{}).get('score_audit')
 if score_binding:
  methodology+=section(t('data_scope', 'Data scope'),'<div data-scoring-input-scope><p>'+esc(t('this_report_uses_the_existing_call_records', 'This report uses the existing {total_attempts} call records and latencies with no new browser calls. {projected_scoring_inputs} scoring inputs are redacted public copies and {recovered_originals} are outputs restored by the original hash. Item-by-item check scope is {reviewed_attempts}/{total_attempts} verdict changes.', total_attempts=score_binding['total_attempts'], projected_scoring_inputs=score_binding['projected_scoring_inputs'], recovered_originals=score_binding['recovered_originals'], reviewed_attempts=score_binding['reviewed_attempts']))+'</p><p>'+link('../audit/index.html',t('input_differences_and_per_task_acceptance_records', 'Input differences and per-task acceptance records'))+'</p></div>')
  input_rows=[]
  for name in sorted({row['provider'] for row in records}):
   selected=[row for row in records if row['provider']==name]
   counts=Counter(row.get('scoring',{}).get('artifact_view','original') for row in selected)
   input_rows.append(('',cell(esc(provider_label(name)))+cell(str(counts['original']))+cell(str(counts['recovered_original']))+cell(str(counts['published_projection']))+cell(str(len(selected)))))
  methodology+=section(t('scoring_input_sources', 'Scoring input sources'),table([t('method', 'Method'),t('raw_output', 'Raw output'),t('recovered_by_hash', 'Recovered by hash'),t('public_redacted_copies', 'Public redacted copies'),t('total', 'Total')],input_rows,metrics=[]))
 revision=dataset.get('definition_revision')
 if revision:
  methodology+=section(t('scoring_definitions', 'Scoring definitions'),('<p>' + t('dataset_2', 'Dataset:') + f"""<code class="break">{esc(dataset['id'])}</code></p><p>""" + t('definition_revision', 'Definition revision:') + f"""{link('../' + revision['path'], revision['id'])} · SHA-256：<code class="break">{esc(revision['sha256'])}</code></p><p>""" + t('original_definition_dataset', 'Original definition dataset:') + f"""<code class="break">{esc(revision['parent_dataset_id'])}</code> · SHA-256：<code class="break">{esc(revision['parent_dataset_sha256'])}</code></p>"""))

 if dataset.get('saved_matrix_scope'):
  methodology+=saved_matrix_scope_section(dataset['saved_matrix_scope'],show_completion=False)
 write(out,'methodology/index.html',(t('report_methodology_48298a', 'Methodology')),methodology)
 downloads=' · '.join(link('../'+path,label) for path,label in [('dataset.json',(t('report_dataset_and_references_efd79b', 'Dataset and references'))),('results.jsonl',(t('report_all_call_records_348a87', 'All call records'))),('summary.json',(t('report_summary_by_family_ab1be5', 'Summary by family'))),('execution.json',(t('report_execution_batches_ac8ac0', 'Execution batches')))]+([('implementation-provenance.json',(t('report_browser_versions_and_binary_sources_da07f0', 'Browser versions and binary sources')))] if (out/'implementation-provenance.json').is_file() else [])+([('reference-revisions.json',(t('report_reference_revision_receipt_ba8a25', 'Reference-revision receipt')))] if (out/'reference-revisions.json').is_file() else [])+([('scoring-revisions.json',(t('report_scoring_revision_receipt_c63127', 'Scoring-revision receipt')))] if (out/'scoring-revisions.json').is_file() else [])+([('latency-corrections.json',(t('report_latency_correction_receipt_b8f2d5', 'Latency-correction receipt')))] if (out/'latency-corrections.json').is_file() else [])+([('request-status-corrections.json',(t('report_request_status_correction_receipt_1f948a', 'Request-status correction receipt')))] if (out/'request-status-corrections.json').is_file() else []))
 reproduce=('<p>' + t('report_run_in_the_benchmark_repository_using_the_current_measu_dedf59', 'Run in the benchmark repository using the current measured data in evaluation/; this does not call provider APIs.') + '</p>' + '<pre>' + 'make setup\nmake report PROFILE=/absolute/path/to/profile.json EVIDENCE=/absolute/path/to/evidence\nmake report-check PROFILE=/absolute/path/to/profile.json' + '</pre>' + '<p>' + t('report_output_directory_reports_webfetch_the_command_fails_imm_ddb34d', 'Output directory: reports/webfetch/. The command fails immediately unless the pinned Python 3.11 and uv.lock dependencies are satisfied. Credentials are loaded only from the local environment and are not included in the report.') + '</p>')
 if headless:
  reproduce=('<p>' + t('run_in_the_benchmark_repository_and_build', 'Run in the benchmark repository and build from an explicitly selected persistent frozen-evidence directory; browsers are not called again.') + '</p><pre>make setup\nmake report PROFILE=/absolute/path/to/profile.json EVIDENCE=/absolute/path/to/evidence\nmake report-check PROFILE=/absolute/path/to/profile.json</pre><p>唯一输出目录为 reports/webfetch/。固定的 Python 3.11 与 uv.lock 依赖不满足时会立即失败。重新测量使用 uv run --frozen python scripts/run_certified_benchmark.py 和本轮方法配置；先锁定题目、答案、规则和执行范围，再调用；明确声明需要实时参考的任务才做前后采集。完整命令与版本见仓库 harness/web_fetch/HEADLESS.md。</p>')
 profile_path=dataset.get('comparison',{}).get('reproduction_profile')
 if profile_path:
  candidate=Path(profile_path)
  if candidate.is_absolute() or not candidate.parts or '..' in candidate.parts or candidate.suffix!='.json':
   raise ValueError('Report reproduction profile must be a relative JSON path without parent traversal')
  destination=dataset['comparison'].get('report_output','reports/webfetch')
  command=f'uv run --frozen python scripts/build_report.py --profile {profile_path} --evidence /absolute/path/to/evidence --output {destination}\nuv run --frozen python scripts/check_report.py {destination} --profile {profile_path}'
  reproduce=('<p>' + t('built_from_this_run_s_independent_configuration', "Built from this run's independent configuration and frozen evidence, without calling browsers again.") + f'</p><pre>{esc(command)}</pre><p>重新测量使用仓库的认证入口；既定题目和验收规则在调用前冻结，只有明确声明需要实时参考的任务才做前后采集。</p>')
 if local_matrix:
  reproduce=('<p>以下命令只从本轮证据重建本地中文报告，不重新调用产品。</p><pre>uv run --frozen python scripts/build_report.py --variant local-audit --evidence /absolute/path/to/report-evidence --output /absolute/path/to/report\nuv run --frozen python scripts/check_report.py /absolute/path/to/report</pre><p>' + t('call_time_configuration_implementation_versions_and_scorer', "Call-time configuration, implementation versions and scorer hashes are in {link}; each call's actual request parameters are in the call log on its detail page. Current source configuration may differ from call-time configuration, so the frozen protocol and original batch manifests govern. Credentials are read only from the repository .env.", link=link('../local-run-contract.json', t('pre_call_frozen_protocol', 'Pre-call frozen protocol'))) + '</p>')
 common=[]
 for label,path in [(t('platform', 'Platform'),('environment','platform')),('Python',('environment','python')),(t('timeout_seconds', 'Timeout (seconds)'),('parameters','timeout')),(t('concurrency_model', 'Concurrency model'),('parameters','concurrency_scope'))]:
  values=sorted({str(m.get(path[0],{}).get(path[1],'—')) for m in manifests})
  common.append(('',cell(label)+cell(esc('；'.join(values)))))
 provenance={}
 if (out/'implementation-provenance.json').is_file():
  provenance={x['binary_sha256']:x for x in json.loads((out/'implementation-provenance.json').read_text(encoding='utf-8')).get('implementations',[])}
 provider_rows=[]
 for name in sorted({r['provider'] for r in records}):
  subset=[r for r in records if r['provider']==name]
  versions=method_versions(subset,provenance)
  identities=[p.get('implementation_identity',{}) for m in manifests for p in m.get('providers',[]) if p.get('name')==name]
  versions=versions or sorted({str(identity['reported_version']) for identity in identities if identity.get('reported_version')})
  versions=versions or sorted({str(identity['repository']['head']) for identity in identities if identity.get('repository',{}).get('head')})
  hashes=sorted({str(r.get('provider_metadata',{}).get('binary_sha256')) for r in subset if r.get('provider_metadata',{}).get('binary_sha256')})
  versions=versions or sorted({provenance[h].get('version') or provenance[h].get('identity') for h in hashes if h in provenance})
  deployments=sorted({str(r.get('provider_metadata',{}).get('deployment','—')) for r in subset})
  timings={provenance[h].get('identity_timing') for h in hashes if h in provenance}
  if not hashes:
   basis=t('source_revision_and_file_hashes_are_locked', 'Source revision and file hashes are locked; see the execution batch') if any(identities) else t('cloud_api_backend_version_not_disclosed', 'Cloud API backend version not disclosed')
  elif any(t and t.startswith('post_hoc') for t in timings):
   basis=t('post_run_hash_matches_the_official_archive', 'Post-run hash matches the official archive; the original call did not record a version string')
  elif versions:
   basis=t('runtime_version_and_sha_256', 'Runtime version and SHA-256')
  else:
   basis=t('runtime_sha_256_the_nightly_source_revision', 'Runtime SHA-256; the nightly source revision is unavailable')
  if local_matrix:
   version='；'.join(versions) or (t('the_cloud_service_does_not_disclose_its', 'The cloud service does not disclose its backend version') if not hashes and not any(identities) else t('no_verifiable_version_information_is_available', 'No verifiable version information is available'))
   if versions and all(re.fullmatch(r'[0-9a-f]{40}', value) for value in versions):
    version=t('source_commit', 'Source commit: {version}', version=version)
   provider_rows.append(('',cell(link('../providers/'+name+'.html',provider_label(name),browser_method_label(name,language='en')))+cell('<span class="break">'+esc(version)+'</span>')))
  else:
   provider_rows.append(('',cell(link('../providers/'+name+'.html',provider_label(name),browser_method_label(name,language='en')))+cell(esc('；'.join(versions) or t('version_not_recorded', 'Version not recorded')))+cell(esc(basis))+cell(esc('；'.join(deployments)))+cell('<br>'.join('<code class="tiny break">'+esc(x)+'</code>' for x in hashes) or '—')+cell(str(len(subset)))))
 write_review_page(out,records)
 repo='https://github.com/lexmount/webstone'
 support=hero((t('report_reproduction_47f6b8', 'Reproduction')),(t('report_benchmark_code_lives_in_webstone_task_definitions_a_83ab02', 'Benchmark code lives in Webstone; task definitions and reference data live on Hugging Face. This page lists the run configuration and reproduction entry points.')))
 support+=section((t('report_code_and_dataset_67a544', 'Code and dataset')),(f"<p>{link(repo, t('report_webstone_repository_1aa369', 'Webstone repository'))}{t('report_contains_invocation_scoring_and_report_tools_its_data_l_8d1d7c', 'contains invocation, scoring and report tools. Its data lock selects task definitions and reference data on Hugging Face; this report provides the frozen results for this run.')}</p><p>{downloads}</p>"))
 if (out/'speed-cohort/speed-comparison.json').is_file():
  support+=section(t('same_window_latency_evidence', 'Same-window latency evidence'),'<p>'+link('../speed-cohort/speed-comparison.json',t('latency_summary_and_sample_denominators', 'Latency summary and sample denominators'))+' · '+link('../speed-cohort/results.jsonl',t('per_call_latency_cohort_records', 'Per-call latency-cohort records'))+'</p>')
 if (out/'resource-cohort/resource-comparison.json').is_file():
  support+=section(t('isolated_resource_evidence', 'Isolated resource evidence'),'<p>'+link('../resource-cohort/resource-comparison.json',t('cpu_memory_and_network_summary_with_shared', 'CPU, memory, and network summary with shared-success cases'))+' · '+link('../resource-cohort/results.jsonl',t('per_call_resource_cohort_records', 'Per-call resource-cohort records'))+' · '+link('../resource-cohort/execution.json',t('container_environment_and_image_identity', 'Container environment and image identity'))+'</p>')
 conditions=(t('the_final_report_includes_calls_execution_began', 'The final report includes {count:,} calls. Execution began after tasks and references were frozen; calls were sequential within each method and did not reuse personal login state.', count=len(records)) if authored_interpretation else t('this_report_includes_logical_results_run_parameters', 'This report includes {count:,} logical results. Run parameters are listed below; each result detail contains its retained physical calls and outputs.', count=len(records)))
 if not manifests and dataset.get('saved_matrix_scope'):
  support+=section(t('shared_conditions', 'Shared conditions'),'<p>'+t('saved_reproduction_settings_link', 'Execution settings and collection periods are verified from original call receipts; inspect them on the methodology page.')+' '+link('../methodology/index.html#execution-settings',t('runtime_configuration', 'Runtime configuration:'))+'</p>')
 else:
  support+=section(t('shared_conditions', 'Shared conditions'),'<p>'+conditions+'</p>'+table([t('parameter', 'Parameter'),t('value', 'Value')],common,metrics=[]))
 if local_matrix:
  support+=section(t('method_versions', 'Method versions'),('<p>' + t('software_versions_or_source_commits_used_for', 'Software versions or source commits used for this run. Undisclosed cloud backend versions are stated explicitly; this does not mean the service has no version.') + f"</p>{table([t('method', 'Method'), t('version_source_commit_used_in_this_run', 'Version / source commit used in this run')], provider_rows, metrics=[])}"))
 else:
  support+=section(t('method_versions', 'Method versions'),('<p>' + t('versions_use_call_time_records_where_available', 'Versions use call-time records where available; source implementations use frozen commits. Undisclosed cloud backend versions are stated explicitly. Binary hashes identify the executable, and retrospectively verified versions state their evidence.') + f"</p>{table([t('method', 'Method'), t('version', 'Version'), t('identity_evidence', 'Identity evidence'), t('deployment', 'Deployment'), t('binary_sha_256', 'Binary SHA-256'), t('calls', 'Calls')], provider_rows, metrics=[])}"))
 if local_matrix:
  options_path=out/'frozen-method-options.json'
  if options_path.is_file():
   options_document=load_frozen_method_options(out,records)
   options=options_document['providers']
   labels=NATIVE_OPTION_LABELS
   def option_text(value):
    return t('on', 'On') if value is True else t('close', 'Close') if value is False else '、'.join(map(str,value)) if isinstance(value,list) else str(value)
   config_rows=[('',cell(link('../providers/'+name+'.html',provider_label(name),browser_method_label(name,language='en')))+cell(''.join('<div>'+esc(labels.get(k,k))+'：<code>'+esc(option_text(v))+'</code></div>' for k,v in value.items()))) for name,value in options.items()]
   support+=section(model_text(options_document['scope_title'],options_document['scope_title_en']),
                    '<p>'+model_text(options_document['scope_description'],options_document['scope_description_en'])+'</p>'
                    +table([t('method', 'Method'),t('fixed_options', 'Fixed options')],config_rows,metrics=[]))
  support+=local_call_chain_section(json.loads((out/'dataset.json').read_text(encoding='utf-8')))
 if not local_matrix:
  support+=section(t('publishing_and_loading', 'Publishing and loading'),('<p>' + t('the_local_build_creates_an_independently_inspectable', 'The local build creates an independently inspectable logical route for every task, method, and evidence item; this does not mean the deployment contains the same number of standalone HTML shells. The publisher compiles the report into one reusable shell, shared styles and scripts, and content-addressed compressed data blocks. Client-side routing changes pages, evidence loads only when its route is opened, and complete text over 256 KiB requires explicit user loading.') + '</p><p class="source-note">' + t('the_publication_manifest_records_logical_routes_source', 'The publication manifest records logical routes, source-file count, compressed blocks, and per-file SHA-256. The publication gate reconstructs every file and validates internal links plus original download bytes. This retains item-level auditability without duplicating page shells or loading all evidence at once.') + '</p>'))
 if not authored_interpretation:
  reproduce=('<p>' + t('run_with_this_task_and_method_configuration', 'Run with this task and method configuration, or rebuild the report from saved results:') + '</p><pre>webstone run --dataset DATASET --provider-config CONFIG --providers METHODS --output NEW_EVIDENCE\nwebstone report EVIDENCE</pre><p>' + t('rerunning_calls_the_configured_methods_rebuilding_reads', 'Rerunning calls the configured methods. Rebuilding reads saved results only. Parameters, versions and executable hashes are listed above.') + '</p>')
 elif headless:
  reproduce+=('<p>' + t('runtime_configuration', 'Runtime configuration:') + '<code>configs/providers.headless.json</code>' + t('credential_variable_names_are_listed_on_access', '; credential variable names are listed on Access & setup, while actual secrets exist only in the ignored repository-root') + ' <code>.env</code>' + t('reference_freezing_complete_reruns_and_review_gates', '. Reference freezing, complete reruns, and review gates are documented in') + f" {link(repo + '/blob/main/harness/web_fetch/HEADLESS.md', t('repository_reproduction_guide', 'repository reproduction guide'))}。</p>")
 else:
  reproduce+=('<p>' + t('see_methods_access_and_the_version_table', 'See Methods, Access, and the version table above for method configuration, credential variable names, and measured versions. Headless-only configuration that does not exist for this cohort is out of scope.') + '</p>')
 if local_matrix:
  frozen_binding=(out/'presentation-binding.json').is_file()
  build_binding=' --presentation-binding /absolute/path/to/report-evidence/presentation-binding.json' if frozen_binding else ''
  check_binding=' --presentation-binding /absolute/path/to/report/presentation-binding.json' if frozen_binding else ''
  reproduce=('<p>' + t('these_commands_build_the_local_report_from', 'These commands build the local report from saved evidence without additional product calls:') + f"</p><pre>uv run --frozen python scripts/build_report.py --variant local-audit --language {('zh --include-english' if render_context().english_generated else 'zh')} --evidence /absolute/path/to/report-evidence --output /absolute/path/to/report{build_binding}\nuv run --frozen python scripts/check_report.py /absolute/path/to/report{check_binding}</pre><p>" + t('use_the_evidence_directory_corresponding_to_these', 'Use the evidence directory corresponding to these results. Call-time versions, configuration, and scorer hashes are in') + f" {link('../local-run-contract.json', t('pre_call_frozen_protocol', 'Pre-call frozen protocol'))} " + t('and', 'and') + f" {link('../execution.json', t('execution_batches', 'Execution batches'))}" + t('the_build_commands_read_saved_scores_after', '. The build commands read saved scores. After changing scoring rules, rescore the declared scope before rebuilding; rebuilding pages alone does not update scores.') + '</p>')
 if local_matrix and frozen_binding:
  public_profile=json.loads((out/'presentation-binding.json').read_text(encoding='utf-8'))
  if public_profile.get('publication_projection'):
   reproduce+='<p>'+model_text(*pair('this_public_copy_omits_host_paths_the', 'This public copy omits host paths. The manifest distinguishes original and public file hashes; public hashes do not claim to be original call identities. Scores, timings and review decisions are unchanged.'))+' '+link('../'+public_profile['publication_projection']['path'],'PUBLICATION-PROJECTION.json')+'</p>'
  if public_profile.get('publication_evidence_views'):
   reproduce+='<p>'+model_text(*pair('scoring_originals_remain_private_public_answers_and', 'Scoring originals remain private. Public answers and complete native responses hide paths or sensitive values. The inventory preserves original scoring identities and separately binds public views. Public files rebuild this report but cannot reconstruct omitted content. Text-quality diagnostics retain the original calculations and may not reproduce exactly from redacted text. Review coordinates refer to originals; public views verify only the visible excerpts.'))+' '+link('../'+public_profile['publication_evidence_views']['path'],'PUBLIC-EVIDENCE-VIEWS.json')+'</p>'
 if dataset.get('saved_matrix_scope'):
  commands='uv run --frozen python scripts/build_report.py --variant saved-matrix --input /absolute/report-input-spec.json --output /absolute/new-report --language zh\nuv run --frozen python scripts/check_report.py /absolute/new-report --variant saved-matrix --source-spec /absolute/report-input-spec.json'
  reproduce='<p>'+t('saved_matrix_reproduction_note', 'Build and verify this report from its original saved-matrix specification and retained source evidence. These commands add no provider calls; private originals are required to reproduce source-backed checks.')+'</p><pre>'+esc(commands)+'</pre>'
 support+=section(t('core_reproduction_commands', 'Core reproduction commands'),reproduce)
 write(out,'reproduce/index.html',t('reproduction', 'Reproduction'),support)
 write(out,'accounts/index.html',t('access_setup', 'Access & setup'),render_account_requirements(records))

 write(out,'adoption/index.html',t('methods', 'Methods'),render_methods(out,records,hero=hero,section=section,table=table,cell=cell,link=link,esc=esc,authored=authored_interpretation))


def render_score_audit(out, audit, binding):
 """Explain changed decisions from one bound review, beside native results."""
 def verdict(value):return t('pass_2', 'Pass') if value else t('failed', 'Failed')
 rows=[]
 for item in audit['providers']:
  rows.append((' data-audit-summary-provider="'+esc(item['provider'])+'"',cell(esc(provider_label(item['provider'])))+cell(str(item['published_passes']))+cell(str(item['historical_passes']))+cell(str(item['initial_rescore_passes']))+(cell(str(item['original_definition_passes'])) if 'original_definition_passes' in item else '')+cell(str(item['corrected_passes']))+cell(str(item['total']))))
 body=hero(audit['title'],audit['summary'])
 body+=section('判定变化来自哪里','<figure><a href="../'+esc(binding['figure'])+'"><img src="../'+esc(binding['figure'])+'" alt="对同一份保存输出核对正文术语、文字和链接格式、卡片字段归属，以及原始与公开副本哈希" style="width:100%;height:auto"></a><figcaption>'+esc(audit['figure_caption'])+'</figcaption></figure>')
 body+=section('版本结果对照','<p>'+esc(audit['metric_note'])+'</p>'+table([t('method', 'Method'),'原页面汇总','按旧规则核对旧记录','修复前重算']+([t('original_definitions_current_scorer', 'Original definitions + current scorer')] if all('original_definition_passes' in item for item in audit['providers']) else [])+[t('revised_definitions_current_scorer', 'Revised definitions + current scorer'),'题数'],rows,id='score-audit-comparison',metrics=[]))
 body+=section(esc(audit['focus_heading']), '<p>'+esc(audit['focus_summary'])+'</p>')
 inventory=[]
 for item in audit['changes']:
  identifier=item['attempt_id']
  inventory.append(('',cell(esc(item['provider']))+cell(esc(item['case_id']))+cell(verdict(item['historical_pass']))+cell(verdict(item['initial_rescore_pass']))+cell(verdict(item['after_fix_pass']))+cell(esc(item['classification']))+cell(link('#'+identifier,'原因与证据'))))
 controls='<label>筛选方法 <select id="audit-provider" onchange="document.querySelectorAll(\'[data-audit-provider]\').forEach(r=>r.hidden=this.value!==\'\'&&r.dataset.auditProvider!==this.value)"><option value="">全部方法</option>'+''.join('<option value="'+esc(item['provider'])+'">'+esc(provider_label(item['provider']))+'</option>' for item in audit['providers'])+'</select></label>'
 markup=table([t('method', 'Method'),'题目','旧判定','修复前','修复后','原因分类','证据'],inventory,metrics=[])
 # Keep paragraphs in the evidence sections, outside the fixed-height inventory.
 document=BeautifulSoup(markup,'html.parser')
 for element,item in zip(document.select('tbody tr'),audit['changes'],strict=True):element['data-audit-provider']=item['provider']
 body+=section('逐项判定',controls+'<div style="max-height:60vh;overflow:auto">'+str(document)+'</div>')
 body+=section('脱敏副本的影响','<p>'+esc(audit['privacy_summary'])+'</p>'+''.join('<p>'+esc(text)+'</p>' for text in audit['privacy_findings']))
 details=[]
 for item in audit['changes']:
  identifier=item['attempt_id']
  facts=''.join('<dt>'+label+'</dt><dd>'+esc(item[key])+'</dd>' for key,label in [('requirement',t('acceptance_requirement', 'Acceptance requirement')),('actual',t('actual_output', 'Actual output')),('reason','变化原因'),('treatment','处理结果'),('source_scope','来源范围')])
  evidence=' · '.join(link('../'+('audit/history/' if evidence.get('historical') else '')+evidence['path'],evidence.get('label') or evidence['path']) for evidence in item.get('evidence',[]))
  quotations=''.join('<h4>'+esc(quote['label'])+'</h4><pre dir="auto">'+esc(quote['text'])+'</pre>' for quote in item.get('quotes',[]))
  details.append(f'''<article id="{esc(identifier)}" class="panel"><h3>{esc(item['provider'] + ' · ' + item['case_id'])}</h3><p>{esc(item['classification'])} · {verdict(item['historical_pass'])} → {verdict(item['initial_rescore_pass'])} → {verdict(item['after_fix_pass'])}</p><dl class="diagnostic-fields">{facts}</dl><details><summary>原文、规则与实际输出片段</summary>{quotations}<p>{evidence}</p></details><p>{link('../attempts/' + identifier + '.html', '完整结果、实际输出和调用日志')}</p></article>''')
 body+=section('每条变化的依据',''.join(details))
 body+=section('审查范围','<p>'+esc(audit['review_scope'])+'</p><p>'+link('score-changes.json','下载逐条复核记录')+' · '+link('scoring-inputs.json','下载原始与公开评分输入哈希')+'</p>')
 write(out,'audit/index.html',audit['title'],body)
 write(out,'review/index.html',(t('report_review_record_efa815', 'Review record')),hero((t('report_review_record_efa815', 'Review record')),(t('report_per_task_acceptance_criteria_and_data_sources_7e4ecd', 'Per-task acceptance criteria and data sources.')))+section((t('report_verification_scope_db68a1', 'Verification scope')),'<p>'+esc(audit['review_scope'])+'</p><p>'+esc(audit['summary'])+'</p><p>'+link('../audit/index.html',(t('report_per_item_reasons_original_text_and_acceptance_rules_1b0986', 'Per-item reasons, original text and acceptance rules')))+'</p>'))


def saved_execution_settings_section(settings):
 """Expose measured settings without treating unlike environments as controls."""
 if not settings:return ''
 rows=[]
 for item in settings:
  status=model_text(*pair('report_uniform_f2d539', 'Uniform')) if item['uniform'] else model_text(*pair('report_mixed_or_insufficient_evidence_53b8d7', 'Mixed or insufficient evidence'))
  details='<details><summary>'+model_text(*pair('report_view_settings_573e52', 'View settings'))+'</summary><pre>'+esc(json.dumps(item['settings'],ensure_ascii=False,indent=2))+'</pre>'
  if item.get('errors'):details+='<p>'+esc('; '.join(item['errors']))+'</p>'
  details+='</details>'
  values=cell(esc(provider_label(item['provider'])))+cell(esc(item['family']))+cell(f"{item['verified_calls']}/{item['calls']}")+cell(status)
  values+=cell(esc(item.get('collection_start') or '—')+'<br>'+esc(item.get('collection_end') or '—'))+cell(details)
  rows.append(('',values))
 note=model_text(*pair('report_actual_settings_and_collection_times_are_grouped_by_49f6f0', 'Actual settings and collection times are grouped by method and task family. Uniformity describes records within each group only. Differences in DNS, budgets, timeouts, collection dates or execution locations prevent a same-window controlled speed claim.'))
 return section(model_text(*pair('report_execution_settings_by_method_and_task_family_65dad7', 'Execution settings by method and task family')),('' if render_context().external_scope_notes else '<p>'+note+'</p>')+table([
  model_text(*pair('report_method_59e05c', 'Method')),model_text(*pair('report_task_family_2346c3', 'Task family')),model_text(*pair('report_verified_calls_8249fa', 'Verified / calls')),model_text(*pair('report_within_group_settings_29de0d', 'Within-group settings')),model_text(*pair('report_collection_period_0d19bd', 'Collection period')),model_text(*pair('report_configuration_evidence_879a28', 'Configuration evidence'))],rows,'saved-execution-settings',metrics=[]),'execution-settings')


def saved_matrix_scope_section(scope,*,show_completion=True):
 if render_context().external_scope_notes:
  return '' if show_completion else saved_execution_settings_section(scope.get('execution_settings',[]))
 limitations=section(saved_matrix_text('limitations'),'<ul>'+''.join('<li>'+esc(note)+'</li>' for note in scope.get('limitations',[]))+'</ul>','saved-limitations')
 if not show_completion:
  quality_note='<p data-acceptance-quality-limit>'+model_text(*pair('report_required_text_and_structure_checks_measure_declared_target_b54265', 'Required-text and structure checks measure declared target coverage, not a complete noise penalty. Passing a task does not establish the absence of navigation, ads or irrelevant text. Full-text precision is computed only where a comparable complete reference exists and does not measure noise for the remaining tasks.'))+'</p>'
  status=section(model_text(*pair('report_report_status_and_metric_limits_fc70fc', 'Report status and metric limits')),'<p data-saved-certification="not-conferred">'+esc(saved_matrix_text('certification_note'))+'</p>'+quality_note)
  return status+saved_execution_settings_section(scope.get('execution_settings',[]))+limitations
 rows=[]
 for item in scope['providers']:
  rows.append(('',cell(link('providers/'+item['provider']+'.html',provider_label(item['provider'])))+''.join(cell(str(item[k])) for k in ('planned','called','saved','normal','passed','rejected','uncommitted','not_called'))))
 missing=[]
 for item in scope['missing']:
  evidence=link(item.get('receipt_path') or item['record_path'],saved_matrix_text('receipt_link'))
  for old in item.get('history', []):
   evidence+='<br>'+link(old['record_path'],saved_matrix_text('history_title'))+' · '+link(old['scope_path'],saved_matrix_text('history_scope'))
  missing.append(('',cell(esc(item['suite']))+cell(esc(item['provider']))+cell(esc(item['case_id']))+cell(saved_matrix_text(item['status']))+cell(esc(item['error']))+cell(evidence)))
 return section(saved_matrix_text('completion_title'),'<p>'+saved_matrix_text('completion_note')+'</p>'+table(saved_matrix_text('completion_headers'),rows,'saved-completion',metrics=[])+(table(saved_matrix_text('missing_headers'),missing,'saved-missing',metrics=[]) if missing else ''),'completion')+limitations


def saved_suite_ranking(records,cases,suite,*,base='',id=None,diagnostic=False):
 """Summarize saved outcomes by declared input suite, including missing slots."""
 records,cases=comparison_population(records,cases)
 selected={k:c for k,c in cases.items() if report_family(c)==suite}
 rows=[]
 for provider in sorted({r['provider'] for r in records} | set(render_context().labels)):
  rs=[r for r in records if r['provider']==provider and r['case_id'] in selected]
  judged=[r for r in rs if measured(r,selected[r['case_id']])]
  passed=sum(succeeded(r,selected[r['case_id']]) for r in judged)
  values=cell(link(base+'providers/'+provider+'.html',provider_label(provider)))
  not_called=sum(r.get('report_invocation_status') == 'adapter_rejected' for r in rs)
  if len(rs)-not_called < len(selected):
   values=values.replace('</a>', '</a><br><small>'+saved_matrix_text('incomplete')+'</small>',1)
  counts=[len(selected),len(rs),len(selected)-len(rs)+not_called,sum(bool(r['ok']) for r in rs),passed]
  values+=''.join(cell(str(count),count) for count in counts)
  ratio=passed/len(judged) if judged else -1
  values+=cell(f'{passed}/{len(judged)} · '+rate(passed,len(judged)),ratio)
  rows.append((-ratio,provider,('',values)))
 rows=[row for _,_,row in sorted(rows,key=lambda item:item[:2])]
 eligible,rank_note=ranking_readiness(records,selected,participants=family_participants(records,selected))
 ranked=not diagnostic and eligible
 if ranked:rows=comparative_rows(rows,{6:('task_pass_rate','max')})
 scores=sorted([sum(succeeded(r,selected[r['case_id']]) for r in records if r['provider']==provider and r['case_id'] in selected) for provider in {r['provider'] for r in records}|set(render_context().labels)],reverse=True)
 return rank_note+'<p>'+saved_matrix_text('suite_denominator_note')+'</p>'+table(saved_matrix_text('suite_result_headers'),rows,id or 'ranking-'+suite,sortable=True,metrics=[],include_index=ranked,rank_values=shared_ranks(scores) if ranked else None,default_sort=(6+int(ranked),'desc'))


# Track-owned page sets of variants that do not render a ReportModel.
_TRACK_PAGE_SETS={'diagnostic-cohorts':_diagnostic_pages,'public-benchmark-research':_research_pages}
