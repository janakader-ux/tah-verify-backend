"""Full real API/provider-adapter journeys; only HTTP transport is simulated."""
import os,sys,tempfile,unittest,json,logging
from pathlib import Path
from unittest.mock import patch
import requests
from concurrent.futures import ThreadPoolExecutor
os.environ['DB_PATH']=str(Path(tempfile.mkdtemp())/'providers.db')
os.environ['STAFF_PASSCODE']='qa-staff-secret'
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
import api_server as api
logging.getLogger('httpx').setLevel(logging.WARNING)

class ProviderJourneys(unittest.TestCase):
 def test_provider_journeys_and_recovery(self):
  sessions={};mail=[];guests=[];controls={'reject_guest':False,'email_fail':False}
  def transport(session,method,url,**kw):
   response=requests.Response();response.status_code=200
   if url.startswith('https://api.stripe.com/v1/checkout/sessions'):
    if method.upper()=='POST':
     p=kw['data'];self.assertEqual(p['line_items[0][price_data][currency]'],'gbp')
     sid='cs_test_'+str(len(sessions));sessions[sid]={'payment_status':'unpaid','status':'open','amount':p['line_items[0][price_data][unit_amount]']}
     body={'id':sid,'url':'https://checkout.stripe.com/c/pay/'+sid}
    else:body=sessions[url.rsplit('/',1)[1]]
   elif url.endswith('/VPE/session/login/'):
    body={'Success':True,'SessionId':'synthetic-session'}
   elif url.endswith('/VPE/guestLink/createGuestLink/'):
    p=kw['json'];guests.append(p)
    self.assertEqual(p['ContainerEventCallbackHeaders'][0]['Header'],'Authorization')
    self.assertTrue(p['SendEmail']);self.assertTrue(p['Email'].endswith('@example.invalid'))
    body={'Success':not controls['reject_guest'],'Message':'Synthetic provider rejection','LinkUrl':'https://example.invalid/guest'}
   elif url=='https://api.brevo.com/v3/smtp/email':
    p=kw['json'];self.assertTrue(p['to'][0]['email'].endswith('@example.invalid'));mail.append(p)
    response.status_code=503 if controls['email_fail'] else 201;body={'messageId':'synthetic-mail'}
   else:raise AssertionError('Unexpected network destination: '+url)
   response._content=json.dumps(body).encode();return response
  with patch.object(requests.sessions.Session,'request',transport),patch.object(api,'STRIPE_TOKEN','sk_test_synthetic'),patch.object(api,'BREVO_API_KEY','synthetic'),patch.object(api,'NOTIFICATION_EMAIL','staff@example.invalid'),patch.object(api.trustid_client,'TRUSTID_SERVER','https://trustid.example.invalid'),patch.object(api.trustid_client,'is_configured',return_value=True),patch.object(api.trustid_client,'TRUSTID_API_KEY','synthetic'),TestClient(api.app) as c:
   staff={'X-Staff-Passcode':'qa-staff-secret'}
   for n,(res,route,fee) in enumerate([('uk','online',49),('overseas','online',119),('uk','in-person',125),('overseas','in-person',125)]):
    ref='QA-PROVIDER-'+str(n);url='/api/applications/'+ref;before=len(guests)
    payload={'case_ref':ref,'full_name':'Synthetic Applicant','first_name':'Synthetic','last_name':'Applicant','email':f'client{n}@example.invalid','residency':res,'route':route,'fee_amount':175,'role':'Director','dob':'1990-01-01','nationality':'British','residence_country':'United Kingdom' if res=='uk' else 'France','home_address':'1 Synthetic Road','address_since':'2020-01','mobile':'+447700900000','company_name':'Synthetic QA Ltd','company_number':'00000000','role_confirm':'Director','sign_name':'Synthetic Applicant','sign_date':'2026-09-06'}
    created=c.post('/api/applications',json=payload);self.assertEqual(created.status_code,201);self.assertEqual(created.json()['fee_amount'],fee)
    h={'X-Case-Token':created.json()['access_token']}
    self.assertEqual(c.post(url+'/verification',headers=staff).status_code,409,'Unpaid retry must be blocked')
    self.assertEqual(c.post(url+'/submitted',headers=h).status_code,200)
    checkout=c.post(url+'/payment',headers=h);self.assertEqual(checkout.status_code,200)
    sid=checkout.json()['checkout_id'];self.assertEqual(int(sessions[sid]['amount']),fee*100)
    self.assertEqual(c.get(url+'/payment-status',headers=h).json()['payment_status'],'pending');self.assertEqual(len(guests),before)
    sessions[sid].update(payment_status='paid',status='complete')
    event={'data':{'object':{'id':sid}}}
    with ThreadPoolExecutor(max_workers=3) as pool:
     results=list(pool.map(lambda _:c.post('/api/webhooks/stripe',json=event).status_code,range(3)))
    self.assertEqual(results,[200,200,200])
    self.assertEqual(c.get(url+'/payment-status',headers=h).json()['payment_status'],'paid')
    self.assertEqual(len(guests)-before,1 if route=='online' else 0)
    receipt=[m for m in mail if m['to'][0]['email']==payload['email']];self.assertEqual(len(receipt),1);self.assertIn(ref,receipt[0]['subject'])
    self.assertEqual(c.post(url+'/payment',headers=h).status_code,409)
    if route=='online':
     self.assertEqual(c.post(url+'/verification',headers=staff).status_code,200);self.assertEqual(len(guests)-before,1,'Already-sent retry must not duplicate')
     callback={'Callback':{'WorkflowName':'AutoReferral','WorkflowState':'Stop','Aborted':False,'WorkflowStorage':[{'Key':'ClientApplicationReference','Value':ref}]},'Response':{'ContainerId':'container-'+ref}}
     auth={'Authorization':guests[-1]['ContainerEventCallbackHeaders'][0]['Value']}
     self.assertEqual(c.post('/api/webhooks/trustid/'+ref,json=callback).status_code,403)
     self.assertTrue(c.post('/api/webhooks/trustid/'+ref,json=callback,headers=auth).json()['queued'])
     self.assertFalse(c.post('/api/webhooks/trustid/'+ref,json=callback,headers=auth).json()['queued'])
     api.deliver_trustid_notifications();self.assertEqual(api.get_application(ref)['verification_status'],'ready_for_review')
     self.assertEqual(len([m for m in mail if m['subject']=='TrustID result ready — Case '+ref]),1)
    else:
     self.assertEqual(c.post(url+'/verification',headers=staff).status_code,409)
     self.assertEqual(c.post(url+'/appointment',headers=h,json={'appointment_office':'bedford','appointment_date':'2026-12-01','appointment_time_pref':'Morning'}).status_code,200)
     self.assertEqual(len(guests),before)
    print(f'PASS provider journey {res}/{route}: £{fee}, actual Stripe/TrustID/Brevo adapters; dummy transport only')
   # Quote-only £175 service: save and notify, never silently create a payment.
   enquiry=c.post('/api/enquiries',json={'name':'Synthetic Enquiry','email':'complex@example.invalid','service':'complex','notes':'Request a complex-case quote from £175'})
   self.assertEqual(enquiry.status_code,201);self.assertIsNone(enquiry.json()['indicative_total']);self.assertTrue(enquiry.json()['notification_sent'])
   # Failed invitation is recoverable once, using the existing paid case.
   ref='QA-PROVIDER-0';api.db.execute("UPDATE applications SET verification_status='error' WHERE case_ref=?",[ref]);api.db.commit()
   controls['reject_guest']=True
   result=c.post('/api/applications/'+ref+'/verification',headers=staff);self.assertEqual(result.json()['verification_status'],'error')
   controls['reject_guest']=False
   result=c.post('/api/applications/'+ref+'/verification',headers=staff);self.assertEqual(result.json()['verification_status'],'link_sent')
   # Provider outage: staff message must remain pending and be retried.
   controls['email_fail']=True
   self.assertFalse(api.send_notification_email('QA outage',{'Case':'synthetic'}))
   self.assertEqual(api.db.execute("SELECT accepted FROM notification_outbox WHERE subject='QA outage'").fetchone()[0],0)
   controls['email_fail']=False
   api.deliver_pending_emails()
   self.assertTrue(api.send_notification_email('QA outage',{'Case':'synthetic'}))
   self.assertEqual(len([m for m in mail if m['subject']=='QA outage']),2,'One failed attempt, one accepted retry; no extra duplicate')
   api.send_notification_email('QA escaping',{'Name':'<img src=x onerror=alert(1)>'})
   self.assertNotIn('<img src=x',mail[-1]['htmlContent'])
   self.assertIn('&lt;img',mail[-1]['htmlContent'])
if __name__=='__main__':unittest.main()
