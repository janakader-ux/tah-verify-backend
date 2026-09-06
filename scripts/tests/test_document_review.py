"""Pre-payment gate, upload isolation, provider fallback and staff review regression."""
import os,sys,tempfile,io,base64,json,time,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from cryptography.fernet import Fernet
from PIL import Image
os.environ['DB_PATH']=str(Path(tempfile.mkdtemp())/'review.db')
os.environ['STAFF_PASSCODE']='test-staff'
os.environ['DPC_DOCUMENT_REVIEW_KEY']=Fernet.generate_key().decode()
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import api_server as api
import document_review as review
from fastapi.testclient import TestClient

def photograph():
    b=io.BytesIO();Image.new('RGB',(600,450),'white').save(b,'JPEG')
    return {'data':base64.b64encode(b.getvalue()).decode()}

class ReviewTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        api.db=sqlite3.connect(api.DB_PATH,check_same_thread=False)

    def test_gate_and_review_lifecycle(self):
        with patch.object(api,'send_notification_email',return_value=True),patch.object(review,'ai_configured',return_value=False),patch.object(api,'STRIPE_TOKEN','test-only'),patch.object(api,'create_stripe_checkout',return_value=('cs_test_review','https://checkout.stripe.com/test')) as checkout,TestClient(api.app) as c:
            payload={'case_ref':'REVIEW-1','full_name':'Test Person','dob':'1980-01-01','email':'test@example.invalid','residency':'overseas','route':'online','fee_amount':1}
            created=c.post('/api/applications',json=payload).json();self.assertEqual(created['fee_amount'],119)
            h={'X-Case-Token':created['access_token']};staff={'X-Staff-Passcode':'test-staff'}
            payment='/api/applications/REVIEW-1/payment';upload='/api/applications/REVIEW-1/documents';status='/api/applications/REVIEW-1/document-review'
            self.assertEqual(c.post(payment,headers=h).status_code,409);checkout.assert_not_called()
            self.assertEqual(c.post(upload,json={'files':[photograph()],'consent':True}).status_code,403)
            self.assertEqual(c.post(upload,headers=h,json={'files':[photograph()],'consent':False}).status_code,400)
            r=c.post(upload,headers=h,json={'files':[{'data':'not-an-image'}],'consent':True});self.assertEqual(r.status_code,400)
            r=c.post(upload,headers=h,json={'files':[photograph()],'consent':True,'use_ai':True});self.assertEqual(r.status_code,200);self.assertEqual(r.json()['status'],'manual')
            self.assertEqual(c.post(payment,headers=h).status_code,409)
            version=r.json()['version']
            private='/api/staff/applications/REVIEW-1/documents'
            self.assertEqual(c.get(private,headers=h).status_code,403)
            r=c.get(private,headers=staff);self.assertEqual(len(r.json()['images']),1);self.assertIn('no-store',r.headers['cache-control'])
            with review.connect(api.DB_PATH) as db:
                encrypted=db.execute('SELECT encrypted_images FROM document_reviews').fetchone()[0]
                self.assertNotIn(photograph()['data'].encode()[:50],encrypted)
            decision='/api/staff/applications/REVIEW-1/document-review'
            self.assertEqual(c.post(decision,headers=staff,json={'version':'stale','approved':True,'note':'Checked the proposed passport'}).status_code,409)
            self.assertEqual(c.post(decision,headers=staff,json={'version':version,'approved':True,'note':'Checked the proposed passport'}).status_code,200)
            self.assertEqual(c.get(status,headers=h).json()['status'],'ready')
            payload['dob']='1981-01-01';self.assertEqual(c.post('/api/applications',headers=h,json=payload).status_code,201)
            self.assertEqual(c.post(payment,headers=h).status_code,409,'Changing identity details invalidates review')
            r=c.post(upload,headers=h,json={'files':[photograph()],'consent':True});version=r.json()['version']
            c.post(decision,headers=staff,json={'version':version,'approved':True,'note':'Rechecked with corrected details'})
            api.db.execute("UPDATE applications SET submitted=1, sign_name='Test Person', sign_date='2026-09-06' WHERE case_ref='REVIEW-1'");api.db.commit()
            self.assertEqual(c.post(payment,headers=h).status_code,200);checkout.assert_called_once()
            self.assertEqual(c.post(upload,headers=h,json={'files':[photograph()],'consent':True}).status_code,409)
            with review.connect(api.DB_PATH) as db:db.execute('UPDATE document_reviews SET expires=?',(time.time()-1,))
            self.assertEqual(c.get(status,headers=h).json()['status'],'required')

    def test_ai_is_conservative_and_manual_choice_never_calls_provider(self):
        record={'case_ref':'AI-TEST','full_name':'Test Person','dob':'1980-01-01','route':'online','residency':'uk','fee_amount':49}
        good={'documents':[{'type':'passport','country':'GB','expiry':'2035-01-01','readable':True,'whole_document':True,'name_matches':True,'dob_matches':True,'machine_readable':True,'cancelled_or_replaced':False,'uncertain':False}]}
        response=Mock();response.json.return_value={'choices':[{'message':{'content':json.dumps(good)}}]}
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test-only'}),patch.object(review.requests,'post',return_value=response) as provider:
            images=review.normalise([photograph()]);self.assertEqual(review.assess(images,record)[0],'ready')
            request=provider.call_args.kwargs['json'];self.assertFalse(request['store'])
            good['documents'][0]['uncertain']='false';response.json.return_value={'choices':[{'message':{'content':json.dumps(good)}}]}
            self.assertEqual(review.assess(images,record)[0],'manual')
            provider.reset_mock();review.save(api.DB_PATH,record,[photograph()],True,False);provider.assert_not_called()
            provider.side_effect=TimeoutError();self.assertEqual(review.assess(images,record)[0],'manual')

    def test_bulk_minimum_and_server_calculation(self):
        # Separate DB connection after lifespan closure from the preceding test.
        with patch.object(api,'send_notification_email',return_value=True),TestClient(api.app) as c:
            body={'name':'Test Buyer','email':'buyer@example.invalid','service':'bulk','uk_count':5,'overseas_count':4}
            self.assertEqual(c.post('/api/enquiries',json=body).status_code,400)
            body['overseas_count']=5;body['indicative_total']=1
            r=c.post('/api/enquiries',json=body);self.assertEqual(r.status_code,201);self.assertEqual(r.json()['indicative_total'],620)

if __name__=='__main__':unittest.main()
