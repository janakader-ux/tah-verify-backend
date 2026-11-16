
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('public/js/enquire.js','utf8');
function setup(fetchImpl,pathname='/contact'){
 const events=[],listeners={},button={disabled:false,textContent:'Send enquiry'},status={textContent:''};
 const data={name:'Private Name',email:'private@example.com',notes:'private passport content',service:'general'};
 const form={elements:{service:{value:'general'}},addEventListener:(n,fn)=>{const old=listeners[n];listeners[n]=old?(...args)=>{old(...args);return fn(...args);}:fn;},querySelector:()=>button};
 const context={window:{DPCAnalytics:{event:(...args)=>events.push(args)}},location:{pathname},document:{getElementById:id=>({enquiryForm:form,enquiryStatus:status}[id])},fetch:fetchImpl,FormData:class{*[Symbol.iterator](){yield* Object.entries(data);}}};
 vm.runInNewContext(source,context);
 return {events,listeners,button,status,context,submit:()=>listeners.submit({preventDefault(){}})};
}
test('first form interaction tracks context without field contents',()=>{
 const c=setup(()=>{throw Error('network should not run');});
 c.listeners.input({target:{name:'website',type:'text'}});c.listeners.input({target:{name:'service',type:'hidden'}});
 assert.equal(c.events.length,0);
 c.listeners.input({target:{name:'email',type:'email',value:'private@example.com'}});
 assert.equal(c.events[0][0],'enquiry_start');assert.equal(c.events[0][1].form_type,'contact');assert.equal(c.events[0][2],true);
 assert(!JSON.stringify(c.events).includes('private'));
});
test('native validation failure is observed before a submission',()=>{
 const c=setup(()=>{throw Error('must not submit');});c.listeners.invalid();
 assert.equal(c.events.at(-1)[0],'enquiry_error');assert.equal(c.events.at(-1)[1].error_stage,'validation');
});
for(const [name,fetchImpl,stage] of [
 ['network',async()=>{throw Error('Private network error');},'network'],
 ['server',async()=>({ok:false,json:async()=>({detail:'Private server message'})}),'server'],
 ['unreadable response',async()=>({ok:true,json:async()=>{throw Error('Private response');}}),'response']
])test(name+' failure records a bounded stage and allows retry',async()=>{
 const c=setup(fetchImpl);await c.submit();assert.equal(c.button.disabled,false);
 const error=c.events.find(e=>e[0]==='enquiry_error');assert.equal(error[1].error_stage,stage);
 assert(!c.events.some(e=>e[0]==='generate_lead'));assert(!JSON.stringify(c.events).includes('Private'));
});
test('accepted enquiry remains a success when email notification is delayed',async()=>{
 const c=setup(async()=>({ok:true,json:async()=>({message:'Saved',reference:'TEST-PRIVATE',notification_sent:false})}),'/enquire.html');
 await c.submit();assert.equal(c.events.at(-1)[0],'generate_lead');assert.equal(c.events.at(-1)[1].form_type,'specialist_enquiry');
 assert(!c.events.some(e=>e[0]==='enquiry_error'));assert(!JSON.stringify(c.events).includes('TEST-PRIVATE'));
 assert.equal(c.button.textContent,'Enquiry received');
});
test('analytics errors cannot prevent an enquiry being accepted',async()=>{
 const c=setup(async()=>({ok:true,json:async()=>({message:'Saved',reference:'TEST',notification_sent:true})}));
 c.context.window.DPCAnalytics.event=()=>{throw Error('analytics unavailable');};
 await c.submit();assert.equal(c.button.textContent,'Enquiry received');
});
