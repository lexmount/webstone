"""Shared, answer-free projections of already-rendered browser content.

Resource and metadata projections only read already-loaded content. The document
projection reads text already loaded in a browser viewer or page; it never
downloads document bytes for external conversion.
"""

import json

RESOURCE_PAGE_EXPRESSION = r"""
(() => {
  const urls = new Set();
  const add = (value, base = location.href) => {
    const raw = String(value || '').trim();
    if (!raw || /^(?:data|blob|javascript|mailto):/i.test(raw)) return;
    try { urls.add(new URL(raw, base).href); } catch {}
  };
  const addSrcset = (value, base) => {
    // HTML srcset URLs end at whitespace; commas inside a URL are literal.
    // Trailing commas delimit candidates without descriptors.
    const text = String(value || '');
    let cursor = 0;
    while (cursor < text.length) {
      while (cursor < text.length && /[\s,]/.test(text[cursor])) cursor++;
      const start = cursor;
      while (cursor < text.length && !/\s/.test(text[cursor])) cursor++;
      const token = text.slice(start, cursor);
      add(token.replace(/,+$/, ''), base);
      if (token.endsWith(',')) continue;
      while (cursor < text.length && text[cursor] !== ',') cursor++;
      cursor++;
    }
  };

  // Enumerate the top document and bounded, already-loaded same-origin frame
  // documents.  This is answer-free: it follows the page's own document
  // hierarchy and never receives an expected URL or case-specific selector.
  const roots = [document];
  for (let cursor = 0; cursor < roots.length && roots.length < 16; cursor++) {
    for (const frame of roots[cursor].querySelectorAll('iframe,frame')) {
      try {
        if (frame.contentDocument && !roots.includes(frame.contentDocument)) {
          roots.push(frame.contentDocument);
          if (roots.length >= 16) break;
        }
      } catch {}
    }
  }
  for (const root of roots) {
    for (const node of root.querySelectorAll(__RESOURCE_SELECTOR__)) {
      for (const name of ['src', 'href', 'data', 'poster', 'srcset']) {
        const value = node.getAttribute(name);
        if (__VIDEO_TASK__ && name === 'href') {
          let path;
          try { path = new URL(value, location.href).pathname.toLowerCase(); } catch { continue; }
          const media = /\.(?:mp4|webm|mov|m4v|mp3|wav|ogg)(?:\.zip)?$/.test(path);
          const detail = __DETAIL_LINKS__ && /\/(?:video|videos|watch)(?:\/|$)/.test(path);
          if (!media && !detail) continue;
        }
        const base = node.baseURI || root.baseURI || location.href;
        if (name === 'srcset') addSrcset(value, base);
        else add(value, base);
      }
    }
  }
  return JSON.stringify({links: [...urls]});
})()
"""


def resource_detail_links(case):
    """Read only the public resource kind; expected URLs never reach the browser."""
    return case.track == 'video' and any(item.get('kind') == 'video-link' for item in getattr(case, 'expected_resources', []))


def resource_expression(track, detail_links=False):
    """Select resource element types from the declared task, never its answers."""
    selectors = {'image':'img,picture source',
                 'video':'video,video source,audio,audio source,iframe,a[href]'}
    if track not in selectors:
        raise ValueError('Unsupported resource task family')
    selector = 'a[href]' if track == 'video' and detail_links else selectors[track]
    return (RESOURCE_PAGE_EXPRESSION.replace('__RESOURCE_SELECTOR__', json.dumps(selector))
            .replace('__VIDEO_TASK__', 'true' if track == 'video' else 'false')
            .replace('__DETAIL_LINKS__', 'true' if detail_links else 'false'))

MEDIA_METADATA_EXPRESSION = r"""
(() => {
  const values = [];
  const add = value => {
    const text = String(value || '').trim();
    if (text && !values.includes(text)) values.push(text);
  };
  add(document.title);
  add(document.body?.innerText);
  for (const node of document.querySelectorAll(
    'meta[name="author"],meta[property$=":author"],meta[itemprop="author"],'
    + '[itemprop="author"],[itemprop="creator"],[itemprop="publisher"]'
  )) {
    add(node.content);
    add(node.getAttribute?.('content'));
    add(node.innerText);
    for (const name of node.querySelectorAll?.('[itemprop="name"]') || []) {
      add(name.content);
      add(name.getAttribute?.('content'));
      add(name.innerText);
    }
  }
  let visited = 0;
  const visit = (value, depth = 0) => {
    if (++visited > 2048 || depth > 6 || value == null) return;
    if (Array.isArray(value)) {
      for (const item of value.slice(0, 64)) visit(item, depth + 1);
      return;
    }
    if (typeof value !== 'object') return;
    for (const [key, item] of Object.entries(value).slice(0, 128)) {
      if (/^(?:author|creator|publisher)$/i.test(key)) {
        if (typeof item === 'string') add(item);
        else if (item && typeof item === 'object') add(item.name);
      }
      visit(item, depth + 1);
    }
  };
  for (const script of [...document.querySelectorAll('script[type="application/ld+json"]')]
    .slice(0, 16)) {
    try { visit(JSON.parse(script.textContent)); } catch {}
  }
  return values.join('\n\n');
})()
"""


DOCUMENT_PAGE_EXPRESSION = r"""
(async () => {
  const app = globalThis.PDFViewerApplication;
  if (app?.pdfDocument) {
    const pdf = app.pdfDocument;
    if (pdf) {
      const pages = [];
      for (let number = 1; number <= pdf.numPages; number++) {
        const page = await pdf.getPage(number), text = await page.getTextContent();
        pages.push(text.items.map(item => item.str).join(' '));
      }
      return JSON.stringify({
        kind: 'text', source: new URL(app.url, location.href).href,
        text: pages.join('\n\n')
      });
    }
  }
  return JSON.stringify({kind: 'page_text', text: document.body?.innerText || ''});
})()
"""

DOCUMENT_PAGE_READY = r"""
() => {
  if (globalThis.PDFViewerApplication) return !!globalThis.PDFViewerApplication.pdfDocument;
  return !!document.querySelector('embed[src],iframe[src],object[data],a[href*=".pdf"]')
    || !!document.body?.innerText?.trim();
}
"""
