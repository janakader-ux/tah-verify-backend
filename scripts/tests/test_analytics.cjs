const {test}=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync('public/js/analytics.js','utf8');
function setup(consent,host='directorpersonalcode.uk') {
 const scripts=[], listeners={};
 const document={readyState:'loading',title:'Apply',referrer:'https://google.com/search?q=private',cookie:'',head:{appendChild:s=>scripts.push(s)},createElement:()=>({addEventListener(){}}),addEventListener:(n,f)=>listeners[n]=f,querySelector:()=>({appendChild(){}})};
 const context={window:{DPC_GA4_ID:'G-TESTONLY'},location:{hostname:host,origin:'https://'+host,pathname:'/apply',search:'?access_token=secret&email=private@example.com&utm_source=google&utm_medium=organic&utm_campaign=gbp_dpc',reload(){}},document,localStorage:{getItem:()=>JSON.stringify({value:consent,expires:Date.now()+100000})},URL,URLSearchParams,Set,Date};
 vm.runInNewContext(source,context);listeners.DOMContentLoaded?.();return {...context,scripts};
}
test('rejection loads no Google script and sends no events',()=>{const c=setup('denied');c.window.DPCAnalytics.event('generate_lead',{});assert.equal(c.scripts.length,0);assert.equal(c.window.dataLayer,undefined);});
test('grant sends one sanitised pageview and allowlisted events only',()=>{const c=setup('granted');assert.equal(c.scripts.length,1);c.window.DPCAnalytics.event('application_step',{step:3,email:'private@example.com',case_ref:'secret'},true);c.window.DPCAnalytics.event('application_step',{step:3},true);c.window.DPCAnalytics.event('unapproved_event',{token:'secret'});const data=JSON.stringify(c.window.dataLayer);assert(!data.includes('secret'));assert(!data.includes('private'));assert.equal(c.window.dataLayer.filter(x=>x[0]==='event'&&x[1]==='page_view').length,1);assert.equal(c.window.dataLayer.filter(x=>x[1]==='application_step').length,1);assert(!data.includes('unapproved_event'));assert(data.includes('gbp_dpc'));});
test('preview hosts never send analytics',()=>{const c=setup('granted','preview.netlify.app');c.window.DPCAnalytics.event('generate_lead',{});assert.equal(c.scripts.length,0);assert.equal(c.window.dataLayer,undefined);});
