// Exercise the real submission handler with controlled HTTP outcomes.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('public/js/apply.js','utf8');
const start=source.indexOf("  submitBtn.addEventListener('click', async () => {");
const end=source.indexOf('\n  });',start)+6;
for (const [name,createOk,token,submittedOk,success] of [
 ['creation failure',false,'',true,false],['missing access token',true,'',true,false],
 ['submission failure',true,'test-token',false,false],['successful application',true,'test-token',true,true]
]) test(name, async()=>{
 let handler,step;
 const state={submitted:false,caseRef:'TEST'};
 const submitBtn={disabled:false,addEventListener:(_,fn)=>handler=fn};
 const context={state,submitBtn,submitStatus:{textContent:'',classList:{remove(){},add(){}}},
 generateCaseRef(){},applicationPayload:()=>({}),caseRefSlug:()=>state.caseRef,
 showStep:n=>step=n, caseFetch:async path=>path.endsWith('/submitted')?{ok:submittedOk}:{ok:createOk,json:async()=>({access_token:token})}};
 vm.runInNewContext(source.slice(start,end),context);
 await handler();
 assert.equal(state.submitted,success);
 assert.equal(step,success?7:undefined);
 assert.equal(submitBtn.disabled,success);
});
