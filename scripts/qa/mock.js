/* Browser fixture only. Build script never includes this in production. */
(()=>{
'use strict';
if(!/^deploy-preview-\d+--directorpersonalcodeuk\.netlify\.app$/.test(location.hostname)||!location.pathname.startsWith('/__qa__/'))throw Error('QA is restricted to the isolated preview path');
let app=null,review='required',version='qa-v1';const calls=[];
const response=(body,status=200)=>Promise.resolve(new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}}));
window.fetch=(url,options={})=>{
 const path=new URL(String(url),location.href).pathname;const method=options.method||'GET';calls.push(method+' '+path);const audit=document.getElementById('qaAudit');if(audit)audit.textContent=calls.join('\n');
 if(path==='/api/config')return response({document_review:{available:true,ai_available:false}});
 if(path.includes('ipify'))return response({ip:'TEST-NO-IP'});
 if(path==='/api/applications'&&method==='POST'){app=JSON.parse(options.body);app.fee_amount=app.route==='in-person'?125:app.residency==='uk'?49:119;return response({...app,access_token:'qa-token'},201);}
 if(!path.startsWith('/api/'))return response({});
 if(!app)return response({detail:'No test application'},404);
 if(path.endsWith('/documents')){review='manual';return response({status:review,version,message:'Synthetic document uploaded. Use Approve test review, then Check review status.'});}
 if(path.endsWith('/document-review'))return response({status:review,version,message:review==='ready'?'Preliminary test review complete. No real identity has been checked.':'Awaiting synthetic team review.',fee_amount:app.fee_amount,route:app.route,residency:app.residency});
 if(path.endsWith('/submitted')){app.submitted=true;return response({submitted:true});}
 if(path.endsWith('/payment'))return app.submitted?response({checkout_id:'cs_dummy_'+app.case_ref,hosted_checkout_url:location.origin+'/__qa__/checkout.html?case='+encodeURIComponent(app.case_ref),provider:'dummy'}):response({detail:'Submission required'},409);
 if(path.endsWith('/payment-status'))return response({payment_status:localStorage.getItem('qa-paid-'+app.case_ref)==='paid'?'paid':'pending'});
 if(path.endsWith('/appointment'))return response({...app,...JSON.parse(options.body)});
 return response({detail:'Unexpected test request'},400);
};
document.addEventListener('DOMContentLoaded',()=>{
 const panel=document.createElement('aside');panel.id='qaControls';panel.style='margin:20px;padding:16px;background:#fff4cf;color:#151515;border:2px solid #805900';
 panel.innerHTML='<h2>Isolated workflow test — no real payments, emails or ID checks</h2><p>Use synthetic data only. These controls are absent from production.</p><button type="button" id="qaFill">Fill synthetic applicant fields</button><p id="qaStatus" role="status"></p><details><summary>Test request audit</summary><pre id="qaAudit"></pre></details>';
 document.querySelector('main').before(panel);
 document.getElementById('qaFill').onclick=()=>{
 const data={firstName:'Synthetic',lastName:'Applicant',dob:'1990-01-01',nationality:'British',residenceCountry:'France',homeAddress:'Synthetic QA address - not submitted',addressSince:'2020-01',email:'qa@example.invalid',mobileCode:'+44',mobile:'7700900000',companyName:'Synthetic QA Ltd',companyNumber:'00000000',roleConfirm:'Director',signName:'Synthetic QA signature'};
 for(const [id,value]of Object.entries(data)){const el=document.getElementById(id);if(!el)throw Error('Missing QA field '+id);el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}
 for(const id of ['ack1','ack2','ack3','ack4','ack5']){const el=document.getElementById(id);el.checked=true;el.dispatchEvent(new Event('change',{bubbles:true}));}
 document.getElementById('qaStatus').textContent='Synthetic fields filled. Choose the category and follow every wizard step.';
 };
});
})();
