"""SDK contract checks without network or real credentials."""
import base64
import hashlib
import io
import unittest
from unittest.mock import patch
from botocore.stub import Stubber
from botocore.response import StreamingBody
from storage_providers import S3Storage, AzureStorage, StorageError


class SDKContractTests(unittest.TestCase):
    def test_s3_conditional_upload_read_delete_and_paginated_inventory(self):
        cloud = S3Storage({'region':'us-east-1','target':'documents'}, {'access_key_id':'test-key','secret_access_key':'test-secret'})
        data = b'document'
        stub = Stubber(cloud.client)
        stub.add_response('put_object', {'ETag':'"etag1"'}, {'Bucket':'documents','Key':'folder/doc.pdf','Body':data,
            'ContentMD5':base64.b64encode(hashlib.md5(data).digest()).decode(), 'ContentType':'application/pdf',
            'ContentDisposition':'inline','Metadata':{'idpn-sha256':hashlib.sha256(data).hexdigest()},'IfNoneMatch':'*'})
        stub.add_response('get_object', {'ETag':'"etag1"','Body':StreamingBody(io.BytesIO(data),len(data))},
                          {'Bucket':'documents','Key':'folder/doc.pdf','IfMatch':'"etag1"'})
        stub.add_response('delete_object', {}, {'Bucket':'documents','Key':'folder/doc.pdf','IfMatch':'"etag1"'})
        stub.add_response('list_objects_v2', {'IsTruncated':True,'NextContinuationToken':'second','Contents':[{'Key':'folder/a.pdf','ETag':'"a"','Size':5}]},
                          {'Bucket':'documents','Prefix':'folder/'})
        stub.add_response('list_objects_v2', {'IsTruncated':False,'Contents':[{'Key':'folder/b.pdf','ETag':'"b"','Size':6}]},
                          {'Bucket':'documents','Prefix':'folder/','ContinuationToken':'second'})
        with stub:
            cloud.put('folder/doc.pdf',data,metadata={'idpn-sha256':hashlib.sha256(data).hexdigest()},mime='application/pdf')
            self.assertEqual(cloud.get('folder/doc.pdf','"etag1"')[0],data)
            cloud.delete('folder/doc.pdf','"etag1"')
            self.assertEqual(len(cloud.inventory('folder/')),2)
            stub.assert_no_pending_responses()

    def test_s3_precondition_error_is_preserved_as_conflict(self):
        cloud = S3Storage({'region':'us-east-1','target':'documents'}, {'access_key_id':'test-key','secret_access_key':'test-secret'})
        with Stubber(cloud.client) as stub:
            stub.add_client_error('delete_object', service_error_code='PreconditionFailed', http_status_code=412,
                                  expected_params={'Bucket':'documents','Key':'file.pdf','IfMatch':'old-etag'})
            with self.assertRaises(StorageError) as error:
                cloud.delete('file.pdf','old-etag')
            self.assertEqual(error.exception.status,412)

    def test_azure_sdk_generates_no_overwrite_and_etag_conditions(self):
        cloud = AzureStorage({'account':'companyaccount','target':'documents'}, {'account_key':'YQ=='})
        blob = cloud.blob('folder/doc.pdf')
        with patch.object(cloud,'blob',return_value=blob), patch.object(blob._client.block_blob,'upload',return_value={'etag':'new-etag'}) as upload:
            cloud.put('folder/doc.pdf',b'doc',metadata={'idpn-sha256':'a'*64},mime='application/pdf')
            params = upload.call_args.kwargs
            self.assertEqual(params['modified_access_conditions'].if_none_match,'*')
            self.assertEqual(params['headers']['x-ms-meta-idpn_sha256'],'a'*64)
            cloud.put('folder/doc.pdf',b'manifest',etag='previous-etag')
            self.assertEqual(upload.call_args.kwargs['modified_access_conditions'].if_match,'previous-etag')
        with patch.object(cloud,'blob',return_value=blob), patch.object(blob._client.blob,'delete',return_value=None) as delete:
            cloud.delete('folder/doc.pdf','original-etag')
            self.assertEqual(delete.call_args.kwargs['modified_access_conditions'].if_match,'original-etag')


if __name__ == '__main__': unittest.main()
