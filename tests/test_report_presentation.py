import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.methods import method_access
from harness.web_fetch.report import (
 active_tracks,
 case_attrs,
 failure_category,
 failure_filters,
 filter_controls,
 overall_ranking,
 overview_matched_latency,
 provider_configuration,
 review_cell,
 table,
 write,
)
from harness.web_fetch.report_interfaces import SPECS
from harness.web_fetch.report_model import MethodScope, RenderContext


class ReportRevisionTests(unittest.TestCase):
 def test_overview_latency_includes_every_configuration_and_same_case_mean(self):
  cases={cid:{'id':cid,'name':cid,'task_kind':'body'} for cid in ('a','b','c')}
  providers=['moli','chromium','lightpanda','obscura','kitesurf','moli-1.0.6','moli-1.1.3']
  records=[]
  for provider in providers:
   for cid,value in zip(cases,[1000,3000,99000],strict=True):
    passed=not(provider=='moli-1.0.6' and cid=='c')
    records.append({'provider':provider,'case_id':cid,'ok':passed,
                    'metrics':{'content_pass':passed},'elapsed_ms':value,
                    'started_at':'2026-09-04T00:00:00+00:00'})
  rendered=overview_matched_latency(records,cases, ctx=RenderContext())
  self.assertIn('全部 7 种受测配置',rendered)
  self.assertIn('<strong>2/3</strong>',rendered)
  self.assertIn('同题均值',rendered)
  self.assertIn('同题 P95',rendered)
  self.assertIn('<summary>按需查看共同成功任务耗时</summary>',rendered)
  self.assertNotIn('<strong>2,000 ms</strong>',rendered)
  self.assertNotIn('<strong>3,000 ms</strong>',rendered)
  self.assertIn('data-row-index="false"',rendered)
  for provider in providers:self.assertIn('providers/'+provider+'.html',rendered)
  self.assertIn('跨日期、跨批次',rendered)
  self.assertNotIn('99,000 ms',rendered)
  self.assertIn('调用矩阵尚不完整',overview_matched_latency(records[1:],cases, ctx=RenderContext()))

 def row(self,**kw):return dict({'ok':True,'metrics':{'content_pass':False,'length_pass':True}},**kw)
 def test_filters_default_to_all_methods_and_failed_results(self):
  controls=filter_controls([('provider','方法',[('one','One'),('two','Two')])], ctx=RenderContext())
  self.assertIn('data-filter="provider" aria-label="方法"><option value="">全部</option>',controls)
  controls=filter_controls([('result','结果',[('pass','任务通过'),('fail','任务失败')])], ctx=RenderContext())
  self.assertIn('value="fail" selected',controls)
 def test_case_search_includes_identifier_and_source_url(self):
  c={'id':'case-a','name':'Readable title','url':'https://example.com/source','category':'test','task_kind':'body'}
  r=self.row(provider='one',case_id='case-a')
  attrs=case_attrs(r,c, ctx=RenderContext())
  self.assertIn('data-search="case-a Readable title https://example.com/source ',attrs)
 def test_overall_ranking_shows_all_call_latency_without_affecting_pass_order(self):
  cases={'a':{'id':'a','track':'web','task_kind':'body'},
         'b':{'id':'b','track':'web','task_kind':'body'}}
  records=[
   {'provider':'fast-low-pass','case_id':'a','ok':True,'elapsed_ms':10,'metrics':{'content_pass':True}},
   {'provider':'fast-low-pass','case_id':'b','ok':False,'elapsed_ms':20,'metrics':{'content_pass':False}},
   {'provider':'slow-high-pass','case_id':'a','ok':True,'elapsed_ms':100,'metrics':{'content_pass':True}},
   {'provider':'slow-high-pass','case_id':'b','ok':True,'elapsed_ms':200,'metrics':{'content_pass':True}},
  ]
  rendered=overall_ranking(records,cases,call_budget_seconds=45, ctx=RenderContext())
  from bs4 import BeautifulSoup
  headers=[cell.get_text(' ',strip=True) for cell in BeautifulSoup(rendered,'html.parser').select('#ranking-overall thead th')]
  self.assertEqual(headers[-1],'最终调用耗时：均值（P50 · P95） ↕')
  self.assertNotIn('累计执行耗时均值', ' '.join(headers))
  self.assertNotIn('全调用', ' '.join(headers))
  self.assertIn('最终调用耗时均值 / P50 / P95',rendered)
  self.assertIn('不是包含此前重试和等待的累计耗时',rendered)
  self.assertIn('每次物理调用预算为 45 秒',rendered)
  from harness.web_fetch.report_i18n import translate_text
  self.assertEqual([translate_text(label) for label in ('均值','P50','P95')],['Mean','P50','P95'])
  self.assertIn('包含通过与失败',rendered)
  self.assertLess(rendered.index('slow-high-pass'),rendered.index('fast-low-pass'))
  self.assertIn('150 ms',rendered)
 def test_comparison_ranks_only_when_enabled(self):
  from bs4 import BeautifulSoup

  from harness.web_fetch.report import ranking
  cases={key:{'id':key,'track':'web','task_kind':'body'} for key in ('a','b')}
  records=[{'provider':name,'case_id':key,'ok':True,'elapsed_ms':10,
            'metrics':{'content_pass':key=='a' or name=='z-high','scoring_source_sha256':'a'*64}}
           for name in ('a-low','z-high','b-low') for key in cases]
  for diagnostic in (False,True):
   # The overall table puts its pass rate right after the method; a family table keeps its order.
   for render,lead in ((lambda diagnostic=diagnostic:overall_ranking(records,cases,diagnostic=diagnostic, ctx=RenderContext()),1),
                       (lambda diagnostic=diagnostic:ranking(records,cases,'web',diagnostic=diagnostic, ctx=RenderContext()),2)):
    with self.subTest(diagnostic=diagnostic,render=render):
     doc=BeautifulSoup(render(),'html.parser');table=doc.select_one('table.ranking')
     column=lead if diagnostic else lead+1
     self.assertEqual(table.get('data-col'),str(column))
     self.assertEqual(table.get('data-dir'),'desc')
     rows=table.select('tbody tr')
     self.assertIn('z-high',rows[0].get_text())
     expected=['100.0%','50.0%','50.0%']
     self.assertEqual([row.select('td')[column].get_text() for row in rows],expected)
     self.assertEqual(table.get('data-row-index'), 'false' if diagnostic else None)
     self.assertEqual(bool(rows[0].select('td')[column].select_one('.comparison-winner')), not diagnostic)
     self.assertFalse(any(row.select('td')[column].select_one('.comparison-winner') for row in rows[1:]))
  # A shared maximum has no winner; unavailable metrics remain neutral.
  for row in records:row['metrics']['content_pass']=True
  doc=BeautifulSoup(overall_ranking(records,cases,diagnostic=True, ctx=RenderContext()),'html.parser')
  self.assertFalse(doc.select('[data-metric="acceptance_2"] .comparison-winner'))
 def test_failure_filter_defaults_and_deduplication(self):
  missing={'content_pass':False,'length_pass':True,'resource_pass':False,
           'resource_results':[{'id':'target','pass':False}], 'unexpected_resource_urls':[]}
  rows=[self.row(case_id='case',metrics=missing),self.row(case_id='case',metrics=missing),
        self.row(case_id='case',metrics={'content_pass':True,'resource_pass':True})]
  options=failure_filters(rows,{'case':{'task_kind':'resource'}}, ctx=RenderContext())
  self.assertEqual(options[1][2],[('缺少目标资源','缺少目标资源')])
  controls=filter_controls(options, ctx=RenderContext())
  for key in ('failureStage','failureCategory'):
   self.assertRegex(controls,'data-filter="'+key+'"[^>]*><option value="">全部</option>')
 def test_failure_stage_partition(self):
  case={'category':'test','task_kind':'body'}
  for ok,passed,expected in [(False,False,'request'),(True,False,'task'),(True,True,'')]:
   r=self.row(ok=ok,provider='test',metrics={'content_pass':passed,'length_pass':False})
   self.assertIn(f'data-failure-stage="{expected}"',case_attrs(r,case, ctx=RenderContext()))
   if passed:self.assertIn('data-failure-category=""',case_attrs(r,case, ctx=RenderContext()))
 def test_failure_category_separates_interface_site_resource_and_pass(self):
  missing=self.row(metrics={'content_pass':False,'resource_pass':False,
                            'resource_results':[{'id':'target','pass':False}],
                            'unexpected_resource_urls':[]})
  for row,case,expected in [
   (self.row(ok=False,error_type='authentication',status_code=403),{},'接口鉴权失败'),
   (self.row(ok=False,error_type='CRAWL_HTTP_401'),{},'源站拒绝该次访问'),
   (missing,{'task_kind':'resource'},'缺少目标资源'),
   (self.row(metrics={'content_pass':True}),{},'—')]:
   self.assertEqual(failure_category(row,case, ctx=RenderContext()),expected)
 def test_all_interfaces(self):
  from harness.web_fetch.report_interfaces import method_spec
  from harness.web_fetch.report_methods import PROFILES
  for name in PROFILES:self.assertTrue(method_spec(name,None, ctx=RenderContext())['entry'])
  self.assertTrue({'kitesurf','lightpanda','obscura','moli','chromium'} <= set(SPECS))
  for s in SPECS.values():
   self.assertTrue(s['params'] and s['fields'] and s['request'] and s['source'])
 def test_current_interface_and_capability_copy_is_localized(self):
  from harness.web_fetch.methods import report_method_catalog
  from harness.web_fetch.report_interfaces import method_spec
  catalog=report_method_catalog(language='en')
  for method in ('moli-webfetch','tavily','exa','firecrawl','cloudflare-markdown','crawl4ai'):
   spec=method_spec(method,None,language='en', ctx=RenderContext())
   copy=[spec['entry'],spec['notes']]
   copy.extend(description for _,_,description in spec['params'])
   copy.extend(description for _,_,description in spec['fields'])
   row=catalog['methods'][method]['capabilities']
   copy.extend([row['scope'],row['tested'],row['notes']])
   copy.extend(row[field]['detail'] for field in ('web','document','image','av','file','js'))
   for text in copy:
    self.assertNotRegex(text,r'[\u4e00-\u9fff]',method)
  for key in ('moli','moli-recipes','moli-release'):
   self.assertNotRegex(catalog['sources'][key]['label'],r'[\u4e00-\u9fff]')
 def test_review_label_links_records_without_claiming_fresh_verification(self):
  audit={'attempts':[{'attempt_id':'one','final_verdict':'pass'}]}
  row=review_cell(audit,[{'attempt_id':'one'}])
  self.assertIn('1/1 · 复核记录',row)
  self.assertNotIn('已核验',row)
 def test_headless_access_covers_every_method_and_lists_setup_variables(self):
  from harness.web_fetch.methods import browser_method_registry
  for name in browser_method_registry(scope=MethodScope()):

   access=method_access(name,[], scope=MethodScope())
   self.assertTrue(access['env_vars'])
   self.assertTrue(access['links'])
 def test_language_toggle_uses_one_shared_report_tree(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)
   write(root,'index.html','中文','<p>正文</p>', ctx=RenderContext())
   from harness.web_fetch.report_i18n import write_english_report
   write_english_report(root)
   zh=(root/'index.html').read_text(encoding='utf-8');en=(root/'en/index.html').read_text(encoding='utf-8')
   self.assertIn('href="en/index.html" hreflang="en" lang="en">English</a>',zh)
   self.assertIn('lang="en"',en)
   self.assertIn('href="../index.html" hreflang="zh-CN" lang="zh-CN">中文</a>',en)
   self.assertNotIn('>首页</a>',zh)
   self.assertNotIn('>Home</a>',en)
 def test_english_tables_can_localize_the_index_header(self):
  rendered=table(['Method'],[('', '<td>Moli</td>')],index_header='No.')
  self.assertIn('<th>No.</th>',rendered)
  self.assertNotIn('<th>序号</th>',rendered)
 def test_overall_copy_and_columns_follow_the_actual_dataset(self):
  cases={
   'a':{'id':'a','track':'web','task_kind':'body'},
   'b':{'id':'b','track':'document','task_kind':'body'},
  }
  records=[{'case_id':key,'provider':'p','ok':True,'metrics':{'content_pass':True},'elapsed_ms':1}
           for key in cases]
  rendered=overall_ranking(records,cases, ctx=RenderContext())
  self.assertIn('计划的 2 个任务',rendered)
  self.assertIn('回到 2 个分榜',rendered)
  self.assertEqual(active_tracks(cases, ctx=RenderContext()),[('web','静态网站正文'),('document','文档正文')])
  self.assertNotIn('150 个任务',rendered)
  self.assertNotIn('六类',rendered)
 def test_provider_configuration_is_method_specific(self):
  default={'endpoint':'endpoint','output':'text'}
  firecrawl=provider_configuration('firecrawl',[{'provider_type':'firecrawl'}],default, ctx=RenderContext())
  builtin=provider_configuration('builtin',[{'provider_type':'cowagent_builtin'}],default, ctx=RenderContext())
  chromium=provider_configuration('chromium',[{'provider_type':'headless_markdown','provider_metadata':{'extractor':'node-html-markdown','output_contract':'markdown'}}],default, ctx=RenderContext())
  self.assertIn('rawBase64',firecrawl)
  self.assertNotIn('Exa、Tavily、Firecrawl',builtin)
  self.assertIn('渲染 DOM',chromium)
  self.assertIn('node-html-markdown',chromium)
  self.assertNotIn('2.0.0',chromium)
  moli=provider_configuration('moli',[{'provider_type':'headless_markdown','provider_metadata':{'extractor':'node-html-markdown','output_contract':'markdown'}}],default, ctx=RenderContext())
  self.assertNotIn('Chromium',moli)
  self.assertIn('node-html-markdown',moli)
  self.assertNotIn('2.0.0',moli)
 def test_english_article_audit_excludes_preserved_evidence_and_task_names(self):
  from harness.web_fetch.report_i18n import untranslated_english_narrative
  page=('<html><body><p>English explanation</p><p>中文任务名</p>'
        '<pre>标准答案原文</pre><code>实际输出原文</code></body></html>')
  self.assertEqual(untranslated_english_narrative(page, {'中文任务名'}),[])
  self.assertEqual(untranslated_english_narrative('<p>漏译的解释</p>'),['漏译的解释'])
  self.assertEqual(untranslated_english_narrative('<img alt="漏译的替代文字">'),
                   ['漏译的替代文字'])
 def test_english_article_gate_rejects_untranslated_narrative(self):
  from harness.web_fetch.report_i18n import write_english_report
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'guide').mkdir()
   (root/'guide/article.html').write_text('<html lang="zh-CN"><body><p>漏译的解释</p></body></html>', encoding='utf-8')
   with self.assertRaisesRegex(ValueError,'Untranslated English article'):
    write_english_report(root)
 def test_english_article_gate_keeps_source_evidence(self):
  from harness.web_fetch.report_i18n import write_english_report
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'guide').mkdir()
   (root/'dataset.json').write_text('{"cases":[{"name":"中文任务名"}]}', encoding='utf-8')
   (root/'guide/article.html').write_text(
       '<html lang="zh-CN"><body><p>中文任务名</p><pre>标准答案与实际输出原文</pre></body></html>', encoding='utf-8')
   write_english_report(root)
   rendered=(root/'en/guide/article.html').read_text(encoding='utf-8')
   self.assertIn('中文任务名',rendered)
   self.assertIn('标准答案与实际输出原文',rendered)
 def test_article_aria_labels_localize_without_touching_evidence_attributes(self):
  from pathlib import PurePosixPath

  from harness.web_fetch.report_i18n import localize_html
  markup='<nav aria-label="文章目录" data-evidence="文章目录"></nav><img src="data:image/png;base64,YQ==" alt="文章目录">'
  translated=localize_html(markup,PurePosixPath('guide/article.html'))
  self.assertIn('aria-label="Article contents"',translated)
  self.assertIn('data-evidence="文章目录"',translated)
  self.assertIn('alt="Article contents"',translated)
  self.assertIn('src="data:image/png;base64,YQ=="',translated)
  other=localize_html(markup,PurePosixPath('attempts/example.html'))
  self.assertIn('aria-label="Article contents"',other)
  self.assertIn('alt="Article contents"',other)
  self.assertIn('data-evidence="文章目录"',other)
 def test_article_selects_localized_assets_before_rewriting_relative_urls(self):
  from pathlib import PurePosixPath

  from harness.web_fetch.report_i18n import localize_html
  markup='<a href="figures/zh.png" data-en-href="figures/en.png"><img src="figures/zh.png" data-en-src="figures/en.png" alt="文章目录" data-evidence="原始证据"></a>'
  for route in ('guide/index.html','guide/article.html'):
   translated=localize_html(markup,PurePosixPath(route))
   self.assertIn('href="../../guide/figures/en.png"',translated)
   self.assertIn('src="../../guide/figures/en.png"',translated)
   self.assertNotIn('data-en-',translated)
   self.assertIn('data-evidence="原始证据"',translated)
 def test_methodology_selects_english_figure_without_localizing_measured_markup(self):
  from pathlib import PurePosixPath

  from harness.web_fetch.report_i18n import localize_html
  markup='<img src="../assets/methodology/zh.png" data-en-src="../assets/methodology/en.png" alt="参考标注"><pre><img src="https://example.org/evidence.png" data-en-src="https://example.org/wrong.png"></pre>'
  result=localize_html(markup,PurePosixPath('methodology/index.html'))
  self.assertIn('src="../../assets/methodology/en.png"',result)
  self.assertNotIn('src="../../assets/methodology/zh.png"',result)
  self.assertIn('<pre><img src="https://example.org/evidence.png" data-en-src="https://example.org/wrong.png"></pre>',result)

 def test_article_embedded_assets_and_measured_markup_stay_distinct(self):
  from pathlib import PurePosixPath

  from harness.web_fetch.report_i18n import localize_html
  markup='<img src="data:image/png;base64,YQ==" data-en-src="data:image/png;base64,Yg==" alt="文章目录"><pre><img src="data:image/png;base64,YQ==" alt="文章目录"></pre>'
  translated=localize_html(markup,PurePosixPath('guide/article.html'))
  self.assertIn('src="data:image/png;base64,Yg==" alt="Article contents"',translated)
  self.assertIn('<pre><img src="data:image/png;base64,YQ==" alt="文章目录"></pre>',translated)
  outside=localize_html(markup,PurePosixPath('attempts/example.html'))
  self.assertIn('src="data:image/png;base64,YQ==" data-en-src="data:image/png;base64,Yg=="',outside)


def test_methodology_image_parity_rejects_wrong_or_missing_english_companion(tmp_path):
 import pytest

 from harness.web_fetch.report_check import PageShape, check_localized_image_sources
 zh=PageShape(tmp_path/'methodology/index.html')
 zh.feed('<img src="zh.png" data-en-src="en.png">')
 for source in ('en.png','zh.png','missing.png',None):
  en=PageShape(tmp_path/'en/methodology/index.html')
  en.feed('<img src="'+source+'">' if source else '<p>No figure</p>')
  if source=='en.png':
   check_localized_image_sources(zh,en,lambda page,value:value)
   assert zh.data==en.data
  else:
   with pytest.raises(ValueError,match='Localized image'):
    check_localized_image_sources(zh,en,lambda page,value:value)
class MeasuredInterfaceIdentityTests(unittest.TestCase):
 def render(self, records, name='moli'):
  import json

  from harness.web_fetch.report_interfaces import render_interfaces
  with tempfile.TemporaryDirectory() as directory:
   out=Path(directory);(out/'adoption').mkdir();(out/'answer.md').write_text('Body', encoding='utf-8')
   rows=[{'case_id':'static-simple','attempt_id':str(i),'artifact_path':'answer.md',**r} for i,r in enumerate(records)]
   render_interfaces(out,rows, ctx=RenderContext())
   return json.loads((out/'adoption/interfaces'/f'{name}.json').read_text(encoding='utf-8'))
 def test_generic_moli_uses_observed_release_and_task_adapter(self):
  record={'provider':'moli','provider_type':'moli_webfetch','provider_metadata':{'reported_version':'moli 1.1.15'}}
  output=self.render([record]);access=output['access']
  self.assertIn('github.com/lexmount/moli',access['links'][0][1])
  self.assertIn('/tag/v1.1.15',access['links'][0][1])
  self.assertNotIn('1.1.5',str(access))
  self.assertEqual(output['interface']['source'],'harness/web_fetch/providers/moli.py')
  old={'provider':'moli-1.0.6','provider_type':'headless_markdown','provider_metadata':{'reported_version':'moli 1.0.6'}}
  output=self.render([old],'moli-1.0.6')
  self.assertEqual(output['access']['env_vars'], [])
  self.assertIn('1.0.6', output['access']['links'][0][1])
  self.assertNotIn('1.1.5', str(output['interface']))

 def test_unknown_or_mixed_generic_versions_do_not_invent_a_release(self):
  rows=[{'provider':'moli','provider_metadata':{'reported_version':'moli '+v}} for v in ('1.1.14','1.1.15')]
  for records in ([],rows):
   self.assertNotIn('/tag/',str(self.render(records)['access']))
 def test_native_metadata_envelopes_keep_native_output_and_actual_cli_examples(self):
  engines=[('lightpanda','lightpanda_json','--json'),('obscura','obscura_eval','--eval')]
  for name,format_name,flag in engines:
   record={'provider':name,'provider_type':'headless_markdown','provider_metadata':{'native_metadata':{'format':format_name,'script_sha256':'a'*64,'script_path':'/private/local-script.js'}}}
   output=self.render([record],name)
   self.assertIn(flag,output['interface']['entry'])
   self.assertNotIn('/private/local-script.js',str(output))
   if name=='obscura':self.assertNotIn('--dump',str(output['interface']['request']))
def test_measurement_display_policy_does_not_grant_certification():
 from harness.web_fetch.report import local_audit_banner
 from harness.web_fetch.report_components import measurement_presentation
 ordinary={'comparison':{'kind':'other_measurement'}}
 assert not measurement_presentation(ordinary).native_outputs
 assert not measurement_presentation(ordinary).local_matrix
 local={'comparison':{'kind':'uniform_native_local_measurement',
                      'publication_state':'local_audit_only_not_for_release'}}
 policy=measurement_presentation(local)
 assert policy.local_matrix
 assert policy.native_outputs and policy.all_call_latency and policy.explain_unjudged
 assert 'data-evidence-state="local_measurement"' in local_audit_banner(local)
 assert '本地未认证诊断结果' in local_audit_banner(local)
 local['comparison']['presentation']={'ranking':'saved_task_pass_rate'}
 assert 'data-ranking-scope="saved-task-pass-rate"' in local_audit_banner(local)


def test_input_suite_comparison_uses_declared_membership_and_selected_denominators():
 from bs4 import BeautifulSoup

 from harness.web_fetch.report import input_suite_comparison
 cases={cid:{'id':cid,'task_kind':'body'} for cid in ['misleading-id','other-id']}
 dataset={'comparison':{'input_sources':[
  {'path':'first/dataset.json','case_ids':['other-id','unselected-parent-case']},
  {'path':'second/dataset.json','case_ids':['misleading-id']}],
  'report_suite_labels':{'first/dataset.json':{'zh':'正文','en':'Body'}}}}
 rows=[{'case_id':cid,'provider':provider,'ok':True,'metrics':{'content_pass':passed}}
  for provider,passed in [('one',True),('two',False)] for cid in cases]
 html=input_suite_comparison(dataset,cases,rows, ctx=RenderContext()); soup=BeautifulSoup(html,'html.parser')
 assert '正文' in html and 'n=1' in html and 'n=2' not in html
 assert len(soup.select('.comparison-winner'))==2
 assert [cell['data-sort'] for cell in soup.select('tbody tr:first-child td[data-sort]')]==['1.0','1.0']
 for source in dataset['comparison']['input_sources']:source['case_ids']=['misleading-id']
 import pytest
 with pytest.raises(ValueError,match='overlapping'):
  input_suite_comparison(dataset,cases,rows, ctx=RenderContext())
 dataset['comparison']['input_sources']=dataset['comparison']['input_sources'][:1]
 with pytest.raises(ValueError,match='omits'):
  input_suite_comparison(dataset,cases,rows, ctx=RenderContext())
