import json
import os
import urllib.error
import urllib.request
from functools import lru_cache

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config


@lru_cache
def session():
    return boto3.Session()


@lru_cache
def client(service):
    timeout = 4 if service == "lexv2-runtime" else 10
    options = {"connect_timeout": 2, "read_timeout": timeout, "retries": {"total_max_attempts": 2}}
    if service == "s3":
        options.update(signature_version="s3v4", s3={"us_east_1_regional_endpoint": "regional"})
    return session().client(service, config=Config(**options))


class SearchServiceError(Exception):
    def __init__(self, status, body):
        super().__init__(f"OpenSearch HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


def opensearch(method, path, body):
    url = f"https://{os.environ['SEARCH_HOST']}{path}"
    payload = json.dumps(body).encode()
    request = AWSRequest(method=method, url=url, data=payload,
                         headers={"Content-Type": "application/json"})
    credentials = session().get_credentials().get_frozen_credentials()
    SigV4Auth(credentials, "es", os.environ["AWS_REGION"]).add_auth(request)
    wire = urllib.request.Request(url, data=payload, headers=dict(request.headers), method=method)
    try:
        with urllib.request.urlopen(wire, timeout=7) as result:
            return json.load(result)
    except urllib.error.HTTPError as error:
        raise SearchServiceError(error.code, error.read().decode(errors="replace")) from error


def normalize(label):
    return " ".join(label.casefold().split())
