(function(){
if(window.reportShell)return;window.reportShell=true;
const root=document.documentElement;
let stored;
try{stored=localStorage.getItem('report-theme')}catch(e){}
const systemTheme=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
root.dataset.theme=stored==='dark'||stored==='light'?stored:systemTheme;
// A toggle button reports whether the dark theme is on.
function pressed(){document.querySelectorAll('[data-shell-theme]').forEach(function(button){button.setAttribute('aria-pressed',String(root.dataset.theme==='dark'))})}
document.addEventListener('click',function(event){
  const button=event.target.closest&&event.target.closest('[data-shell-theme]');
  if(button){const next=root.dataset.theme==='dark'?'light':'dark';root.dataset.theme=next;try{localStorage.setItem('report-theme',next)}catch(e){}pressed()}
  setTimeout(mark,0);
});
function scroller(table){
  for(let node=table.parentElement;node&&node!==document.body;node=node.parentElement){
    const overflow=getComputedStyle(node).overflowX;
    if(overflow==='auto'||overflow==='scroll')return node;
  }
  return null;
}
function update(box){
  const room=box.scrollWidth-box.clientWidth,edges=[];
  // A pinned first column stays readable, so only the far edge fades.
  if(room>1&&box.scrollLeft>1&&!box.querySelector('table[data-sticky-first]'))edges.push('left');
  if(room>1&&box.scrollLeft<room-1)edges.push('right');
  if(edges.length)box.setAttribute('data-overflow',edges.join(' '));else box.removeAttribute('data-overflow');
  // The swipe hint follows whether the table overflows at all, so reaching an edge never moves the layout.
  box.toggleAttribute('data-scrollable',room>1);
}
// On a desktop page a table wider than the text column widens its scroller toward the
// viewport edges, so the whole table shows without sideways scrolling where it fits.
const BREAKOUT_MIN=1100,GUTTER=24;
function breakout(box,table){
  box.removeAttribute('data-breakout');box.style.removeProperty('width');box.style.removeProperty('max-width');box.style.removeProperty('margin-left');
  if(innerWidth<BREAKOUT_MIN||box.closest('dialog'))return;
  const need=table.scrollWidth-box.clientWidth;
  if(need<=1)return;
  const rect=box.getBoundingClientRect(),width=Math.min(rect.width+need,innerWidth-2*GUTTER),extra=width-rect.width;
  if(extra<=1)return;
  const shift=Math.max(0,Math.min(extra/2,rect.left-GUTTER),extra-(innerWidth-GUTTER-rect.right));
  box.setAttribute('data-breakout','');box.style.width=width+'px';box.style.maxWidth='none';box.style.marginLeft=(-shift)+'px';
}
function mark(){
  pressed();
  const source=document.querySelector('[data-table-hint]'),hint=source?source.textContent.trim():'';
  document.querySelectorAll('table').forEach(function(table){
    if(!table.hasAttribute('data-no-sticky')&&!table.querySelector('[rowspan],[colspan]')&&table.rows.length&&table.rows[0].cells.length>2)table.setAttribute('data-sticky-first','');
    const box=scroller(table);
    if(!box)return;
    if(hint)box.setAttribute('data-hint',hint);
    if(!box.hasAttribute('data-shell-scroll')){box.setAttribute('data-shell-scroll','');box.addEventListener('scroll',function(){update(box)},{passive:true})}
    breakout(box,table);
    update(box);
  });
}
document.addEventListener('DOMContentLoaded',mark);
addEventListener('load',mark);
addEventListener('report-ready',mark);
addEventListener('resize',mark);
document.addEventListener('toggle',mark,true);
})();
