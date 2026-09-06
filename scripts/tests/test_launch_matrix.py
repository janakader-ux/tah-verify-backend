"""All four categories through real API routes and dummy external providers."""
import os,sys,tempfile,unittest,io,base64
from pathlib import Path
from datetime import date,timedelta
from unittest.mock import patch
from cryptography.fernet import Fernet
os.environ['DB_PATH']=str(Path(tempfile.mkdtemp())/'launch.db')
os.environ['STAFF_PASSCODE']='synthetic-staff'
os.environ['DPC_DOCUMENT_REVIEW_KEY']=Fernet.generate_key().decode()
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from PIL import Image
from fastapi.testclient import TestClient
import api_server as api

def photo():
 b=io.BytesIO();Image.new('RGB',(600,450),'white').save(b,'JPEG');return {'data':base64.b64encode(b.getvalue()).decode()}

class LaunchMatrix(unittest.TestCase):
 def test_four_categories(self):
  staff={'X-Staff-Passcode':'synthetic-staff'}
  with patch('requests.sessions.Session.request',side_effect=AssertionError('Live network forbidden')),patch.object(api,'STRIPE_TOKEN','test-only'),patch.object(api,'send_notification_email',return_value=True),patch.object(api,'send_payment_confirmation_email') as receipt,patch.object(api.trustid_client,'create_guest_link',return_value={'status':'link_sent','notes':'Synthetic provider result'}) as identity,patch.object(api,'create_stripe_checkout') as checkout,patch.object(api,'stripe_request') as provider,TestClient(api.app) as c:
   for residency,route,fee in [('uk','online',49),('overseas','online',119),('uk','in-person',125),('overseas','in-person',125)]:
    with self.subTest(residency=residency,route=route):
     identity.reset_mock();receipt.reset_mock();checkout.reset_mock()
     ref=f'LAUNCH-{residency}-{route}';url='/api/applications/'+ref
     payload={'case_ref':ref,'full_name':'Synthetic Applicant','first_name':'Synthetic','last_name':'Applicant','dob':'1990-01-01','email':'qa@example.invalid','residency':residency,'route':route,'fee_amount':1,'payment_status':'paid','role':'Director','company_name':'Synthetic QA Ltd','company_number':'00000000','sign_name':'Synthetic signature','sign_date':'2026-09-06'}
     created=c.post('/api/applications',json=payload);self.assertEqual(created.status_code,201)
     h={'X-Case-Token':created.json()['access_token']};self.assertEqual(created.json()['fee_amount'],fee);self.assertNotEqual(created.json()['payment_status'],'paid')
     self.assertEqual(c.post(url+'/payment',headers=h).status_code,409)
     body={'appointment_office':'bedford','appointment_date':str(date.today()+timedelta(days=7)),'appointment_time_pref':'Morning (9:30–12:30)'}
     self.assertEqual(c.post(url+'/appointment',headers=h,json=body).status_code,409)
     upload=c.post(url+'/documents',headers=h,json={'consent':True,'use_ai':False,'files':[photo()]*(2 if route=='in-person' else 1)})
     self.assertEqual(upload.status_code,200);self.assertEqual(upload.json()['status'],'manual')
     self.assertEqual(c.post(url+'/payment',headers=h).status_code,409);checkout.assert_not_called();identity.assert_not_called()
     decision=c.post('/api/staff/applications/'+ref+'/document-review',headers=staff,json={'version':upload.json()['version'],'approved':True,'note':'Synthetic preliminary review completed'})
     self.assertEqual(decision.status_code,200);self.assertEqual(decision.json()['status'],'ready')
     self.assertEqual(c.post(url+'/submitted',headers=h).status_code,200)
     checkout.return_value=('cs_dummy_'+ref,'https://checkout.stripe.com/test-only')
     payment=c.post(url+'/payment',headers=h);self.assertEqual(payment.status_code,200);self.assertEqual(checkout.call_args.args[1]['fee_amount'],fee)
     self.assertEqual(c.post(url+'/payment',headers=h).json()['checkout_id'],payment.json()['checkout_id']);checkout.assert_called_once()
     # An unpaid/declined card must not mark the application paid or start TrustID.
     provider.return_value={'payment_status':'unpaid','status':'open'}
     self.assertEqual(c.get(url+'/payment-status',headers=h).json()['payment_status'],'pending');identity.assert_not_called();receipt.assert_not_called()
     # Expired checkouts can be replaced only while document approval is valid.
     provider.return_value={'payment_status':'unpaid','status':'expired'}
     self.assertEqual(c.get(url+'/payment-status',headers=h).json()['payment_status'],'expired')
     checkout.return_value=('cs_dummy_retry_'+ref,'https://checkout.stripe.com/test-only-retry')
     self.assertEqual(c.post(url+'/payment',headers=h).status_code,200)
     provider.return_value={'payment_status':'paid','status':'complete'}
     for _ in range(2):self.assertEqual(c.get(url+'/payment-status',headers=h).json()['payment_status'],'paid')
     receipt.assert_called_once();self.assertEqual(identity.call_count,1 if route=='online' else 0)
     self.assertEqual(c.post(url+'/payment',headers=h).status_code,409,'Never create a second checkout after payment')
     if route=='in-person':
      self.assertEqual(c.post(url+'/appointment',headers=h,json={**body,'appointment_office':'invalid'}).status_code,400)
      self.assertEqual(c.post(url+'/appointment',headers=h,json=body).status_code,200)
     else:self.assertEqual(c.post(url+'/appointment',headers=h,json=body).status_code,409)
     listing=c.get('/api/applications',headers=staff).json()
     rows=listing if isinstance(listing,list) else listing['applications']
     row=next(r for r in rows if r['case_ref']==ref)
     self.assertEqual(row['payment_status'],'paid');self.assertEqual(row['fee_amount'],fee);self.assertTrue(row['submitted'])
     if route=='online':self.assertEqual(row['verification_status'],'link_sent')
     else:self.assertEqual(row['appointment_office'],'bedford')
     print(f'PASS {residency}/{route}: £{fee}, review → unpaid/expired → paid → '+('TrustID' if route=='online' else 'office request')+'; staff paid status confirmed')

if __name__=='__main__':unittest.main()
