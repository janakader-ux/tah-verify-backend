const {test}=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync('public/js/analytics.js','utf8');
function setup(consent,host='directorpersonalcode.uk',search) {
 const scripts=[], listeners={}, dispatched=[];
 const document={readyState:'loading',title:'Apply',referrer:'https://google.com/search?q=private',cookie:'',head:{appendChild:s=>scripts.push(s)},createElement:()=>({addEventListener(){}}),addEventListener:(n,f)=>listeners[n]=f,dispatchEvent:e=>dispatched.push(e.type),querySelector:()=>({appendChild(){}})};
 const context={window:{DPC_GA4_ID:'G-TESTONLY'},location:{hostname:host,origin:'https://'+host,pathname:'/apply',search:'?access_token=secret&email=private@example.com&utm_source=google&utm_medium=organic&utm_campaign=gbp_dpc',reload(){}},document,localStorage:{getItem:()=>JSON.stringify({value:consent,expires:Date.now()+100000})},URL,URLSearchParams,Set,Date,CustomEvent:class{constructor(type){this.type=type;}}};
 if (search !== undefined) context.location.search=search;
 vm.runInNewContext(source,context);listeners.DOMContentLoaded?.();return {...context,scripts,dispatched};
}
test('rejection loads no Google script and sends no events',()=>{const c=setup('denied');c.window.DPCAnalytics.event('generate_lead',{});assert.equal(c.scripts.length,0);assert.equal(c.window.dataLayer,undefined);});
test('grant sends one sanitised pageview and allowlisted events only',()=>{const c=setup('granted');assert.equal(c.scripts.length,1);c.window.DPCAnalytics.event('application_step',{step:3,email:'private@example.com',case_ref:'secret'},true);c.window.DPCAnalytics.event('application_step',{step:3},true);c.window.DPCAnalytics.event('unapproved_event',{token:'secret'});const data=JSON.stringify(c.window.dataLayer);assert(!data.includes('secret'));assert(!data.includes('private'));assert.equal(c.window.dataLayer.filter(x=>x[0]==='event'&&x[1]==='page_view').length,1);assert.equal(c.window.dataLayer.filter(x=>x[1]==='application_step').length,1);assert(!data.includes('unapproved_event'));assert(data.includes('gbp_dpc'));});
test('preview hosts never send analytics',()=>{const c=setup('granted','preview.netlify.app');c.window.DPCAnalytics.event('generate_lead',{});assert.equal(c.scripts.length,0);assert.equal(c.window.dataLayer,undefined);});
test('approved campaign links retain attribution without exposing query data',()=>{
 for(const [source,medium,content] of [['linkedin','organic','post_01'],['partner','referral','partner_30']]) {
  const query=`?utm_source=${source}&utm_medium=${medium}&utm_campaign=dpc_14day_sep2026&utm_content=${content}&email=private@example.com&access_token=secret`;
  const c=setup('granted','directorpersonalcode.uk',query), config=c.window.dataLayer.find(x=>x[0]==='config')[2];
  assert.equal(config.campaign_source,source);assert.equal(config.campaign_medium,medium);assert.equal(config.campaign_content,content);
  const data=JSON.stringify(c.window.dataLayer);assert(!data.includes('private'));assert(!data.includes('secret'));
  assert.equal(setup('denied','directorpersonalcode.uk',query).window.dataLayer,undefined);
 }
});
test('unapproved campaign values cannot carry personal data into analytics',()=>{
 for(const query of [
  '?utm_source=private@example.com&utm_medium=referral&utm_campaign=dpc_14day_sep2026',
  '?utm_source=partner&utm_medium=private@example.com&utm_campaign=dpc_14day_sep2026',
  '?utm_source=partner&utm_medium=referral&utm_campaign=private@example.com',
  '?utm_source=partner&utm_medium=referral&utm_campaign=dpc_14day_sep2026&utm_content=private@example.com',
  '?utm_source=__proto__&utm_medium=referral&utm_campaign=dpc_14day_sep2026'
 ]) { const c=setup('granted','directorpersonalcode.uk',query);assert(!JSON.stringify(c.window.dataLayer).includes('private'));assert(!JSON.stringify(c.window.dataLayer).includes('__proto__')); }
});

test('enquiry analytics accept only bounded context and deduplicate each error stage',()=>{
 const c=setup('granted');
 c.window.DPCAnalytics.event('enquiry_start',{form_type:'contact',email:'private@example.com'},true);
 c.window.DPCAnalytics.event('enquiry_start',{form_type:'contact'},true);
 for(const stage of ['validation','network','server','response']){
  c.window.DPCAnalytics.event('enquiry_error',{form_type:'contact',error_stage:stage,message:'private secret'},true);
  c.window.DPCAnalytics.event('enquiry_error',{form_type:'contact',error_stage:stage},true);
 }
 const sent=c.window.dataLayer.filter(x=>x[0]==='event');
 assert.equal(sent.filter(x=>x[1]==='enquiry_start').length,1);
 assert.equal(sent.filter(x=>x[1]==='enquiry_error').length,4);
 assert(!JSON.stringify(sent).includes('private'));
 assert(!JSON.stringify(sent).includes('secret'));
 c.window.DPCAnalytics.event('enquiry_error',{form_type:'private@example.com',error_stage:'private@example.com'});
 const last=c.window.dataLayer.at(-1)[2];
 assert.equal(last.form_type,undefined);assert.equal(last.error_stage,undefined);
});
test('denied analytics suppress new enquiry events',()=>{
 const c=setup('denied');
 for(const event of ['enquiry_start','enquiry_error'])c.window.DPCAnalytics.event(event,{form_type:'contact',error_stage:'validation'},true);
 assert.equal(c.window.dataLayer,undefined);
});

test('analytics readiness is signalled only after consent',()=>{
 assert.deepEqual(setup('granted').dispatched,['dpc:analytics-ready']);
 assert.deepEqual(setup('denied').dispatched,[]);
});
test('late consent reports the current application screen without replaying earlier steps',()=>{
 const source=fs.readFileSync('public/js/apply.js','utf8'),start=source.indexOf("  document.addEventListener('dpc:analytics-ready'"),end=source.indexOf('  showStep(1);',start);
 const events=[];let listener;const context={state:{step:3},track:(...args)=>events.push(args),document:{addEventListener:(_,fn)=>listener=fn}};
 assert(start>=0&&end>start);vm.runInNewContext(source.slice(start,end),context);listener();
 assert.equal(events.length,1);assert.equal(events[0][0],'application_step');assert.equal(events[0][1].step,3);
 context.state.step=2;listener();assert.equal(events.at(-1)[0],'application_start');
});
