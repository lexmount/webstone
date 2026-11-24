"""The WebMainBench sample and complete-cohort page sets, contributed to the Web Fetch template through the WebMainBench benchmark views."""
from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import statistics

from bs4 import BeautifulSoup
from collections import Counter, defaultdict
from harness.distribution import repository_root
from harness.record_io import load_jsonl
from harness.report_languages import write_language_contract
from harness.reporting.presentation import esc
from harness.reporting.style import STYLE
from harness.safety import safe_href
from harness.web_fetch.methods import browser_method_registry, method_access, native_browser_engine, report_method, report_method_catalog, with_measured_method_labels
from harness.web_fetch.providers.headless import engine_definition
from harness.web_fetch.report import EXTRA, JS, cell, hero, link, ms, pct, readable_output, section, table
from harness.web_fetch.report_changes import render_webmainbench_history
from harness.web_fetch.report_i18n import report_text, t
from harness.web_fetch.report_interfaces import _observed_route, method_spec
from harness.web_fetch.report_methods import PROFILES, adoption_counts
from harness.web_fetch.report_style import asset_text
from harness.web_fetch.template import role_attribute
from harness.web_fetch.webmainbench_diagnostics import ERROR_PAGE
from lexbench_webfetch.metrics import percentile
from lexbench_webfetch.paths import contained_path
from pathlib import Path
from urllib.parse import urlencode, urlsplit

_WMB_ROUTES=[
 ('datasets/index.html',t('dataset', 'Dataset'),'Dataset'),('methodology/index.html',t('methodology', 'Methodology'),'Methodology'),
 ('analysis/index.html',t('analysis', 'Analysis'),'Analysis'),('review/index.html',t('review_record', 'Review record'),'Review'),
 ('guide/index.html',t('interpretation', 'Interpretation'),'Guide'),('adoption/index.html',t('methods', 'Methods'),'Methods'),
 ('reproduce/index.html',t('reproduction', 'Reproduction'),'Reproduction'),('changes/index.html',t('changelog', 'Changelog'),'Changes'),
 ('accounts/index.html',t('access_setup', 'Access & setup'),'Accounts and setup'),
]


def _wmb_html_preview(source,english=False):
 soup=BeautifulSoup(source,'html.parser')
 # This is an inert reading view, never a replay of untrusted website scripts.
 for node in soup.select('script,iframe,object,embed,base,meta,link,svg,math,video,audio,source'):
  node.decompose()
 for node in soup.find_all(True):
  for attr in list(node.attrs):
   if attr.lower().startswith('on') or attr.lower() in {'src','srcset','href','action','formaction','background','ping','xlink:href','srcdoc'}:
    del node.attrs[attr]
 for img in soup.find_all('img'):
  placeholder=soup.new_tag('span')
  placeholder.string=(report_text('en' if english else 'zh')('image_not_loaded', '[Image not loaded'))+(': '+img.get('alt','') if img.get('alt') else '')+(']' if english else '］')
  img.replace_with(placeholder)
 policy="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; form-action 'none'; base-uri 'none'"
 document='<meta http-equiv="Content-Security-Policy" content="'+html.escape(policy,quote=True)+'"><style>body{margin:20px;color:#172331;background:white;overflow-wrap:anywhere}img,table{max-width:100%}pre{white-space:pre-wrap}</style>'+str(soup)
 title=report_text('en' if english else 'zh')('frozen_html_reading_view', 'Frozen HTML reading view')
 return '<iframe class="wmb-html-preview" sandbox="" referrerpolicy="no-referrer" loading="lazy" title="'+title+'" srcdoc="'+html.escape(document,quote=True)+'"></iframe>'


def _wmb_table_example(english=False):
 h=report_text('en' if english else 'zh')
 examples=[
  (h('recognized_complete', 'Recognized, complete'), '| Name | Value |\n| --- | --- |\n| A | 10 |\n| B | 20 |', '<table><tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>10</td></tr><tr><td>B</td><td>20</td></tr></table>',h('enters_table_comparison_an_exact_reference_match', 'Enters table comparison; an exact reference match scores full marks.')),
  (h('recognized_one_row_missing', 'Recognized, one row missing'), '| Name | Value |\n| --- | --- |\n| A | 10 |', '<table><tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>10</td></tr></table>',h('still_scored_missing_content_and_structure_reduce', 'Still scored; missing content and structure reduce the score. Complete recovery is not a prerequisite.')),
  (h('text_exists_table_not_recognized', 'Text exists, table not recognized'), 'Name Value A 10 B 20', '<p>Name Value A 10 B 20</p>',h('with_a_table_in_the_reference_both', 'With a table in the reference, both table scores are zero; this text still contributes to text_edit.')),
 ]
 return '<div class="wmb-table-examples">'+''.join('<article><h3>'+title+'</h3><pre>'+esc(md)+'</pre><div class="wmb-table-render">'+rendered+'</div><p>'+note+'</p></article>' for title,md,rendered,note in examples)+'</div><p class="source-note">'+h('these_are_rule_illustrations_not_new_measurements', 'These are rule illustrations, not new measurements. A table-like appearance is insufficient: the scorer must recognize a table in the output. Table_edit compares normalized table HTML strings; TEDS compares rows, cells and content. Missing structure affects both.')+'</p>'


def _wmb_denominator_example(records, english, case_count, provider=None):
 h=report_text('en' if english else 'zh')
 if not records:return ''
 provider=provider or records[0]['provider']
 selected=[row for row in records if row['provider']==provider]
 if any('_body_present' not in row or not row.get('metrics',{}).get('formula_edit') or (row.get('metrics',{}).get('formula_edit',{}).get('success') and not {'groundtruth_formula_length','predicted_formula_length'} <= set(row['metrics']['formula_edit'].get('details',{}))) for row in selected):return '<p>'+h('formula_scoring_population_unavailable', 'Formula-scoring population unavailable')+'</p>'
 counts=Counter()
 for row in records:
  if row['provider']!=provider:
   continue
  metric=row.get('metrics',{}).get('formula_edit')
  if not row['_body_present']:
   counts['no_body']+=1
  elif metric and metric.get('success'):
   detail=metric['details']
   counts[(bool(detail['groundtruth_formula_length']),bool(detail['predicted_formula_length']))]+=1
  elif metric and metric.get('error')=='Both predicted and groundtruth are empty':
   counts[(False,False)]+=1
  else:
   return '<p>'+h('formula_scoring_population_unavailable', 'Formula-scoring population unavailable')+'</p>'
 scored=sum(value for key,value in counts.items() if isinstance(key,tuple) and key!=(False,False))
 if sum(counts.values())!=case_count or scored!=counts[(False,True)]+counts[(True,False)]+counts[(True,True)]:
  raise ValueError('Formula denominator example does not cover the frozen cohort')
 successful=case_count-counts['no_body']
 flow_source='flowchart LR\n A["'+str(case_count)+h('frozen_pages', ' frozen pages')+'"] --> B{"'+h('final_call_succeeded', 'Final call succeeded?')+'"}\n B --> C["'+str(successful)+h('successful_pages', ' successful pages')+'"]\n C --> D["'+str(scored)+h('formula_scored_pages', ' formula-scored pages')+'"]'
 fallback='<div class="wmb-flow-fallback" data-wmb-flow-fallback><span>'+str(case_count)+h('frozen_pages', ' frozen pages')+'</span><span>'+str(successful)+h('calls_succeeded', ' calls succeeded; ')+str(counts['no_body'])+h('failed_and_are_not_scored', ' failed and are not scored')+'</span><span>'+str(counts[(False,False)])+h('pages_have_no_formula_on_either_side', ' pages have no formula on either side and are skipped; ')+str(scored)+h('pages_enter_the_formula_mean_including_zeros', ' pages enter the formula mean, including zeros')+'</span></div>'
 matrix='<div class="table-wrap"><table class="wmb-denominator-matrix"><thead><tr><th>'+h('reference_output', 'Reference / output')+'</th><th>'+h('no_output_formula', 'No output formula')+'</th><th>'+h('output_formula_present', 'Output formula present')+'</th></tr></thead><tbody>'
 matrix+='<tr><td>'+h('no_reference_formula', 'No reference formula')+'</td><td><b>'+str(counts[(False,False)])+'</b><span>'+h('both_empty_skip', 'Both empty: skip')+'</span></td><td><b>'+str(counts[(False,True)])+'</b><span>'+h('output_only_zero_enters', 'Output only: zero enters')+'</span></td></tr>'
 matrix+='<tr><td>'+h('reference_formula_present', 'Reference formula present')+'</td><td><b>'+str(counts[(True,False)])+'</b><span>'+h('reference_only_zero_enters', 'Reference only: zero enters')+'</span></td><td><b>'+str(counts[(True,True)])+'</b><span>'+h('both_present_similarity_enters', 'Both present: similarity enters')+'</span></td></tr></tbody></table></div>'
 return '<div class="wmb-denominator-example"><h3 id="denominator-example">'+h('formula_scoring_population', 'Formula-scoring population: ')+esc(provider)+' · '+str(scored)+' / '+str(case_count)+'</h3><div class="wmb-flow">'+fallback+'<pre class="mermaid" hidden>'+esc(flow_source)+'</pre></div>'+matrix+'<p class="source-note">'+h('the_formula_mean_uses_the_upper_right', 'The formula mean uses the upper-right, lower-left and lower-right cells: {scored} pages, including {value} zero scores. Recognition in the two Markdown files determines the denominator; it is neither the body-text count nor a count of correct pages.', scored=scored, value=counts[False, True] + counts[True, False])+'</p></div>'


def _wmb_latency_summary(records,names):
 """Final successful fetch time; retain all rows and historical execution cost."""
 by_method={name:[r for r in records if r['provider']==name] for name in names}
 successful={name:[r for r in rows if r['_body_present']] for name,rows in by_method.items()}
 common=set.intersection(*(set(r['case_id'] for r in rows) for rows in successful.values())) if names else set()
 def stats(values):
  return {'count':len(values),'mean':statistics.fmean(values) if values else None,'p50':statistics.median(values) if values else None,'p95':percentile(values,.95) if values else None}
 return {'common_cases':common,
         'successful':{name:stats([r['elapsed_ms'] for r in rows]) for name,rows in successful.items()},
         'matched':{name:stats([r['elapsed_ms'] for r in rows if r['case_id'] in common]) for name,rows in successful.items()},
         'cost':{name:stats([r['total_elapsed_ms'] for r in rows]) for name,rows in by_method.items()}}


def _wmb_table_population(records,names,labels,english=False,prefix='../'):
 h=report_text('en' if english else 'zh')
 reference_pages={r['case_id'] for r in records if r.get('metrics',{}).get('table_edit',{}).get('details',{}).get('groundtruth_table_length',0)>0}
 rows=[]
 for name in names:
  eligible=extra=0;example=None
  for r in records:
   if r['provider']!=name or not r.get('ok'):continue
   detail=r.get('metrics',{}).get('table_edit',{}).get('details',{})
   gt=detail.get('groundtruth_table_length',0)>0;pred=detail.get('predicted_table_length',0)>0
   eligible+=gt
   if pred and not gt:extra+=1;example=example or r['attempt_id']
  rows.append(('',cell(link(prefix+'providers/'+name+'.html',labels[name]))+cell(str(eligible),eligible)+cell(str(extra),extra)+cell('<b>'+str(eligible+extra)+'</b>',eligible+extra)+cell(link(prefix+'attempts/'+example+'.html',h('inspect_output_only_tables', 'Inspect output-only tables')) if example else '—')))
 return '<div class="wmb-table-population"><h3>'+h('why_do_table_scoring_counts_differ', 'Why do table-scoring counts differ?')+'</h3><p>'+h('every_page_has_a_human_reference_but', 'Every page has a human reference, but only {count} references contain recognizable tables. After a successful call, every reference-table page is scored. A page with tables only in the output also enters the average at zero. Only pages with no tables on either side are skipped.', count=len(reference_pages))+'</p>'+table([h('method', 'Method'),h('reference_table_successful_call', 'Reference table, successful call'),h('output_only_table', 'Output-only table'),h('scored_pages_sum', 'Scored pages (sum)'),h('raw_evidence', 'Raw evidence')],rows,cls='wmb-table-population-table',metrics=[])+'<p class="source-note">'+h('output_only_tables_may_come_from_page', 'Output-only tables may come from page regions excluded from the main-content reference, or structures parsed as tables; the human reference is not missing. Two Kitesurf calls on reference-table pages failed and were excluded before scoring.')+'</p></div>'


def _wmb_timing_cell(stats,name,key):
 value=stats[name][key]
 values=[item[key] for item in stats.values() if item[key] is not None]
 minimum=min(values) if values else None
 marked=value is not None and value==minimum and values.count(minimum)==1
 content=ms(value)
 if marked:content='<strong class="comparison-winner">'+content+'</strong>'
 return cell(content,value).replace('<td', '<td data-metric-direction="min" data-metric="'+key+'"',1)


def _wmb_timing_note(english=False,matched=False):
 if matched:return (report_text('en' if english else 'zh')('green_marks_the_unique_minimum_in_each', 'Green marks the unique minimum in each column. P50 is the median; P95 is the time within which 95% finish.'))
 return (report_text('en' if english else 'zh')('green_marks_the_unique_maximum_score_or', 'Green marks the unique maximum score or minimum duration in this column. Timing uses final successful fetches only. P50: half finish within this time; P95: 95% finish within this time. Success counts define the timing population; scoring counts do not.'))


def _wmb_matched_latency(records,names,labels,english=False,prefix='../'):
 h=report_text('en' if english else 'zh')
 data=_wmb_latency_summary(records,names);common=data['common_cases']
 excluded=sorted({r['case_id'] for r in records}-common)
 rows=[]
 for name in names:
  rows.append(('',cell(link(prefix+'providers/'+name+'.html',labels[name]))+''.join(_wmb_timing_cell(data['matched'],name,key) for key in ('mean','p50','p95'))))
 return '<div class="wmb-matched-latency"><h3>'+h('matched_page_timing_pages_with_success_across', 'Matched-page timing: {count} pages with success across all methods', count=len(common))+'</h3><p>'+h('every_row_uses_the_same_pages_and', 'Every row uses the same {count} pages and the duration of the final successful fetch only. This diagnoses same-page timing differences. The shared-success subset is determined after execution, so it does not rank speed over the entire declared benchmark.', count=len(common))+'</p><p class="source-note">'+_wmb_timing_note(english,matched=True)+'</p>'+table([h('method', 'Method'),h('mean_latency_2', 'Mean latency'),h('p50_latency', 'P50 latency'),h('p95_latency', 'P95 latency')],rows,cls='wmb-matched-latency-table',metrics=[])+'<details><summary>'+h('pages_outside_this_comparison_retained_in_the', '{count} pages outside this comparison (retained in the full matrix)', count=len(excluded))+'</summary><ul>'+''.join('<li>'+link(prefix+'datasets/'+case_id+'.html',case_id)+'</li>' for case_id in excluded)+'</ul></details></div>'


def _wmb_latency_rerun(diagnostic,english=False,prefix='',formal_round=None):
 """Render recorded crossover summaries without assuming versions or cohort size."""
 h=report_text('en' if english else 'zh')
 if not diagnostic:return ''
 runs=diagnostic['balanced_200']['runs']
 rows=[]
 for version,run in runs.items():
  values=[]
  for key in ('mean','p50','p95'):
   comparable=[other[key] for other in runs.values()]
   value=run[key]
   won=value==min(comparable) and comparable.count(value)==1
   content='<strong class="comparison-winner">'+ms(value)+'</strong>' if won else ms(value)
   values.append(cell(content,value).replace('<td',f'<td data-metric="rerun-{key}" data-metric-direction="min"',1))
  rows.append(('',cell(esc(version))+cell(f"{run['n']}/{run['calls']}")+''.join(values)))
 body='<p>'+h('mean_p50_and_p95_use_successful_calls', 'Mean, P50 and P95 use successful calls with body text; successful and total call counts are shown together. Versions, matched tasks, call order and the selected round follow the bound receipts; failed calls remain retained.')+'</p>'
 if formal_round is not None:
  body+=f'<p class="source-note" data-timing-context="separate-diagnostic" data-formal-round="{int(formal_round)}">'+h('this_table_retains_an_earlier_diagnostic_formal', 'This table retains an earlier diagnostic. Formal home-page timing comes from preselected round {formal_round} of a separate certified receipt. Their calls and environment conditions are bound separately and are not one measurement.', formal_round=formal_round)+'</p>'
 body+=table([h('version', 'Version'),h('successful_all_calls', 'Successful / all calls'),h('mean_latency_2', 'Mean latency'),h('p50_latency', 'P50 latency'),h('p95_latency', 'P95 latency')],rows,cls='wmb-rerun-table',metrics=[])
 tail=diagnostic['tail_repeat'];cases=tail['cases']
 outliers=[row for row in cases if abs(row['older_median_ms']-row['newer_median_ms'])>20]
 if tail['within_20_ms']+len(outliers)!=len(cases):
  raise ValueError('Latency tail summary disagrees with the per-page evidence')
 if cases:
  body+='<details><summary>'+h('inspect_repeated_calls_for_pages', 'Inspect repeated calls for {count} pages', count=len(cases))+'</summary>'
  body+=table([h('page', 'Page'),h('earlier_version_median', 'Earlier-version median'),h('later_version_median', 'Later-version median'),h('earlier_version_maximum', 'Earlier-version maximum'),h('later_version_maximum', 'Later-version maximum')],[('',cell(link(prefix+'datasets/'+row['case_id']+'.html',row['case_id']))+cell(ms(row['older_median_ms']),row['older_median_ms'])+cell(ms(row['newer_median_ms']),row['newer_median_ms'])+cell(ms(row['older_max_ms']),row['older_max_ms'])+cell(ms(row['newer_max_ms']),row['newer_max_ms'])) for row in cases],cls='wmb-rerun-cases',metrics=[])
  body+='</details>'
 resource_rows=[]
 for name,resource in diagnostic['resource'].items():
  resource_rows.append(('',cell(esc(name))+cell(str(resource['samples']))+cell(f"{resource['min_idle_cpu_pct']:.1f}%")+cell(f"{resource['min_unused_memory_mb']:.0f} MB")+cell(str(resource['swapouts_delta']))))
 newer_faster=sum(row['newer_median_ms']<row['older_median_ms'] for row in outliers)
 older_faster=len(outliers)-newer_faster
 body+=f'<p data-tail-outliers="{len(outliers)}" data-tail-newer-faster="{newer_faster}" data-tail-older-faster="{older_faster}">'+h('pages_were_repeated_among_pages_differing_by', '{count} pages were repeated; among pages differing by more than 20 ms in median latency, the later version was faster on {newer_faster} and the earlier version on {older_faster}.', count=len(cases), newer_faster=newer_faster, older_faster=older_faster)+'</p>'
 failure=diagnostic.get('failure_retest') or {}
 if failure.get('case_id'):
  body+='<p>'+h('failure_retests_record_calls_per_version_formal', 'Failure retests record {get} calls per version; formal failed results remain unchanged.', get=failure.get('attempts_per_version', '—'))+' '+link(prefix+'datasets/'+failure['case_id']+'.html',h('inspect_the_failed_page', 'Inspect the failed page'))+'</p>'
  if failure.get('script_disabled_returns_body'):
   body+='<p>'+h('disabling_page_authored_javascript_returned_body_text', 'Disabling page-authored JavaScript returned body text in the diagnostic calls. This changed the execution condition and does not replace formal outcomes.')+'</p>'
 body+=table([h('recorded_run', 'Recorded run'),h('system_samples', 'System samples'),h('minimum_idle_cpu', 'Minimum idle CPU'),h('minimum_unused_memory', 'Minimum unused memory'),h('new_swap_outs', 'New swap-outs')],resource_rows,cls='wmb-rerun-resource',metrics=[])
 body+='<p class="source-note">'+h('host_samples_alone_do_not_establish_that', 'Host samples alone do not establish that load caused slow calls. Responses, failure retests and evidence bindings remain in the source receipt.')+' '+link(('../' if english else '')+prefix+'latency-diagnostic.json',h('download_repeated_call_evidence', 'Download repeated-call evidence'))+'</p>'
 return '<div class="wmb-rerun-diagnostic">'+body+'</div>'


def _wmb_guide(*, english, names, labels, cases, bycase, case_count, score, denom,
               failures, out=None, latency_diagnostic=None, formal_round=None):
 h=report_text('en' if english else 'zh')
 records=[row for rows in bycase.values() for row in rows]
 metrics=['overall','text_edit','code_edit','formula_edit','table_edit','table_TEDS']
 def chapter(key,title,body):return '<section id="'+key+'"><h2>'+title+'</h2>'+body+'</section>'
 def paragraph(key,source,**values):return '<p>'+h(key,source,**values)+'</p>'
 def highlighted(name,metric):
  value=score(name,metric)
  values=[score(candidate,metric) for candidate in names]
  present=[v for v in values if v is not None]
  winner=value is not None and present and value==max(present) and present.count(value)==1
  text=_wmb_pct(value)
  return '<span class="comparison-winner">'+text+'</span>' if winner else text
 rows=[]
 for name in names:
  body=cell(link('../providers/'+name+'.html',labels.get(name,name)))
  for metric in metrics:
   body+=cell(highlighted(name,metric)+'<small>'+str(denom(name,metric) or 0)+' / '+str(case_count)+'</small>',score(name,metric))
  rows.append(('',body))
 overall=chapter('overall',h('current_results', 'Current results'),
  paragraph('this_run_contains_pages_and_methods_scores', 'This run contains {case_count} pages and {count} methods. Scores come from saved results; counts below each score show its population. Green marks the unique column maximum.', case_count=case_count, count=len(names))+
  table([h('method', 'Method'),h('overall', 'Overall'),h('full_text_similarity', 'Full-text similarity'),h('code_similarity', 'Code similarity'),h('formula_similarity', 'Formula similarity'),h('table_content_similarity', 'Table-content similarity'),h('table_structure_similarity', 'Table-structure similarity')],rows,cls='wmb-guide-results',metrics=[]))
 scope=chapter('scope',h('inputs_and_scoring_scope', 'Inputs and scoring scope'),
  paragraph('inputs_are_frozen_html_and_references_cover', 'Inputs are frozen HTML and references cover selected main content. Similarity reflects omissions, additional content and formatting; it is not a direct fraction of body text retrieved. Component populations may differ.')+
  _wmb_denominator_example(records,english,case_count))
 def specimen(metric):
  if not cases:return paragraph('no_task_is_available', 'No task is available.')
  case_id=next(iter(cases))
  selected=bycase.get(case_id,[])
  stats=[]
  for row in selected:
   details=row.get('metrics',{}).get(metric,{})
   length=(details.get('details') or {}).get('predicted_text_length')
   length_text=f'{length:,}' if length is not None else h('length_not_recorded', 'Length not recorded')
   stats.append('<span>'+esc(labels.get(row['provider'],row['provider']))+': '+_wmb_pct(details.get('score'))+' · '+length_text+'</span>')
  evidence=''
  if out is not None:
   paths=[(h('reference', 'Reference'),Path('references')/(case_id+'.md'))]
   if selected:paths.append((labels.get(selected[0]['provider'],selected[0]['provider']),Path(selected[0]['artifact_path'])))
   for title,relative in paths:
    path=contained_path(out,relative,reject_symlinks=True)
    if path.is_file():
     evidence+='<article><h3>'+esc(title)+'</h3><pre>'+esc(path.read_text(encoding='utf-8')[:550])+'</pre></article>'
  return '<div class="wmb-case-study">'+link('../datasets/'+case_id+'.html',h('inspect_the_task_and_every_output', 'Inspect the task and every output'))+'<div class="wmb-evidence-pair">'+evidence+'</div><div class="wmb-case-stats">'+''.join(stats)+'</div></div>'
 content=chapter('content',h('output_and_reference', 'Output and reference'),specimen('text_edit'))
 tables=chapter('tables',h('table_recognition_and_structure', 'Table recognition and structure'),
  _wmb_table_example(english)+_wmb_table_population(records,names,labels,english)+specimen('table_TEDS'))
 version=chapter('version-differences',h('evidence_of_method_differences', 'Evidence of method differences'),
  paragraph('per_page_results_retain_outputs_component_scores', 'Per-page results retain outputs, component scores and errors for each method. Causes of version differences require review evidence bound to these results; aggregate scores alone do not establish them.')+
  link('../analysis/index.html',h('compare_same_page_results', 'Compare same-page results')))
 timing=_wmb_latency_summary([row for row in records if '_body_present' in row],names)['successful']
 timing_rows=[]
 for name in names:
  item=timing[name]
  values=cell(labels.get(name,name))+cell(item['count'])
  for key in ('mean','p50','p95'):
   present=[value[key] for value in timing.values() if value[key] is not None]
   value=item[key]
   winner=value is not None and present and value==min(present) and present.count(value)==1
   display=ms(value)
   values+=cell('<span class="comparison-winner">'+display+'</span>' if winner else display,value)
  timing_rows.append(('',values))
 delivery=chapter('delivery',h('delivery_and_latency', 'Delivery and latency'),
  paragraph('mean_p50_and_p95_use_each_method', 'Mean, P50 and P95 use each method’s final calls that successfully deliver body text, with sample counts shown. Failed-call and retry execution costs remain in the result evidence.')+
  table([h('method', 'Method'),h('delivered_pages', 'Successful pages'),h('mean_latency_2', 'Mean latency'),'P50','P95'],timing_rows,cls='wmb-guide-latency',metrics=[]))
 if latency_diagnostic is not None:
  delivery=delivery.removesuffix('</section>')+_wmb_latency_rerun(latency_diagnostic,english,prefix='../',formal_round=formal_round)+'</section>'
 decision=chapter('decision',h('using_these_results', 'Using these results'),
  paragraph('use_component_scores_and_per_page_evidence', 'Use component scores and per-page evidence for the required output. Conclusions apply to the declared inputs, versions and conditions; a new experiment needs its own evidence and interpretation.'))
 return '<div class="wrap wmb-editorial"><article><h1>'+h('reading_the_results', 'Reading the results')+'</h1>'+overall+scope+content+tables+version+delivery+decision+'</article></div>'


def _wmb_write(out,path,title,body,english=False):
 if english:
  body=body.replace(('<div class="eyebrow">' + t('web_fetch_evaluation_report', 'Web Fetch · Evaluation report') + '</div>'),'<div class="eyebrow">Web Fetch · Measured report</div>')
  body=body.replace('aria-label="内容选择"','aria-label="Choose content"').replace(('<th>' + t('no', 'No.') + '</th>'),'<th>No.</th>')
 local=('en/'+path) if english else path;depth=len(Path(local).parts)-1;base='../'*depth
 nav=''.join(link(base+('en/' if english else '')+route,en if english else zh) for route,zh,en in _WMB_ROUTES)
 brand=link(base+('en/index.html' if english else 'index.html'),'Web Fetch')
 counterpart=base+path if english else base+'en/'+path
 language=f'''<a href="{safe_href(counterpart)}">{('中文' if english else 'English')}</a>'''
 footer=(report_text('en' if english else 'zh')('scores_apply_to_the_frozen_inputs_and', 'Scores apply to the frozen inputs and methods declared in this report. They do not represent the live web or the official WebMainBench leaderboards.'))
 page=f'''<!doctype html><html lang="{('en' if english else 'zh-CN')}"><head><meta name="webstone-report" content="1"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Web Fetch</title><link rel="stylesheet" href="{base}assets/style.css"><script src="{base}assets/report.js" defer></script></head><body><header class="top"><nav class="wrap">{brand}{nav}{language}<button class="theme-toggle" onclick="theme()">{report_text('en' if english else 'zh')('toggle_theme', 'Theme')}</button></nav></header><main{role_attribute(path)}>{body}</main><footer class="wrap source-note"><p>{footer}</p></footer></body></html>'''
 page=page.replace(f'href="{base}assets/style.css"',f'href="{base}assets/style.css?v={hashlib.sha256((STYLE+EXTRA).encode()).hexdigest()[:16]}"').replace(f'src="{base}assets/report.js"',f'src="{base}assets/report.js?v={hashlib.sha256(JS.encode()).hexdigest()[:16]}"')
 target=contained_path(out,local);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(page,encoding='utf-8')


def _wmb_category(case,axis):
 value=case['meta'].get(axis)
 return '+'.join(value) if isinstance(value,list) and value else ('none' if isinstance(value,list) else str(value or 'unavailable'))


def _wmb_inspect(base='analysis/index.html',**filters):
 values={key:value for key,value in filters.items() if value is not None and value!=''}
 return base+'#inspect?'+urlencode(values)


def _wmb_controls(cases,method_labels,english=False,include_method=True,include_metric=True,default_request='',failure_types=(),include_delivery=True):
 h=report_text('en' if english else 'zh')
 axes=('language','level','table','code','equation')
 axis_labels={'language':h('language', 'Language'),'level':h('difficulty', 'Difficulty'),'table':h('table_type', 'Table type'),'code':h('code_type', 'Code type'),'equation':h('formula_type', 'Formula type')}
 categories=sorted({_wmb_category(case,axis) for case in cases.values() for axis in axes})
 fields=[]
 def select(key,label,options,default=''):
  entries=''.join(f'<option value="{esc(value)}"'+(' selected' if value==default else '')+f'>{esc(text)}</option>' for value,text in options)
  fields.append(f'<label>{label}<select data-wmb-filter="{key}" data-wmb-default="{esc(default)}" aria-label="{label}">{entries}</select></label>')
 if include_method:
  select('method',h('method', 'Method'),[('',h('all_methods', 'All methods'))]+[(key,value) for key,value in method_labels.items()])
 select('axis',h('category_dimension', 'Category dimension'),[('',h('all_categories', 'All categories'))]+[(axis,axis_labels[axis]) for axis in axes])
 select('category',h('category', 'Category'),[('',h('all', 'All'))]+[(value,value) for value in categories])
 if include_delivery:
  select('request',h('body_text_produced', 'Body text produced'),[('',h('all', 'All')),('failed',h('no_body_text', 'No body text')),('success',h('body_text_produced_2', 'Body text produced'))],default_request)
  if failure_types:
   select('failure',h('error_type', 'Error type'),[('',h('all', 'All'))]+[(value,value) for value in failure_types])
 if include_metric:
  select('metric',h('inspect_metric', 'Inspect metric'),[('',h('all_metrics', 'All metrics'))]+[(key,key) for key in ('text_edit','code_edit','formula_edit','table_edit','table_TEDS')])
  select('band',h('metric_score_band', 'Metric score band'),[('',h('any', 'Any')),("0:0.1",'0–10%'),("0:0.25",'0–25%'),("0.25:0.5",'25–50%'),("0.5:0.75",'50–75%'),("0.75:1",'75–100%'),('unavailable',h('unavailable_not_applicable', 'Unavailable / not applicable'))])
 fields.append(f'''<label>{h('search_track_id_url_or_error', 'Search track ID, URL or error')}<input data-wmb-filter="query" aria-label="{h('search', 'Search')}" placeholder="{h('enter_keywords', 'Enter keywords')}"></label>''')
 return '<div class="wmb-controls">'+''.join(fields)+'</div><div class="wmb-toolbar"><strong data-wmb-count></strong><button type="button" onclick="wmbReset()">'+h('clear_filters', 'Clear filters')+'</button></div>'


def _wmb_row_attrs(case,record=None):
 parts=[]
 for axis in ('language','level','table','code','equation'):
  parts.append(f' data-{axis}="{esc(_wmb_category(case,axis))}"')
 if record:
  error=str(record.get('error_message') or '')
  # Search a bounded excerpt, including the terminal error; full logs stay in evidence.
  if len(error)>1024:error=error[:512]+' … '+error[-512:]
  parts.extend([f' data-method="{esc(record["provider"])}"',f' data-request="{"success" if record.get("_body_present",record["ok"]) else "failed"}"',f' data-failure="{esc(record.get("_output_error") or record.get("error_type") or "")}"',
                f' data-search="{esc(case["id"]+" "+case["url"]+" "+str(record.get("error_type") or "")+" "+error)}"'])
  for metric in ('text_edit','code_edit','formula_edit','table_edit','table_TEDS'):
   item=record.get('metrics',{}).get(metric,{})
   parts.append(f' data-{metric}="{item.get("score") if item.get("success") else ""}"')
 else:
  parts.append(f' data-search="{esc(case["id"]+" "+case["url"])}"')
 return ''.join(parts)


def _wmb_pct(value):
 if value is not None and 0<value<.0005:return f'{value*100:.6g}%'
 return pct(value)


def _wmb_metric_cell(record,metric):
 item=record.get('metrics',{}).get(metric,{})
 return cell(_wmb_pct(item.get('score')) if item.get('success') else '—',item.get('score') if item.get('success') else -1)


def _wmb_error_page(text):
 return bool(ERROR_PAGE.search(text))


def _wmb_output_label(record,text,english=False):
 chars=len(text)
 if _wmb_error_page(text):return (report_text('en' if english else 'zh')('error_page_in_output', 'Error page in output'))+f' · {chars:,}'
 if not record.get('ok'):
  return ((report_text('en' if english else 'zh')('error_response', 'Error response'))+f' · {chars:,}' if chars else (report_text('en' if english else 'zh')('no_readable_response', 'No readable response')))
 if not chars:return report_text('en' if english else 'zh')('empty_body_text', 'Empty body text')
 return report_text('en' if english else 'zh')('body_text_characters', 'Body text · {chars:,} characters', chars=chars)


def _wmb_body_state(record,text,english=False):
 if _wmb_error_page(text):return report_text('en' if english else 'zh')('error_page_not_target_content', 'Error page, not target content')
 if not record.get('ok') or not text.strip():return report_text('en' if english else 'zh')('no_body_text', 'No body text')
 return report_text('en' if english else 'zh')('body_text_produced_3', 'Body text produced')


def _wmb_filter_table(headers,rows,id,english=False):
 result=table(headers,rows,id,True,metrics=[],index_header=report_text('en' if english else 'zh')('no', 'No.'))
 return result.replace('<div class="table-wrap">','<div class="table-wrap wmb-table-wrap">',1).replace('<table class=""','<table class="wmb-table" data-wmb-table',1)


def _wmb_comparison(cases,bycase,names,labels,english=False):
 h=report_text('en' if english else 'zh')
 default_a=names[0]
 options=''.join(f'<option value="{esc(name)}"'+(' selected' if name==default_a else '')+f'>{esc(labels[name])}</option>' for name in names)
 relations=[('mixed',h('body_text_outcome_differs', 'Body-text outcome differs')),('a_success_others_fail',h('a_produced_body_text_others_did_not', 'A produced body text; others did not')),('a_fail_others_success',h('a_produced_no_body_text_others_did', 'A produced no body text; others did')),('all_success',h('all_selected_methods_produced_body_text', 'All selected methods produced body text')),('all_fail',h('no_selected_method_produced_body_text', 'No selected method produced body text')),('exact_success',h('only_selected_methods_produced_body_text', 'Only selected methods produced body text'))]
 relation_options=''.join(f'<option value="{value}">{esc(label)}</option>' for value,label in relations)
 axes=('language','level','table','code','equation')
 axis_labels={'language':h('language', 'Language'),'level':h('difficulty', 'Difficulty'),'table':h('table_type', 'Table type'),'code':h('code_type', 'Code type'),'equation':h('formula_type', 'Formula type')}
 axis_options='<option value="">'+h('all_categories', 'All categories')+'</option>'+''.join(f'<option value="{axis}">{axis_labels[axis]}</option>' for axis in axes)
 categories=sorted({_wmb_category(case,axis) for case in cases.values() for axis in axes})
 category_options='<option value="">'+h('all', 'All')+'</option>'+''.join(f'<option value="{esc(value)}">{esc(value)}</option>' for value in categories)
 checks=''.join('<label'+(' class="disabled"' if name==default_a else '')+f'><input type="checkbox" data-wmb-compare-method value="{esc(name)}"'+(' disabled' if name==default_a else ' checked' if name==names[1] else '')+f'> {esc(labels[name])}</label>' for name in names)
 controls='<div class="comparison-controls"><label>'+h('method_a', 'Method A')+'<select id="wmb-compare-a" data-wmb-compare-control>'+options+'</select></label><label>'+h('comparison_condition', 'Comparison condition')+'<select id="wmb-compare-relation" data-wmb-compare-control>'+relation_options+'</select></label><label>'+h('category_axis', 'Category axis')+'<select id="wmb-compare-axis" data-wmb-compare-control>'+axis_options+'</select></label><label>'+h('category', 'Category')+'<select id="wmb-compare-category" data-wmb-compare-control>'+category_options+'</select></label></div><fieldset class="comparison-methods"><legend>'+h('methods_to_compare_multiple_selection', 'Comparison methods (select multiple)')+'</legend><div class="method-checks">'+checks+'</div></fieldset>'
 rows=[]
 for case in cases.values():
  results={r['provider']:r for r in bycase[case['id']]}
  delivered=[name for name in names if results[name]['_body_present']]
  failed=[name for name in names if not results[name]['_body_present']]
  def pills(selected,kind,results=results):
   return '<span class="outcome-list">'+''.join('<a class="outcome-pill '+kind+'" href="../attempts/'+esc(results[name]['attempt_id'])+'.html">'+esc(labels[name])+'</a>' for name in selected)+'</span>' if selected else '—'
  values=cell(link('../datasets/'+case['id']+'.html',case['id']))+cell(esc(_wmb_category(case,'language'))+' · '+esc(_wmb_category(case,'level')))+cell(pills(delivered,'pass'))+cell(pills(failed,'fail'))
  attrs=' data-delivered="'+esc(' '.join(delivered))+'" data-failed="'+esc(' '.join(failed))+'"'+_wmb_row_attrs(case)
  rows.append((attrs,values))
  # All selected frozen pages remain in the DOM; filtering is client-side and evidence links stay per call.
 result='<div class="method-comparison"><div class="comparison-heading"><div><h3>'+h('body_text_on_the_same_page', 'Body text on the same page')+'</h3><p>'+h('this_only_compares_whether_non_empty_markdown', 'This only compares whether non-empty Markdown body text was produced by the deadline; it is not a content-quality pass. Open a method for scores, the human reference and actual output.')+'</p></div><strong class="comparison-count" id="wmb-compare-count"></strong></div>'+controls+'<div class="table-wrap"><table class="wmb-comparison-table"><thead><tr><th>'+h('no', 'No.')+'</th><th>Track ID</th><th>'+h('category', 'Category')+'</th><th>'+h('methods_with_body_text', 'Methods with body text')+'</th><th>'+h('methods_without_body_text', 'Methods without body text')+'</th></tr></thead><tbody id="wmb-compare-rows">'+''.join(f'<tr{attrs}><td>{i+1}</td>{values}</tr>' for i,(attrs,values) in enumerate(rows))+'</tbody></table></div></div>'
 return result


def _wmb_result_detail(r,c,output,reference,labels,metric_labels,failure_notes,corrected,english=False):
 h=report_text('en' if english else 'zh')
 dialogs=[]
 def button(target,label):
  return '<button type="button" class="call-log-button" data-call-log-open="'+target+'" aria-haspopup="dialog">'+esc(label)+'</button>'
 def dialog(target,title,body):
  dialogs.append('<dialog class="call-log-dialog wmb-detail-dialog" id="'+target+'" aria-labelledby="'+target+'-title"><div class="call-log-shell"><header><h2 id="'+target+'-title">'+esc(title)+'</h2><button type="button" data-call-log-close aria-label="'+h('close', 'Close')+'">'+h('close', 'Close')+'</button></header><div class="wmb-dialog-content">'+body+'</div></div></dialog>')
  return button(target,title)
 raw_prefix='../../' if english else '../'
 metadata=r.get('provider_metadata') or {}
 case_id=c['id'];failed=not r['_body_present']
 body=hero(labels[r['provider']],h('page_id', 'Page ID: ')+case_id)
 body+='<nav class="wrap actions">'+link('../analysis/index.html',h('all_results', 'All results'))+' · '+link('../analysis/index.html#inspect?query='+case_id,h('compare_methods_on_this_page', 'Compare methods on this page'))+' · '+link('../datasets/'+case_id+'.html#frozen-input',h('frozen_input_and_reference', 'Frozen input and reference'))+'</nav>'
 summary=[(h('result', 'Outcome'),_wmb_body_state(r,output,english)),(h('final_call_latency', 'Final-call latency'),ms(r['elapsed_ms'])),(h('per_page_score', 'Per-page score'),_wmb_pct(r.get('metrics',{}).get('overall',{}).get('score')) if r.get('metrics',{}).get('overall',{}).get('success') else '—')]
 body+=section(h('result_summary', 'Result summary'),'<div class="wmb-attempt-summary" data-result-summary>'+''.join('<article class="card"><b>'+esc(k)+'</b><strong>'+esc(v)+'</strong></article>' for k,v in summary)+'</div><p class="source-note">'+h('body_text_does_not_imply_quality_acceptance', 'Body text does not imply quality acceptance. Per-page score averages this page’s available components; the home score averages five cohort means.')+'</p>','result-summary')
 # Exact error and available stderr streams have one owner: the log dialog.
 error=r.get('error_message') or (output if _wmb_error_page(output) else '')
 streams=[]
 if error:streams.append((h('final_error_verbatim', 'Final error verbatim'),str(error),'error'))
 for key in ('stderr','browser_stderr','stdout'):
  value=metadata.get(key)
  if value and not any(str(value)==text for _,text,_ in streams):streams.append((key,str(value),key))
 log_html='<p>'+h('these_are_recorded_fields_from_the_final', 'These are recorded fields from the final call; warnings are not automatically root causes.')+'</p>'
 for label,text,kind in streams:
  lines=text.splitlines(keepends=True)
  matches=[i for i,line in enumerate(lines) if re.search(r'(?i)(\berror\b|^reason:|ERR_[A-Z_]+)',line)]
  locator=(matches[-1] if matches else len(lines)-1)
  target='log-'+kind+'-'+str(locator)
  log_html+='<h3>'+esc(label)+'</h3><button type="button" class="call-log-button" data-log-line="'+target+'">'+h('locate_last_error_record', 'Locate last error record')+'</button><pre class="wmb-log" data-kind="'+kind+'">'+''.join('<span id="log-'+kind+'-'+str(i)+'"'+(' class="wmb-log-focus" tabindex="-1"' if i==locator else '')+'>'+esc(line)+'</span>' for i,line in enumerate(lines))+'</pre>'
 if not streams:log_html+='<p data-log-unavailable>'+h('no_error_or_output_logs_were_retained', 'No error or output logs were retained for this call.')+'</p>'
 status={key:r.get(key) for key in ('ok','status_code','error_type','started_at','completed_at','elapsed_ms')}
 status.update({key:metadata[key] for key in ('exit_code','browser_exit_code','upstream_status','completion','stderr_truncated','stdout_truncated') if key in metadata})
 log_html+='<details><summary>'+h('call_status_and_timestamps', 'Call status and timestamps')+'</summary><pre>'+esc(json.dumps(status,ensure_ascii=False,indent=2))+'</pre></details>'
 log_button=dialog('result-logs',h('errors_and_runtime_logs', 'Errors and runtime logs'),log_html)
 if failed:
  reviewed=(r['provider'],case_id) in failure_notes
  observed,cause=failure_notes.get((r['provider'],case_id),('',''))
  stage=h('output_validation', 'Output validation') if r['ok'] else h('conversion_call_internal_stage_not_recorded', 'Conversion call (internal stage not recorded)')
  if not r['ok'] and re.search(r'(?i)(failed to navigate|failed to fetch|curl request failed)',str(error)):stage=h('navigation_or_page_request', 'Navigation or page request')
  diagnosis=[(h('failure_location', 'Failure location'),esc(stage)),(h('program_error_category', 'Program error category'),'<code>'+esc(r.get('_output_error') or r.get('error_type') or 'empty_body')+'</code>'),(h('reviewed_cause', 'Reviewed cause'),esc(cause) if reviewed else h('unverified', 'Unverified'))]
  body+=section(h('failure_diagnosis', 'Failure diagnosis'),'<dl class="wmb-diagnosis" data-failure-diagnosis>'+''.join('<dt>'+k+'</dt><dd>'+v+'</dd>' for k,v in diagnosis)+'</dl><p>'+log_button+' '+button('result-output',h('inspect_returned_content', 'Inspect returned content'))+'</p>'+('<details><summary>'+h('review_evidence', 'Review evidence')+'</summary><p>'+esc(observed)+'</p>'+link(raw_prefix+'webmainbench-anomaly-review.json',h('full_review_record', 'Full review record'))+'</details>' if reviewed else ''),'failure-diagnosis')
 metric_rows=[]
 for metric,label in metric_labels.items():
  item=r.get('metrics',{}).get(metric,{})
  detail='<pre data-kind="score">'+esc(json.dumps(item,ensure_ascii=False,indent=2))+'</pre>'
  if corrected and metric in ('overall','table_TEDS'):
   detail+='<details><summary>'+h('original_official_score', 'Original official score')+'</summary><pre>'+esc(json.dumps(r.get('official_metrics',{}).get(metric,{}),ensure_ascii=False,indent=2))+'</pre></details>'
  open_score=dialog('score-'+metric,label+' · '+h('scoring_evidence', 'Scoring evidence'),detail)
  metric_rows.append(('',cell(esc(label))+cell(h('calculated', 'Calculated') if item.get('success') else h('not_calculated', 'Not calculated'))+cell(_wmb_pct(item.get('score')) if item.get('success') else '—')+cell(open_score)))
 body+=section(h('component_scores', 'Component scores'),table([h('metric', 'Metric'),h('status', 'Status'),h('score', 'Score'),h('evidence_3', 'Evidence')],metric_rows,cls='wmb-detail-scores',metrics=[]),'component-scores')
 output_html='<p>'+link(raw_prefix+r['artifact_path'],h('download_method_output', 'Download method output'))+' · '+link(raw_prefix+'references/'+case_id+'.md',h('download_human_reference', 'Download human reference'))+'</p><div class="evidence-compare wmb-evidence">'+''.join('<div class="evidence-pane"><header><h3>'+title+'</h3></header><div class="pane-body"><pre data-kind="'+kind+'">'+esc(text)+'</pre></div></div>' for title,kind,text in [(h('human_reference', 'Human reference'),'reference',reference),(h('final_actual_output', 'Final actual output'),'output',readable_output(output))])+'</div>'
 output_button=dialog('result-output',h('reference_and_output_comparison', 'Reference and output comparison'),output_html)
 provenance={key:r.get(key) for key in ('run_id','attempt_id','case_id','input_html_sha256','reference_sha256','output_sha256')}
 fields={key:value for key,value in metadata.items() if key not in ('stderr','browser_stderr','stdout')}
 provenance_button=dialog('result-provenance',h('provenance_and_response_fields', 'Provenance and response fields'),'<h3>'+h('ids_and_hashes', 'IDs and hashes')+'</h3><pre>'+esc(json.dumps(provenance,ensure_ascii=False,indent=2))+'</pre><h3>'+h('response_fields', 'Response fields')+'</h3><pre data-kind="metadata">'+esc(json.dumps(fields,ensure_ascii=False,indent=2))+'</pre>')
 body+=section(h('evidence', 'Evidence'),'<div class="actions wmb-evidence-actions">'+output_button+('' if failed else log_button)+provenance_button+'</div>','result-evidence')
 return '<div data-report-detail="failed">'+body+''.join(dialogs)+'</div>' if failed else '<div data-report-detail="success">'+body+''.join(dialogs)+'</div>'


@with_measured_method_labels
def sample_pages(out:Path,dataset:dict,records:list,summaries:dict,manifests:list,review:dict|None=None,research:dict|None=None,corrections:dict|None=None,adoption_data:dict|None=None,fixture_correction:dict|None=None,teds_revision:tuple|None=None,anomaly_review:dict|None=None,latency_diagnostic:dict|None=None,*,include_english:bool=False,report_content:dict|None=None):
 """Render the current WebMainBench report with the established report shell."""
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA,encoding='utf-8')
 (out/'assets/report.js').write_text(JS,encoding='utf-8')
 # Presentation-only classification: do not change provider status, scores or evidence.
 classified=[]
 for record in records:
  text=contained_path(out,record['artifact_path']).read_text(encoding='utf-8',errors='replace')
  error_page=_wmb_error_page(text)
  classified.append({**record,'_body_present':bool(record['ok'] and text.strip() and not error_page),
                     '_output_error':'error_page' if error_page else 'empty_body' if record['ok'] and not text.strip() else record.get('error_type')})
 records=classified
 cases={row['id']:row for row in dataset['cases']};bycase=defaultdict(list)
 for row in records:bycase[row['case_id']].append(row)
 names=list(dataset['coverage_plan']['static_html'])
 failure_types=sorted({r['_output_error'] for r in records if not r['_body_present'] and r['_output_error']})
 failed_request_count=sum(not r['_body_present'] for r in records)
 case_count=len(cases);method_count=len(names);result_count=case_count*method_count
 labels={name: browser_method_registry().get(name,{}).get('label') or (native_browser_engine(name) or name).capitalize() for name in names}
 def scope(text):
  return text.replace('{case_count}',str(case_count)).replace('{method_count}',str(len(names))).replace('{result_count}',str(result_count))
 metric_order=['overall','text_edit','code_edit','formula_edit','table_edit','table_TEDS']
 corrected=teds_revision is not None
 display_score=_wmb_pct
 def score(name,metric):return summaries[name]['official_aggregate']['scores'][metric]
 def denom(name,metric):return summaries[name]['official_aggregate']['denominators'].get(metric)
 timing=_wmb_latency_summary(records,names)
 latency_corrected=(out/'latency-corrections.json').is_file()
 formal_round=json.loads((out/'latency-corrections.json').read_text(encoding='utf-8'))['selected_round'] if latency_corrected else None
 def failures(name):return [r for r in records if r['provider']==name and not r['_body_present']]
 def unique_winner(values,higher=True):
  ordered=sorted(values,key=lambda item:item[1],reverse=higher)
  return ordered[0][0] if len(ordered)==1 or ordered[0][1]!=ordered[1][1] else None
 ranked_metrics=metric_order if corrected else metric_order[1:-1]
 score_winners={metric:unique_winner([(name,score(name,metric)) for name in names]) for metric in ranked_metrics}
 latency_values=timing['successful']
 def value_html(value,winner=False,n=None,population=None,english=False):
  content=f'<span class="metric-value">{value}</span>'
  if n is not None:
   count=case_count if population is None else population
   label=report_text('en' if english else 'zh')('scored_on_pages', 'scored on {n}/{count} pages', n=n, count=count)
   content+=f'<span class="tiny">{label}</span>'
  return f'<span class="comparison-winner">{content}</span>' if winner else content
 def core_rows(english=False):
  rows=[]
  for name in sorted(names,key=lambda item:(-score(item,'overall'),labels[item])):
   values=cell(link('providers/'+name+'.html',labels[name]))
   values+=cell(value_html(display_score(score(name,'overall')),winner=score_winners.get('overall')==name),score(name,'overall'))
   for metric in metric_order[1:]:
    values+=cell(value_html(display_score(score(name,metric)),winner=score_winners.get(metric)==name,n=denom(name,metric),english=english),score(name,metric))
   values+=cell(str(latency_values[name]['count'])+'/'+str(case_count))
   for key in ('mean','p50','p95'):
    values+=_wmb_timing_cell(latency_values,name,key)
   rows.append(('',values))
  return rows
 for english in ((False,True) if include_english else (False,)):
  h=lambda key,source,/,text_=report_text('en' if english else 'zh'),**values:scope(text_(key,source,**values))
  corrected_label=h('recomputed_after_correction', 'recomputed after correction')
  corrected_suffix=(f' ({corrected_label})' if english else f'（{corrected_label}）')
  metric_labels={name:name+(corrected_suffix if corrected and name in ('overall','table_TEDS') else '') for name in metric_order}
  status=(h('pages_methods_results_evidence_and_arithmetic_checks', '{case_count} pages × {count} methods = {result_count:,} results; evidence and arithmetic checks passed, but table-structure score validity did not.', case_count=case_count, count=len(names), result_count=result_count) if review and review.get('status')=='passed' else h('measurement_complete_independent_review_has_not_passed', 'Measurement complete; independent review has not passed, so this build is not publishable.'))
  if corrected:
   status=h('pages_methods_the_official_repository_and_leaderboard', '{case_count} pages × {count} methods. The official repository and leaderboard remain unchanged. An independent recomputation corrects the demonstrated TEDS defect on the same saved outputs.', case_count=case_count, count=len(names))
  home=hero(h('webmainbench_545_preselected_measured_methods', 'WebMainBench-545 · preselected {case_count} · {count} measured methods', case_count=case_count, count=len(names)),h('methods_read_the_same_frozen_html_and', '{count} methods read the same frozen HTML and are compared with the same human-calibrated Markdown; page-level responses, empty body text and causes live in result details.', count=len(names)))
  home+='<div class="wrap reliability'+('' if review and review.get('status')=='passed' else ' failure')+'"><strong>'+esc(status)+'</strong></div>'
  home+=section(h('what_this_ranking_measures', 'What this ranking measures'),'<div class="wmb-scope-map"><article><b>'+h('input_identical_archived_html', 'Input: identical archived HTML')+'</b><p>'+h('pages_methods_no_live_origin_fetch', '{case_count} pages × {count} methods; no live origin fetch.', case_count=case_count, count=len(names))+'</p></article><article><b>'+h('target_main_content_not_every_page_element', 'Target: main content, not every page element')+'</b><p>'+h('body_text_code_formulas_and_tables_navigation', 'Body text, code, formulas and tables; navigation, ads and recommendations are outside the target.')+'</p></article><article><b>'+h('score_similarity_to_the_human_main_content', 'Score: similarity to the human main-content reference')+'</b><p>'+h('extra_boilerplate_and_lost_table_structure_reduce', 'Extra boilerplate and lost table structure reduce scores. A low score does not mean the page was not retrieved.')+'</p></article></div><p class="actions">'+link('guide/index.html#content',h('see_a_real_scored_page', 'See a real scored page →'))+' · '+link('methodology/index.html#input-routes',h('how_all_methods_read_the_same_html', 'How all {count} methods read the same HTML →', count=len(names)))+'</p><p class="source-note">'+h('this_is_not_a_live_fetch_success', 'This is not a live-fetch success rate or an overall browser-capability ranking.')+'</p>','benchmark-scope')
  core_headers=[h('method', 'Method'),metric_labels['overall'] if corrected else h('overall_under_review', 'overall (under review)'),'text_edit','code_edit','formula_edit','table_edit',metric_labels['table_TEDS'] if corrected else h('table_teds_under_review', 'table_TEDS (under review)'),h('successful_all_pages', 'Successful / all pages'),h('mean_latency_2', 'Mean latency'),h('p50_latency', 'P50 latency'),h('p95_latency', 'P95 latency')]
  core_note=_wmb_timing_note(english)
  core_note+=' '+h('timing_corrections_use_preselected_round_physical_calls', 'Timing corrections use preselected round {formal_round}; physical calls and correction receipts remain available.', formal_round=formal_round) if latency_corrected else ''
  timing_key='<div class="wmb-reading-key"><span><b>'+h('quality_scores', 'Quality scores')+'</b> '+h('averaged_over_the_pages_shown_under_each', 'Averaged over the pages shown under each component')+'</span><span><b>'+h('mean_latency_p50_p95', 'Mean latency · P50 · P95')+'</b> '+h('final_successful_fetch_only_previous_failures_are', 'Final successful fetch only; previous failures are not added, and the success count is shown separately')+'</span></div>'
  coverage_note=''
  if latency_diagnostic:
   coverage_note='<p class="source-note">'+h('delivered_page_counts_and_failure_details_describe', 'Delivered-page counts and failure details describe the selected frozen pages, not live-site retrieval ability.')+'</p>'
   coverage_note=coverage_note.replace('</p>',' '+link('datasets/'+latency_diagnostic['failure_retest']['case_id']+'.html',h('inspect_the_failed_page_evidence', 'Inspect the failed-page evidence'))+'</p>')
  home+=section(h('core_results', 'Core results'),timing_key+'<p class="source-note">'+core_note+'</p>'+table(core_headers,core_rows(english),'wmb-core-results',True,cls='wmb-core-table',metrics=[],default_sort=(2,'desc'))+coverage_note+_wmb_table_population(records,names,labels,english,prefix='')+'<details><summary>'+h('formula_scoring_example_and_flow', 'Formula-scoring example and flow')+'</summary>'+_wmb_denominator_example(records,english,case_count)+'</details>'+f"""<p class="actions">{link('analysis/index.html', h('filter_page_level_results_and_responses', 'Filter page-level results and responses →'))} · {link('methodology/index.html', h('full_scoring_rules', 'Full scoring rules →'))} · {link('review/index.html', h('original_scores_and_corrections', 'Original scores and corrections →'))}</p>""")
  home+=section(h('timing_on_identical_pages', 'Timing on identical pages'),_wmb_matched_latency(records,names,labels,english,prefix=''),'matched-latency')
  if latency_diagnostic:
   home+=section(h('version_latency_comparison', 'Version latency comparison'),_wmb_latency_rerun(latency_diagnostic,english,formal_round=formal_round),'moli-latency-rerun')
  _wmb_write(out,'index.html',h('benchmark_report', 'Evaluation report'),home,english)

  dataset_rows=[]
  for c in cases.values():
   meta=c['meta']
   dataset_rows.append((_wmb_row_attrs(c),cell(link(c['id']+'.html',c['id']))+cell(esc(meta.get('language') or 'unavailable'))+cell(esc(meta.get('level')))+cell(esc('+'.join(meta.get('table') or []) or 'none'))+cell(esc('+'.join(meta.get('code') or []) or 'none'))+cell(esc('+'.join(meta.get('equation') or []) or 'none'))))
  body=hero(h('dataset', 'Dataset'),h('a_frozen_page_sample_from_the_webmainbench', 'A frozen {{case_count}}-page sample from the WebMainBench parent, selected before any method result was produced.'))
  body+=section(h('pages_4', '{{case_count}} pages'),'<p class="source-note">'+h('filter_by_language_difficulty_and_content_type', 'Filter by language, difficulty and content type. Open a track ID for the frozen HTML, human-calibrated Markdown reference and official metadata; method results are in the analysis section. Official scoring compares each conversion with the reference directly, without a prewritten per-task checklist.')+'</p>'+_wmb_controls(cases,labels,english,include_method=False,include_metric=False,include_delivery=False)+_wmb_filter_table(['Track ID',h('language', 'Language'),h('difficulty', 'Difficulty'),h('table', 'Table'),h('code', 'Code'),h('formula', 'Formula')],dataset_rows,'wmb-cases',english))
  sample=dataset.get('sample',{});sampling=sample.get('sampling',{})
  if sampling:
   distribution_rows=[]
   parent_distribution=sample.get('parent_distribution',{});sample_distribution=sample.get('sample_distribution',{})
   axis_labels={'language':h('language', 'Language'),'level':h('difficulty', 'Difficulty'),'table':h('table_type', 'Table type'),'code':h('code_type', 'Code type'),'equation':h('formula_type', 'Formula type')}
   for axis in ('language','level','table','code','equation'):
    parent_values=parent_distribution.get(axis,{});sample_values=sample_distribution.get(axis,{})
    for category in sorted(set(parent_values)|set(sample_values)):
     parent_count=parent_values.get(category,0);sample_count=sample_values.get(category,0)
     distribution_rows.append((f' data-axis="{esc(axis)}" data-parent="{parent_count}" data-sample="{sample_count}"',cell(esc(category))+cell(str(parent_count))+cell(str(sample_count))))
   manifest_href='../sample-manifest.json' if not english else '../../sample-manifest.json'
   distribution_controls='<div class="wmb-distribution-controls"><label>'+h('distribution_axis', 'Distribution axis')+'<select id="wmb-distribution-axis" data-wmb-distribution-filter>'+''.join(f'<option value="{axis}">{label}</option>' for axis,label in axis_labels.items())+'</select></label><p id="wmb-distribution-count" class="source-note" aria-live="polite"></p></div>'
   distribution_table=table([h('category', 'Category'),h('parent_2', 'Parent'),h('sample_3', 'Sample {{case_count}}')],distribution_rows,'wmb-distribution',metrics=[])
   body+=section(h('how_these_pages_were_selected', 'How these {{case_count}} pages were selected'),'<p>'+h('language_difficulty_table_code_and_formula_are', 'Language, difficulty, table, code and formula are five independent classification axes; every page belongs to one category on each axis. After selecting an axis, the “Sample {{case_count}}” column sums to {{case_count}}. Counts across different axes must not be added together. The manifest was fixed before any browser ran; scores did not affect selection.')+'</p><p><b>'+h('fixed_seed', 'Fixed seed: ')+'</b><code>'+esc(sampling.get('seed',''))+'</code> · '+link(manifest_href,h('download_the_page_list', 'Download the {{case_count}}-page list'))+'</p>'+distribution_controls+distribution_table)
  if research:
   def evidence_links(urls):
    values=[]
    for url in urls:
     parsed=urlsplit(url);label=(parsed.netloc+parsed.path.rstrip('/')) or url
     values.append(link(url,label))
    return '<br>'.join(values) or '—'
   selection=[]
   for item in research.get('direct_benchmarks',[]):
    sources=evidence_links(item.get('sources',[]))
    selection.append(('',cell(esc(item['name']))+cell(esc(item.get('scope' if english else 'scope_zh','—')))+cell(esc(item.get('metric' if english else 'metric_zh','—')))+cell(esc('；'.join(item.get('users',[])) or '—'))+cell(esc(item.get('disposition' if english else 'disposition_zh','—')))+cell(sources)))
   body+=section(h('why_webmainbench', 'Why WebMainBench'),'<p>'+esc(research['decision']['summary_en' if english else 'summary_zh'])+'</p><p class="callout">'+esc(research['decision']['qualification_en' if english else 'qualification_zh'])+'</p>'+table([h('candidate', 'Candidate'),h('scope_3', 'Scope'),h('metric', 'Metric'),h('named_users', 'Named users'),h('decision', 'Decision'),h('evidence', 'Evidence')],selection,cls='wide-prose-table',metrics=[]))
   wcxb=next((item for item in research.get('direct_benchmarks',[]) if item.get('id')=='wcxb'),None)
   if wcxb:
    limits=wcxb.get('limitations' if english else 'limitations_zh',[])
    body+=section(h('wcxb_assessment', 'WCXB assessment'),'<p>'+esc(wcxb.get('maturity' if english else 'maturity_zh',''))+'</p><ul>'+''.join('<li>'+esc(value)+'</li>' for value in limits)+'</ul>')
   adjacent=[]
   for item in research.get('adjacent_browser_benchmarks',[]):
    adjacent.append(('',cell(esc(item['name']))+cell(esc(item.get('size' if english else 'size_zh')))+cell(esc(item.get('measures' if english else 'measures_zh')))+cell(esc(item.get('adoption' if english else 'adoption_zh')))+cell(esc(item.get('why_separate' if english else 'why_separate_zh')))+cell(evidence_links(item.get('sources',[])))))
   body+=section(h('adjacent_browser_and_web_agent_benchmarks', 'Adjacent browser and web-agent benchmarks'),table([h('benchmark_2', 'Benchmark'),h('scale', 'Size'),h('measures_2', 'Measures'),h('adoption', 'Adoption'),h('why_separate_2', 'Why separate'),h('evidence', 'Evidence')],adjacent,cls='wide-prose-table',metrics=[]))
  _wmb_write(out,'datasets/index.html',h('dataset', 'Dataset'),body,english)

  method=hero(h('methodology', 'Methodology'),h('start_with_one_frozen_page_what_the', 'Start with one frozen page: what the browser delivered, how the official scorer calculates it, and which figures cannot currently support conclusions.'))
  flow_source=scope(report_text('zh' if not english else 'en')('flowchart_lr_a_1_frozen_html_br', 'flowchart LR\n    A["1  Frozen HTML<br/>the same {{case_count}} pages"] --> B["2  {{method_count}} methods convert<br/>one final response per page"]\n    B --> C{{"3  Did the final<br/>call succeed?"}}\n    C -->|"No"| D["Result detail<br/>error type · response · cause"]\n    C -->|"Yes"| E["4  Compare with human Markdown<br/>text · code · formula · table"]\n    E --> F["5  Per-page components<br/>absent on both sides: skipped<br/>present on one side: scored zero"]\n    F --> G["6  Component means<br/>scored pages / {{case_count}} shown below score"]\n    E --> H["Exclude empty content and error pages<br/>final successful fetch: mean · P50 · P95"]'))
  flow_fallback='<div class="wmb-flow-fallback" data-wmb-flow-fallback>'+''.join(f'<span><b>{index}</b> {label}</span>' for index,label in enumerate(([h('frozen_input', 'Frozen input'),h('conversion', 'Conversion'),h('inspect_response_and_body', 'Inspect response and body'),h('per_page_scoring', 'Per-page scoring'),h('mean_over_scored_pages', 'Mean over scored pages')]),1))+'</div>'
  flow='<div class="wmb-flow">'+flow_fallback+'<pre class="mermaid" hidden>'+esc(flow_source)+'</pre></div>'
  method+=section(h('from_one_page_to_one_result', 'How one page becomes one result'),'<p class="source-note">'+h('start_with_the_full_path_then_open', 'Start with the full path, then open implementation details only when needed. Whether body text exists, whether the interface responded and why it is empty belong to the page result; quality scores only describe similarity to the human reference.')+'</p>'+flow)
  input_text=h('every_method_receives_the_same_selected_frozen', 'Every method receives the same selected frozen HTML bytes. Submission interfaces, isolation controls and physical calls follow the execution evidence; the current scope is not inferred from historical batches.')
  cue_text=h('the_dataset_html_contains_public_code_cc', 'The dataset HTML contains public <code>cc-select</code> annotation attributes. None of the {{method_count}} generic configurations is customized to use them, but they remain an inherent benchmark cue.')
  method+=section(h('input_and_runtime_boundary', 'Input and runtime boundary'),'<div class="wmb-input-routes"><article><b>'+h('kitesurf_cloudflare_api', 'Kitesurf · Cloudflare API')+'</b><code>'+h('frozen_html_api_html_field_markdown', 'Frozen HTML → API html field → Markdown')+'</code><span>'+h('the_same_html_bytes_are_submitted_directly', 'The same HTML bytes are submitted directly; the remote service does not refetch the source URL.')+'</span></article><article><b>Lightpanda · Moli · Obscura · Chromium</b><code>'+h('frozen_html_local_https_browser_markdown', 'Frozen HTML → local HTTPS → browser → Markdown')+'</code><span>'+h('the_browser_opens_a_local_url_whose', 'The browser opens a local URL whose server returns the same HTML.')+'</span></article></div><p>'+h('each_page_has_frozen_html_and_a', 'Each page has frozen HTML and a human-calibrated Markdown reference. All {{method_count}} methods convert the same HTML; the original URL records provenance and is not fetched live in this run.')+'</p><details><summary>'+h('network_isolation_local_https_server_and_batch', 'Network isolation, local HTTPS server and batch details')+'</summary><p>'+input_text+'</p><p class="callout">'+cue_text+'</p></details><p class="actions">'+link('../datasets/index.html',h('open_a_page_with_full_input_and', 'Open a page with full input and reference'))+'</p>','input-routes')
  method+=section(h('how_tables_enter_scoring', 'How tables enter scoring'),_wmb_table_example(english),'table-scoring')
  formulas=[('text_edit',h('full_markdown_against_the_human_reference', 'Full Markdown against the human reference'),h('character_edit_distance_divided_by_the_longer', 'Character edit distance divided by the longer text; extra navigation and footer content can lower the score.')),('code_edit',h('code_blocks_found_in_the_two_markdown', 'Code blocks found in the two Markdown files'),h('compare_extracted_code_characters_normalized_by_the', 'Compare extracted code characters, normalized by the longer code length.')),('formula_edit',h('formulas_found_in_the_two_markdown_files', 'Formulas found in the two Markdown files'),h('compare_extracted_formula_characters_normalized_by_the', 'Compare extracted formula characters, normalized by the longer formula length.')),('table_edit',h('tables_found_in_the_two_markdown_files', 'Tables found in the two Markdown files'),h('compare_normalized_table_characters_table_words_in', 'Compare normalized table characters; table words in a paragraph do not form a recognized table.')),('table_TEDS',h('the_table_row_cell_structure_reconstructed_from', 'The table–row–cell structure reconstructed from Markdown'),h('reconstruct_tables_rows_and_cells_from_markdown', 'Reconstruct tables, rows and cells from Markdown, then count the insertions, deletions and changes needed to turn the output structure into the reference structure. Cell text without recognizable Markdown table syntax cannot be reconstructed as a table.'))]
  method+=section(h('how_component_scores_are_produced', 'How component scores are produced'),'<p>'+h('the_scorer_first_extracts_code_formulas_and', 'The scorer first extracts code, formulas and tables from the output and reference; text_edit instead keeps the entire Markdown. For character metrics, score = 1 − edit distance / longer length: identical content scores 100%, while content on only one side scores 0%. The table-structure component is recomputed after correcting the calculation defect documented below.')+'</p>'+table([h('metric', 'Metric'),h('compared_content', 'Compared content'),h('calculation_and_reading', 'Calculation and reading')],[('',cell(k)+cell(subject)+cell(explanation)) for k,subject,explanation in formulas],cls='wide-prose-table',metrics=[])+f"<p>{h('the_pinned_official_commit_is_code_9d991bd', 'The pinned official commit is <code>9d991bd</code> with deterministic <code>use_llm=false</code>. When code, formulas or tables are absent on both sides, the official edit-distance metric returns a failed result and the component is excluded from the mean. When content exists on only one side, edit distance succeeds with a zero score and the page enters the mean.')}</p>")
  denominator_rows=[('',cell(h('absent_on_both_sides', 'Absent on both sides'))+cell(h('no_score_outside_the_component_mean', 'No score; outside the component mean'))),('',cell(h('reference_only', 'Reference only'))+cell(h('zero_score_included', 'Zero score; included'))),('',cell(h('output_only', 'Output only'))+cell(h('zero_score_included', 'Zero score; included'))),('',cell(h('present_on_both_sides', 'Present on both sides'))+cell(h('similarity_score_included', 'Similarity score; included')))]
  method+=section(h('which_pages_enter_each_component_mean', 'Which pages enter each component mean'),'<p>'+h('each_method_receives_fixed_pages_failed_final', 'Each method receives {{case_count}} fixed pages. Failed final calls are not scored. Successful calls are scored from output and reference content; calculated scores remain even when the output is later classified as an error page. “Not calculated” is different from zero. A metric execution error also excludes the page from that component mean.')+'</p>'+table([h('component_content', 'Component content'),h('official_treatment', 'Official treatment')],denominator_rows,metrics=[])+f"""<p class="actions">{link('../index.html#denominator-example', h('see_the_actual_page_moli_formula_split', 'See the actual {{case_count}}-page Moli formula split'))}</p>""",'denominator-rules')
  method+=section(h('why_the_table_structure_score_is_recomputed', 'Why the table-structure score is recomputed'),'<p>'+h('the_pinned_official_version_first_serializes_the', 'The pinned official version first serializes the table–row–cell structure into one bracketed string and then passes the string directly to APTED. APTED needs a tree whose children can be traversed; with the string it does not see the real rows and cells. In one measured page, the output has 5 structural items and the reference has 570, yet the official recorded edit distance is below 1 and the TEDS score is 99.8%. Adding the 565 missing structural items alone cannot cost less than 565 edits.')+'</p><p class="callout failure">'+h('this_report_does_not_modify_the_official', 'This report does not modify the official repository or leaderboard. An independent recomputation passes the same table to APTED as an explicit table–row–cell tree while retaining the official Markdown parsing, text handling and edit costs. Browser outputs, scored-page counts and latency are unchanged; original official scores and recomputed values are both retained. Table_edit still compares normalized table HTML strings.')+'</p><p class="actions">'+link('../attempts/obscura__949e1468-eed2-43ab-96e6-5dcb07c52d4b__r001.html',h('inspect_the_complete_5_versus_570_item', 'Inspect the complete 5-versus-570-item case'))+' · '+link('https://github.com/opendatalab/WebMainBench/blob/9d991bdc00c57b57521499494d96be85c31317ba/webmainbench/metrics/teds_metrics.py',h('official_teds_source', 'Official TEDS source'))+'</p>')
  method+=section(h('aggregation_timing_and_non_comparable_fields', 'Aggregation, timing and non-comparable fields'),'<p>'+h('each_component_aggregate_is_the_sum_of', 'Each component aggregate is the sum of successfully calculated page scores divided by its actual scored-page count; leaderboard overall is the arithmetic mean of the five aggregates. Per-page overall averages only that page’s calculated components, so it cannot replace leaderboard overall. A page-level component is marked “not calculated” for exactly two reasons: the content type is absent from both the reference and output, or the scorer failed. The result detail identifies which reason applies.')+'</p><p>'+h('successful_fetch_time_is_the_final_successful', 'Successful-fetch time is the final successful call from request start to converted response, excluding earlier failures. Accumulated time across all calls is retained separately as execution cost. Calls are serial within a browser batch, but method batches may overlap; Cloudflare remote time and local CLI time come from different execution environments. Body-text outcome, quality and latency are reported separately, not folded into one score.')+'</p>','calculation')
  cost_rows=[('',cell(labels[name])+cell(str(case_count))+''.join(cell(ms(timing['cost'][name][key])) for key in ('mean','p50','p95'))) for name in names]
  method+=section(h('successful_timing_matched_diagnosis_and_execution_cost', 'Successful timing, matched diagnosis and execution cost'),'<p>'+h('the_primary_table_uses_elapsed_ms_from', 'The primary table uses elapsed_ms from the final successful fetch with target content, alongside the success count, mean, P50 and P95. Error pages, empty content and failed final calls are excluded. A retry does not create a separate logical result or add the previous call’s time to the successful fetch.')+'</p><p>'+h('the_matched_diagnosis_uses_the_intersection_of', 'The matched diagnosis uses the intersection of successful pages across all {{method_count}} configurations. Every method uses identical pages. This post-execution subset is not a speed ranking for all {{case_count}} pages and does not remove any other results. Original call chains and accumulated execution cost remain available for audit, separately from successful-fetch latency.')+'</p><details><summary>'+h('previous_measure_accumulated_execution_cost_on_all', 'Previous measure: accumulated execution cost on all {{case_count}} pages (not successful-fetch speed)')+'</summary><p>'+h('this_retains_total_elapsed_ms_sum_all', 'This retains total_elapsed_ms: sum all physical-call durations per page, then calculate mean, P50 and P95 over the fixed {{case_count}} pages. It describes execution cost for this run.')+'</p>'+table([h('method', 'Method'),h('pages_3', 'Pages'),h('mean', 'Mean'),'P50','P95'],cost_rows,metrics=[])+'</details>','latency-policy')
  _wmb_write(out,'methodology/index.html',h('methodology', 'Methodology'),method,english)

  analysis=hero(h('analysis', 'Analysis'),h('compare_methods_on_the_same_page_then_2', 'Compare methods on the same page, then inspect category scores, and finally drill into each response, body text and cause.'))
  analysis+=section(h('method_comparison', 'Method group comparison'),_wmb_comparison(cases,bycase,names,labels,english),'comparison')
  slice_rows=[];slice_categories=set();slice_axis_labels={'language':h('language', 'Language'),'level':h('difficulty', 'Difficulty'),'table':h('table_type', 'Table type'),'code':h('code_type', 'Code type'),'equation':h('formula_type', 'Formula type')}
  for axis in ('language','level','table','code','equation'):
   categories=sorted({category for name in names for category in summaries[name].get('dimensions',{}).get(axis,{})})
   slice_categories.update(categories)
   for category in categories:
    category_winners={}
    for metric in ranked_metrics:
     eligible=[]
     for candidate in names:
      candidate_aggregate=summaries[candidate]['dimensions'][axis][category]
      if candidate_aggregate['denominators'].get(metric,0)>0:eligible.append((candidate,candidate_aggregate['scores'][metric]))
     category_winners[metric]=unique_winner(eligible) if eligible else None
    for name in sorted(names,key=lambda x:(-score(x,'overall'),x)):
     aggregate=summaries[name]['dimensions'][axis][category]
     values=[]
     for metric in metric_order:
      n=aggregate['denominators'].get(metric,0) if metric!='overall' else None
      population=sum(_wmb_category(case,axis)==category for case in cases.values())
      value=value_html(display_score(aggregate['scores'][metric]),winner=category_winners.get(metric)==name,n=n,population=population,english=english) if n is None or n>0 else '—'
      values.append(cell(value,aggregate['scores'][metric] if n is None or n>0 else -1))
     slice_rows.append((f' data-axis="{esc(axis)}" data-category="{esc(category)}" data-method="{esc(name)}"',cell(esc(slice_axis_labels[axis]))+cell(link(_wmb_inspect('index.html',method=name,axis=axis,category=category),category))+cell(link('../providers/'+name+'.html',labels[name]))+''.join(values)))
  slice_filter='<div class="wmb-slice-controls"><label>'+h('category_dimension', 'Category dimension')+'<select id="wmb-slice-axis" data-wmb-slice-filter>'+''.join('<option value="'+esc(axis)+'">'+esc(slice_axis_labels[axis])+'</option>' for axis in ('language','level','table','code','equation'))+'</select></label><label>'+h('category', 'Category')+'<select id="wmb-slice-category" data-wmb-slice-filter><option value="">'+h('all', 'All')+'</option>'+''.join('<option value="'+esc(category)+'">'+esc(category)+'</option>' for category in sorted(slice_categories))+'</select></label><label>'+h('method', 'Method')+'<select id="wmb-slice-method" data-wmb-slice-filter><option value="">'+h('all', 'All')+'</option>'+''.join('<option value="'+esc(name)+'">'+esc(labels[name])+'</option>' for name in names)+'</select></label></div><p id="wmb-slice-count" class="source-note" aria-live="polite"></p>'
  slice_table=table([h('category_axis', 'Axis'),h('category', 'Category'),h('method', 'Method')]+[metric_labels[m] for m in metric_order],slice_rows,'wmb-slices',True,cls='wmb-slice-table',metrics=[]).replace('<div class="table-wrap">','<div class="table-wrap wmb-slice-table-wrap">',1)
  analysis_tail=section(h('compare_by_page_category', 'Compare by page category'),'<p class="source-note">'+h('select_one_independent_classification_axis_then_narrow', 'Select one independent classification axis, then narrow by category or method. The small fraction below a score is scored pages / all pages in that category; “—” means no scored page, not zero. Green marks a unique highest score within the category and component. TEDS and overall use the correction labeled on the home page.')+'</p>'+slice_filter+slice_table)
  inspect_rows=[]
  for r in sorted(records,key=lambda row:(not row['ok'],row['case_id'],row['provider'])):
   case=cases[r['case_id']]
   target='../attempts/'+r['attempt_id']+'.html'
   output=contained_path(out,r['artifact_path']).read_text(encoding='utf-8',errors='replace')
   state_class='success' if r['ok'] and output.strip() and not _wmb_error_page(output) else 'failure'
   request=f'<span class="wmb-pill {state_class}">{esc(_wmb_body_state(r,output,english))}</span>'
   vals=cell(link(target,r['case_id']))+cell(link(target,labels[r['provider']]))+cell(esc(_wmb_category(case,'language')))+cell(esc(_wmb_category(case,'level')))+cell(esc(_wmb_category(case,'table')))+cell(esc(_wmb_category(case,'code')))+cell(esc(_wmb_category(case,'equation')))+cell(request)+cell(esc(_wmb_output_label(r,output,english)))
   vals+=''.join(_wmb_metric_cell(r,metric) for metric in metric_order[1:])
   vals+=cell(ms(r['elapsed_ms']),r['elapsed_ms'])+cell(link(target,h('metric_evidence', 'Metric evidence →')))
   inspect_rows.append((_wmb_row_attrs(case,r),vals))
  analysis+=section(h('page_results_and_responses', 'Page results and responses'),'<p class="wmb-metric-note">'+h('the_complete_matrix_contains_results_and_initially', 'The complete matrix contains {case_count}×{method_count} results and initially shows the {failed_request_count} conversions without body text. Each row states whether body text was produced and whether the final API/worker material is body text, an error response, an error page or no readable response. Open the track ID or method for the complete response, human reference, component scores and the currently supported cause.', case_count=case_count, method_count=method_count, failed_request_count=failed_request_count)+'</p>'+_wmb_controls(cases,labels,english,default_request='failed',failure_types=failure_types)+'<p class="wmb-scroll-note">'+h('scroll_horizontally_for_component_scores_and_latency', 'Scroll horizontally for component scores and latency; both the track ID and method name open the detail.')+'</p>'+_wmb_filter_table(['Track ID',h('method', 'Method'),h('language', 'Language'),h('difficulty', 'Difficulty'),h('table', 'Table'),h('code', 'Code'),h('formula', 'Formula'),h('body_text_produced', 'Body text produced'),h('response_and_body', 'Response and body')]+[metric_labels[m] for m in metric_order[1:]]+[h('final_fetch_latency', 'Final fetch latency'),h('detail', 'Detail')],inspect_rows,'wmb-attempt-matrix',english))
  _wmb_write(out,'analysis/index.html',h('analysis', 'Analysis'),analysis+analysis_tail,english)

  review_body=hero(h('review_record', 'Review'),h('original_official_results_are_retained_alongside_the', 'Original official results are retained alongside the scope and effects of corrections; page-level causes are in result details.'))
  raw_prefix='../../' if english else '../'
  if corrected:
   receipt,revision_review=teds_revision
   effects=[]
   for name in names:
    impact=revision_review['impacts'][name];before=impact['official'];after=impact['corrected']
    effects.append(('',cell(link('../providers/'+name+'.html',labels[name]))+cell(display_score(before['scores']['table_TEDS']))+cell(display_score(after['scores']['table_TEDS']))+cell(display_score(before['scores']['overall']))+cell(display_score(after['scores']['overall']))+cell(str(after['denominators']['table_TEDS']))))
   review_body+=section(h('teds_calculation_correction', 'TEDS calculation correction'),'<p>'+h('all_results_were_reprocessed_teds_values_changed', 'All {logical_results:,} results were reprocessed; {changed_scores} TEDS values changed. No browser was called again. Inputs, raw outputs, scored-page counts and latency are unchanged. This report does not modify the official repository or leaderboard; “corrected” values are independently recomputed by this report.', logical_results=revision_review['logical_results'], changed_scores=revision_review['changed_scores'])+'</p>'+table([h('method', 'Method'),h('original_teds', 'Original TEDS'),h('corrected_teds', 'Corrected TEDS'),h('original_overall', 'Original overall'),h('corrected_overall', 'Corrected overall'),h('pages_scored_for_teds', 'Pages scored for TEDS')],effects,'wmb-correction-effects',metrics=[])+
    '<p class="actions">'+link('../methodology/index.html',h('tree_node_defect_and_correction', 'Tree-node defect and correction'))+' · '+link(raw_prefix+'teds-correction.json',h('correction_scope_and_source_hashes', 'Correction scope and source hashes'))+' · '+link(raw_prefix+'teds-correction-review.json',h('integrity_and_arithmetic_audit', 'Integrity and arithmetic audit'))+' · '+link(raw_prefix+'original-results.jsonl',h('original_official_page_results', 'Original official page results'))+'</p>')
  if review:
   review_body+=section(h('original_result_evidence_checks', 'Original-result evidence checks'),'<p>'+h('the_original_audit_checked_the_result_scope', 'The original audit checked the {{result_count}}-result scope, file hashes, call latency, text metrics and component aggregation. It does not alone establish algorithm validity or the root cause of every failure. The TEDS correction and its follow-up checks are recorded above.')+'</p><p>'+link(raw_prefix+'result-review.json',h('download_the_original_audit', 'Download the original audit'))+'</p>')
  else:
   review_body+=section(h('original_audit_missing', 'Original audit missing'),'<p class="callout failure">'+h('the_original_audit_is_missing_publication_is', 'The original audit is missing; publication is blocked.')+'</p>')
  error_outputs=sum(1 for r in records if _wmb_error_page(contained_path(out,r['artifact_path']).read_text(encoding='utf-8',errors='replace')))
  if anomaly_review:
   anomaly_rows=[]
   for finding in anomaly_review['cases']:
    providers=(', ' if english else '、').join(labels[name] for name in finding['affected_providers'])
    anomaly_rows.append(('',cell(link('../datasets/'+finding['case_id']+'.html',finding['case_id']))+cell(esc(providers))+cell(esc(finding['observed_en' if english else 'observed_zh']))+cell(esc(finding['cause_en' if english else 'cause_zh']))))
   anomaly_scope=anomaly_review['scope']
   anomaly_href='../../webmainbench-anomaly-review.json' if english else '../webmainbench-anomaly-review.json'
   review_body+=section(h('root_cause_review_of_anomalous_results', 'Root-cause review of anomalous results'),'<p>'+h('deterministic_rules_triggered_of_results_concentrated_in', 'Deterministic rules triggered {signal_triggered_results} of {result_count} results, concentrated in {distinct_cases} pages. Review used the authoritative outputs, human references, program errors and same-page six-method differences, then non-scoring ablation controls to isolate triggers. Every triggered result maps to a cause below. Controls are not the original task, so no call, score, denominator or latency was replaced.', result_count=result_count, signal_triggered_results=anomaly_scope['signal_triggered_results'], distinct_cases=anomaly_scope['distinct_cases'])+'</p>'+table([h('page', 'Page'),h('affected_methods', 'Affected methods'),h('control_observation', 'Control observation'),h('cause_and_evidence_boundary', 'Cause and evidence boundary')],anomaly_rows,cls='wide-prose-table',metrics=[])+f"<p>{link(anomaly_href, h('download_the_hash_bound_review_record', 'Download the hash-bound review record'))}</p>")
  review_body+=section(h('review_boundary', 'Review boundary'),'<p>'+h('the_authoritative_matrix_has_conversions_without_target', 'The authoritative matrix has {failed_request_count} conversions without target body text, including {error_outputs} browser error-page output. Each now links from its detail page to direct evidence and a verified cause. The anomaly review covers the 36 deterministically triggered results; it is not a manual editorial-quality review of every remaining normal result. Undisclosed service-internal resource thresholds remain explicitly indivisible.', failed_request_count=failed_request_count, error_outputs=error_outputs)+'</p><p>'+link('../analysis/index.html#inspect?request=failed',h('inspect_results_without_body_text', 'Inspect results without body text'))+'</p>')
  if fixture_correction:
   correction_rows=[]
   for provider,effect in fixture_correction['effects'].items():
    correction_rows.append(('',cell(link('../attempts/'+provider+'__'+fixture_correction['case_id']+'__r001.html',labels[provider]))+cell(pct(effect['old_text_edit']))+cell(pct(effect['new_text_edit']))+cell(esc(effect['old_output_sha256'][:12]))+cell(esc(effect['new_output_sha256'][:12]))))
   receipt_href='../../fixture-correction.json' if english else '../fixture-correction.json'
   review_body+=section(h('frozen_page_query_navigation_correction', 'Frozen-page query-navigation correction'),'<p>'+h('one_frozen_page_appended_a_query_parameter', 'One frozen page appended a query parameter after its inline script ran. The former local test server served only the bare path, so three methods converted a 404 error page that was counted as output. The local test server now serves the same frozen page by path even with a query; the original source URL is still not fetched. The three methods were remeasured with matching binaries and the unchanged scorer; prior evidence remains in a separate archive.')+'</p>'+table([h('method', 'Method'),h('prior_page_text_edit', 'Prior page text_edit'),h('corrected_page_text_edit', 'Corrected page text_edit'),h('prior_output_digest_prefix', 'Prior output digest prefix'),h('new_output_digest_prefix', 'New output digest prefix')],correction_rows,cls='wide-prose-table',metrics=[])+f"<p>{link(receipt_href, h('download_complete_correction_effects', 'Download complete correction effects'))}</p>")
  _wmb_write(out,'review/index.html',h('review_record', 'Review'),review_body,english)

  failure_notes={}
  for finding in (anomaly_review or {}).get('cases',[]):
   observed=finding['observed_en' if english else 'observed_zh']
   cause=finding['cause_en' if english else 'cause_zh']
   for provider in finding.get('affected_providers',[]):
    failure_notes[(provider,finding['case_id'])]=(observed,cause)
  guide=report_content["guide"]["en" if english else "zh"] if report_content is not None else _wmb_guide(english=english,names=names,labels=labels,cases=cases,
                             bycase=bycase,case_count=case_count,score=score,denom=denom,
                             failures=failures,out=out,latency_diagnostic=latency_diagnostic,
                             formal_round=formal_round)
  _wmb_write(out,'guide/index.html',h('interpretation', 'Guide'),guide,english)

  provider_rows=[]
  identities={m['provider']:m['implementation_identity'] for m in manifests}
  for name in names:
   ident=identities[name]
   provider_rows.append(('',cell(labels[name])+cell(esc(ident.get('reported_version') or h('hosted_backend_undisclosed_nightly_has_no_version', 'Hosted backend undisclosed / nightly has no version command')))+cell('<code class="tiny break">'+esc(ident.get('binary_sha256') or '—')+'</code>')+cell(esc(ident.get('deployment')))))
  adoption=hero(h('methods', 'Methods'),h('compare_the_registered_configurations_versions_and_interfaces', 'Compare the {{method_count}} registered configurations; versions and interfaces are listed below.'))
  adoption+=section(h('measured_implementations', 'Measured implementations'),table([h('method', 'Method'),h('version', 'Version'),h('binary_sha_256', 'Binary SHA-256'),h('deployment_2', 'Deployment')],provider_rows,metrics=[]))
  interface_rows=[]
  language='en' if english else 'zh'
  for name in names:
   example=next((r for r in records if r['provider']==name and r['ok']),next(r for r in records if r['provider']==name))
   entry=method_spec(name,example,language=language)['entry']
   metadata=example.get('provider_metadata',{})
   input_route=_observed_route(native_browser_engine(name,example),metadata,language=language)
   output_route=report_method_catalog(language)['output_contract_labels'].get(metadata.get('output_contract'),h('not_recorded', 'Not recorded'))
   official=browser_method_registry().get(name,{}).get('source_url') or report_method(name).get('source_url','')

   interface_rows.append(('',cell(link('../providers/'+name+'.html',labels[name]))+cell(esc(entry))+cell(esc(input_route))+cell(esc(output_route))+cell(link('../attempts/'+example['attempt_id']+'.html',h('actual_input_output_and_component_scores', 'Actual input, output and component scores')))+cell(link(official,h('official_source_2', 'Official source')))))
  adoption+=section(h('run_inputs_outputs_and_examples', 'Run inputs, outputs and examples'),'<p>'+h('these_are_the_frozen_html_routes_used', 'These are the frozen-HTML routes used for this public benchmark, not live fetching of original URLs. Example links open archived calls; account fields show variable names or placeholders only.')+'</p>'+table([h('method', 'Method'),h('interface_program', 'Interface / program'),h('run_input', 'Run input'),h('run_output', 'Run output'),h('archived_call_example', 'Archived call example'),h('official_source_3', 'Official source')],interface_rows,cls='wide-prose-table',metrics=[]))
  if research:
   use_rows=[]
   for item in research.get('direct_benchmarks',[]):
    source_links='<br>'.join(link(url,urlsplit(url).netloc) for url in item.get('sources',[]))
    use_rows.append(('',cell(esc(item['name']))+cell(esc('；'.join(item.get('users',[])) or '—'))+cell(esc(item.get('maturity' if english else 'maturity_zh','—')))+cell(source_links)))
   adoption+=section(h('named_use_of_public_benchmarks', 'Named use of public benchmarks'),'<p>'+h('papers_executable_benchmark_repositories_and_framework_integrations', 'Papers, executable benchmark repositories and framework integrations document public use; no independently auditable commercial production-deployment total was found. Stars or extractor support are not presented as production adoption.')+'</p>'+table([h('dataset', 'Benchmark'),h('verified_users', 'Verified users'),h('assessment', 'Assessment'),h('primary_evidence', 'Primary evidence')],use_rows,metrics=[]))
  if adoption_data:
   counts=adoption_counts(adoption_data,sorted(PROFILES))
   (out/'adoption/counts.json').write_text(json.dumps({'snapshot':adoption_data['generated_at'],'scope':'verified source-signature hits in a bounded candidate set, not production deployment','methods':counts},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
   count_rows=[]
   details=[]
   for item in counts:
    name=item['provider'];n=item['repository_count'];known=n is not None
    label=labels.get(name,name)+(h('not_measured', ' (not measured)') if name not in names else '')
    count_rows.append(('',cell(link('#evidence-'+name,label) if known else esc(label))+cell(str(n) if known else '—',n if known else -1)+cell(str(item['file_count']) if known else '—',item['file_count'] if known else -1)))
    if known:
     evidence_rows=[('',cell(link(repo['url'],repo['repo']))+cell('<br>'.join(link(match['url'],match['path']) for match in repo['matches']))) for repo in item['repositories']]
     details.append('<details id="evidence-'+esc(name)+'"><summary>'+esc(label)+f' · {n} '+h('candidate_repositories', 'candidate repositories')+'</summary>'+table([h('repository', 'Repository'),h('matched_files', 'Matched files')],evidence_rows,metrics=[] )+'</details>')
   data_prefix='../../adoption/' if english else ''
   adoption+=section(h('open_source_adoption_evidence', 'Open-source adoption evidence'),'<p>'+h('this_independent_source_snapshot_covers_registered_methods', 'This independent source snapshot covers registered methods, not only the {{method_count}} measured configurations. Search candidates have ranking, activity and star thresholds; the counts are verified signature hits in the bounded candidate set, not global share or production users. Open a method to inspect repositories and files.')+'</p><p class="source-note">'+esc(adoption_data['generated_at'])+' · '+link(data_prefix+'evidence.json',h('full_source_evidence', 'Full source evidence'))+' · '+link(data_prefix+'counts.json',h('per_method_counts', 'Per-method counts'))+'</p>'+table([h('method', 'Method'),h('matched_repositories', 'Matched repositories'),h('matched_files', 'Matched files')],count_rows,'wmb-adoption-counts',True,metrics=[])+''.join(details))
  _wmb_write(out,'adoption/index.html',h('methods', 'Methods'),adoption,english)

  reproduce=hero(h('reproduction', 'Reproduction'),h('dataset_scorer_browsers_and_outputs_are_fixed', 'Dataset, scorer, browsers and outputs are fixed by hashes or commits.'))
  parent_sha=esc(dataset['source_dataset_sha256'])
  reproduce+=section(h('core_reproduction_commands', 'Core reproduction commands'),'<p>'+h('the_commands_use_this_repository_s_locked', 'The commands use this repository’s locked dependencies. Running all {{method_count}} methods and official scoring first requires the public parent set, the pinned scorer checkout and the measured binaries. Evaluation credentials come only from the local repository .env and are not included in this report. Angle brackets denote local paths.')+'</p><pre>uv run --frozen --extra headless-eval python -m harness.web_fetch run --benchmark webmainbench --providers &lt;name&gt; --dataset &lt;WebMainBench_545.jsonl&gt; --expected-dataset-sha256 '+parent_sha+' --case-ids-file &lt;sample-manifest&gt; --output &lt;method-evidence&gt;\nuv run --frozen --extra webmainbench-eval python -m harness.web_fetch score --benchmark webmainbench --evidence &lt;method-evidence&gt; --dataset &lt;WebMainBench_545.jsonl&gt; --expected-dataset-sha256 '+parent_sha+' --scorer-repo &lt;pinned-scorer-checkout&gt;\nuv run --frozen python -m harness.web_fetch webmainbench-assemble --source-root &lt;six-method-batches&gt; --output &lt;assembled-evidence&gt; --dataset &lt;WebMainBench_545.jsonl&gt; --sample-manifest &lt;sample-manifest&gt; --review &lt;independent-review.json&gt;\nuv run --frozen --extra webmainbench-eval python -m harness.web_fetch webmainbench-teds-correction --source &lt;assembled-evidence&gt; --output &lt;corrected-evidence&gt; --scorer-repo &lt;pinned-scorer-checkout&gt;\nuv run --frozen --extra webmainbench-eval python -m harness.web_fetch webmainbench-teds-review --source &lt;assembled-evidence&gt; --corrected &lt;corrected-evidence&gt;\nmake public-benchmark-report PROFILE=&lt;profile&gt; EVIDENCE=&lt;corrected-evidence&gt;</pre>')
  reproduce+=section(h('pinned_identities', 'Pinned identities'),f"<p>Parent dataset SHA-256: <code>{esc(dataset['source_dataset_sha256'])}</code></p><p>Sample manifest SHA-256: <code>{esc(dataset['sample_manifest_sha256'])}</code> · {link('../sample-manifest.json' if not english else '../../sample-manifest.json', h('download_page_manifest', 'Download {{case_count}}-page manifest'))}</p><p>Official scorer commit: <code>9d991bdc00c57b57521499494d96be85c31317ba</code></p>")
  batch_rows=[]
  for manifest in manifests:
   batch_rows.append(('',cell(labels[manifest['provider']])+cell(esc(manifest['started_at']))+cell(esc(manifest['completed_at']))+cell(str(manifest['case_count']))+cell(str(manifest['timeout_seconds']))+cell('<code class="tiny break">'+esc(manifest['runner_sha256'])+'</code>')))
  reproduce+=section(h('observed_execution_batches', 'Observed execution batches'),table([h('method', 'Method'),h('started_utc_2', 'Started (UTC)'),h('completed_utc', 'Completed (UTC)'),h('task_2', 'Cases'),h('deadline_seconds', 'Deadline seconds'),'Runner SHA-256'],batch_rows,metrics=[]))
  _wmb_write(out,'reproduce/index.html',h('reproduction', 'Reproduction'),reproduce,english)

  release_path=out/'report-changes.json'
  if not release_path.is_file():
   release_data={'schema_version':2,'releases':[]}
   release_path.write_text(json.dumps(release_data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
  else:
   release_data=json.loads(release_path.read_text(encoding='utf-8'))
  release_links=(
   ('latency-corrections.json','查看逐页纠正收据','Inspect the per-page correction receipt'),
   ('prior-latency-results.jsonl','下载原始逐页结果','Download prior per-page results'),
  ) if latency_corrected else ()
  changes=(render_webmainbench_history(release_data,english,release_links) if release_data.get('releases') else '<p>'+h('no_releases_recorded', 'No releases recorded.')+'</p>')
  _wmb_write(out,'changes/index.html',h('changelog', 'Changes'),changes,english)

  account_rows=[]
  for name in names:
   access=method_access(name,records)

   account_rows.append(('',cell(labels[name])+cell(esc(access['account']))+cell('<br>'.join('<code>'+esc(v)+'</code>' for v in access['env_vars']))+cell('<br>'.join(link(url,label) for label,url in access['links']))))
  accounts=hero(h('access_setup', 'Accounts and setup'),h('only_the_kitesurf_cloudflare_route_needs_account', 'Only the Kitesurf Cloudflare route needs account credentials; values are never published.'))
  accounts+=section(h('configuration', 'Configuration'),table([h('method', 'Method'),h('account', 'Account'),h('variables', 'Variables'),h('official_source', 'Official source')],account_rows,cls='accounts-table',metrics=[]))
  _wmb_write(out,'accounts/index.html',h('access_setup', 'Accounts and setup'),accounts,english)

  for c in cases.values():
   ref=contained_path(out,'references/'+c['id']+'.md').read_text(encoding='utf-8')
   frozen_html=contained_path(out,'sources/'+c['id']+'.html.txt').read_text(encoding='utf-8',errors='replace')
   ref_prefix='../../' if english else '../'
   body=hero(h('frozen_page', 'Frozen page')+' · '+c['id'],h('official_html_snapshot_and_human_calibrated_reference', 'Official HTML snapshot and human-calibrated reference'))+'<nav class="wrap actions">'+link('#frozen-input',h('read_archived_page', 'Read archived page'))+' · '+link(c['url'],h('open_source_website', 'Open source website ↗'))+' · '+link('../analysis/index.html#inspect?query='+c['id'],h('compare_method_outputs', 'Compare method outputs'))+'</nav>'
   body+=section(h('source_and_metadata', 'Source and metadata'),f"<p>{link(c['url'], h('original_url_2', 'Original URL ↗'))}</p><pre>{esc(json.dumps(c['meta'], ensure_ascii=False, indent=2))}</pre><p>HTML SHA-256: <code>{esc(c['input_html_sha256'])}</code></p>")
   body+=section(h('human_calibrated_reference', 'Human-calibrated reference'),'<p>'+h('this_markdown_is_the_official_main_content', 'This Markdown is the official main-content reference, not the entire page; it excludes navigation and recommendations and does not guarantee coverage of text in images. The scorer extracts text, code, formulas and tables from each conversion and reference, then calculates five components.')+'</p><p class="actions">'+link(ref_prefix+'references/'+c['id']+'.md',h('download_reference_2', 'Download reference'))+' · '+link('../methodology/index.html',h('official_scoring_method', 'Official scoring method'))+'</p><pre class="wmb-source-pre">'+esc(ref)+'</pre>')
   body+=section(h('frozen_page_reading_view', 'Frozen-page reading view'),'<p class="actions">'+link(c['url'],h('source_website_live_may_have_changed', 'Source website ↗ (live, may have changed)'))+' · '+link('../analysis/index.html#inspect?query='+c['id'],h('inspect_method_results_for_this_page', 'Inspect method results for this page'))+'</p><p class="source-note">'+h('read_the_html_archive_used_in_this', 'Read the HTML archive used in this run below. This safe preview disables scripts, external resources and navigation; images appear as placeholders. It is a static reading view, not a measured browser screenshot or a replay of script execution. The complete input remains in the source view.')+'</p>'+_wmb_html_preview(frozen_html,english)+'<details><summary>'+h('view_complete_html_source', 'View complete HTML source')+'</summary><pre class="wmb-source-pre">'+esc(frozen_html)+'</pre><p>'+link(ref_prefix+'sources/'+c['id']+'.html.txt',h('save_raw_file', 'Save raw file'))+'</p></details>','frozen-input')
   _wmb_write(out,'datasets/'+c['id']+'.html',c['id'],body,english)

  for name in names:
   rs=[r for r in records if r['provider']==name]
   rows=[]
   for r in rs:
    c=cases[r['case_id']]
    output=contained_path(out,r['artifact_path']).read_text(encoding='utf-8',errors='replace')
    state_class='success' if r['ok'] and output.strip() and not _wmb_error_page(output) else 'failure'
    request=f'<span class="wmb-pill {state_class}">{esc(_wmb_body_state(r,output,english))}</span>'
    values=cell(link('../attempts/'+r['attempt_id']+'.html',r['case_id']))+cell(esc(_wmb_category(c,'language')))+cell(esc(_wmb_category(c,'level')))+cell(esc(_wmb_category(c,'table')))+cell(esc(_wmb_category(c,'code')))+cell(esc(_wmb_category(c,'equation')))+cell(request)+cell(esc(_wmb_output_label(r,output,english)))
    values+=''.join(_wmb_metric_cell(r,metric) for metric in metric_order[1:])
    values+=cell(ms(r['elapsed_ms']),r['elapsed_ms'])+cell(link('../datasets/'+r['case_id']+'.html',h('page_and_reference', 'Page and reference')))
    rows.append((_wmb_row_attrs(c,r),values))
   body=hero(labels[name],h('final_results_latency_is_the_final_fetch', '{case_count} final results; latency is the final fetch duration, excluding earlier failures.', case_count=case_count))
   body+=section(h('per_page_results', 'Per-page results'),'<p class="source-note">'+h('open_a_track_id_for_scores_the', 'Open a track ID for scores, the complete response and cause; the last column opens the frozen page and human reference. “Body text produced” only states that readable Markdown exists, not that content passed; error pages are explicit in the response column.')+'</p>'+_wmb_controls(cases,labels,english,include_method=False,failure_types=failure_types)+'<p class="wmb-scroll-note">'+h('scroll_horizontally_and_vertically_within_the_table', 'Scroll horizontally and vertically within the table.')+'</p>'+_wmb_filter_table(['Track ID',h('language', 'Language'),h('difficulty', 'Difficulty'),h('table', 'Table'),h('code', 'Code'),h('formula', 'Formula'),h('body_text_produced', 'Body text produced'),h('response_and_body', 'Response and body') ]+[metric_labels[m] for m in metric_order[1:]]+[h('final_fetch_latency', 'Final fetch latency'),h('page_and_reference', 'Page and reference')],rows,'wmb-provider-'+name,english))
   _wmb_write(out,'providers/'+name+'.html',labels[name],body,english)

  for r in records:
   c=cases[r['case_id']];case_id=r['case_id'];attempt_id=r['attempt_id']
   output_path=contained_path(out,r['artifact_path'])
   output_text=output_path.read_text(encoding='utf-8',errors='replace') if output_path.is_file() else ''
   reference_text=contained_path(out,'references/'+case_id+'.md').read_text(encoding='utf-8')
   attempt_body=_wmb_result_detail(r,c,output_text,reference_text,labels,metric_labels,failure_notes,corrected,english)
   _wmb_write(out,'attempts/'+attempt_id+'.html',labels[r['provider']]+' · '+case_id,attempt_body,english)
 write_language_contract(out,include_english=include_english)


def webmainbench_full_machine_issue(root: Path, row: dict) -> bool:
    """Identify request failures and successful calls without usable page text."""
    if not row["ok"] or not row["output_chars"]:
        return True
    return bool(ERROR_PAGE.search(contained_path(root, row["artifact_path"]).read_text(encoding="utf-8")))


WEBMAINBENCH_FULL_CAUSE_OWNERS = {
    "Moli 解析器崩溃": "Moli 引擎",
    "Kitesurf 服务错误": "Kitesurf 服务",
    "Kitesurf 请求超时": "Kitesurf 服务或传输，内部阶段未定位",
    "Kitesurf 空正文": "Kitesurf 接口，内部原因未知",
    "Obscura 空响应": "Obscura",
    "Obscura 导航循环": "Obscura",
    "冻结页跳转受阻": "共享输入与隔离环境",
}


def full_pages(out: Path, dataset: dict, records: list[dict],
                               summaries: dict, profile: dict) -> None:
    """Render the complete frozen WebMainBench cohort inside the shared report module."""
    case_count = profile["case_count"]
    reference_pages = next(iter(summaries.values()))["official_aggregate"]["denominators"]
    content = json.loads((out / "report-content.json").read_text(encoding="utf-8"))
    failure_analysis = json.loads((out / "failure-analysis.json").read_text(encoding="utf-8"))
    reviewed_failures = {(row["provider"], row["case_id"]): row for row in failure_analysis["reviewed_failures"]}
    machine_issue_count = sum(webmainbench_full_machine_issue(out, row) for row in records)
    adoption = json.loads((out / "provider-adoption.json").read_text(encoding="utf-8"))
    cloudflare_matches = {(entry["repo"], match["path"]) for entry in adoption["included"]
                          for match in entry["matches"] if match["signature"] == "cloudflare-markdown"}
    cloudflare_repositories = len({repo for repo, _ in cloudflare_matches})
    def prose(key):
        return esc(content[key])
    correction_path = out / 'case-correction.json'
    correction = json.loads(correction_path.read_text(encoding='utf-8')) if correction_path.exists() else None
    scoring_correction_path = out / 'scoring-correction.json'
    scoring_correction = json.loads(scoring_correction_path.read_text(encoding='utf-8')) if scoring_correction_path.exists() else None
    def illustration(name, caption):
        if not (out / 'assets' / name).is_file():
            return ''
        return ('<figure data-report-illustration="' + name + '"><div style="overflow-x:auto"><a href="assets/' + name +
                '"><img style="width:100%;min-width:800px;height:auto" loading="lazy" src="assets/' + name +
                '" alt="' + esc(caption) + '"></a></div><figcaption>' + esc(caption) + '（窄屏可横向滚动，点击查看原图。）</figcaption></figure>')
    providers = profile["providers"]
    method_error_profiles = content.get("method_error_profiles")
    if (not isinstance(method_error_profiles, dict)
            or set(method_error_profiles) != set(providers)
            or any(set(item) != {"runtime", "quality", "reading"}
                   for item in method_error_profiles.values())):
        raise ValueError("Report content lacks a complete registered-method error profile")
    moli_fix_profiles = content.get("moli_fix_profiles")
    expected_moli_fix_images = [f"moli-fix-{index:02d}-{suffix}.png" for index, suffix in (
        (1, "visibility-disclosure"), (2, "fallback-fragments"),
        (3, "live-form-state"), (4, "media-candidates"),
        (5, "semantic-boundaries"), (6, "parser-resume-ownership"))]
    if (not isinstance(moli_fix_profiles, list)
            or [item.get("image") for item in moli_fix_profiles] != expected_moli_fix_images
            or any(set(item) != {"image", "title", "symptom", "root", "fix", "verification"}
                   for item in moli_fix_profiles)):
        raise ValueError("Report content lacks the six bound Moli fix profiles")
    cases = {case["id"]: case for case in dataset["cases"]}
    grouped = defaultdict(dict)
    for row in records:
        grouped[row["case_id"]][row["provider"]] = row
    variant_labels = profile.get("variant_labels", {})
    labels = {p: (variant_labels[p] if p in variant_labels else profile["versions"][p] if engine_definition(p).playwright_launched else p.capitalize() +
                  (" " + profile["versions"][p] if not engine_definition(p).hosted else "")) for p in providers}
    content_fixed_commit = json.loads((out / 'moli-content-fixed-report.json').read_text(encoding='utf-8'))['source_commit']
    content_fixed_version = profile['versions']['moli-content-fixed']
    measures = [("overall", "综合分"), ("text_edit", "全文"), ("code_edit", "代码"),
                ("formula_edit", "公式"), ("table_edit", "表格内容"), ("table_TEDS", "表格结构")]

    def percent(value):
        return "—" if value is None else f"{value * 100:.1f}%"

    def score(row, metric):
        return row.get("metrics", {}).get(metric, {}).get("score")

    def classify(row):
        if not row["ok"]:
            return t('request_failure', 'Request failure')
        if not row["output_chars"]:
            return "空输出"
        if ERROR_PAGE.search(contained_path(out, row["artifact_path"]).read_text(encoding="utf-8")):
            return "错误页"
        if score(row, "text_edit") is not None and score(row, "text_edit") < .1:
            return "低全文相似度"
        return "未触发诊断"

    categories = {row["attempt_id"]: classify(row) for row in records}
    counts = Counter(categories.values())
    target_body = {
        row["attempt_id"]: categories[row["attempt_id"]] not in {t('request_failure', 'Request failure'), "空输出", "错误页"}
        for row in records
    }

    def target_body_count(provider):
        return sum(target_body[row["attempt_id"]] for row in records if row["provider"] == provider)
    css = asset_text("webmainbench-full.css")
    js = asset_text("webmainbench-full.js")
    def js_string(value):
        return (json.dumps(value, ensure_ascii=False)
                .replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
                .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))
    js = js.replace("__PROVIDER_LABELS_JSON__", js_string(labels))
    (out / "assets").mkdir(exist_ok=True)
    (out / "assets/style.css").write_text(css, encoding="utf-8")
    (out / "assets/report.js").write_text(js, encoding="utf-8")
    asset_version = hashlib.sha256((css + "\0" + js).encode()).hexdigest()[:12]

    def anchor(path, label):
        return '<a href="' + esc(safe_href(path)) + '">' + esc(label) + '</a>'

    def table(headers, rows, sort_types=None, table_id=None):
        sort_types = sort_types or [None] * len(headers)
        if len(sort_types) != len(headers):
            raise ValueError("Table sort contract differs from its columns")
        header_cells = []
        for label, sort_type in zip(headers, sort_types, strict=True):
            if sort_type:
                header_cells.append('<th aria-sort="none"><button type="button" class="sort-button" data-table-sort onclick="sortReportTable(this)" data-sort-type="' +
                                    esc(sort_type) + '">' + esc(label) + '<span aria-hidden="true"> ↕</span></button></th>')
            else:
                header_cells.append('<th>' + esc(label) + '</th>')
        identity = ' id="' + esc(table_id) + '"' if table_id else ''
        return '<div class="scroll"><table' + identity + '><thead><tr>' + ''.join(header_cells) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'

    navigation = [("总览", "index.html")]
    navigation += [("数据集", "dataset/index.html"),
                  (t('analysis', 'Analysis'), "analysis/index.html"), ("解读", "guide/index.html"),
                  (t('method', 'Method'), "methodology/index.html"), ("复现", "reproduce/index.html"),
                  ("更新记录", "history/index.html")]

    def page(route, title, body):
        prefix = "../" * route.count("/")
        links = ''.join(anchor(path, label) for label, path in navigation)
        markup = ((f'''<!doctype html><html lang="zh"><head><meta name="webstone-report" content="1"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><base href="{prefix or './'}"><title>{esc(title)} · WebMainBench 545</title><link rel="stylesheet" href="assets/style.css?v={asset_version}"><script defer src="assets/report.js?v={asset_version}"></script></head><body><header><div class="wrap"><strong>{anchor('index.html', 'WebMainBench · 545')}</strong><nav>{links}</nav>{anchor('en/index.html', 'English')}<button onclick="theme()">''' + t('toggle_theme', 'Toggle theme') + f"""</button></div></header><main class="wrap"{role_attribute(route, complete_cohort=True)}>{body}</main><footer class="wrap">WebMainBench 545 例 · {str(len(providers))} 种方法 · {anchor('https://github.com/lexmount/WebMainBench/tree/lex-main', 'lex-main 数据与评分器')}</footer></body></html>"""))
        target = out / route
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markup, encoding="utf-8")

    def metric_table():
        rows = []
        for provider in providers:
            summary = summaries[provider]
            scores = summary["official_aggregate"]["scores"]
            denominators = summary["official_aggregate"]["denominators"]
            method_detail = ('<small>实测提交 ' + esc(content_fixed_commit[:9]) + '；Moli ' +
                             esc(content_fixed_version) + '</small>'
                             if provider == 'moli-content-fixed' else '')
            cells = ['<td data-sort="' + esc(labels[provider]) + '">' + anchor("analysis/index.html?provider=" + provider, labels[provider]) + method_detail + "</td>"]
            for metric, _ in measures:
                value = scores[metric]
                all_values = [summaries[other]["official_aggregate"]["scores"][metric] for other in providers]
                winner = "winner comparison-winner" if value == max(all_values) and all_values.count(value) == 1 else ""
                detail = "<small>" + str(denominators[metric]) + " 页计分</small>" if metric != "overall" else ""
                digits = 2 if metric == 'overall' else 3 if metric == 'code_edit' else 1
                cells.append('<td class="number" data-metric="' + metric + '" data-metric-direction="max" data-sort="' + str(value) +
                             '"><span class="' + winner + '">' + f'{value * 100:.{digits}f}%' + "</span>" + detail + "</td>")
            cells.append('<td class="number" data-target-body-provider="' + provider + '" data-sort="' + str(target_body_count(provider)) + '">' +
                         str(target_body_count(provider)) + "/" + str(case_count) + "</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return table([t('method', 'Method')] + [label for _, label in measures] + ["成功生成正文"], rows,
                     ["text"] + ["number"] * len(measures) + ["number"], "core-results")

    def latency_table():
        rows = []
        by_provider = {provider: [row for row in records if row["provider"] == provider] for provider in providers}
        successful_by_provider = {
            provider: [row["elapsed_ms"] for row in by_provider[provider] if target_body[row["attempt_id"]]]
            for provider in providers
        }
        values_by_provider = {provider: ([statistics.mean(values), percentile(values, .5), percentile(values, .95)] if values else [None] * 3) +
                              [sum(row["total_elapsed_ms"] for row in by_provider[provider])]
                              for provider, values in successful_by_provider.items()}
        for provider in providers:
            numbers = []
            for index, value in enumerate(values_by_provider[provider]):
                eligible = [values_by_provider[other][index] for other in providers if other not in variant_labels and values_by_provider[other][index] is not None]
                unique = provider not in variant_labels and value is not None and value == min(eligible) and eligible.count(value) == 1
                marker = '<strong class="comparison-winner winner">' if unique else '<span>'
                end = '</strong>' if unique else '</span>'
                numbers.append('<td class="number" data-metric="success-latency-' + str(index) + '" data-metric-direction="' + ('neutral' if provider in variant_labels else 'min') + '"' +
                               (' data-sort="' + str(value) + '"' if value is not None else '') + '>' + marker +
                               (f"{value:,.0f} ms" if value is not None else "—") + end + '</td>')
            rows.append("<tr><td>" + esc(labels[provider]) + "</td><td data-sort=\"" + str(len(successful_by_provider[provider])) + "\">" + str(len(successful_by_provider[provider])) + "/" + str(case_count) + "</td>" + ''.join(numbers) + "</tr>")
        return table([t('method', 'Method'), "成功生成正文", t('mean', 'Mean'), "P50", "P95", "含重试总耗时"], rows,
                     ["text", "number", "number", "number", "number", "number"])

    def all_call_latency_table():
        distributions = {provider: [row["elapsed_ms"] for row in records if row["provider"] == provider] for provider in providers}
        values = {provider: [statistics.mean(items), percentile(items, .5), percentile(items, .95)]
                  for provider, items in distributions.items()}
        rows = []
        for provider in providers:
            cells = []
            for index, number in enumerate(values[provider]):
                column = [values[other][index] for other in providers if other not in variant_labels]
                unique = provider not in variant_labels and number == min(column) and column.count(number) == 1
                marker = '<strong class="comparison-winner winner">' if unique else '<span>'
                cells.append('<td class="number" data-metric="all-call-latency-' + str(index) + '" data-metric-direction="' + ('neutral' if provider in variant_labels else 'min') + '" data-sort="' +
                             str(number) + '">' + marker + f'{number:,.0f} ms' + ('</strong>' if unique else '</span>') + '</td>')
            rows.append('<tr><td>' + esc(labels[provider]) + '</td><td data-sort="' + str(case_count) + '">' + str(case_count) + '/' + str(case_count) + '</td>' + ''.join(cells) + '</tr>')
        return table([t('method', 'Method'), '全部逻辑调用', t('mean', 'Mean'), 'P50', 'P95'], rows,
                     ['text', 'number', 'number', 'number', 'number'])

    quality_issue_specs = (
        ('delivery', f'未生成正文 / {case_count}', None, None),
        ('text', f'全文 <10% / {reference_pages["text_edit"]}', 'text_edit', .1),
        ('code', f'代码 <50% / {reference_pages["code_edit"]}', 'code_edit', .5),
        ('formula', f'公式 <50% / {reference_pages["formula_edit"]}', 'formula_edit', .5),
        ('table', f'表格内容 <50% / {reference_pages["table_edit"]}', 'table_edit', .5),
        ('teds', f'表格结构 <50% / {reference_pages["table_TEDS"]}', 'table_TEDS', .5),
    )

    def quality_issue_flags(row):
        flags = set()
        if not target_body[row['attempt_id']]:
            flags.add('delivery')
        for key, _, metric, threshold in quality_issue_specs[1:]:
            entry = row.get('metrics', {}).get(metric, {})
            if (entry.get('success') is True
                    and isinstance(entry.get('score'), (int, float))
                    and entry['score'] < threshold):
                flags.add(key)
        return flags

    quality_flags = {row['attempt_id']: quality_issue_flags(row) for row in records}

    def quality_issue_matrix():
        counts_by_provider = {
            provider: {
                key: sum(key in quality_flags[row['attempt_id']]
                         for row in records if row['provider'] == provider)
                for key, _, _, _ in quality_issue_specs
            }
            for provider in providers
        }
        rows = []
        for provider in providers:
            cells = ['<td data-sort="' + esc(labels[provider]) + '">' +
                     anchor('analysis/index.html?provider=' + provider, labels[provider]) + '</td>']
            for key, _, _, _ in quality_issue_specs:
                value = counts_by_provider[provider][key]
                column = [counts_by_provider[other][key] for other in providers]
                winner = 'winner comparison-winner' if value == min(column) and column.count(value) == 1 else ''
                cells.append('<td class="number" data-metric="issue-' + key +
                             '" data-metric-direction="min" data-sort="' + str(value) + '">' +
                             '<a class="' + winner + '" href="analysis/index.html?provider=' + esc(provider) +
                             '&metric=' + esc(key) + '&kind=all">' + str(value) + '</a></td>')
            rows.append('<tr data-quality-provider="' + provider + '">' + ''.join(cells) + '</tr>')
        return table([t('method', 'Method'), *[label for _, label, _, _ in quality_issue_specs]], rows,
                     ['text'] + ['number'] * len(quality_issue_specs), 'quality-issue-matrix')

    home = (f"""<section class="hero"><h1>{prose('home_title')}</h1><p>{prose('home_lead')}</p></section><section><h2>核心结果</h2>{metric_table()}<p class="note">{prose('score_note')}</p></section><section><h2>各方法的质量风险定位</h2>{quality_issue_matrix()}<p class="note">同一结果可同时进入多列。未生成正文按 {case_count} 页统计；全文按 {reference_pages['text_edit']} 页统计；代码、公式、表格内容与表格结构分别只统计 {reference_pages['code_edit']}、{reference_pages['formula_edit']}、{reference_pages['table_edit']}、{reference_pages['table_TEDS']} 页固定参考总体。绿色仅表示该列唯一最少，不代表综合质量排名。</p></section><section><h2>全部页面的调用耗时</h2>{all_call_latency_table()}<p class="note">{prose('all_latency_note')}</p></section><section><h2>目标正文页面的调用耗时与重试成本</h2>{latency_table()}<p class="note">{prose('latency_note')}</p></section><section><h2>问题分布</h2>{table(['自动诊断', '结果数'], ['<tr><td>' + anchor('analysis/index.html?kind=' + kind, kind) + '</td><td data-sort="' + str(count) + '">' + str(count) + '</td></tr>' for kind, count in sorted(counts.items())], ['text', 'number'])}<p class="note">{prose('diagnostic_note')}</p></section>""")
    page("index.html", "完整结果", home)
    dataset_rows = []
    for case in dataset["cases"]:
        case_id = case["id"]
        meta = case["meta"]
        language = meta.get("language") or "—"
        level = meta.get("level") or "—"
        dataset_rows.append('<tr data-dataset data-language="' + esc(language) + '" data-level="' + esc(level) + '" data-search="' +
                            esc((case_id + ' ' + case['url'] + ' ' + language + ' ' + level).lower()) + '"><td>' + anchor("cases/index.html?id=" + case_id, case_id) + "</td><td>" +
                            esc(language) + "</td><td>" + esc(level) +
                            '</td><td class="source">' + anchor(case["url"], case["url"]) + "</td><td>" +
                            anchor("viewer/index.html?path=sources/" + case_id + ".html.txt", "冻结网页") + " · " +
                            anchor("viewer/index.html?path=references/" + case_id + ".md", "人工答案") + "</td></tr>")
    dataset_languages = sorted({(case['meta'].get('language') or '—') for case in dataset['cases']})
    dataset_levels = sorted({(case['meta'].get('level') or '—') for case in dataset['cases']})
    dataset_filters = ('<div class="filters"><label>搜索任务或网址<input id="dataset-q" type="search"></label>'
                       '<label>语言<select id="dataset-language"><option value="">全部语言</option>' +
                       ''.join('<option value="' + esc(value) + '">' + esc(value) + '</option>' for value in dataset_languages) +
                       '</select></label><label>难度<select id="dataset-level"><option value="">全部难度</option>' +
                       ''.join('<option value="' + esc(value) + '">' + esc(value) + '</option>' for value in dataset_levels) +
                       '</select></label></div><p>当前显示 <strong id="dataset-count"></strong> 题。'
                       '<span id="dataset-empty" hidden>当前条件无任务。</span></p>')
    page("dataset/index.html", "数据集", f"""<section class="hero"><h1>冻结网页与人工答案</h1><p>{prose('dataset_lead')}</p></section>{dataset_filters}{table([t('task_id', 'Task ID'), '语言', '难度', t('sources_2', 'Source'), '证据'], dataset_rows, ['text', 'text', 'text', 'text', None], 'dataset-inventory')}""")
    options = ["issue", "all", t('request_failure', 'Request failure'), "空输出", "错误页", "低全文相似度", "未触发诊断"]
    cause_owners = WEBMAINBENCH_FULL_CAUSE_OWNERS
    reviewed_cause_labels = {row["category_label"] for row in failure_analysis["reviewed_failures"]}
    cause_order = tuple(cause for cause in cause_owners if cause in reviewed_cause_labels)
    filters = (('<div class="filters"><label>搜索任务或网址<input id="q" type="search"></label><label>' + t('method', 'Method') + f"""<select id="provider"><option value="">全部方法</option>{''.join(('<option value="' + provider + '">' + esc(labels[provider]) + '</option>' for provider in providers))}</select></label><label>诊断<select id="kind">{''.join(('<option value="' + esc(kind) + '">' + esc('全部问题' if kind == 'issue' else '全部结果' if kind == 'all' else kind) + '</option>' for kind in options))}</select></label><label>质量风险<select id="metric"><option value="">全部类别</option>{''.join(('<option value="' + key + '">' + esc(label) + '</option>' for key, label, _, _ in quality_issue_specs))}</select></label><label>已复核原因<select id="cause"><option value="">全部原因</option>{''.join(('<option value="' + esc(kind) + '">' + esc(kind) + '</option>' for kind in cause_order))}</select></label></div>"""))
    result_rows = []
    for row in records:
        case_id, provider = row["case_id"], row["provider"]
        kind = categories[row["attempt_id"]]
        review = reviewed_failures.get((provider, case_id))
        result_rows.append('<tr data-result data-provider="' + provider + '" data-case-id="' + case_id + '" data-kind="' + esc(kind) +
                           '" data-cause="' + esc(review["category_label"] if review else "") +
                           '" data-quality="' + esc(' '.join(sorted(quality_flags[row['attempt_id']]))) +
                           '" data-search="' + esc((case_id + " " + row["original_url"]).lower()) + '"><td>' +
                           anchor("cases/index.html?id=" + case_id, case_id) + "</td><td>" +
                           anchor("results/index.html?provider=" + provider + "&id=" + case_id, labels[provider]) + "</td><td>" +
                           ('<button type="button" data-diagnose onclick="openDiagnosis(this)">' + esc(kind) + ' · 诊断</button>' if kind != '未触发诊断' else esc(kind)) + "</td>" +
                           '<td data-sort="' + (str(score(row, "text_edit")) if score(row, "text_edit") is not None else '') + '">' +
                           percent(score(row, "text_edit")) + '</td><td data-sort="' + str(row["elapsed_ms"]) + '">' + f'{row["elapsed_ms"]:,.0f} ms' +
                           "</td><td>" + anchor("cases/index.html?id=" + case_id, "同页") + "</td></tr>")
    page("analysis/index.html", t('analysis', 'Analysis'), (f"""<section class="hero"><h1>逐项结果与问题定位</h1><p>{prose('analysis_lead')}</p></section>{filters}<p>当前显示 <strong id="count"></strong> 条。<span id="empty" hidden>当前条件无结果。</span></p>{table([t('task', 'Task'), t('method', 'Method'), '诊断', '全文分', '最终调用', '同页'], result_rows, ['text', 'text', 'text', 'number', 'number', None], 'result-inventory')}<dialog id="diagnosis-dialog" aria-labelledby="diagnosis-title"><button type="button" id="diagnosis-close">关闭诊断</button><h2 id="diagnosis-title"></h2><p id="diagnosis-stage"></p><p id="diagnosis-error"></p><p id="diagnosis-cause"></p><p id="diagnosis-treatment"></p><details><summary>原始运行日志</summary><pre id="diagnosis-log"></pre></details><p class="actions"><a id="diagnosis-result">完整结果与调用记录</a><a id="diagnosis-output">""" + t('raw_output', 'Raw output') + '</a><a id="diagnosis-case">同页各方法对照</a></p></dialog>'))
    page("cases/index.html", "同页比较",
         (f"""<section class="hero"><h1>人工答案与各方法的完整输出</h1><p id="case-id"></p><p class="source" id="case-url"></p></section><p class="actions"><a id="case-source">冻结网页</a><a id="case-reference">人工答案</a>{anchor('dataset/index.html', '返回数据集')}</p><div class="scroll"><table id="case-result-table"><thead><tr><th aria-sort="none"><button type="button" class="sort-button" data-table-sort onclick="sortReportTable(this)" data-sort-type="text">""" + t('method', 'Method') + f"""<span aria-hidden="true"> ↕</span></button></th><th aria-sort="none"><button type="button" class="sort-button" data-table-sort onclick="sortReportTable(this)" data-sort-type="text">诊断<span aria-hidden="true"> ↕</span></button></th>{''.join(('<th aria-sort="none"><button type="button" class="sort-button" data-table-sort onclick="sortReportTable(this)" data-sort-type="number">' + esc(label) + '<span aria-hidden="true"> ↕</span></button></th>' for _, label in measures))}</tr></thead><tbody id="case-results"></tbody></table></div><section><h2>同页内容对照</h2><div class="compare-grid" id="case-comparison"></div></section>"""))
    page("results/index.html", "逐项结果",
         ('<div data-report-detail="pending"><section class="hero"><h1 id="result-title">正在载入结果…</h1><p class="source" id="result-source"></p></section><section class="panel" data-result-summary><h2>结果摘要</h2><p id="result-summary"></p></section><p id="result-correction" hidden>本条为保留原网址后的认证补测结果，已替换旧环境下同一方法的结果。<a href="guide/index.html#fixture-correction">原因、各方法对照与旧调用记录</a></p><section class="panel" data-failure-diagnosis id="result-failure" hidden><h2>错误与诊断</h2><p id="result-stage"></p><p id="result-error"></p><p id="result-cause"></p><p id="result-treatment"></p><details id="result-logs"><summary>原始运行日志</summary><pre data-kind="error" id="result-log"></pre></details></section><section><h2>五项质量指标</h2><div class="scroll"><table><thead><tr><th>' + t('metric', 'Metric') + '</th><th>分数</th><th>状态</th></tr></thead><tbody id="result-metrics"></tbody></table></div></section><details id="result-physical" hidden><summary>每次物理调用</summary><ol id="result-physical-list"></ol></details><p class="actions"><a id="result-case">返回同页比较</a><a id="result-output">完整输出</a><a id="result-reference">人工答案</a><a id="result-source-link">冻结网页</a></p></div>'))
    shards = defaultdict(lambda: {"results": {}, "artifacts": {}, "blobs": {}, "physical": {}})
    result_index = {}
    artifact_index = {}
    def shard_name(provider, case_id):
        bucket = int(hashlib.sha256(case_id.encode()).hexdigest()[:2], 16) % 8
        return f"shards/{provider}-{bucket}.json"
    def bound_artifact(path, expected, shard_path):
        source = contained_path(out, path)
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Public artifact changed: " + path)
        shards[shard_path]["artifacts"][path] = expected
        shards[shard_path]["blobs"][expected] = raw.decode("utf-8")
        artifact_index[path] = shard_path
    for row in records:
        key = row["provider"] + "|" + row["case_id"]
        target = shard_name(row["provider"], row["case_id"])
        shards[target]["results"][key] = {**row, "diagnosis": categories[row["attempt_id"]],
                                          "review": reviewed_failures.get((row["provider"], row["case_id"]))}
        result_index[key] = target
        bound_artifact(row["artifact_path"], row["output_sha256"], target)
    physical_path = out / "physical-attempts.jsonl"
    physical = load_jsonl(physical_path, skip_blank=True) if physical_path.is_file() else []
    for attempt in physical:
        case_id = attempt["track_id"]
        target = shard_name(attempt["provider"], case_id)
        shards[target]["physical"].setdefault(case_id, []).append(attempt)
        bound_artifact(attempt["physical_artifact_path"], attempt["output_sha256"], target)
    for historical_name, path_key in [('superseded-case-results.jsonl', 'artifact_path'),
                                      ('superseded-case-calls.jsonl', 'physical_artifact_path')]:
        historical_path = out / historical_name
        if historical_path.is_file():
            for attempt in iter(load_jsonl(historical_path)):
                case_id = attempt.get('case_id', attempt.get('track_id'))
                target = shard_name(attempt['provider'], case_id)
                bound_artifact(attempt[path_key], attempt['output_sha256'], target)
    for case in dataset["cases"]:
        case_id = case["id"]
        target = shard_name("source", case_id)
        bound_artifact(f"sources/{case_id}.html.txt", case["input_html_sha256"], target)
        bound_artifact(f"references/{case_id}.md", case["reference_sha256"], target)
    for relative, data in shards.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "result-shard-index.json").write_text(json.dumps(result_index, separators=(",", ":")), encoding="utf-8")
    (out / "artifact-shard-index.json").write_text(json.dumps(artifact_index, separators=(",", ":")), encoding="utf-8")
    shutil.rmtree(out / "artifacts")
    shutil.rmtree(out / "sources")
    shutil.rmtree(out / "references")
    interface_rows = ['<tr><td>' + esc(labels[provider]) + '</td><td><code>' + esc(content['interfaces'][provider]) + '</code></td></tr>'
                      for provider in providers]
    retry_count = sum(row["physical_attempt_count"] - 1 for row in records)
    recovered_count = sum(row["ok"] and row["physical_attempt_count"] > 1 for row in records)
    final_request_failures = sum(not row["ok"] for row in records)
    failed_call_counts = Counter(row['physical_attempt_count'] for row in records if not row['ok'])
    failure_calls_note = ('（' + '，'.join(f'{count} 条调用 {calls} 次'
                          for calls, count in sorted(failed_call_counts.items())) + '）') if failed_call_counts else ''
    page("methodology/index.html", t('methodology', 'Methodology'),
         (f"""<section class="hero"><h1>测量与评分方法</h1><p>{prose('method_lead')}</p></section><section class="panel"><h2>""" + t('input', 'Input') + f"""</h2><p>{prose('method_input')}</p><p>数据 SHA-256：<code>{esc(profile['source_dataset_sha256'])}</code>。原始评分器提交：<code>{esc(profile['official_scorer_commit'])}</code>{('；lex-main 修正评分器提交：<code>' + esc(scoring_correction['scorer_commit']) + '</code>' if scoring_correction else '')}。</p></section><section class="panel"><h2>失败重试</h2><p>本轮共 {str(len(records) + retry_count)} 次调用，其中 {str(retry_count)} 次为失败后的重试。{str(recovered_count)} 条请求在重试后成功，{str(final_request_failures)} 条请求最终失败{failure_calls_note}。返回成功但正文为空或为错误页的结果不触发请求失败重试。</p></section><section class="panel"><h2>评分</h2><p>{prose('method_scoring')}</p><p>{prose('method_correction')}</p>{(anchor('teds-correction.json', '历史 TEDS 修正收据') + ' · ' + anchor('teds-correction-review.json', '历史修正复核') if (out / 'teds-correction.json').is_file() else anchor('scoring-correction.json', 'lex-main 统一评分修正收据'))}</section><section><h2>调用接口</h2>{table([t('method', 'Method'), '本轮调用方式'], interface_rows)}</section><p class="note">{prose('kitesurf_version_note')}</p><section class="panel"><h2>公开采用证据</h2><p>{prose('adoption_lead')} 截至 {esc(adoption['cutoff'][:10])}，Cloudflare Markdown 接口在筛选范围内见于 {str(cloudflare_repositories)} 个仓库、{str(len(cloudflare_matches))} 个文件。</p><p>{prose('adoption_limit')}</p>{anchor('provider-adoption.json', '查看检索样本与纳入规则')}</section>"""))
    example_id = dataset["cases"][0]["id"]
    example_row = grouped[example_id][providers[0]]
    example_links = " · ".join([
        anchor("viewer/index.html?path=sources/" + example_id + ".html.txt", "冻结 HTML"),
        anchor("viewer/index.html?path=references/" + example_id + ".md", "人工答案"),
        anchor("viewer/index.html?path=" + example_row["artifact_path"], (f'{labels[providers[0]]} ' + t('output', 'Output'))),
        anchor("cases/index.html?id=" + example_id, "各方法同页比较"),
    ])
    overall_scores = {provider: summaries[provider]["official_aggregate"]["scores"]["overall"]
                      for provider in providers}
    highest = max(overall_scores.values())
    leaders = [provider for provider in providers if overall_scores[provider] == highest]
    outcome = (f"本轮 {case_count} 页中，" + labels[leaders[0]] + " 的综合分最高，为 " + f'{highest * 100:.2f}%' + "。"
               if len(leaders) == 1 else
               f"本轮 {case_count} 页的综合分最高值为 " + percent(highest) + "，由 " +
               "、".join(labels[provider] for provider in leaders) + " 并列取得。")
    variant_ids = [item['provider'] for item in profile.get('variants', [])]
    if len(variant_ids) != 2:
        raise ValueError('The complete report requires both independently measured Moli repair variants')
    prior_moli, content_moli = variant_ids
    changed_moli = [case_id for case_id in cases if len({
        target_body[grouped[case_id][provider]['attempt_id']]
        for provider in ('moli', prior_moli, content_moli)
    }) > 1]
    changed_moli_rows = ["<tr><td>" + anchor("cases/index.html?id=" + case_id, case_id) +
                         "</td><td>" + ("有正文" if target_body[grouped[case_id]['moli']['attempt_id']] else "无正文") +
                         "</td><td>" + ("有正文" if target_body[grouped[case_id][prior_moli]['attempt_id']] else "无正文") +
                         "</td><td>" + ("有正文" if target_body[grouped[case_id][content_moli]['attempt_id']] else "无正文") +
                         "</td><td>" + anchor("cases/index.html?id=" + case_id, "查看三版输出") +
                         "</td></tr>" for case_id in changed_moli]
    comparison_rows = []
    for provider in ('moli', prior_moli, content_moli):
        aggregate = summaries[provider]['official_aggregate']['scores']
        comparison_rows.append('<tr><td>' + esc(labels[provider]) + '</td><td>' +
                               str(target_body_count(provider)) + '/' + str(case_count) + '</td>' + ''.join(
                               '<td>' + f"{aggregate[metric] * 100:.2f}%" + '</td>'
                               for metric, _ in measures) + '</tr>')
    fixed_section = (f"""<section id="moli-repair-lineage"><h2>Moli 三个版本：两次修复各自保留</h2><p>{prose('fixed_quality')}</p><p>{prose('fixed_mechanism')}</p>{table([t('version', 'Version'), '目标正文', *[label for _, label in measures]], comparison_rows)}{(table([t('task', 'Task'), '原版正文', labels[prior_moli], labels[content_moli], '逐项证据'], changed_moli_rows) if changed_moli_rows else '')}<p class="note">两个修复版均为独立的 {case_count} 页认证运行，运行日期不同，耗时不作版本快慢结论。{anchor(profile.get('method_binding_file', 'seven-method-binding.json'), '三轮证据绑定')} · {anchor('https://github.com/lexmount/moli/pull/785', 'PR785')} · {anchor('https://github.com/lexmount/moli/pull/814', 'PR814（历史前身）')} · {anchor('https://github.com/lexmount/moli/pull/919', 'PR919（本次实测来源）')}</p><p class="note">上表内容修复版绑定 PR919 提交 <code>{esc(content_fixed_commit[:9])}</code>：在最新 main 上完成 release 编译和 {case_count} 页认证运行。历史 PR814 结果保留在更新记录中，不混入本表。</p></section>""")
    correction_section = ''
    if correction:
        corrected_id = correction['preflight']['case_id']
        correction_section = (f"""<section id="fixture-correction"><h2>狼人杀规则页：测试地址导致的假失败已纠正</h2><p>页面脚本会检查当前域名和路径。旧环境把地址替换为 localhost，触发跳回原站；隔离规则随后阻断访问。修正后的环境保留原网址，由本地代理提供同一份冻结 HTML，继续阻断外部资源。七种方法使用各自原受测二进制完成同页认证补测，均返回正文。</p>{table([t('method', 'Method'), '补测正文字符数', '补测全文分', '证据'], ['<tr><td>' + esc(labels[p]) + '</td><td>' + str(grouped[corrected_id][p]['output_chars']) + '</td><td>' + percent(score(grouped[corrected_id][p], 'text_edit')) + '</td><td>' + anchor('results/index.html?provider=' + p + '&id=' + corrected_id, '完整输出') + '</td></tr>' for p in providers])}<p>这七条结果统一替换旧环境下的同页结果，其他 {case_count - 1} 页不变。两个修复版 Moli 均生成正文 {min(target_body_count(prior_moli), target_body_count(content_moli))}/{case_count}；历史 {case_count - 1}/{case_count} 对应修正前的测试环境。旧调用额外耗时 {correction['superseded_execution_ms']:,.0f} ms 单独保留，不混入补测最终调用延迟。</p><p>{anchor('case-correction.json', '认证与修订依据')} · {anchor('superseded-case-results.jsonl', '被替换的七条历史结果')}</p></section>""")
    method_rows = []
    diagnostic_rows = []
    diagnostic_kinds = (t('request_failure', 'Request failure'), "空输出", "错误页", "低全文相似度")
    for provider in providers:
        selected = [row for row in records if row["provider"] == provider]
        issues = sum(categories[row["attempt_id"]] != "未触发诊断" for row in selected)
        profile_item = method_error_profiles[provider]
        method_rows.append("<tr><td>" + anchor("analysis/index.html?provider=" + provider, labels[provider]) +
                           "</td><td>" + str(target_body_count(provider)) +
                           "/" + str(case_count) + "</td><td>" + str(issues) + "</td><td>" + esc(profile_item["runtime"]) +
                           "</td><td>" + esc(profile_item["quality"]) + "</td><td>" +
                           esc(profile_item["reading"]) + "</td></tr>")
        diagnostic_rows.append("<tr><td>" + esc(labels[provider]) + "</td>" + "".join(
            '<td data-diagnostic-provider="' + provider + '" data-kind="' + kind + '">' +
            anchor("analysis/index.html?provider=" + provider + "&kind=" + kind,
                   str(sum(categories[row["attempt_id"]] == kind for row in selected))) + "</td>"
            for kind in diagnostic_kinds) + "</tr>")
    cause_counts = Counter((row["category_label"], row["provider"])
                           for row in failure_analysis["reviewed_failures"])
    unknown_causes = {cause for cause, _ in cause_counts} - set(cause_owners)
    if unknown_causes:
        raise ValueError("Reviewed failure cause lacks an owner: " + ", ".join(sorted(unknown_causes)))
    cause_rows = []
    for cause in cause_order:
        cells = []
        for provider in providers:
            count = cause_counts[cause, provider]
            label = str(count) if count else "—"
            target = "analysis/index.html?provider=" + provider + "&cause=" + cause
            cells.append('<td data-cause-provider="' + provider + '" data-cause-kind="' + esc(cause) + '">' +
                         (anchor(target, label) if count else label) + '</td>')
        cause_rows.append('<tr><td>' + esc(cause) + '</td>' + ''.join(cells) +
                          '<td>' + esc(cause_owners[cause]) + '</td></tr>')
    moli_fix_slides = []
    for item in moli_fix_profiles:
        facts = ''.join('<div><strong>' + label + '</strong>' + esc(item[key]) + '</div>'
                        for label, key in (("现象", "symptom"), ("根因", "root"),
                                           ("修复", "fix"), ("验证", "verification")))
        moli_fix_slides.append(
            '<article class="presentation-slide" data-moli-fix-slide="' + esc(item["image"]) + '"><h3>' +
            esc(item["title"]) + '</h3>' + illustration(item["image"], item["title"]) +
            '<div class="slide-facts">' + facts + '</div></article>')
    moli_fix_figures = ('<section id="moli-fix-types"><h2>Moli：六类问题各用一张图说明</h2>'
                        '<p class="note">每张图按现象、根因、修复和泛化验证组织，可单独作为评审幻灯片。</p>' +
                        ''.join(moli_fix_slides) + '</section>')
    guide_error_landscape = (
        f"""<section id="error-landscape"><h2>各方法错在哪、差在哪</h2><div class="presentation-slide">{illustration('seven-method-error-taxonomy.png', '错误先分为运行交付故障和内容质量缺口，再按方法定位主要问题族。')}</div><div class="presentation-slide"><h3>精确数量：固定总体质量风险</h3>{illustration('method-error-landscape.png', '七种方法的正文交付、全文、代码、公式和表格问题分布。')}{quality_issue_matrix()}<p class="note">同一结果可以同时属于多类问题；低分阈值用于定位，不等于二元通过率。</p></div><div class="presentation-slide"><h3>运行结果类型与已复核根因</h3>{table([t('method', 'Method'), *diagnostic_kinds], diagnostic_rows)}{table(['根因', *[labels[provider] for provider in providers], '归属或定位'], cause_rows)}<p>{str(len(reviewed_failures))}/{str(machine_issue_count)} {prose('review_scope_failure')}；{str(len(records) - len(reviewed_failures))} {prose('review_scope_pass')}</p>{anchor('failure-analysis.json', '查看逐条失败原因与处理')}</div><div class="presentation-slide"><h3>逐方法错误画像</h3>{table([t('method', 'Method'), '目标正文', '自动诊断数', '运行交付错误', '主要质量缺口', '处理优先级'], method_rows, table_id='method-error-profiles')}<p class="note">质量缺口是固定阈值下的测量结果；只有有直接证据的运行故障才给出根因归属。</p></div></section>""")
    page("guide/index.html", "结果解读",
         (f"""<section class="hero"><h1>结果解读</h1><p>{esc(outcome)} {prose('guide_lead')}</p></section>{illustration('seven-method-overview.png', f'{case_count} 页七方法结果、目标正文覆盖与两次 Moli 修复的关系；分数与计分范围以表格为准。')}<section><h2>核心结果</h2>{metric_table()}<p class="note">{prose('guide_scope')}</p></section>{guide_error_landscape}{moli_fix_figures}{illustration('moli-repair-mechanisms.png', '从内容缺陷、实现原因到修复层和输出变化的对应关系。')}{fixed_section}{correction_section}<section><h2>核对完整输出</h2><p>{prose('guide_outputs')}</p>{anchor('analysis/index.html?kind=低全文相似度', '查看低全文分案例')}</section><section><h2>可检查的一页</h2><p>{prose('guide_example')}</p><p>{example_links}</p></section><section id="content-audit"><h2>{case_count} 页内容审计如何进入修复</h2><p>{prose('audit_scope')}</p><p>{prose('audit_findings')}</p><p>{prose('audit_resolution')}</p><p class="note">{prose('audit_limit')}</p></section><section><h2>""" + t('decision', 'Conclusion') + f"</h2><p>{prose('guide_conclusion')}</p></section>"))
    evidence_links = ["current-report.json", "cohort-certificate.json", "scoring-certificate.json",
                      *[item['profile'] for item in profile.get('variants', [])],
                      *[item['provider'] + "-cohort-certificate.json" for item in profile.get('variants', [])],
                      *[item['provider'] + "-scoring-certificate.json" for item in profile.get('variants', [])],
                      profile.get("method_binding_file", "seven-method-binding.json"),
                      "execution.json", "dataset.json",
                      "results.jsonl", "physical-attempts.jsonl",
                      "score-summaries.json", "scoring-correction.json",
                      "report-content.json", "failure-analysis.json"]
    if (out / "teds-correction-review.json").is_file():
        evidence_links.append("teds-correction-review.json")
    if correction:
        evidence_links += ['case-correction.json', 'case-correction-scoring.json',
                           'superseded-case-results.jsonl', 'superseded-case-calls.jsonl']
    page("reproduce/index.html", "复现依据",
         '<section class="hero"><h1>复现依据</h1><p>' + prose('reproduce_lead') + '</p></section><p>' +
         " · ".join(anchor(name, name) for name in evidence_links) + "</p>")
    releases = json.loads((repository_root() / 'research/report-changes.json').read_text(encoding='utf-8'))['releases']
    webmainbench_releases = [item for item in releases if 'webmainbench_frozen_html_to_markdown' in item.get('comparison_kinds', [])]
    history_rows = ['<tr data-release-id="' + esc(item['id']) + '"><td>' + esc(item['date']) +
                    '</td><td>' + esc(item['title']) + '</td><td>' + esc(item['summary']) + '</td></tr>'
                    for item in webmainbench_releases]
    page('history/index.html', '更新记录',
         f"""<section class="hero"><h1>报告更新记录</h1><p>按发布记录说明读者可见的结果与内容变化。</p></section>{table(['日期', '更新', '内容'], history_rows)}""")
    page("viewer/index.html", "证据查看器",
         ('<section class="hero"><h1>证据查看器</h1><p>按需载入原始证据。</p></section><div class="panel"><p id="evidence-path"></p><p><a id="evidence-raw" href="#">' + t('original_file', 'Original file') + '</a></p><pre id="evidence-content">正在载入…</pre></div>'))
    page("en/index.html", "英文版未生成",
         f"""<section class="hero"><h1>英文版未生成</h1><p>本轮报告仅生成中文版。</p></section>{anchor('index.html', '返回中文报告')}""")
    (out / "report-manifest.json").write_text(json.dumps({
        "schema_version": 1, "report": "webmainbench-545-full", "languages": ["zh"],
        "case_count": case_count, "provider_count": len(providers), "result_count": len(records),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
