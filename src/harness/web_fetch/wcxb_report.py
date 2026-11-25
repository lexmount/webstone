"""The WCXB report page set, contributed to the Web Fetch template through the WCXB benchmark view."""
from __future__ import annotations

import json
import re

from collections import Counter as WordCounter, defaultdict
from harness.report_languages import write_language_contract
from harness.reporting.mermaid import mermaid_script_attributes
from harness.reporting.presentation import esc
from harness.web_fetch.methods import method_access, native_browser_engine, report_method, versioned_method_label
from harness.web_fetch.report_model import RenderContext
from harness.web_fetch.report import readable_output
from harness.web_fetch.webmainbench_report import _wmb_html_preview
from harness.web_fetch.report_i18n import report_text, t
from harness.web_fetch.report_interfaces import _observed_route, method_spec
from harness.web_fetch.report_style import asset_text
from harness.web_fetch.template import role_attribute
from harness.web_fetch.wcxb_latency import load_full_paired_latency, summarize_full_paired_latency
from harness.web_fetch.wcxb_scoring import paired_latency_methods
from lexbench_webfetch.paths import contained_path
from pathlib import Path

def format_wcxb_conditional_latency(value: float | None) -> str:
 return f'{value:,.0f} ms' if value is not None else '—'


def render_pages(out: Path, rows: list[dict], summary: dict, ground_truth: dict,
                  identities: dict, profile: dict, report_content: dict, *,
                  include_english: bool = False) -> None:
 """Render the current WCXB comparison from one certified semantic model."""
 ctx=RenderContext();scope=ctx.methods
 languages=('zh','en') if include_english else ('zh',)
 if (report_content.get('schema_version') != 1
     or not isinstance(report_content.get('guide'),dict)
     or any(not isinstance(report_content['guide'].get(lang),str)
            or not report_content['guide'][lang].strip() for lang in languages)):
  raise ValueError('WCXB authored interpretation lacks requested languages')
 providers=list(summary['providers'])
 comparison=profile.get('paired_comparison') or {}
 baseline=comparison.get('baseline')
 candidate=comparison.get('candidate')
 if comparison and (baseline == candidate or {baseline,candidate} - set(providers)):
  raise ValueError('WCXB paired comparison must name two registered participants')
 audit=summary['reference_audit']
 case_count=summary['case_count']
 quality_count=summary['quality_case_count']
 result_count=case_count*len(providers)
 visible_count=quality_count*len(providers)
 review_scope=json.loads((out/'research/wcxb-representative-review.json').read_text(encoding='utf-8'))
 reviewed_cases=review_scope['case_count']
 reviewed_results=review_scope['result_count']
 unreviewed_results=result_count-reviewed_results
 exclusion_ids={row['file_id']:row['reason'] for row in summary.get('exclusions',[])}
 source_repository=profile['source_repository'].rstrip('/')
 source_commit=profile['source_commit']
 by_case=defaultdict(dict)
 for row in rows:by_case[row['case_id']][row['provider']]=row
 if len(by_case)!=case_count or any(set(group)!=set(providers) for group in by_case.values()):
  raise ValueError('WCXB same-case matrix is incomplete')
 paired_methods={}
 pair={}
 paired_latency=None
 if profile.get('latency_correction_receipt_sha256'):
  paired_methods=paired_latency_methods(profile,{"providers":[{"provider":p,"identity":identities[p]} for p in providers]})
  pair={role:provider for provider,role in paired_methods.items()}
  _, selected_latency = load_full_paired_latency(out/'research/wcxb-full-paired-latency', profile, set(by_case))
  paired_latency = summarize_full_paired_latency(selected_latency, set(by_case))

 adoption_data=json.loads((out/'research/provider-adoption.json').read_text(encoding='utf-8'))
 cloudflare_matches=[(entry['repo'], match['path']) for entry in adoption_data['included']
                     for match in entry['matches'] if match['signature']=='cloudflare-markdown']
 cloudflare_repo_count=len({repo for repo,_ in cloudflare_matches})
 cloudflare_file_count=len(set(cloudflare_matches))
 style=asset_text('wcxb.css')
 script=asset_text('wcxb.js').replace('/*mermaid-script-attributes*/',mermaid_script_attributes('loader'))
 L={
  'zh':{'home':'首页','results':t('analysis', 'Analysis'),'method':t('methodology', 'Methodology'),'guide':t('interpretation', 'Interpretation'),'repro':t('reproduction', 'Reproduction'),'theme':t('toggle_theme', 'Toggle theme'),'headline':'WCXB 主内容提取对比','core':'核心结果','provider':t('browser', 'Browser'),'delivered':'生成非空内容','precision':'精确率','recall':'召回率','f1':'F1','latency':'平均端到端耗时','type':'页面类型','case':'网页','diagnosis':'定位','detail':'查看同页证据','all':t('all', 'All'),'issues':'待查问题','query':'搜索网页或错误','count':'条结果','reference':'人工主内容参考','output':'完整浏览器输出','error':'原始程序错误','missing':'参考中未匹配的高频词','extra':'输出中多出的高频词','summary':t('all_method_results_for_the_same_page', 'All method results for the same page'),'note':'低精确率通常表示输出夹带较多参考外文字；低召回率表示参考词语大量缺失。这是定位线索，不能仅凭词袋分数确定程序故障。','scope':t('counting_methodology', 'Counting methodology'),'limit':'边界与已知标注问题','source':'冻结来源','version':'实测版本或服务身份','no_review':f'典型异常审查覆盖 {reviewed_cases} 页、{reviewed_results} 条输出；其余 {unreviewed_results:,} 条尚未逐项人工审查。定位分类由确定性规则产生，不等同于根因。'},
  'en':{'home':'Conclusions','results':'Results analysis','method':'Method','guide':'Interpretation','repro':'Reproduce','theme':'Theme','headline':'WCXB main-content extraction comparison','core':'Core results','provider':'Browser','delivered':'Nonempty outputs','precision':'Precision','recall':'Recall','f1':'F1','latency':'Mean end-to-end latency','type':'Page type','case':'Page','diagnosis':'Locator','detail':'Same-page evidence','all':'All','issues':'Issues to inspect','query':'Search page or error','count':'results','reference':'Human main-content reference','output':'Complete browser output','error':'Exact program error','missing':'Frequent unmatched reference words','extra':'Frequent extra output words','summary':'All method results for this page','note':'Low precision often means output contains much text outside the reference; low recall means much reference text is missing. These are locators, not proof of a program defect.','scope':'Population and calculation','limit':'Limits and annotation issues','source':'Frozen source','version':'Measured version or service identity','no_review':f'Targeted anomaly review covers {reviewed_cases} pages and {reviewed_results} outputs; the other {unreviewed_results:,} results have not been individually reviewed. Deterministic locators are not root causes.'}}
 L['zh'].update({'dataset':t('dataset', 'Dataset'),'review':t('review_record', 'Review record'),'adoption':t('methods', 'Methods'),'changes':t('changelog', 'Changelog'),'accounts':t('access_setup', 'Access & setup')})
 L['en'].update({'dataset':'Dataset','review':'Verification','adoption':'Methods compared','changes':'Changes','accounts':'Accounts and setup'})
 L['zh']['headline']=t('wcxb_main_content_extraction_configurations', 'WCXB main-content extraction: {count} configurations', count=len(providers))
 L['en']['headline']=f'WCXB main-content extraction: {len(providers)} configurations'
 L['zh']['summary']=t('all_method_results_for_the_same_page', 'All method results for the same page')
 L['en']['summary']='All method results for this page'
 L['zh']['intro']=t('configurations_process_the_same_frozen_pages_each', '{count} configurations process the same {case_count} frozen pages; each is scored separately. F1 compares extracted words with the human main-content reference and does not measure Markdown structure or link usability.', count=len(providers), case_count=case_count)

 L['en']['intro']=f'{len(providers)} configurations processed the same {case_count} frozen pages; each configuration has separate results. F1 measures word overlap with human main content, not Markdown structure or link usability.'
 (out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(style,encoding='utf-8')
 (out/'assets/report.js').write_text(script,encoding='utf-8')
 def href(lang,path):return ('en/' if lang=='en' else '')+path
 def page(lang,path,title,body):
  x=L[lang]
  route=('en/' if lang=='en' else '')+path
  root_prefix='../'*route.count('/')
  nav=''.join(f'<a href="{href(lang,p)}">{esc(x[k])}</a>' for k,p in [('home','index.html'),('dataset','datasets/index.html'),('method','methodology/index.html'),('results','analysis/index.html'),('review','review/index.html'),('guide','guide/index.html'),('adoption','adoption/index.html'),('repro','reproduce/index.html'),('changes','changes/index.html'),('accounts','accounts/index.html')])
  other=('en/'+path if include_english else 'en/index.html') if lang=='zh' else path
  head=f'''<!doctype html><html lang="{lang}"><head><meta name="webstone-report" content="1"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><base href="{root_prefix or './'}"><title>{esc(title)} · WCXB</title><link rel="stylesheet" href="assets/style.css"><script defer src="assets/report.js"></script></head><body><header><div class="wrap"><strong><a href="{href(lang, 'index.html')}">Web Fetch</a></strong><nav>{nav}</nav><a href="{other}">{('English' if lang == 'zh' else '中文')}</a><button onclick="theme()">{esc(x['theme'])}</button></div></header><main class="wrap"{role_attribute(path)}>{body}</main><footer class="wrap">WCXB v1.0 · {case_count} {report_text('zh' if lang == 'zh' else 'en')('evaluated_test_pages', 'evaluated test pages')} · CC BY 4.0 · <a href="{source_repository}/tree/{source_commit}">{esc(x['source'])}</a></footer></body></html>'''
  target=out/('en' if lang=='en' else '')/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(head,encoding='utf-8')
 def fmt(v,digits=1):return f'{v*100:.{digits}f}%'
 diag_labels={
  'zh':{'none':'未触发定位','request_error':t('request_failure', 'Request failure'),'empty_output':'输出为空','error_page':'错误页内容','empty_reference':'标准答案为空','excluded':'参考不合格','low_precision_and_recall':'精确率与召回率均低','low_precision':'精确率低','low_recall':'召回率低'},
  'en':{'none':'No locator triggered','request_error':'Request error','empty_output':'Empty output','error_page':'Error page','empty_reference':'Empty reference','excluded':'Reference excluded','low_precision_and_recall':'Low precision and recall','low_precision':'Low precision','low_recall':'Low recall'},
 }
 def diag(lang,value):return diag_labels[lang].get(value,value.replace('_',' '))
 type_labels={'article':'文章','service':'服务','product':'商品','collection':'合集','forum':'论坛','listing':'列表','documentation':t('documents', 'Documents')}
 def type_label(lang,value):return type_labels.get(value,value) if lang=='zh' else value
 access_records=[{'provider':p, 'provider_metadata':identities[p]} for p in providers]
 def browser_label(provider):
  if profile.get('participants',{}).get(provider,{}).get('label'):
   return profile['participants'][provider]['label']
  record={'provider':provider, 'provider_metadata':identities[provider]}
  registered=report_method(provider,record, scope=scope)
  base=registered.get('label',{}).get('en',identities[provider].get('engine') or provider)
  version=identities[provider].get('reported_version')
  label=versioned_method_label(registered,base,version) if version else base
  if provider == candidate and profile.get('candidate_source_commit'):
   label+=' · '+profile['candidate_source_commit'][:8]
  return label

 def comparison_cell(value, metric, direction, winning, display):
  marker=f'<strong class="comparison-winner">{display}</strong>' if winning else display
  return f'<td data-metric="{metric}" data-metric-direction="{direction}" data-sort="{value}">{marker}</td>'
 def unique_winner(values, direction):
  extreme=(min if direction=='min' else max)(values.values())
  return next((name for name,value in values.items() if value==extreme),None) if list(values.values()).count(extreme)==1 else None
 def score_digits(values, direction='max'):
  winner=unique_winner(values,direction)
  if winner is None:return 1
  for digits in (1,2,3,4):
   rounded={name:round(value*100,digits) for name,value in values.items()}
   if unique_winner(rounded,direction)==winner:return digits
  return 4
 for lang in (('zh','en') if include_english else ('zh',)):
  x=L[lang]
  best_value=max(summary['providers'][p]['quality_f1'] for p in providers)
  fast_value=min(summary['providers'][p]['mean_latency_ms'] for p in providers)
  best=[p for p in providers if summary['providers'][p]['quality_f1']==best_value]
  fast=[p for p in providers if summary['providers'][p]['mean_latency_ms']==fast_value]
  metrics=(('quality_precision','max','precision'),('quality_recall','max','recall'),('quality_f1','max','f1'),('mean_latency_ms','min','mean'),('p50_latency_ms','min','p50'),('p95_latency_ms','min','p95'))
  winners={key:unique_winner({p:summary['providers'][p][key] for p in providers},direction) for key,direction,_ in metrics}
  metric_digits={key:score_digits({p:summary['providers'][p][key] for p in providers},direction) for key,direction,_ in metrics if direction=='max'}
  metric_digits['quality_f1']=max(metric_digits['quality_f1'],4)

  core_rows=[]
  for p in sorted(providers,key=lambda name:summary['providers'][name]['quality_f1'],reverse=True):
   s=summary['providers'][p]
   cells=''.join(comparison_cell(s[key],label,direction,p==winners[key],fmt(s[key],metric_digits[key]) if direction=='max' else f'{s[key]:,.0f} ms') for key,direction,label in metrics)
   core_rows.append(f'<tr><td><a href="{href(lang,"providers/"+p+".html")}">{esc(browser_label(p))}</a></td><td>{s["delivered"]}/{case_count}</td>{cells}</tr>')
  core_headers=[x['provider'],x['delivered'],x['precision'],x['recall'],f'F1 ({quality_count})',(report_text('zh' if lang == 'zh' else 'en')('mean_latency', 'Mean latency')),(report_text('zh' if lang == 'zh' else 'en')('p50_latency', 'P50 latency')),(report_text('zh' if lang == 'zh' else 'en')('p95_latency', 'P95 latency'))]
  core_header_html=[]
  for index,label in enumerate(core_headers):
   sort_attr=' aria-sort="descending"' if index==4 else ''
   next_direction=' data-next-direction="asc"' if index==4 else ''
   core_header_html.append(f'<th{sort_attr}><button type="button" data-core-sort="{index}"{next_direction}>{esc(label)} ↕</button></th>' if index else f'<th>{esc(label)}</th>')
  core='<div class="scroll"><table id="wcxb-core"><thead><tr>'+''.join(core_header_html)+'</tr></thead><tbody>'+''.join(core_rows)+'</tbody></table></div>'
  moli_before=summary['providers'].get(baseline)
  moli_after=summary['providers'].get(candidate)
  if moli_before and moli_after:
   page_deltas=[by_case[c][candidate]['f1']-by_case[c][baseline]['f1'] for c in by_case if c not in exclusion_ids]
   up=sum(delta>1e-10 for delta in page_deltas);down=sum(delta< -1e-10 for delta in page_deltas);same=quality_count-up-down
   precision_delta=(moli_after['quality_precision']-moli_before['quality_precision'])*100
   recall_delta=(moli_after['quality_recall']-moli_before['quality_recall'])*100
   f1_delta=(moli_after['quality_f1']-moli_before['quality_f1'])*100
   example_case=max((c for c in by_case if c not in exclusion_ids), key=lambda c: abs(by_case[c][candidate]['f1']-by_case[c][baseline]['f1']))
   relation_counts={'higher':up,'lower':down,'equal':same}
   relation_labels=({'higher':'上升','lower':'下降','equal':'不变'} if lang=='zh' else {'higher':'higher','lower':'lower','equal':'unchanged'})
   change_counts=' · '.join(f'<span data-f1-relation="{relation}" data-count="{count}">{count} {relation_labels[relation]}</span>' for relation,count in relation_counts.items())
   paired=(report_text('zh' if lang == 'zh' else 'en')('section_id_configuration_change_class_panel_data', '<section id="configuration-change" class="panel" data-baseline="{baseline}" data-candidate="{candidate}"><h2>Page-level changes between configurations</h2><p>F1 across {quality_count} pages: {change_counts}. Mean recall changes {recall_delta:+.4f} percentage points, mean precision {precision_delta:+.4f}, and mean F1 {f1_delta:+.4f}. F1 is the mean of page-level F1 values, not a recomputation from mean precision and recall.</p><p><a href="{href}">Inspect same-page evidence</a> · <a href="{href2}">Read the page-level analysis</a></p></section>', baseline=esc(baseline), candidate=esc(candidate), quality_count=quality_count, change_counts=change_counts, recall_delta=recall_delta, precision_delta=precision_delta, f1_delta=f1_delta, href=href(lang, 'cases/' + example_case + '.html'), href2=href(lang, 'guide/index.html')))
  else:paired=''
  before=summary['providers'].get(pair.get('release'))
  after=summary['providers'].get(pair.get('pr'))
  latency_context=(report_text('zh' if lang == 'zh' else 'en')('p_id_paired_latency_context_and_were', '<p id="paired-latency-context">{browser_label} and {browser_label2} were measured in a balanced same-window crossover on all {case_count} pages. The preselected final round gives {mean_latency_ms:,.0f} ms for the release and {mean_latency_ms2:,.0f} ms for the PR, a {paired_delta_mean_ms:+.1f} ms page-paired difference. Timing differences describe this measured comparison; timing alone does not establish their cause. <a href="{href}">Read the timing basis</a></p>', browser_label=esc(browser_label(pair['release'])), browser_label2=esc(browser_label(pair['pr'])), case_count=case_count, mean_latency_ms=before['mean_latency_ms'], mean_latency_ms2=after['mean_latency_ms'], paired_delta_mean_ms=paired_latency['paired_delta_mean_ms'], href=href(lang, 'guide/index.html'))) if before and after and paired_latency else ''
  best_labels=('、' if lang=='zh' else ', ').join(browser_label(p) for p in best)
  fast_labels=('、' if lang=='zh' else ', ').join(browser_label(p) for p in fast)
  lead=(f"{best_labels} 在 {quality_count} 页有效参考上的平均 F1 {('并列最高' if len(best) > 1 else '最高')}（{fmt(best_value)}）；{fast_labels} 的 {case_count} 次调用平均端到端耗时{('并列最短' if len(fast) > 1 else '最短')}（{fast_value:,.0f} ms）。" if lang=='zh' else f'{best_labels} {"tie for the highest" if len(best)>1 else "has the highest"} mean F1 across {quality_count} qualified pages ({fmt(best_value)}); {fast_labels} {"tie for the shortest" if len(fast)>1 else "has the shortest"} mean end-to-end latency across {case_count} calls ({fast_value:,.0f} ms).')

  type_counts=WordCounter(gt['page_type'] for c,gt in ground_truth.items() if c not in exclusion_ids)
  slice_values={t:{p:sum(by_case[c][p]['f1'] for c,g in ground_truth.items() if g['page_type']==t and c not in exclusion_ids)/type_counts[t] for p in providers} for t in type_counts}
  slices=''.join(f'<tr data-page-type="{esc(t)}"><td>{esc(type_label(lang,t))}</td><td>{type_counts[t]}</td>'+''.join(comparison_cell(slice_values[t][p],f'type-{t}','max',p==unique_winner(slice_values[t],'max'),fmt(slice_values[t][p],score_digits(slice_values[t]))) for p in providers)+'</tr>' for t in sorted(type_counts))
  slice_table='<div class="filters"><label>'+esc(x['type'])+'<select id="home-type"><option value="">'+esc(x['all'])+'</option>'+''.join(f'<option value="{esc(t)}">{esc(type_label(lang,t))}</option>' for t in sorted(type_counts))+'</select></label></div><div class="scroll"><table id="wcxb-types"><thead><tr><th>'+esc(x['type'])+'</th><th>'+(report_text('zh' if lang == 'zh' else 'en')('pages', 'Pages'))+'</th>'+''.join(f'<th>{esc(browser_label(p))} F1</th>' for p in providers)+'</tr></thead><tbody>'+slices+'</tbody></table></div>'
  home=f'<div class="hero"><span class="eyebrow">Web Fetch · WCXB v1.0</span><h1>{esc(x["headline"])}</h1><p>{esc(x["intro"])}</p></div><section><h2>{esc(x["core"])}</h2><p>{esc(lead)}</p><p class="small muted">'+(report_text('zh' if lang == 'zh' else 'en')('green_stars_mark_a_unique_column_best', 'Green stars mark a unique column best. Quality and all three latency measures use the same {case_count} pages; latency includes failed retries. P50 is the median and P95 is the time within which 95% of calls complete. Each method describes its measured batch; the designated pair’s timings use the certified same-window crossover.', case_count=case_count))+f'</p>{core}{latency_context}</section>'+f'''<section><h2>{report_text('zh' if lang == 'zh' else 'en')('by_page_type', 'By page type')}</h2><p>{report_text('zh' if lang == 'zh' else 'en')('filter_by_page_type_each_cell_is', 'Filter by page type. Each cell is mean page F1; a green star marks the unique row leader.')}</p>{slice_table}</section><section class="lead-grid"><div class="panel"><h3>{esc(x['dataset'])}</h3><p>{report_text('zh' if lang == 'zh' else 'en')('inspect_frozen_pages_source_urls_and_references', 'Inspect frozen pages, source URLs and references.')}</p><a href="{href(lang, 'datasets/index.html')}">{esc(x['dataset'])} →</a></div><div class="panel"><h3>{esc(x['results'])}</h3><p>{report_text('zh' if lang == 'zh' else 'en')('filter_individual_results_and_inspect_responses_and', 'Filter individual results and inspect responses and evidence.')}</p><a href="{href(lang, 'analysis/index.html')}">{esc(x['results'])} →</a></div><div class="panel"><h3>{esc(x['guide'])}</h3><p>{report_text('zh' if lang == 'zh' else 'en')('understand_differences_and_scoring_limits', 'Understand differences and scoring limits.')}</p><a href="{href(lang, 'guide/index.html')}">{esc(x['guide'])} →</a></div></section>'''
  home+=paired
  success_rows=''.join(f'<tr><td>{esc(p)}</td><td>{summary["providers"][p]["success_latency_count"]}/{case_count}</td><td>{format_wcxb_conditional_latency(summary["providers"][p]["success_mean_latency_ms"])}</td><td>{format_wcxb_conditional_latency(summary["providers"][p]["success_p50_latency_ms"])}</td><td>{format_wcxb_conditional_latency(summary["providers"][p]["success_p95_latency_ms"])}</td></tr>' for p in providers)
  if any(summary['providers'][p]['success_latency_count'] != case_count for p in providers):
   home+='<section><h2>'+(report_text('zh' if lang == 'zh' else 'en')('conditional_latency_for_delivered_content', 'Conditional latency for delivered content'))+'</h2><p>'+(report_text('zh' if lang == 'zh' else 'en')('only_pages_with_a_final_nonempty_response', 'Only pages with a final nonempty response enter this table, so denominators differ by method; failed retry time remains part of that page. The core table above retains the full common-denominator latency.'))+'</p><div class="scroll"><table><thead><tr><th>'+esc(x['provider'])+'</th><th>'+(report_text('zh' if lang == 'zh' else 'en')('delivered_pages', 'Delivered pages'))+'</th><th>Mean</th><th>P50</th><th>P95</th></tr></thead><tbody>'+success_rows+'</tbody></table></div></section>'
  issue_counts={(p,d):sum(r['provider']==p and r['quality_eligible'] and r['diagnosis']==d for r in rows) for p in providers for d in ('request_error','empty_output','error_page','low_precision_and_recall','low_precision','low_recall')}
  issue_types=[d for d in ('request_error','empty_output','error_page','low_precision_and_recall','low_precision','low_recall') if any(issue_counts[p,d] for p in providers)]
  issue_rows=''.join('<tr><td>'+esc(p)+'</td>'+''.join(f'<td>{issue_counts[p,d]}</td>' for d in issue_types)+'</tr>' for p in providers)
  issue_table='<div class="scroll"><table><thead><tr><th>'+esc(x['provider'])+'</th>'+''.join('<th>'+esc(diag(lang,d))+'</th>' for d in issue_types)+'</tr></thead><tbody>'+issue_rows+'</tbody></table></div>'
  home+='<section><h2>'+(report_text('zh' if lang == 'zh' else 'en')('issue_distribution', 'Issue distribution'))+'</h2><p>'+(report_text('zh' if lang == 'zh' else 'en')('programmatic_locators_across_qualified_pages_per_browser', 'Programmatic locators across {quality_count} qualified pages per browser; filter the results analysis to inspect corresponding pages.', quality_count=quality_count))+'</p>'+issue_table+'</section>'
  page(lang,'index.html',x['headline'],home)
  opts=lambda values,x=x:'<option value="">'+esc(x['all'])+'</option>'+''.join(f'<option value="{esc(v)}">{esc(v)}</option>' for v in values)
  types=sorted({g['page_type'] for g in ground_truth.values()})
  type_options='<option value="">'+esc(x['all'])+'</option>'+''.join(f'<option value="{esc(t)}">{esc(type_label(lang,t))}</option>' for t in types)
  filters=f"""<div class="filters"><label>{esc(x['query'])}<input id="q" type="search"></label><label>{esc(x['provider'])}<select id="provider">{opts(providers)}</select></label><label>{esc(x['type'])}<select id="type">{type_options}</select></label><label>{report_text('zh' if lang == 'zh' else 'en')('diagnostic_result', 'Diagnostic result')}<select id="scope"><option value="fail">{report_text('zh' if lang == 'zh' else 'en')('inspect', 'Inspect')}</option><option value="pass">{report_text('zh' if lang == 'zh' else 'en')('no_locator', 'No locator')}</option><option value="all">{esc(x['all'])}</option></select></label><label>{esc(x['diagnosis'])}<select id="diagnosis"><option value="">{esc(x['all'])}</option><option value="issues">{esc(x['issues'])}</option>"""+''.join(f'<option value="{esc(v)}">{esc(diag(lang,v))}</option>' for v in sorted({r['diagnosis'] for r in rows if r['quality_eligible'] and r['diagnosis']!='none'}))+'</select></label></div>'
  inventory=[]
  for r in rows:
   if not r['quality_eligible']:continue
   case=r['case_id'];linkto=f'cases/{case}.html#{r["provider"]}'
   if lang=='en':linkto='en/'+linkto
   label=r['diagnosis']
   delivery='pass' if label=='none' else 'fail'
   error_text=(r['error_type'] or '')+((': '+r['error_message']) if r['error_message'] else '')
   inventory.append(f'''<tr data-case="{esc(case)}" data-provider="{esc(r['provider'])}" data-type="{esc(r['page_type'])}" data-result="{delivery}" data-diagnosis="{esc(label)}" data-diagnosis-label="{esc(diag(lang, label))}" data-error="{esc(error_text)}" data-search="{esc((case + ' ' + r['original_url'] + ' ' + str(r['error_message'] or '')).lower())}"><td><a href="{linkto}">{esc(case)}</a></td><td>{esc(type_label(lang, r['page_type']))}</td><td>{esc(r['provider'])}</td><td class="number">{fmt(r['f1'])}</td><td class="number">{fmt(r['precision'])}</td><td class="number">{fmt(r['recall'])}</td><td>{esc(diag(lang, label))}</td><td class="number">{r['total_elapsed_ms']:,.0f} ms</td><td><button type="button" data-diagnosis-open>{report_text('zh' if lang == 'zh' else 'en')('inspect_2', 'Inspect')}</button></td></tr>''')
  comparison_data={case:{'type':ground_truth[case]['page_type'],'label':type_label(lang,ground_truth[case]['page_type']),'url':by_case[case][providers[0]]['original_url'],'rows':{p:{'f1':by_case[case][p]['f1']} for p in providers}} for case in sorted(by_case) if case not in exclusion_ids}
  comparison_json=json.dumps(comparison_data,ensure_ascii=True).replace('<','\\u003c')
  comparison_options=''.join(f'<option value="{esc(p)}">{esc(p)}</option>' for p in providers)
  comparison_types='<option value="">'+esc(x['all'])+'</option>'+''.join(f'<option value="{esc(t)}">{esc(type_label(lang,t))}</option>' for t in types)
  comparison_choices=''.join(f'<label><input type="checkbox" name="compare-provider" value="{esc(p)}"{" checked" if p==providers[1] else ""}> {esc(p)}</label>' for p in providers)
  explorer=f"""<section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('same_page_method_comparison', 'Same-page method comparison')}</h2><p>{report_text('zh' if lang == 'zh' else 'en')('choose_a_baseline_and_comparison_browsers_then', 'Choose a baseline and comparison browsers, then filter qualified pages by F1 difference. A positive F1 percentage-point difference means the comparison browser scored higher.')}</p><div class="comparison-controls"><label>{report_text('zh' if lang == 'zh' else 'en')('baseline_browser', 'Baseline browser')}<select id="compare-baseline">{comparison_options}</select></label><label>{esc(x['type'])}<select id="compare-type">{comparison_types}</select></label><label>{report_text('zh' if lang == 'zh' else 'en')('outcome_relation', 'Outcome relation')}<select id="compare-relation"><option value="">{esc(x['all'])}</option><option value="higher">{report_text('zh' if lang == 'zh' else 'en')('comparison_higher', 'Comparison higher')}</option><option value="lower">{report_text('zh' if lang == 'zh' else 'en')('comparison_lower', 'Comparison lower')}</option><option value="equal">{report_text('zh' if lang == 'zh' else 'en')('equal', 'Equal')}</option></select></label></div><div class="choice-list">{comparison_choices}</div><label>{report_text('zh' if lang == 'zh' else 'en')('search_page_id_or_url', 'Search page ID or URL')} <input id="compare-query" type="search"></label><p class="summary-band"><span><b id="compare-count"></b> / {quality_count} {report_text('zh' if lang == 'zh' else 'en')('pages_2', 'pages')}</span></p><div class="scroll result-scroll comparison-result"><table><thead><tr id="compare-head"></tr></thead><tbody id="comparison-rows"></tbody></table></div><script>window.WCXB_COMPARE={comparison_json}</script></section>"""
  dialog=f'''<dialog id="diagnosis-dialog" aria-labelledby="diagnosis-title"><div class="dialog-inner"><button type="button" class="dialog-close" data-call-log-close aria-label="{report_text('zh' if lang == 'zh' else 'en')('close', 'Close')}">×</button><h2 id="diagnosis-title" data-diagnosis-title></h2><p><b>{report_text('zh' if lang == 'zh' else 'en')('locator', 'Locator')}:</b> <span data-diagnosis-stage></span></p><p><b>{report_text('zh' if lang == 'zh' else 'en')('program_error', 'Program error')}:</b> <span data-diagnosis-error></span></p><p>{report_text('zh' if lang == 'zh' else 'en')('this_is_a_programmatic_locator_without_independently', 'This is a programmatic locator without independently reviewed root cause. The detail has complete output, human reference and retry evidence.')}</p><p><a data-diagnosis-link href="#">{report_text('zh' if lang == 'zh' else 'en')('open_complete_same_page_evidence', 'Open complete same-page evidence →')}</a></p></div></dialog>'''
  inv=f"""<div class="hero"><h1>{esc(x['results'])}</h1><p>{report_text('zh' if lang == 'zh' else 'en')('compare_methods_on_the_same_page_then', 'Compare methods on the same page, then filter qualified-reference results.')}</p></div>{explorer}<section><h2>{report_text('zh' if lang == 'zh' else 'en')('individual_results', 'Individual results')}</h2>{filters}<p><b id="count"></b> {esc(x['count'])} / {visible_count:,}</p><p id="empty-results" class="muted" hidden>{report_text('zh' if lang == 'zh' else 'en')('no_results_match_these_filters_adjust_the', 'No results match these filters. Adjust the page, browser or locator.')}</p><div class="scroll result-scroll"><table><thead><tr>"""+''.join('<th>'+esc(x[k])+'</th>' for k in ('case','type','provider','f1','precision','recall','diagnosis','latency'))+f"<th>{report_text('zh' if lang == 'zh' else 'en')('cause_and_evidence', 'Cause and evidence')}</th></tr></thead><tbody>"+''.join(inventory)+f'</tbody></table></div>{dialog}</section>'
  page(lang,'analysis/index.html',x['results'],inv)
  flow_labels=(['冻结 HTML',f'{len(providers)} 种配置转换','最终响应与重试','官方逐页评分','有效参考汇总'] if lang=='zh' else ['Frozen HTML',f'{len(providers)} conversions','Final response and retries','Official page score','Qualified aggregate'])
  flow_source='flowchart LR\n A["'+flow_labels[0]+'"] --> B["'+flow_labels[1]+'"]\n B --> C["'+flow_labels[2]+'"]\n C --> D["'+flow_labels[3]+'"]\n D --> E["'+flow_labels[4]+'"]\n C -- '+(report_text('zh' if lang == 'zh' else 'en')('failure_retry', 'failure retry'))+' --> B'
  flow='<div class="panel"><h2>'+(report_text('zh' if lang == 'zh' else 'en')('from_one_page_to_one_result', 'From one page to one result'))+'</h2><div class="flow"><div class="flow-fallback">'+' <b class="arrow">→</b> '.join(f'<span class="tag">{esc(label)}</span>' for label in flow_labels)+'</div><pre class="mermaid" hidden>'+esc(flow_source)+'</pre></div></div>'
  measured_records={p:{'provider':p,'provider_metadata':identities[p]} for p in providers}
  route_descriptions={p:_observed_route(native_browser_engine(p,measured_records[p], scope=scope),identities[p],language=lang, ctx=ctx) for p in providers}
  interface_specs={p:method_spec(p,measured_records[p],language=lang, ctx=ctx) for p in providers}
  measured_routes='<ul>'+''.join(f'<li>{esc(browser_label(p))}: {esc(route_descriptions[p])}</li>' for p in providers)+'</ul>'
  if lang=='zh':
   method=('<div class="hero"><h1>' + t('methodology', 'Methodology') + f"""</h1><p>{case_count} 份冻结 HTML、全部执行配置、一套官方词语重合评分。</p></div>{flow}<section class="panel"><h2>输入与运行边界</h2><p>WCXB v1.0 test 分区中，按冻结输入和人工参考核定的 {case_count} 页进入本报告。全部配置读取完全相同的冻结 HTML；本地配置通过隔离的回环 HTTPS 服务读取，远程配置使用 HTML 输入接口。外部子资源被阻断。每页一次逻辑调用；失败最多重试 {profile.get('max_failure_retries', 0)} 次，全部物理调用保留并累计端到端耗时，最后一次响应是唯一正式结果。</p><p>没有共同的主内容选择器。</p>{measured_routes}</section><section class="panel"><h2>分母与评分</h2><p>官方评估器将文本转为小写词语，按词频计算精确率、召回率和 F1。质量比较只使用事前验证的 {quality_count} 页有效参考，逐页等权。F1 不计段落顺序、Markdown 结构或表格行列。应包含和应排除片段另作诊断，不进入 F1。</p><p>质量与耗时都使用同一批 {case_count} 页；正式版与 PR 构建版分别保留原始调用证据。</p></section><section class="panel"><h2>定位与耗时</h2><p>""" + t('request_failures_empty_output_and_error_pages', 'Request failures, empty output and error pages are located separately; with a non-empty reference, precision or recall below 25% shows the matching low value. This threshold is only a filter, not a WCXB official pass mark. Latency includes calls and failure retries and is not browser-engine speed; configurations differ in work route and deployment distance.') + '</p></section>')
  else:
   method=f'<div class="hero"><h1>Method</h1><p>All {case_count} frozen HTML pages, participating configurations, one official word-overlap evaluator.</p></div>'+flow+f'<section class="panel"><h2>Inputs and execution</h2><p>The {case_count} WCXB v1.0 test pages qualified by frozen input and human reference enter this report. The participating configurations read the same frozen HTML. Local configurations use an isolated loopback HTTPS fixture; remote configurations use an HTML input interface. External subresources are blocked. Each page has one logical result; a failed call allows up to {profile.get("max_failure_retries",0)} retries. Every physical call and its latency are retained, with the final response authoritative.</p><p>No shared main-content selector is applied.</p>{measured_routes}</section><section class="panel"><h2>Denominators and score</h2><p>The official evaluator lowercases and tokenizes text, then computes word-frequency precision, recall and F1. Quality compares only the {quality_count} references qualified before calls, with equal page weights. F1 does not score paragraph order, Markdown structure or table layout. Required and forbidden snippets are separate diagnostics outside F1.</p><p>Quality and latency use the same {case_count} pages. The paired configurations’ calls each retain their own immutable evidence.</p></section><section class="panel"><h2>Locators and latency</h2><p>Request errors, empty outputs and error pages are separate locators. Precision or recall below 25% on nonempty references triggers a low-value locator; this is a filter, not an official WCXB pass threshold. Latency includes calls and failed retries, not pure browser-core time; local and remote routes differ.</p></section>'

  guide=report_content['guide'][lang]
  method+='<section class="panel"><h2>'+(report_text('zh' if lang == 'zh' else 'en')('snippet_annotation_boundary', 'Snippet annotation boundary'))+'</h2><p>'+(report_text('zh' if lang == 'zh' else 'en')('forbidden_snippets_also_appear_in_their_human', '{without_present_in_main_content} forbidden snippets also appear in their human main-content references. Official snippet values are retained, but these conflicts do not diagnose browser errors; result pages identify them explicitly.', without_present_in_main_content=audit['without_present_in_main_content']))+'</p></section>'
  page(lang,'methodology/index.html',x['method'],method)
  page(lang,'guide/index.html',x['guide'],guide)
  dataset_rows=[]
  for case in sorted(ground_truth):
   gt=ground_truth[case]
   source_url=by_case[case][providers[0]]['original_url']
   reference=gt['main_content']
   dataset_rows.append(
    f'''<tr data-dataset-case="{esc(case + ' ' + source_url.lower())}" data-page-type="{esc(gt['page_type'])}"><td>{esc(case)}</td><td>{esc(type_label(lang, gt['page_type']))}</td><td class="source">{esc(source_url)}</td><td class="number">{len(reference):,}</td><td><a href="{href(lang, 'cases/' + case + '.html#preview')}">{report_text('zh' if lang == 'zh' else 'en')('read_frozen_html', 'Read frozen HTML')}</a></td><td><a href="{href(lang, 'cases/' + case + '.html#reference')}">{report_text('zh' if lang == 'zh' else 'en')('human_main_content', 'Human main content')}</a></td><td><a href="{href(lang, 'cases/' + case + '.html')}">{report_text('zh' if lang == 'zh' else 'en')('view_results', 'View results')}</a></td></tr>''')
  dataset_intro=(report_text('zh' if lang == 'zh' else 'en')('the_task_is_to_extract_main_content', 'The task is to extract main content from {case_count} frozen HTML pages. This inventory owns inputs and human references; the measured browser scores are in Results analysis.', case_count=case_count))
  dataset=f"""<div class="hero dataset-hero"><h1>{esc(x['dataset'])}</h1><p>{esc(dataset_intro)}</p></div><section><h2>{report_text('zh' if lang == 'zh' else 'en')('frozen_pages_and_references', 'Frozen pages and references')}</h2><p>{report_text('zh' if lang == 'zh' else 'en')('the_source_benchmark_provides_human_selected_main', 'The source benchmark provides human-selected main-content plain text, not a complete Markdown answer. Character count describes annotation scope, not page completeness.')}</p><div class="filters"><label>{report_text('zh' if lang == 'zh' else 'en')('search_page_id_or_url_2', 'Search page ID or URL')}<input id="dataset-query" type="search"></label><label>{esc(x['type'])}<select id="dataset-type">{type_options}</select></label></div><p><b id="dataset-count">{case_count}</b> / {case_count} {report_text('zh' if lang == 'zh' else 'en')('pages_2', 'pages')}</p><div class="scroll result-scroll"><table id="wcxb-dataset"><thead><tr><th>ID</th><th>{esc(x['type'])}</th><th>{report_text('zh' if lang == 'zh' else 'en')('original_url', 'Original URL')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('reference_characters', 'Reference characters')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('frozen_input', 'Frozen input')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('human_reference', 'Human reference')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('results', 'Results')}</th></tr></thead><tbody>{''.join(dataset_rows)}</tbody></table></div></section>"""
  page(lang,'datasets/index.html',x['dataset'],dataset)
  review_intro=(report_text('zh' if lang == 'zh' else 'en')('certification_covers_logical_results_source_bytes_matched', 'Certification covers {result_count:,}/{result_count:,} logical results; source bytes matched before and after all calls. Targeted anomaly review inspected {reviewed_cases} pages and {reviewed_results} outputs; the other {unreviewed_results:,} results were not individually reviewed.', result_count=result_count, reviewed_cases=reviewed_cases, reviewed_results=reviewed_results, unreviewed_results=unreviewed_results))
  review_zh=f"当前评测范围按冻结 HTML 和人工参考的有效性确定，不依据浏览器输出或得分。全部配置在同一批 {case_count} 页上比较。"
  review_en=f"The current evaluation population was qualified from frozen HTML and human references before reading browser outputs or scores. All participating configurations are compared on the same {case_count} pages."
  review=f"""<div class="hero"><h1>{esc(x['review'])}</h1><p>{esc(review_intro)}</p></div><section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('source_qualification', 'Source qualification')}</h2><p>{(review_zh if lang == 'zh' else review_en)}</p></section><section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('execution_evidence', 'Execution evidence')}</h2><p>SHA-256: <code>{summary['source_sha256']}</code></p><p><a href="certification.json">{report_text('zh' if lang == 'zh' else 'en')('certification_receipt', 'Certification receipt')}</a> · <a href="wcxb-score-summary.json">{report_text('zh' if lang == 'zh' else 'en')('score_summary_json', 'Score summary JSON')}</a> · <a href="evidence-manifest.json">{report_text('zh' if lang == 'zh' else 'en')('per_page_evidence_hashes', 'Per-page evidence hashes')}</a> · <a href="research/wcxb-representative-review.json">{report_text('zh' if lang == 'zh' else 'en')('representative_review_record', 'Representative review record')}</a></p></section>"""
  page(lang,'review/index.html',x['review'],review)

  versions=''.join(f'<tr><td><a href="{href(lang,"providers/"+p+".html")}">{esc(browser_label(p))}</a></td><td>{esc(identities[p].get("reported_version") or identities[p].get("endpoint") or "service version undisclosed")}</td><td>{esc(route_descriptions[p])}</td><td><code>{esc(identities[p].get("binary_sha256") or "hosted")}</code></td></tr>' for p in providers)
  interface_example='\n\n'.join(browser_label(p)+': '+interface_specs[p]['entry']+'\n'+json.dumps(interface_specs[p]['request'],ensure_ascii=False,indent=2) for p in providers)
  adoption=f"""<div class="hero"><h1>{esc(x['adoption'])}</h1><p>{report_text('zh' if lang == 'zh' else 'en')('measured_configurations_versions_input_routes_and_output', 'Measured configurations, versions, input routes and output contracts.')}</p></div><section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('measured_implementations', 'Measured implementations')}</h2><div class="scroll"><table><thead><tr><th>{esc(x['provider'])}</th><th>{esc(x['version'])}</th><th>{report_text('zh' if lang == 'zh' else 'en')('route', 'Route')}</th><th>SHA-256</th></tr></thead><tbody>{versions}</tbody></table></div></section><section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('interface_examples', 'Interface examples')}</h2><pre>{esc(interface_example)}</pre><p>{report_text('zh' if lang == 'zh' else 'en')('these_are_registered_interface_examples_measured_routes', 'These are registered interface examples; measured routes follow the saved call records.')}</p></section>"""
  adoption+='<section class="panel"><h2>'+(report_text('zh' if lang == 'zh' else 'en')('public_code_adoption_signals', 'Public-code adoption signals'))+'</h2><p>'+(report_text('zh' if lang == 'zh' else 'en', legacy='wcxb')('in_the_verified_signature_sample_the_cloudflare', generated_at=adoption_data['generated_at'][:10], cloudflare_repo_count=cloudflare_repo_count, cloudflare_file_count=cloudflare_file_count))+'</p><p><a href="research/provider-adoption.json">'+(report_text('zh' if lang == 'zh' else 'en')('verified_sample_and_sources', 'Verified sample and sources'))+'</a></p></section>'

  page(lang,'adoption/index.html',x['adoption'],adoption)
  for p in providers:
   s=summary['providers'][p]
   profile_body=f'''<div class="hero"><h1>{esc(p)}</h1><p>{esc(route_descriptions[p])}</p></div><section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('measured_result', 'Measured result')}</h2><p>{report_text('zh' if lang == 'zh' else 'en')('qualified_f1', 'Qualified F1')}: <strong>{fmt(s['quality_f1'])}</strong> ({quality_count}) · {report_text('zh' if lang == 'zh' else 'en')('nonempty_outputs', 'Nonempty outputs')}: {s['delivered']}/{case_count}</p><p>{report_text('zh' if lang == 'zh' else 'en')('all_call_end_to_end_mean_p50', 'All-call end-to-end mean / P50 / P95')}: {s['mean_latency_ms']:,.0f} / {s['p50_latency_ms']:,.0f} / {s['p95_latency_ms']:,.0f} ms ({case_count})</p><p>{esc(x['version'])}: {esc(identities[p].get('reported_version') or identities[p].get('endpoint') or 'service version undisclosed')}</p><p><code>{esc(identities[p].get('binary_sha256') or 'hosted')}</code></p></section><p><a href="{href(lang, 'analysis/index.html')}">{esc(x['results'])} →</a></p>'''
   page(lang,'providers/'+p+'.html',p,profile_body)
  changes_zh=f"全部配置完成全部 {case_count} 页调用；质量展示只使用事前核定的 {quality_count} 页有效参考，共 {visible_count:,} 条结果。端到端耗时保留每款 {case_count} 次调用，以免事后剔除慢或失败的调用。"
  changes_en=f"All participating configurations completed {case_count} calls. Quality displays use only {quality_count} references qualified before calls, or {visible_count:,} results. End-to-end latency retains all {case_count} calls per method so slow or failed calls are not removed after measurement."
  changes=f"""<div class="hero"><h1>{esc(x['changes'])}</h1><p>{report_text('zh' if lang == 'zh' else 'en')('current_result_denominator_and_evidence_scope', 'Current result, denominator and evidence scope.')}</p></div><section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('qualified_references_and_all_calls', 'Qualified references and all calls')}</h2><p>{(changes_zh if lang == 'zh' else changes_en)}</p><p><a href="research/report-changes.json">{report_text('zh' if lang == 'zh' else 'en')('structured_change_record', 'Structured change record')}</a></p></section>"""
  page(lang,'changes/index.html',x['changes'],changes)
  account_rows=[]
  for p in providers:
   access=method_access(p,access_records,language=lang, scope=scope)
   links=' '.join(f'<a href="{esc(url)}">{esc(label)}</a>' for label,url in access['links'])
   account_rows.append(f'<tr><td>{esc(browser_label(p))}</td><td>{esc("、".join(access["env_vars"]))}</td><td>{esc(access["account"])}</td><td>{links}</td><td>{esc(access["configuration"])}</td></tr>')
  accounts=f"""<div class="hero"><h1>{esc(x['accounts'])}</h1><p>{report_text('zh' if lang == 'zh' else 'en')('account_requirements_come_from_the_method_registry', 'Account requirements come from the method registry. Missing installation bindings are disclosed; local paths and credential values are not published.')}</p></div><section class="panel"><div class="scroll"><table><thead><tr><th>{esc(x['provider'])}</th><th>{report_text('zh' if lang == 'zh' else 'en')('environment_variables', 'Environment variables')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('account_requirement', 'Account requirement')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('official_source', 'Official source')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('installation_configuration', 'Installation configuration')}</th></tr></thead><tbody>{''.join(account_rows)}</tbody></table></div></section>"""

  page(lang,'accounts/index.html',x['accounts'],accounts)
  repro=f'''<div class="hero"><h1>{esc(x['repro'])}</h1><p>{report_text('zh' if lang == 'zh' else 'en')('versions_and_source_are_bound_by_this', 'Versions and source are bound by this cohort certificate.')}</p></div><section class="panel"><p>WCXB v1.0 test · {case_count} × {len(providers)} · SHA-256 <code>{summary['source_sha256']}</code></p><p>{esc(x['source'])}: <a href="{source_repository}/tree/{source_commit}">GitHub {source_commit[:7]}</a> · <a href="sample-manifest.json">{report_text('zh' if lang == 'zh' else 'en')('full_test_manifest', 'Full test manifest')}</a> · <a href="certification.json">certification.json</a> · <a href="wcxb-score-summary.json">score summary JSON</a></p><div class="scroll"><table><thead><tr><th>{esc(x['provider'])}</th><th>{esc(x['version'])}</th><th>{report_text('zh' if lang == 'zh' else 'en')('route', 'Route')}</th><th>SHA-256</th></tr></thead><tbody>{versions}</tbody></table></div><pre>uv run --frozen python scripts/run_certified_benchmark.py --benchmark wcxb --dataset &lt;WCXB source&gt; --sample-manifest evaluation/public-wcxb-test-qualified.json --output-root &lt;new evidence dir&gt; --env-file .env\nuv run --frozen python -m harness.web_fetch score --benchmark wcxb --source &lt;WCXB source&gt; --sample-manifest evaluation/public-wcxb-test-qualified.json --evidence-root &lt;certified evidence dir&gt;\nuv run --frozen python scripts/build_report.py --profile &lt;WCXB report profile&gt; --evidence &lt;certified evidence dir&gt; --wcxb-source &lt;WCXB source&gt; --output reports/wcxb</pre><p class="small muted">{esc(x['no_review'])}</p></section>'''
  page(lang,'reproduce/index.html',x['repro'],repro)
  for case,group in sorted(by_case.items()):
   if case in exclusion_ids:continue
   gt=ground_truth[case];reference=gt['main_content'];evidence_path=f'evidence/{case}.json';source_link=f'{source_repository}/blob/{source_commit}/test/html/{case}.html.gz';source_html=contained_path(out,f'inputs/{case}.txt').read_text(encoding='utf-8')
   same=''.join(f'<tr><td><a href="{href(lang,"cases/"+case+".html#"+p)}">{esc(p)}</a></td><td>{fmt(group[p]["precision"])}</td><td>{fmt(group[p]["recall"])}</td><td>{fmt(group[p]["f1"])}</td><td>{fmt(group[p]["with_rate"])}</td><td>{fmt(group[p]["without_rate"])}</td><td>{esc(diag(lang,group[p]["diagnosis"]))}</td></tr>' for p in providers)
   panels=[]
   for p in providers:
    r=group[p];output_path=contained_path(out,f'artifacts/{p}/{case}.txt');prediction=output_path.read_text(encoding='utf-8')
    pred_words=WordCounter(re.findall(r'\w+',prediction.lower()));ref_words=WordCounter(re.findall(r'\w+',reference.lower()))
    missing=', '.join(f'{word} ×{count}' for word,count in (ref_words-pred_words).most_common(12)) or '—'
    extra=', '.join(f'{word} ×{count}' for word,count in (pred_words-ref_words).most_common(12)) or '—'
    missing_snippets=[s for s in gt['with'] if s.lower() not in prediction.lower()]
    conflicts=[s for s in gt['without'] if s.lower() in reference.lower()]
    included_forbidden=[s for s in gt['without'] if s.lower() in prediction.lower() and s not in conflicts]
    snippet_detail='<p>'+(report_text('zh' if lang == 'zh' else 'en')('missing_required_snippets', 'Missing required snippets'))+': '+esc('; '.join(missing_snippets) or '—')+'</p><p>'+(report_text('zh' if lang == 'zh' else 'en')('included_valid_forbidden_snippets', 'Included valid forbidden snippets'))+': '+esc('; '.join(included_forbidden) or '—')+'</p>'+(('<p>'+(report_text('zh' if lang == 'zh' else 'en')('annotation_conflict_these_forbidden_snippets_also_occur', 'Annotation conflict: these forbidden snippets also occur in the human main-content reference, so they cannot diagnose a browser failure.'))+' '+esc('; '.join(conflicts))+'</p>') if conflicts else '')
    retries=r.get('retry_failures') or []
    retry_html=('<details><summary>'+(report_text('zh' if lang == 'zh' else 'en')('earlier_failed_calls', 'Earlier failed calls'))+'</summary><ul>'+''.join(f'<li>#{v["physical_attempt"]} · {v["elapsed_ms"]:,.0f} ms · {esc(v["error_type"])} · {esc(v["error_message"])} · <a href="{esc(v["artifact_path"])}">'+(report_text('zh' if lang == 'zh' else 'en')('raw_response', 'Raw response'))+'</a></li>' for v in retries)+'</ul></details>') if retries else ''
    timing_note=('<p class="small muted">'+(report_text('zh' if lang == 'zh' else 'en')('latency_is_from_the_selected_final_crossover', 'Latency is from the selected final crossover round; content and scores are from the original call.'))+' <a href="research/wcxb-full-paired-latency/calls.jsonl">'+(report_text('zh' if lang == 'zh' else 'en')('timing_calls', 'Timing calls'))+'</a></p>') if r.get('latency_source')=='paired_final_round' else ''
    panels.append(f'<section class="panel" id="{p}"><h2>{esc(p)} · F1 {fmt(r["f1"])}</h2><p><span class="tag">{esc(diag(lang,"excluded" if case in exclusion_ids else r["diagnosis"]))}</span> · {r["total_elapsed_ms"]:,.0f} ms · {r["physical_attempt_count"]} calls · {esc(r["attempt_id"])}</p>'+timing_note+f'<p>{esc(x["missing"])}: {esc(missing)}</p><p>{esc(x["extra"])}: {esc(extra)}</p>'+snippet_detail+(f'<p class="bad">{esc(x["error"])}: {esc(r["error_type"])}{(" · HTTP "+str(r["status_code"])) if r.get("status_code") else ""}{(" · "+esc(r["error_message"])) if r["error_message"] else ""}</p>' if r['error_type'] else '')+retry_html+f'''<details data-output><summary>{esc(x['output'])} · {r['output_chars']:,} characters</summary><p><a href="{evidence_path}">{report_text('zh' if lang == 'zh' else 'en')('complete_evidence_json', 'Complete evidence JSON')} ↓</a></p><pre data-src="{evidence_path}" data-provider="{p}"></pre></details></section>''')
   qualification=(f"""<p class="callout">{report_text('zh' if lang == 'zh' else 'en')('this_reference_was_excluded_before_calls_official', 'This reference was excluded before calls; official raw scores and outputs remain visible, but it does not enter the main quality mean.')} {esc(exclusion_ids[case])}</p>""" if case in exclusion_ids else '')
   preview=_wmb_html_preview(source_html,lang=='en').replace('class="wmb-html-preview"','class="frozen-preview"')
   raw_input=evidence_path
   preview_note=(report_text('zh' if lang == 'zh' else 'en')('this_reading_view_follows_the_frozen_html', 'This reading view follows the frozen HTML document order; it is not a screenshot of the original website. The dataset does not preserve site stylesheets or image files, so the original layout cannot be reconstructed from this input. Scripts, external resources and navigation are disabled.'))
   preview_section=f'''<section class="panel" id="preview"><h2>{report_text('zh' if lang == 'zh' else 'en')('frozen_html_reading_view', 'Frozen HTML reading view')}</h2><p>{preview_note} <a href="{raw_input}">{report_text('zh' if lang == 'zh' else 'en')('download_complete_input_and_evidence_json', 'Download complete input and evidence JSON')}</a></p>{preview}</section>'''
   body=f'''<div class="hero"><h1>{esc(case)} · {esc(type_label(lang, gt['page_type']))}</h1><p class="source"><a href="{href(lang, 'cases/' + case + '.html#preview')}" title="{report_text('zh' if lang == 'zh' else 'en')('read_frozen_html', 'Read frozen HTML')}">{esc(group[providers[0]]['original_url'])}</a></p></div>{qualification}<section><h2>{esc(x['summary'])}</h2><div class="scroll"><table><thead><tr>'''+''.join(f'<th>{esc(x[k])}</th>' for k in ('provider','precision','recall','f1'))+f"<th>{report_text('zh' if lang == 'zh' else 'en')('required_hits', 'Required hits')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('forbidden_hits', 'Forbidden hits')}</th><th>{esc(x['diagnosis'])}</th></tr></thead><tbody>"+same+f'''</tbody></table></div></section>{preview_section}<section class="panel" id="reference"><h2>{esc(x['reference'])}</h2><p><a href="{evidence_path}">{report_text('zh' if lang == 'zh' else 'en')('complete_evidence_json', 'Complete evidence JSON')}</a> · <a href="{source_link}">{esc(x['source'])} HTML.gz</a></p><details data-output><summary>{report_text('zh' if lang == 'zh' else 'en')('frozen_html_input', 'Frozen HTML input')}</summary><pre data-src="{evidence_path}"></pre></details><pre>{esc(readable_output(reference))}</pre></section>'''+''.join(panels)
   page(lang,f'cases/{case}.html',case,body)
 write_language_contract(out, include_english=include_english)
