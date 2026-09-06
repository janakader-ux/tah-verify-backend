"""Isolated backend workflow: no real payments, email, identity checks or customer data."""
import os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
os.environ['DB_PATH'] = str(Path(tempfile.mkdtemp()) / 'workflow.db')
os.environ['STAFF_PASSCODE'] = 'test-only-passcode'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
import api_server as api

class WorkflowTests(unittest.TestCase):
    def test_online_and_appointment_journeys(self):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('Live network forbidden')), patch.object(api, 'STRIPE_TOKEN', 'test-only'), patch.object(api, 'send_notification_email', return_value=True) as staff_mail, patch.object(api, 'send_payment_confirmation_email') as receipt, patch.object(api.trustid_client, 'create_guest_link', return_value={'status':'link_sent','notes':'mock link'}) as identity, patch.object(api, 'create_stripe_checkout', return_value=('cs_test_local','https://checkout.stripe.com/test-only')) as checkout, patch.object(api, 'stripe_request', return_value={'payment_status':'paid','status':'complete'}), TestClient(api.app) as client:
            payload = {'case_ref':'TEST-ONLINE','full_name':'Test Applicant','email':'test@example.invalid','residency':'uk','route':'online','fee_amount':1,'sign_name':'Test Applicant','sign_date':'2026-09-06'}
            response=client.post('/api/applications', json=payload)
            self.assertEqual(response.status_code,201)
            headers={'X-Case-Token':response.json()['access_token']}
            self.assertEqual(response.json()['fee_amount'],49)
            self.assertEqual(client.post('/api/applications',json=payload).status_code,403)
            self.assertEqual(client.post('/api/applications',json=payload,headers=headers).status_code,201)
            self.assertEqual(client.get('/api/applications').status_code,403)
            self.assertEqual(client.post('/api/applications/TEST-ONLINE/submitted',headers=headers).status_code,200)
            url='/api/applications/TEST-ONLINE/payment'
            self.assertEqual(client.post(url).status_code,403)
            self.assertEqual(client.post(url,headers=headers).status_code,200)
            self.assertEqual(client.post(url,headers=headers).status_code,200)
            self.assertEqual(checkout.call_count,1, 'Repeated checkout must reuse pending session')
            self.assertEqual(client.post('/api/applications',json=payload,headers=headers).status_code,409)
            status='/api/applications/TEST-ONLINE/payment-status'
            self.assertEqual(client.get(status,headers=headers).json()['payment_status'],'paid')
            self.assertEqual(client.get(status,headers=headers).json()['payment_status'],'paid')
            self.assertEqual(identity.call_count,1)
            self.assertEqual(receipt.call_count,1)
            self.assertEqual(client.post(url,headers=headers).status_code,409)
            self.assertEqual(api.get_application('TEST-ONLINE')['verification_status'],'link_sent')
            payload.update(case_ref='TEST-OFFICE',route='in-person')
            response=client.post('/api/applications',json=payload)
            self.assertEqual(response.json()['fee_amount'],125)
            office_headers={'X-Case-Token':response.json()['access_token']}
            appointment='/api/applications/TEST-OFFICE/appointment'
            body={'appointment_office':'bedford','appointment_date':'2026-12-01','appointment_time_pref':'Morning'}
            self.assertEqual(client.post(appointment,json=body).status_code,403)
            self.assertEqual(client.post(appointment,json=body,headers=office_headers).status_code,409)
            api.db.execute("UPDATE applications SET payment_status='paid' WHERE case_ref='TEST-OFFICE'");api.db.commit()
            self.assertEqual(client.post(appointment,json=body,headers=office_headers).status_code,200)
            self.assertEqual(api.get_application('TEST-OFFICE')['appointment_office'],'bedford')
            self.assertGreaterEqual(staff_mail.call_count,3)
            self.assertEqual(client.get('/api/applications',headers={'X-Staff-Passcode':'test-only-passcode'}).status_code,200)

if __name__=='__main__': unittest.main()
