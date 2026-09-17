const {test}=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync('public/js/analytics.js','utf8');
function setup(consent,host='directorpersonalcode.uk',search) {
 const scripts=[], listeners={};
 const document={readyState:'loading',title:'Apply',referrer:'https://google.com/search?q=private',cookie:'',head:{appendChild:s=>scripts.push(s)},createElement:()=>({addEventListener(){}}),addEventListener:(n,f)=>listeners[n]=f,querySelector:()=>({appendChild(){}})};
 const context={window:{DPC_GA4_ID:'G-TESTONLY'},location:{hostname:host,origin:'https://'+host,pathname:'/apply',search:'?access_token=secret&email=private@example.com&utm_source=google&utm_medium=organic&utm_campaign=gbp_dpc',reload(){}},document,localStorage:{getItem:()=>JSON.stringify({value:consent,expires:Date.now()+100000})},URL,URLSearchParams,Set,Date};
 if (search !== undefined) context.location.search=search;
 vm.runInNewContext(source,context);listeners.DOMContentLoaded?.();return {...context,scripts};
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
