"""Authenticated completion callback, durable notification and duplicate protection."""
import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
os.environ['DB_PATH']=str(Path(tempfile.mkdtemp())/'callback.db')
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
import api_server as api
class CallbackTests(unittest.TestCase):
 def test_completion(self):
  with patch.object(api.trustid_client,'TRUSTID_API_KEY','synthetic-key'),patch.object(api,'send_notification_email',return_value=True) as mail,TestClient(api.app) as c:
   ref='CALLBACK-TEST';c.post('/api/applications',json={'case_ref':ref,'full_name':'Test','email':'qa@example.invalid','route':'online','residency':'uk','fee_amount':49})
   url='/api/webhooks/trustid/'+ref;body={'Callback':{'WorkflowName':'AutoReferral','WorkflowState':'Stop','Aborted':False,'WorkflowStorage':[{'Key':'ClientApplicationReference','Value':ref}]},'Response':{'ContainerId':'synthetic-container'}}
   headers={'Authorization':api.trustid_client.callback_token(ref)}
   self.assertEqual(c.post(url,json=body).status_code,403)
   self.assertEqual(c.post(url,json=body,headers={'Authorization':'wrong'}).status_code,403)
   self.assertEqual(c.post(url,json=body,headers={'Authorization':api.trustid_client.callback_token('OTHER-CASE')}).status_code,403)
   self.assertEqual(c.post(url,json=body,headers=headers).status_code,409)
   api.db.execute("UPDATE applications SET payment_status='paid' WHERE case_ref=?",[ref]);api.db.commit()
   self.assertTrue(c.post(url,json=body,headers=headers).json()['queued'])
   self.assertFalse(c.post(url,json=body,headers=headers).json()['queued'])
   self.assertEqual(api.get_application(ref)['verification_status'],'ready_for_review')
   mail.return_value=False;api.deliver_trustid_notifications()
   self.assertEqual(api.db.execute('SELECT sent FROM trustid_result_notifications').fetchone()[0],0)
   mail.return_value=True;api.deliver_trustid_notifications();mail.reset_mock()
   api.deliver_trustid_notifications();mail.assert_not_called()
 def test_guest_link_callback_headers(self):
  client=api.trustid_client;reply=Mock();reply.json.return_value={'Success':True,'GuestLinkUrl':'https://example.invalid/guest'}
  with patch.object(client,'is_configured',return_value=True),patch.object(client,'TRUSTID_API_KEY','synthetic-key'),patch.object(client,'_login',return_value={'Success':True,'SessionId':'fake'}),patch.object(client.requests,'post',return_value=reply) as post:
   client.create_guest_link('Synthetic','Applicant','qa@example.invalid','CASE-ONE')
   payload=post.call_args.kwargs['json']
   self.assertEqual(payload['ContainerEventCallbackHeaders'][0]['Header'],'Authorization')
   self.assertTrue(payload['SendEmail']);self.assertTrue(payload['ContainerEventCallbackUrl'].endswith('/CASE-ONE'))
   self.assertEqual(payload['ContainerEventCallbackHeaders'][0]['Value'],client.callback_token('CASE-ONE'))
if __name__=='__main__':unittest.main()
