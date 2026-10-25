let appMode = localStorage.getItem('pa-mode') || 'work', currentSession = null, serverStatus = {}, displayedModelMode = null, navigationBusy = false;
let compactHeader = localStorage.getItem('pa-compact-header')==='1';
const post = (path, body) => api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function lockedUI() { return busy || preparingSend || retryChecking || navigationBusy; }
function applyCompactHeader(){document.body.classList.toggle('compact-header',compactHeader);$('layoutToggle').textContent=compactHeader?'⌄':'⌃';$('layoutToggle').title=compactHeader?'展开顶部工具栏':'收起顶部工具栏';$('layoutToggle').setAttribute('aria-label',$('layoutToggle').title);}
function setNavigationBusy(value) {
  navigationBusy=value;
  ['modeSel','menuBtn','newBtn','modelSel','reasoningSel','reasoningDrawerBtn','sendBtn','workspaceBtn','skillsBtn','contextBtn','accountBtn','presetBtn','memoryBtn','memoryToggle','importBtn','layoutToggle'].forEach(id=>$(id).disabled=lockedUI());
  $('input').disabled=value;
  if(!value) applyStatus(serverStatus);
}
function applySession(info) {
  if (!info) return;
  currentSession=info; appMode=info.mode || 'work'; localStorage.setItem('pa-mode',appMode);
  localStorage.setItem('pa-last-'+appMode,info.id);
  $('modeSel').value=appMode; document.body.dataset.mode=appMode;
  $('input').placeholder=appMode==='chat'?'说说你在想什么…':'给助手下达任务…';
  $('workspaceBtn').hidden=appMode!=='work'; $('skillsBtn').hidden=appMode!=='work';
  $('contextBtn').hidden=appMode!=='work';
  $('memoryBtn').hidden=appMode!=='chat'; $('presetBtn').hidden=appMode!=='chat';
  $('workspaceBtn').textContent=info.settings?.workdir || '选择工作目录';
  $('memoryToggleLabel').hidden=appMode!=='chat'; $('memoryToggle').checked=info.settings?.memory_enabled!==false;
  $('contextInfo').textContent=''; $('approvalsBtn').textContent=serverStatus.auto_approve_work && appMode==='work'?'自动批准已开启':'工具权限';
  applyStatus(serverStatus);
  if (info.settings?.model && [...$('modelSel').options].some(o=>o.value===info.settings.model)) $('modelSel').value=info.settings.model;
  renderReasoningSelector();
}
async function switchMode(mode) {
  if (lockedUI()) { $('modeSel').value=appMode; return; }
  saveDraft();const previous=appMode, oldSession=currentSession;
  setNavigationBusy(true);
  try {
    appMode=mode; localStorage.setItem('pa-mode',mode);
    const last=localStorage.getItem('pa-last-'+mode);
    const item=convs.find(c=>c.id===last && (c.mode||'work')===mode) || convs.find(c=>(c.mode||'work')===mode);
    if(item) await openConv(item.id,true); else await newConv(true);
    renderConvList();
  } catch(e) { appMode=previous;localStorage.setItem('pa-mode',previous);applySession(oldSession);throw e; }
  finally {setNavigationBusy(false);}
}
$('modeSel').onchange=()=>switchMode($('modeSel').value).catch(e=>alert(e.message));
$('modelSel').onchange=async()=>{
  const model=$('modelSel').value, cid=curConv;
  $('chatgptPlanHint').hidden=!model.startsWith('chatgpt/');
  renderReasoningSelector();
  try {
    if(currentSession && !lockedUI()) {
      const data=await post(`convs/${cid}/settings`,{model});
      if(curConv===cid) currentSession=data.session;
    }
    localStorage.setItem('pa-model-'+appMode,model);
  } catch(e) { applyStatus(serverStatus);alert(e.message); }
};
function reasoningChoices(model=$('modelSel').value){
  const raw=serverStatus.model_registry?.[model]?.reasoning_efforts;
  if(!Array.isArray(raw))return [];
  const supported=['low','medium','high','xhigh','max'];
  return raw.filter((v,i,a)=>typeof v==='string'&&supported.includes(v)&&a.indexOf(v)===i);
}
function renderReasoningSelector(){
  const sel=$('reasoningSel'),model=$('modelSel').value,choices=reasoningChoices(model);
  sel.replaceChildren();
  if(!choices.length){sel.hidden=true;$('reasoningDrawerBtn').hidden=true;return;}
  const meta=serverStatus.model_registry?.[model]||{},stored=currentSession?.mode===appMode?currentSession.settings?.reasoning_effort:null;
  const fallback=choices.includes(meta.default_reasoning_effort)?meta.default_reasoning_effort:(choices.includes('low')?'low':choices[0]);
  const selected=choices.includes(stored)?stored:fallback;
  const names={low:'低 · low',medium:'中 · medium',high:'高 · high',xhigh:'很高 · xhigh',max:'最大 · max'};
  choices.forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=names[v]||v;sel.append(o);});
  sel.value=selected;sel.hidden=false;$('reasoningDrawerBtn').hidden=false;$('reasoningDrawerBtn').textContent=`思考强度 · ${names[selected]||selected}`;
}
async function saveReasoningEffort(value){
  const cid=curConv,model=$('modelSel').value,choices=reasoningChoices(model);
  if(!currentSession||!cid||!choices.includes(value))return;
  const sel=$('reasoningSel'),old=currentSession.settings?.reasoning_effort;
  sel.disabled=true;$('reasoningDrawerBtn').disabled=true;
  try{const d=await post(`convs/${cid}/settings`,{reasoning_effort:value});if(curConv===cid&&$('modelSel').value===model){currentSession=d.session||currentSession;currentSession.settings=currentSession.settings||{};currentSession.settings.reasoning_effort=value;renderReasoningSelector();}}
  catch(e){renderReasoningSelector();alert('保存思考强度失败：'+e.message);}
  finally{sel.disabled=lockedUI();$('reasoningDrawerBtn').disabled=lockedUI();}
}
$('reasoningSel').onchange=()=>saveReasoningEffort($('reasoningSel').value);
$('reasoningDrawerBtn').onclick=()=>{
  const choices=reasoningChoices();if(!choices.length)return;
  const content=document.createElement('div');const names={low:'低 · low',medium:'中 · medium',high:'高 · high',xhigh:'很高 · xhigh',max:'最大 · max'};
  choices.forEach(v=>content.append(button(((currentSession?.settings?.reasoning_effort||$('reasoningSel').value)===v?'✓ ':'')+(names[v]||v),async()=>{await saveReasoningEffort(v);$('modalWrap').classList.add('hidden');})));
  panel('思考强度',content);
};
$('layoutToggle').onclick=()=>{compactHeader=!compactHeader;localStorage.setItem('pa-compact-header',compactHeader?'1':'0');applyCompactHeader();};
applyCompactHeader();
function secretField(label){const wrap=document.createElement('label');wrap.className='provider-field';wrap.append(document.createTextNode(label));const input=document.createElement('input');input.type='password';input.autocomplete='new-password';input.placeholder='留空表示保留已保存的值';input.setAttribute('aria-label',label);wrap.append(input);return {wrap,input};}
function clearField(label){const wrap=document.createElement('label');wrap.className='provider-clear';const input=document.createElement('input');input.type='checkbox';wrap.append(input,document.createTextNode(' '+label));return {wrap,input};}
async function showProviderSettings(){
  const state=await api('provider-settings'), content=document.createElement('div');
  content.append(rowText('密钥只保存在此设备的 PhoneAgent 配置中，已保存的值不会回显。留空表示保留现值；勾选清除后保存才会删除。也可以不配置 API 密钥，直接使用 ChatGPT 账户登录。'));
  const providers=[['deepseek','DeepSeek API Key'],['gemini','Gemini API Key'],['exa','Exa API Key（可选搜索）']];
  const fields={};
  for(const [key,label] of providers){const f=secretField(label),c=clearField('清除 '+key+' 密钥');fields[key]={...f,clear:c.input};content.append(f.wrap,c.wrap);}
  const status=document.createElement('p');status.className='provider-status';
  const renderStatus=s=>{status.textContent='密钥状态：'+providers.map(([key,label])=>label.split(' ')[0]+' '+(s[key+'_key_configured']?'已设置':'未设置')).join(' · ');};renderStatus(state);content.append(status);
  const save=button('保存 API 设置',async()=>{
    if(lockedUI())throw new Error('当前任务仍在运行，请先停止任务后再保存。');
    const body={};for(const [key] of providers){const f=fields[key];if(f.clear.checked)body['clear_'+key+'_key']=true;else if(f.input.value.trim())body[key+'_key']=f.input.value.trim();}
    try{const updated=await post('provider-settings',body);renderStatus(updated);for(const [key] of providers){fields[key].input.value='';fields[key].clear.checked=false;}alert('API 设置已保存。');}
    catch(e){if(/409|running|active/i.test(e.message))throw new Error('当前任务仍在运行，请先停止任务后再保存。');throw new Error('保存失败，请确认服务版本后重试。');}
  });content.append(save);
  panel('API 与连接',content);
}
$('providerSettingsBtn').onclick=()=>showProviderSettings().catch(()=>alert('无法读取 API 状态，请确认 PhoneAgent 服务已启动。'));
function panel(title, content) {
  const wrap=$('modalWrap'); wrap.classList.remove('hidden'); wrap.replaceChildren();
  const mask=document.createElement('div'); mask.className='manage-mask';
  const box=document.createElement('section'); box.className='manage-panel';
  const head=document.createElement('div'); head.className='manage-head';
  const h=document.createElement('h2'); h.textContent=title;
  const close=document.createElement('button'); close.textContent='关闭'; close.onclick=()=>wrap.classList.add('hidden');
  head.append(h,close); box.append(head,content); wrap.append(mask,box); mask.onclick=close.onclick;
}
function rowText(text) { const p=document.createElement('p');p.textContent=text;return p; }
function button(text, action) {const b=document.createElement('button');b.textContent=text;b.onclick=()=>Promise.resolve(action()).catch(e=>alert(e.message));return b;}
async function ensureSession() {if(!curConv) await newConv(); return curConv;}
$('workspaceBtn').onclick=async()=>{
  if(lockedUI()) return; await ensureSession();
  const value=prompt('输入 Termux 可访问的具体项目目录。共享存储需先在 Termux 授予权限。',currentSession?.settings?.workdir||'');
  if(!value) return;
  try{const d=await post(`convs/${curConv}/settings`,{workdir:value});applySession(d.session);}
  catch(e){alert('目录无效或未授权：'+e.message);}
};
$('skillsBtn').onclick=async()=>{
  await ensureSession();const d=await api(`convs/${curConv}/skills`);const content=document.createElement('div');
  content.append(rowText('按描述选择，按需加载。Skills 不会提升工具权限。'));
  if(!d.skills.length) content.append(rowText('尚未安装。全局放 ~/.phoneagent/skills/名称/SKILL.md，项目放 .skills/名称/SKILL.md。'));
  d.skills.forEach(s=>{
    const card=document.createElement('article');card.append(rowText(s.name+' · '+s.scope+' · '+s.version),rowText(s.description));
    if(s.missing_dependencies.length)card.append(rowText('缺少依赖：'+s.missing_dependencies.join(', ')));
    card.append(button(s.enabled?'禁用':'启用',async()=>{let disabled=currentSession.settings.disabled_skills||[];disabled=s.enabled?[...disabled,s.name]:disabled.filter(n=>n!==s.name);const v=await post(`convs/${curConv}/settings`,{disabled_skills:disabled});applySession(v.session);$('skillsBtn').click();}));content.append(card);
  });panel('Skills',content);
};
$('quotaBtn').onclick=async()=>{
  const d=await api('quota');const content=document.createElement('div');
  content.append(rowText('以下为本机记录的估算。其他应用共享项目额度，真实剩余额度请在 Google AI Studio 核对。每日按美国太平洋时间重置。'));
  d.quota.forEach(q=>{const card=document.createElement('article');card.append(rowText((serverStatus.model_labels?.[q.model]||q.model)),rowText(`今日 ${q.requests_today} 次 · 输入 ${q.input_tokens} / 输出 ${q.output_tokens} tokens · 使用量未知 ${q.unknown_usage_requests} 次`),rowText(`本地估算可用 ${q.estimated_remaining??'不限'} 次 · RPM ${q.limits.rpm||'未设置'} · TPM ${q.limits.tpm||'未设置'}`));content.append(card);});panel('请求与配额',content);
};
$('contextBtn').onclick=async()=>{
  if(lockedUI()||appMode!=='work')return;await ensureSession();
  const data=await api(`convs/${curConv}/context`), content=document.createElement('div');
  content.append(rowText('Work 会话的上下文预算与自动压缩。最近检查点只显示模型归纳摘要，供参考，不代表事实核验。'));
  const enabledLabel=document.createElement('label'), enabled=document.createElement('input');enabled.type='checkbox';enabled.checked=data.enabled!==false;
  enabledLabel.append(enabled,document.createTextNode(' 自动压缩旧上下文（关闭时仍按预算裁切原文）'));content.append(enabledLabel);
  const budget=document.createElement('input');budget.type='number';budget.min='2048';budget.max='64000';budget.step='1024';budget.value=String(data.budget||12000);budget.setAttribute('aria-label','上下文预算 tokens');content.append(budget);
  const estimate=rowText('');const updateContextEstimate=()=>estimate.textContent=`当前估算 ${data.estimated_input_tokens||0} / ${data.budget||12000} tokens · ${enabled.checked?'超出后自动压缩旧上下文':'自动压缩已关闭，超出预算时裁切原文'}`;
  enabled.addEventListener('change',updateContextEstimate);updateContextEstimate();content.append(estimate);
  const checkpoints=Array.isArray(data.compaction?.checkpoints)?data.compaction.checkpoints:[];
  content.append(rowText('最近检查点'));
  if(!checkpoints.length)content.append(rowText('暂无检查点。'));
  checkpoints.slice(-3).reverse().forEach(c=>{if(c.summary)content.append(rowText(`${c.created_at||''} · 模型归纳（不是事实）\n${c.summary}`));});
  content.append(button('保存上下文设置',async()=>{
    const value=Number(budget.value);if(!Number.isInteger(value)||value<2048||value>64000)throw new Error('预算须为 2048 到 64000 的整数 tokens');
    const saved=await post(`convs/${curConv}/context`,{enabled:enabled.checked,budget:value});
    $('contextInfo').textContent=`上下文约 ${saved.estimated_input_tokens||0} / ${saved.budget||value} tokens`;
    panel('Work 上下文',content);
  }));
  panel('Work 上下文',content);
};
$('accountBtn').onclick=async()=>{
  const d=await api('chatgpt/accounts'), content=document.createElement('div');
  content.append(rowText('通过 OpenAI 官方 OAuth 在系统浏览器授权。密码和访问令牌不会显示在此页面。'));
  if(!d.available)content.append(rowText(d.reason||'本机暂不可用 ChatGPT 账户连接。'));
  const activeAccount=(d.accounts||[]).find(a=>(a.active||a.id===d.active_id)&&a.plan_enabled);
  const welcomeKey=activeAccount?'pa-chatgpt-plan-welcomed-'+activeAccount.id:'';
  if(activeAccount&&welcomeKey&&!localStorage.getItem(welcomeKey)){
    localStorage.setItem(welcomeKey,'1');
    content.append(rowText("You're using your ChatGPT plan. ChatGPT model usage follows your plan."));
    content.append(button('Manage usage',()=>location.assign('https://chatgpt.com/settings/usage')));
  }
  const authorize=async(accountId)=>{
    const body=accountId?{account_id:accountId}:{};const login=await post('chatgpt/login',body), url=new URL(login.url,location.href);
    if(url.protocol!=='https:'||!(url.hostname==='openai.com'||url.hostname.endsWith('.openai.com')||url.hostname==='chatgpt.com'||url.hostname.endsWith('.chatgpt.com')))throw new Error('服务器返回的授权地址不是 OpenAI 官方 HTTPS 地址');
    location.assign(url.href);
  };
  const continueButton=button('Continue with ChatGPT',()=>authorize(null));continueButton.disabled=!d.available;content.append(continueButton);
  (d.accounts||[]).forEach(a=>{
    const card=document.createElement('article');
    card.append(rowText(`${a.label||'ChatGPT'}${a.email?' · '+a.email:''}`),rowText(`${a.active||a.id===d.active_id?'当前账户':'已保存账户'} · ${a.plan_enabled?'套餐已启用':'套餐未授权，可继续授权'}`));
    if(a.plan_enabled)card.append(button('切换账户',async()=>{await post('chatgpt/select',{account_id:a.id});await refreshChatGPTModels();$('accountBtn').click();}));
    const continueAccount=button('继续授权',()=>authorize(a.id));continueAccount.disabled=!d.available;card.append(continueAccount);
    card.append(button('退出账户',async()=>{await post('chatgpt/logout',{account_id:a.id});const st=await api('status');applyStatus(st);$('accountBtn').click();}));content.append(card);
  });
  content.append(button('刷新 ChatGPT 模型',async()=>{await refreshChatGPTModels();$('accountBtn').click();}));
  panel('ChatGPT 账户',content);
};
async function refreshChatGPTModels(){await post('chatgpt/models',{});const st=await api('status');applyStatus(st);}
$('presetBtn').onclick=async()=>{
  if(lockedUI())return;await ensureSession();const content=document.createElement('div');
  (serverStatus.presets||[]).forEach(p=>content.append(button(p.name,async()=>{const d=await post(`convs/${curConv}/settings`,{identity:p.identity});applySession(d.session);$('modalWrap').classList.add('hidden');})));
  content.append(button('自定义角色与场景',async()=>{const identity=prompt('固定身份与规则（普通聊天不会改写）',currentSession.settings.identity||'');if(identity===null)return;const scene=prompt('当前场景',currentSession.settings.scene||'');if(scene===null)return;const perspective=prompt('当前角色名（仅检索公开记忆和该角色可知的信息）',currentSession.settings.perspective||'');const d=await post(`convs/${curConv}/settings`,{identity,scene,perspective:perspective||''});applySession(d.session);$('modalWrap').classList.add('hidden');}));panel('角色预设',content);
};
$('memoryToggle').onchange=async()=>{await ensureSession();const d=await post(`convs/${curConv}/settings`,{memory_enabled:$('memoryToggle').checked});applySession(d.session);};
async function showMemory() {
  await ensureSession();const d=await api(`convs/${curConv}/memories`);const content=document.createElement('div');
  content.append(rowText('自动记忆只记录用户明确提示的偏好、承诺和更正，保留为“用户声称”；不会把模型虚构确认成事实。'));
  content.append(button('添加记忆',async()=>{const text=prompt('记忆内容');if(text)await post(`convs/${curConv}/memories`,{content:text,kind:'fact',epistemic:'confirmed'});await showMemory();}));
  if(!d.memories.length)content.append(rowText('暂无长期记忆。原始对话仍完整保存在本机。'));
  d.memories.forEach(m=>{const card=document.createElement('article');card.append(rowText(`${m.kind} · ${m.epistemic} · ${m.validity} · ${m.entity}`),rowText(m.content));
    card.append(button('查看来源',async()=>{const data=await api(`convs/${curConv}/messages`);const source=data.messages.find(x=>x.id===m.source_id);alert(source?source.content:'人工添加，无原始消息来源');}),button('纠正',async()=>{const text=prompt('修订记忆内容；旧版本保留并失效',m.content);if(text){await post(`convs/${curConv}/memories`,{...m,content:text,revises:m.id});await showMemory();}}),button(m.validity==='active'?'撤销':'恢复',async()=>{await post(`convs/${curConv}/memory-edit`,{id:m.id,validity:m.validity==='active'?'retracted':'active'});await showMemory();}),button('建立语义索引',async()=>{if(confirm('将这一条记忆发送给 Google Embedding API？')){await post(`convs/${curConv}/embed`,{id:m.id});await showMemory();}}));content.append(card);
  });
  content.append(button(currentSession.settings.semantic_enabled?'关闭云端语义查询':'启用云端语义查询',async()=>{if(!currentSession.settings.semantic_enabled&&!confirm('每轮将当前消息发送给 Google Embedding API，受本地配额控制。继续？'))return;const s=await post(`convs/${curConv}/settings`,{semantic_enabled:!currentSession.settings.semantic_enabled});applySession(s.session);await showMemory();}));panel('长期记忆',content);
}
$('memoryBtn').onclick=()=>showMemory().catch(e=>alert(e.message));
$('approvalsBtn').onclick=async()=>{
  await ensureSession();const d=await api(`convs/${curConv}/approvals`);const content=document.createElement('div');
  if(appMode==='work') {
    const label=document.createElement('label'), toggle=document.createElement('input');toggle.type='checkbox';toggle.checked=d.auto_approve_work===true;
    label.append(toggle,document.createTextNode(' Work 自动批准（所有工作会话）'));content.append(label);
    const outsideLabel=document.createElement('label'),outsideToggle=document.createElement('input');outsideToggle.type='checkbox';outsideToggle.checked=d.allow_outside_workspace===true;
    outsideLabel.append(outsideToggle,document.createTextNode(' 允许访问工作区外文件（Termux 权限范围）'));content.append(outsideLabel);
    const scope=rowText('');const updateScope=()=>{scope.textContent=(outsideToggle.checked?'文件工具可访问 Termux 权限内路径。':'文件工具限制在当前项目范围内。')+'文件读取和修改都遵循 Termux 当前权限；此项不增加 Android 权限。受保护的私有凭据不会作为模型文件内容。可随时切回项目范围。命令、删除、覆盖和导出遵循自动批准设置。';};updateScope();content.append(scope);
    content.append(rowText('关闭自动批准后，下一项工具操作恢复逐项批准。切换权限不会自动重试已等待的任务。'));
    toggle.onchange=async()=>{const desired=toggle.checked;toggle.disabled=true;try{const data=await post('approval-policy',{auto_approve_work:desired});serverStatus.auto_approve_work=data.auto_approve_work;toggle.checked=data.auto_approve_work;applySession(currentSession);}catch(e){toggle.checked=!desired;alert('保存权限失败：'+e.message);}finally{toggle.disabled=false;}};
    outsideToggle.onchange=async()=>{const desired=outsideToggle.checked;outsideToggle.disabled=true;try{const data=await post('approval-policy',{allow_outside_workspace:desired});outsideToggle.checked=data.allow_outside_workspace===true;d.allow_outside_workspace=outsideToggle.checked;updateScope();}catch(e){outsideToggle.checked=!desired;updateScope();alert('保存文件范围失败：'+e.message);}finally{outsideToggle.disabled=false;}};
  } else content.append(rowText('Chat 不执行工具。自动批准只适用于 Work。'));
  content.append(rowText('手动批准仅用于下方具体操作，15 分钟内一次有效。已有等待任务不会因切换权限自行恢复，请重试本轮。'));
  if(!d.approvals.length)content.append(rowText('没有等待批准的操作。'));
  d.approvals.forEach(a=>{const card=document.createElement('article');const pre=document.createElement('pre');pre.textContent=a.args;card.append(rowText(a.tool),pre,button('批准',async()=>{await post(`convs/${curConv}/approve`,{id:a.id,approve:true});$('modalWrap').classList.add('hidden');alert('已批准，请点击“重试本轮”。');}),button('拒绝',async()=>{await post(`convs/${curConv}/approve`,{id:a.id,approve:false});$('approvalsBtn').click();}));content.append(card);});panel('工具权限',content);
};
$('importInput').onchange=async()=>{try{const f=$('importInput').files[0];if(!f)return;const d=await post('import',JSON.parse(await f.text()));await refreshConvs();await openConv(d.id);}catch(e){alert('导入失败：'+e.message);}finally{$('importInput').value='';}};
$('importBtn').onclick=()=>$('importInput').click();
const oldExport=exportCurrent;
exportCurrent=async()=>{if(!curConv)return;const d=await api(`convs/${curConv}/export`);const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:'application/json'}));a.download='phoneagent-'+appMode+'-'+curConv+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000);};
$('exportBtn').onclick=exportCurrent;
