/* Bundled mature parser + DOMPurify. Raw HTML and remote images stay inert. */
function mdToHtml(src) {
  if (!src) return '';
  if (!window.marked || !window.DOMPurify) return '<pre>' + escapeHtml(src) + '</pre>';
  // Escaping raw HTML before parsing also avoids hidden prompt-injection UI.
  const renderer = new marked.Renderer();
  renderer.html = token => escapeHtml(token.text);
  return DOMPurify.sanitize(marked.parse(src, {gfm:true, breaks:true, renderer}), {
    FORBID_TAGS:['script','style','iframe','object','embed','form','img','svg','math'],
    FORBID_ATTR:['style','onerror','onclick'], ALLOW_DATA_ATTR:false
  });
}
function setMarkdown(el, src) {
  el.innerHTML = mdToHtml(src);
  el.querySelectorAll('a').forEach(a => {
    try {
      if (!['http:','https:','mailto:'].includes(new URL(a.getAttribute('href'), location.href).protocol)) a.removeAttribute('href');
    } catch (_) { a.removeAttribute('href'); }
    a.target='_blank'; a.rel='noopener noreferrer';
  });
  el.querySelectorAll('pre code').forEach(code => {
    if (window.hljs) hljs.highlightElement(code);
    const button = document.createElement('button'); button.className='copy'; button.textContent='复制';
    button.onclick=()=>copyTxt(code.textContent); code.parentElement.prepend(button);
  });
  if (window.renderMathInElement) renderMathInElement(el, {throwOnError:false, trust:false,
    delimiters:[{left:'$$',right:'$$',display:true},{left:'\\[',right:'\\]',display:true},{left:'$',right:'$',display:false},{left:'\\(',right:'\\)',display:false}],
    ignoredTags:['script','noscript','style','textarea','pre','code']});
}
