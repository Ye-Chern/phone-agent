/* Offline DOM checks against the actual bundled frontend, no cloud requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM,requestInterceptor,VirtualConsole} = require('../work/upgrade-20261011/tooling/node_modules/jsdom');
const root=path.resolve(__dirname,'..','web'), errors=[];
let autoApproveWork=false;
let allowOutsideWorkspace=false;
let policyPostCount=0;
let chatgptModelsAvailable=false;
let contextConfig={enabled:true,budget:12000,estimated_input_tokens:3000,compaction:{checkpoints:[{created_at:'2026-10-12',summary:'模型归纳检查点'}]}};
let activeChatGPT='acct-1';
let providerSettings={deepseek_key_configured:false,gemini_key_configured:true,exa_key_configured:false,providers:[],version:'test'};
let providerPostBody=null;
const chatgptAccounts=[{id:'acct-1',label:'OpenAI',email:'test@example.com',plan_enabled:true,active:true}];
const sessions={work:{id:'work',mode:'work',title:'工作测试',settings:{workdir:'/test/project'}},chat:{id:'chat',mode:'chat',title:'聊天测试',settings:{memory_enabled:true,model:'gemma-4-31b-it'}}};
const registry={'deepseek-flash':{modes:['work','chat'],reasoning_efforts:['low','high','max'],default_reasoning_effort:'low'},'gemma-4-26b-a4b-it':{modes:['chat']},'gemma-4-31b-it':{modes:['chat']},'gemini-3.5-flash-lite':{modes:['work']},'gemini-3.8-flash':{modes:['work']}};
const status={models:Object.keys(registry),model_registry:registry,default_model:'deepseek-flash',chat_default_model:'gemma-4-26b-a4b-it',provider:'DeepSeek + Google',presets:[],vision_models:[]};
const localResources={interceptors:[requestInterceptor(request=>{
  const name=new URL(request.url).pathname.replace(/^\/web\//,''), file=path.resolve(root,name);
  if(file.startsWith(root+path.sep)&&fs.existsSync(file))return new Response(fs.readFileSync(file),{headers:{'Content-Type':file.endsWith('.js')?'application/javascript':'text/css'}});
  return new Response('',{status:404});
})]};
const logs=new VirtualConsole();logs.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{url:'http://127.0.0.1:8756/web/',runScripts:'dangerously',resources:localResources,pretendToBeVisual:true,virtualConsole:logs,beforeParse(w){
  w.alert=()=>{};w.matchMedia=()=>({matches:false,addListener(){}});w.HTMLElement.prototype.scrollTo=function(){};
  w.fetch=async(url,opts={})=>{const route=String(url).replace(/^\/api\//,'');let data={};
    if(route==='status')data=status;
    else if(route==='provider-settings'){
      if(opts.method==='POST'){
        providerPostBody=JSON.parse(opts.body);
        for(const key of ['deepseek','gemini','exa']){
          if(providerPostBody['clear_'+key+'_key']===true)providerSettings[key+'_key_configured']=false;
          else if(typeof providerPostBody[key+'_key']==='string'&&providerPostBody[key+'_key'].trim())providerSettings[key+'_key_configured']=true;
        }
      }
      data=providerSettings;
    }
    else if(route==='chatgpt/accounts')data={available:true,accounts:chatgptAccounts,active_id:activeChatGPT};
    else if(route==='chatgpt/login')data={url:'https://auth.openai.com/oauth/authorize?client_id=test'};
    else if(route==='chatgpt/select'){activeChatGPT=JSON.parse(opts.body).account_id;data={accounts:chatgptAccounts,active_id:activeChatGPT};}
    else if(route==='chatgpt/logout')data={accounts:[],active_id:null};
    else if(route==='chatgpt/models'){chatgptModelsAvailable=true;registry['chatgpt/gpt-5']={modes:['work','chat'],reasoning_efforts:['low','medium','high','xhigh','max'],default_reasoning_effort:'medium'};status.models=Object.keys(registry);data={models:['chatgpt/gpt-5']};}
    else if(route==='convs/work/context'){if(opts.method==='POST')contextConfig={...contextConfig,...JSON.parse(opts.body)};data=contextConfig;}
    else if(route==='convs')data={convs:Object.values(sessions)};
    else if(route.endsWith('/messages')){const id=route.split('/')[1];data={session:sessions[id],messages:[],run:null};}
    else if(route.endsWith('/settings')){const id=route.split('/')[1];Object.assign(sessions[id].settings,JSON.parse(opts.body));data={session:sessions[id]};}
    else if(route==='quota')data={quota:[]};
    else if(route.endsWith('/approvals'))data={approvals:[],auto_approve_work:autoApproveWork,allow_outside_workspace:allowOutsideWorkspace};
    else if(route==='approval-policy'){if(opts.method==='POST'){policyPostCount++;const body=JSON.parse(opts.body);if(typeof body.auto_approve_work==='boolean')autoApproveWork=body.auto_approve_work;if(typeof body.allow_outside_workspace==='boolean')allowOutsideWorkspace=body.allow_outside_workspace;}data={auto_approve_work:autoApproveWork,allow_outside_workspace:allowOutsideWorkspace};}
    return {ok:true,status:200,json:async()=>data};
  };
}});
async function ready(){for(let i=0;i<100;i++){if(dom.window.document.querySelector('#modelSel').options.length&&dom.window.document.querySelector('#convTitle').textContent==='工作测试')return;await new Promise(r=>setTimeout(r,20));}throw Error('UI did not initialize: '+errors.join(' | '));}
(async()=>{await ready();const w=dom.window,d=w.document;
  await d.querySelector('#providerSettingsBtn').onclick();
  const secretInputs=[...d.querySelectorAll('#modalWrap input[type="password"]')];
  assert.equal(secretInputs.length,3);assert.ok(secretInputs.every(input=>input.value===''));
  const providerSave=[...d.querySelectorAll('#modalWrap button')].find(button=>button.textContent==='保存 API 设置');
  await providerSave.onclick();assert.deepEqual(providerPostBody,{});assert.equal(providerSettings.gemini_key_configured,true);
  secretInputs[0].value='mock-deepseek-secret';d.querySelector('#modalWrap input[type="checkbox"]').checked=true;
  await providerSave.onclick();assert.equal(providerPostBody.deepseek_key,undefined);assert.equal(providerPostBody.gemini_key,undefined);assert.equal(providerPostBody.clear_deepseek_key,true);
  assert.ok(!d.querySelector('#modalWrap').textContent.includes('mock-deepseek-secret'));assert.equal(secretInputs[0].value,'');
  assert.ok(d.querySelector('#modalWrap .provider-status').textContent.includes('DeepSeek 未设置'));
  d.querySelector('#modalWrap').classList.add('hidden');
  assert.equal(d.body.dataset.mode,'work');assert.equal(d.querySelector('#memoryToggleLabel').hidden,true);
  assert.deepEqual([...d.querySelector('#modelSel').options].map(o=>o.value),['deepseek-flash','gemini-3.5-flash-lite','gemini-3.8-flash']);
  assert.deepEqual([...d.querySelector('#reasoningSel').options].map(o=>o.value),['low','high','max']);assert.equal(d.querySelector('#reasoningSel').value,'low');
  d.querySelector('#reasoningSel').value='high';await d.querySelector('#reasoningSel').onchange();assert.equal(sessions.work.settings.reasoning_effort,'high');
  d.querySelector('#layoutToggle').onclick();assert.equal(d.body.classList.contains('compact-header'),true);assert.equal(w.localStorage.getItem('pa-compact-header'),'1');
  assert.equal(d.querySelector('#reasoningDrawerBtn').hidden,false);d.querySelector('#layoutToggle').onclick();assert.equal(d.body.classList.contains('compact-header'),false);
  d.querySelector('#modelSel').value='gemini-3.5-flash-lite';await d.querySelector('#modelSel').onchange();assert.equal(d.querySelector('#reasoningSel').hidden,true);
  d.querySelector('#modelSel').value='deepseek-flash';await d.querySelector('#modelSel').onchange();assert.equal(d.querySelector('#reasoningSel').hidden,false);assert.equal(d.querySelector('#reasoningSel').value,'high');
  w.eval("saveDraft(); $('input').value='独立工作草稿'; saveDraft();");
  await w.eval("switchMode('chat')");assert.equal(d.body.dataset.mode,'chat');assert.equal(d.querySelector('#modelSel').value,'gemma-4-31b-it');assert.equal(d.querySelector('#workspaceBtn').hidden,true);assert.equal(d.querySelector('#reasoningSel').hidden,true);
  d.querySelector('#input').value='独立聊天草稿';w.eval('saveDraft()');await w.eval("switchMode('work')");assert.equal(d.querySelector('#input').value,'独立工作草稿');assert.equal(d.querySelector('#reasoningSel').value,'high');await w.eval("switchMode('chat')");assert.equal(d.querySelector('#input').value,'独立聊天草稿');
  delete sessions.chat.settings.model;await w.eval("switchMode('work')");await w.eval("switchMode('chat')");assert.equal(d.querySelector('#modelSel').value,'gemma-4-26b-a4b-it');
  await w.eval("switchMode('work')");await d.querySelector('#approvalsBtn').onclick();
  assert.equal(d.querySelector('#contextBtn').hidden,false);await d.querySelector('#contextBtn').onclick();
  assert.ok(d.querySelector('#modalWrap').textContent.includes('模型归纳检查点'));
  assert.ok(d.querySelector('#modalWrap').textContent.includes('关闭时仍按预算裁切原文'));
  const budget=d.querySelector('#modalWrap input[type=number]');budget.value='16000';
  assert.equal(budget.min,'2048');assert.equal(budget.max,'64000');
  const saveContext=[...d.querySelectorAll('#modalWrap button')].find(b=>b.textContent==='保存上下文设置');await saveContext.onclick();assert.equal(contextConfig.budget,16000);
  d.querySelector('#modalWrap').classList.add('hidden');await d.querySelector('#approvalsBtn').onclick();
  const permission=d.querySelector('#modalWrap input[type=checkbox]');assert.ok(permission);assert.equal(permission.checked,false);
  permission.checked=true;await permission.onchange();assert.equal(autoApproveWork,true);assert.equal(d.querySelector('#approvalsBtn').textContent,'自动批准已开启');
  permission.checked=false;await permission.onchange();assert.equal(autoApproveWork,false);assert.equal(d.querySelector('#approvalsBtn').textContent,'工具权限');
  const outside=d.querySelector('#modalWrap input[type=checkbox]:nth-of-type(2)')||d.querySelectorAll('#modalWrap input[type=checkbox]')[1];
  assert.ok(outside);assert.equal(outside.checked,false);const beforeOutside=policyPostCount;
  outside.checked=true;await outside.onchange();assert.equal(allowOutsideWorkspace,true);assert.equal(outside.checked,true);assert.equal(policyPostCount,beforeOutside+1);
  assert.ok(d.querySelector('#modalWrap').textContent.includes('文件工具可访问 Termux 权限内路径'));
  outside.checked=false;await outside.onchange();assert.equal(allowOutsideWorkspace,false);assert.ok(d.querySelector('#modalWrap').textContent.includes('文件工具限制在当前项目范围内'));
  await w.eval("switchMode('chat')");await d.querySelector('#approvalsBtn').onclick();assert.equal(d.querySelector('#modalWrap input[type=checkbox]'),null);d.querySelector('#modalWrap').classList.add('hidden');
  await d.querySelector('#accountBtn').onclick();assert.ok(d.querySelector('#modalWrap').textContent.includes('test@example.com'));
  assert.ok(d.querySelector('#modalWrap').textContent.includes("You're using your ChatGPT plan."));
  assert.ok(w.localStorage.getItem('pa-chatgpt-plan-welcomed-acct-1'));
  const refreshModels=[...d.querySelectorAll('#modalWrap button')].find(b=>b.textContent==='刷新 ChatGPT 模型');await refreshModels.onclick();
  assert.ok([...d.querySelector('#modelSel').options].some(o=>o.value==='chatgpt/gpt-5'));
  d.querySelector('#modelSel').value='chatgpt/gpt-5';await d.querySelector('#modelSel').onchange();
  assert.deepEqual([...d.querySelector('#reasoningSel').options].map(o=>o.value),['low','medium','high','xhigh','max']);assert.equal(d.querySelector('#reasoningSel').value,'medium');
  d.querySelector('#reasoningSel').value='max';await d.querySelector('#reasoningSel').onchange();assert.equal(sessions.chat.settings.reasoning_effort,'max');
  assert.equal(d.querySelector('#chatgptPlanHint').hidden,false);assert.equal(d.querySelector('#chatgptPlanHint').getAttribute('href'),'https://chatgpt.com/settings/usage');
  w.eval('busy=true; applyStatus(serverStatus)');assert.equal(d.querySelector('#sendBtn').disabled,true);w.eval('busy=false');
  w.eval("setNavigationBusy(true); $('input').value='切换中不可发送'");await w.eval('sendMessage()');
  assert.equal(w.eval('busy || preparingSend'),false);assert.equal(d.querySelector('#sendBtn').disabled,true);w.eval('setNavigationBusy(false)');
  const el=d.createElement('div');const markdown='# 标题\n\n**强调**\n\n- [x] 任务\n\n| 名称 | 数量 |\n| --- | --- |\n| 茶 | 2 |\n\n> 引用\n\n```javascript\nconst a = "<script>alert(1)</script>";\n```\n\n[链接](https://example.org) [危险](javascript:alert(1))\n\n<img src=x onerror=alert(1)><script>alert(1)</script>\n\n公式 $x^2$';
  w.setMarkdown(el,markdown);
  assert.equal(el.querySelectorAll('h1').length,1);assert.equal(el.querySelectorAll('table').length,1);assert.equal(el.querySelectorAll('pre code').length,1);assert.ok(el.querySelector('code').classList.contains('hljs'));assert.ok(el.querySelector('.katex'));assert.equal(el.querySelectorAll('script,img,iframe,svg').length,0);assert.ok(![...el.querySelectorAll('a')].some(a=>a.href.startsWith('javascript:')));
  assert.ok(el.textContent.includes('<script>alert(1)</script>'));assert.equal(el.querySelectorAll('button.copy').length,1);
  w.setMarkdown(el,'流式未闭合代码\n```python\nprint("<&")');assert.ok(el.querySelector('pre code'));
  const streamResult=w.eval(`(()=>{
    const q=newStreamDisplayQueue(),payload='A😀'.repeat(5000),reason='思😀'.repeat(10),parts=Array.from(payload);
    for(let i=0;i<parts.length;i+=733)enqueueStreamDisplay(q,{type:'text',delta:parts.slice(i,i+733).join('')});
    enqueueStreamDisplay(q,{type:'tool_start',id:'tool-1',name:'read_file',args:JSON.stringify({path:'/safe/a'})});
    enqueueStreamDisplay(q,{type:'tool_end',id:'tool-1',ok:true,summary:'first'});
    enqueueStreamDisplay(q,{type:'reasoning',delta:reason});
    enqueueStreamDisplay(q,{type:'tool_start',id:'tool-2',name:'write_file',args:JSON.stringify({path:'/safe/b'})});
    enqueueStreamDisplay(q,{type:'tool_end',id:'tool-2',ok:true,summary:'second'});
    enqueueStreamDisplay(q,{type:'text',delta:'完成'});enqueueStreamDisplay(q,{type:'done'});
    let now=0,credit=0,shown='',shownReason='',md=0,lastMd=0,order=[],ticks=0;
    while(q.queue.length&&ticks<3000){ticks++;now+=50;credit=Math.min(30,credit+STREAM_DISPLAY_RATE*.05);const b=takeStreamDisplay(q,Math.floor(credit));credit-=b.used;
      for(const e of b.events){if(e.type==='text'){shown+=e.delta;if(now-lastMd>=150){md++;lastMd=now;}}else if(e.type==='reasoning')shownReason+=e.delta;else order.push(e.type+':'+(e.id||''));}
    }
    return {received:q.fullText,reasoning:q.fullReasoning,shown,shownReason,order,md,ticks,durationMs:now,rate:STREAM_DISPLAY_RATE,remaining:q.queue.length};
  })()`);
  const validSurrogates=s=>{for(let i=0;i<s.length;i++){const c=s.charCodeAt(i);if(c>=0xd800&&c<=0xdbff){if(i+1>=s.length||s.charCodeAt(i+1)<0xdc00||s.charCodeAt(i+1)>0xdfff)return false;i++;}else if(c>=0xdc00&&c<=0xdfff)return false;}return true;};
  assert.equal(streamResult.remaining,0);assert.equal(streamResult.shown,streamResult.received);assert.equal(streamResult.shownReason,streamResult.reasoning);assert.ok(validSurrogates(streamResult.shown));assert.ok(streamResult.durationMs>=Math.ceil((Array.from(streamResult.shown).length/120)*1000));
  assert.deepEqual(Array.from(streamResult.order),['tool_start:tool-1','tool_end:tool-1','tool_start:tool-2','tool_end:tool-2','done:']);assert.ok(streamResult.md>0);
  const stale=JSON.parse(JSON.stringify(w.eval(`(()=>{const el=document.createElement('div');document.body.appendChild(el);const d={token:viewLoadToken,convId:curConv,message:el};const current=isStreamDisplayCurrent(d);viewLoadToken++;const stale=!isStreamDisplayCurrent(d);el.remove();return {current,stale};})()`)));assert.deepEqual(stale,{current:true,stale:true});
  console.log('STREAM_BURST '+JSON.stringify({receivedCodepoints:Array.from(streamResult.received).length,displayedCodepoints:Array.from(streamResult.shown).length,rateCharsPerSecond:streamResult.rate,simulatedDurationMs:streamResult.durationMs,markdownRenders:streamResult.md,toolOrder:streamResult.order,unicodeSafe:validSurrogates(streamResult.shown),remainingEvents:streamResult.remaining,staleViewRejected:stale.stale}));
  assert.deepEqual(errors,[]);console.log('WEB_OK: mode/model/draft separation, burst stream queue, context settings, account model refresh, approvals, Markdown and HTML/link safety');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>dom.window.close());
