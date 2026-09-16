import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
os.environ['DB_PATH']=str(Path(tempfile.mkdtemp())/'enquiry.db')
os.environ['STAFF_PASSCODE']='synthetic-staff'
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
import api_server as api
class Enquiries(unittest.TestCase):
 def test_saved_even_when_email_unavailable(self):
  with patch.object(api,'send_notification_email',return_value=False) as email,TestClient(api.app) as c:
   data={'name':'Synthetic Test','email':'qa@example.invalid','service':'general','notes':'Synthetic enquiry'}
   r=c.post('/api/enquiries',json=data);self.assertEqual(r.status_code,201);self.assertFalse(r.json()['notification_sent']);email.assert_called_once()
   self.assertEqual(c.get('/api/staff/enquiries').status_code,403)
   saved=c.get('/api/staff/enquiries',headers={'X-Staff-Passcode':'synthetic-staff'}).json()
   self.assertTrue(any(x['reference']==r.json()['reference'] for x in saved))
   self.assertEqual(c.post('/api/enquiries',json={**data,'website':'spam'}).status_code,400)
   self.assertEqual(c.post('/api/enquiries',json={**data,'email':'invalid'}).status_code,400)
   self.assertEqual(c.post('/api/enquiries',json={**data,'service':'bulk','uk_count':9}).status_code,400)
if __name__=='__main__':unittest.main()
