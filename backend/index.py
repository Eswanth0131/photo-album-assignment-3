import hashlib
import logging
from urllib.parse import unquote_plus

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

MAPPING = {
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "bucket": {"type": "keyword"},
            "objectKey": {"type": "keyword"},
            "createdTimestamp": {"type": "date"},
            "labels": {"type": "keyword"},
            "labelKeys": {"type": "keyword"},
            "versionId": {"type": "keyword"},
            "contentType": {"type": "keyword"},
            "sequencer": {"type": "keyword"},
        },
    }
}

UPDATE_SCRIPT = """
String incoming = params.photo.sequencer;
String current = ctx._source.sequencer;
if (current == null || incoming.length() > current.length() ||
    (incoming.length() == current.length() && incoming.compareTo(current) > 0)) {
    ctx._source = params.photo;
} else {
    ctx.op = 'noop';
}
"""


@lru_cache(maxsize=1)
def ensure_index():
    try:
        opensearch("PUT", "/photos", MAPPING)
    except SearchServiceError as error:
        if error.status != 400 or "resource_already_exists_exception" not in error.body:
            raise


def index_record(record):
    if record.get("eventName") != "ObjectCreated:Put":
        return
    bucket = record["s3"]["bucket"]["name"]
    if bucket != os.environ["PHOTO_BUCKET"]:
        raise ValueError("Unexpected source bucket")
    obj = record["s3"]["object"]
    key = unquote_plus(obj["key"])
    version = obj.get("versionId")
    location = {"Bucket": bucket, "Key": key}
    image = {"Bucket": bucket, "Name": key}
    if version and version != "null":
        location["VersionId"] = version
        image["Version"] = version
    head = client("s3").head_object(**location)
    if head["ContentLength"] > 10 * 1024 * 1024:
        LOG.warning("Skipping oversized S3 object %r", key)
        return
    try:
        detected = client("rekognition").detect_labels(Image={"S3Object": image})
    except client("rekognition").exceptions.InvalidImageFormatException:
        LOG.warning("Skipping unsupported S3 image %r", key)
        return
    labels = {}
    metadata = {name.lower(): value for name, value in head.get("Metadata", {}).items()}
    for label in metadata.get("customlabels", "").split(",") + [
        item["Name"] for item in detected["Labels"]
    ]:
        label = " ".join(label.split())
        if label:
            labels.setdefault(normalize(label), label)
    photo = {
        "bucket": bucket,
        "objectKey": key,
        "createdTimestamp": record["eventTime"],
        "labels": list(labels.values()),
        "labelKeys": list(labels),
        "sequencer": format(int(obj["sequencer"], 16), "x"),
        "contentType": "image/png" if head.get("ContentType") == "image/png" else "image/jpeg",
    }
    if "VersionId" in location:
        photo["versionId"] = version
    doc_id = hashlib.sha256(f"{bucket}/{key}".encode()).hexdigest()
    update = {"script": {"source": UPDATE_SCRIPT, "params": {"photo": photo}}, "upsert": photo}
    path = f"/photos/_update/{doc_id}"
    ensure_index()
    try:
        result = opensearch("POST", path, update)
    except SearchServiceError as error:
        if error.status != 404 or "index_not_found_exception" not in error.body:
            raise
        ensure_index.cache_clear()
        ensure_index()
        result = opensearch("POST", path, update)
    LOG.info("Photo %r: %s (%d labels)", key, result.get("result"), len(labels))


def handler(event, context):
    for record in event.get("Records", []):
        index_record(record)
