#!/usr/bin/env python3
"""Verify a deployed full stack. Creates three test objects and leaves them for inspection."""
import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def aws(*args):
    return subprocess.check_output(["aws", *args, "--output", "json"],
                                   env={**os.environ, "AWS_PAGER": ""})


def http(url, method="GET", headers=None, data=None):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=32) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="A JPEG or PNG with detectable subjects")
    parser.add_argument("--stack", default=os.getenv("APP_STACK", "photo-album-a3"))
    args = parser.parse_args()
    raw = args.image.read_bytes()
    require(0 < len(raw) <= 10 * 1024 * 1024, "Use a nonempty image no larger than 10 MB.")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        ext, mime = "png", "image/png"
    elif raw.startswith(b"\xff\xd8\xff"):
        ext, mime = "jpg", "image/jpeg"
    else:
        raise SystemExit("Use a real JPEG or PNG.")
    outputs = {item["OutputKey"]: item["OutputValue"] for item in json.loads(
        aws("cloudformation", "describe-stacks", "--stack-name", args.stack))["Stacks"][0]["Outputs"]}
    require("DomainEndpoint" in outputs, "Deploy in full mode before running this test.")
    api, origin, bucket = (outputs[key] for key in ("ApiUrl", "WebsiteUrl", "PhotoBucket"))
    key = json.loads(aws("apigateway", "get-api-key", "--api-key", outputs["ApiKeyId"],
                         "--include-value", "--query", "value"))
    headers = {"x-api-key": key, "Origin": origin}
    require(http(origin)[0] == 200 and http(origin.rstrip("/") + "/sdk/apigClient.js")[0] == 200,
            "The public website or generated SDK is missing.")
    for path, method in (("/photos", "PUT"), ("/search", "GET")):
        code, cors, _ = http(api + path, "OPTIONS", {
            "Origin": origin, "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type,x-api-key,x-amz-meta-customLabels",
        })
        require(code == 200 and cors.get("Access-Control-Allow-Origin") == origin
                and "x-amz-meta-customlabels" in cors.get("Access-Control-Allow-Headers", "").lower(),
                f"Preflight failed for {path}.")
    require(http(api + "/search?q=cat", headers={"Origin": origin})[0] == 403,
            "Search succeeded without an API key.")
    identifier = uuid.uuid4().hex[:10]
    first, second = f"A3first{identifier}", f"A3second{identifier}"
    keys = [f"a3-smoke-{identifier}-{i}.{ext}" for i in range(3)]
    put_url = api + "/photos?" + urllib.parse.urlencode({"key": keys[0]})
    require(http(put_url, "PUT", {"Origin": origin, "Content-Type": mime}, raw)[0] == 403,
            "Upload accepted a request without an API key.")
    for object_key, labels in zip(keys[:2], ([first, second], [])):
        upload_headers = {**headers, "Content-Type": mime}
        if labels:
            upload_headers["x-amz-meta-customLabels"] = ", ".join(labels)
        url = api + "/photos?" + urllib.parse.urlencode({"key": object_key})
        status, cors, _ = http(url, "PUT", upload_headers, raw)
        require(status == 200 and cors.get("Access-Control-Allow-Origin") == origin,
                f"Upload failed with HTTP {status}.")
        head = json.loads(aws("s3api", "head-object", "--bucket", bucket, "--key", object_key))
        require(head["ContentLength"] == len(raw) and head["ContentType"] == mime,
                "S3 length or MIME type differs from the uploaded image.")
        require(head["Metadata"].get("customlabels", "") == ", ".join(labels),
                "S3 custom metadata differs from the upload.")
    aws("s3api", "put-object", "--bucket", bucket, "--key", keys[2], "--body", str(args.image),
        "--content-type", mime, "--metadata", f"customlabels={first}")

    def search(query):
        code, cors, body = http(api + "/search?" + urllib.parse.urlencode({"q": query}), headers=headers)
        require(code == 200 and cors.get("Access-Control-Allow-Origin") == origin,
                f"Search failed with HTTP {code} for {query!r}.")
        return json.loads(body)["results"]

    def photo_key(photo):
        return urllib.parse.unquote(urllib.parse.urlsplit(photo["url"]).path).rsplit("/", 1)[-1]

    deadline = time.monotonic() + 180
    while True:
        photos = {photo_key(photo): photo for photo in search(f"show me photos with {first} and {second} in them")}
        if {keys[0], keys[2]} <= photos.keys():
            break
        require(time.monotonic() < deadline, "Custom labels still missing after 180 seconds. Check index-photos logs.")
        time.sleep(4)

    photo = photos[keys[0]]
    require({first, second} <= set(photo["labels"]), "Both custom labels must be in the result.")
    require(http(photo["url"])[2] == raw, "The returned photo bytes differ from the upload.")
    for label in (first, second):
        require(keys[0] in {photo_key(item) for item in search(label)}, f"Custom label {label} is not searchable.")
    automatic = set(photo["labels"]) - {first, second}
    require(automatic, "No Rekognition labels found. Use a clearer fixture.")
    detected = json.loads(aws("rekognition", "detect-labels", "--image",
                              json.dumps({"S3Object": {"Bucket": bucket, "Name": keys[0]}})))
    require({item["Name"].casefold() for item in detected["Labels"]} <= {
        label.casefold() for label in photo["labels"]}, "An automatic label is absent from the result.")
    for label in sorted(automatic):
        time.sleep(0.25)
        results = {photo_key(item) for item in search(f'"{label}"')}
        require(set(keys) <= results, f"Some matching photos are missing for label {label!r}.")
    require(search(f"unmatched{identifier}") == [] and search("") == [],
            "Empty or unmatched queries must return an empty results array.")
    print(f"Smoke passed for two API uploads, one direct S3 PUT, and {len(automatic)} automatic labels.")
    print("Test objects left for inspection:", ", ".join(keys))


if __name__ == "__main__":
    main()
