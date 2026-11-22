import logging
import re
import time
from uuid import uuid4

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

PREFIX = re.compile(
    r"^(?:please\s+)?(?:can you\s+|could you\s+|i want to see\s+)?"
    r"(?:(?:show|find|search|display|give|get|look)(?:\s+me)?"
    r"(?:\s+for)?\s+)?(?:(?:photos?|pictures?|images?)\s+(?:with|of|containing)\s+)?"
)
SUFFIX = re.compile(r"(?:\s+(?:in (?:them|it)|please))+$")
EMPTY = {"", "please", "show me", "find", "search", "show me photos", "show me pictures",
         "show me images", "photos with", "pictures with", "images with"}
IRREGULAR = [("person", "people"), ("child", "children"), ("man", "men"),
             ("woman", "women"), ("mouse", "mice"), ("goose", "geese"),
             ("foot", "feet"), ("tooth", "teeth"), ("leaf", "leaves"),
             ("wolf", "wolves"), ("bus", "buses"), ("gas", "gases"),
             ("axis", "axes"), ("crisis", "crises")]


def phrases(text):
    text = normalize(text).strip(" .?!")
    if text in EMPTY:
        return []
    # Quotes preserve a literal label, including words such as "and".
    if len(text) >= 2 and text[0] == text[-1] == '"' and '"' not in text[1:-1]:
        return [text[1:-1].strip()] if text[1:-1].strip() else []
    stripped = SUFFIX.sub("", PREFIX.sub("", text)).strip()
    parts = re.split(r"\s+(?:and|or)\s+|\s*,\s*", stripped)
    labels = [part.strip(' "') for part in parts if re.search(r"\w", part)]
    return list(dict.fromkeys([stripped.strip(' "'), *labels])) if labels else []


def variants(phrase):
    article, _, rest = phrase.partition(" ")
    if article in {"a", "an", "the"} and rest:
        return {phrase} | variants(rest)
    prefix, space, word = phrase.rpartition(" ")
    forms = {word}
    if not word.isalpha():
        return {phrase}
    special = next((pair for pair in IRREGULAR if word in pair), None)
    if special:
        forms.update(special)
    elif word.endswith("ies") and len(word) > 3:
        forms.add(word[:-3] + "y")
    elif word.endswith(("ches", "shes", "sses", "xes", "zzes", "oes")):
        forms.add(word[:-2])
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 2:
        forms.add(word[:-1])
    elif word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        forms.add(word[:-1] + "ies")
    elif word.endswith(("ch", "sh", "ss", "x", "zz", "o")):
        forms.add(word + "es")
    else:
        forms.add(word + "s")
    return {prefix + space + form for form in forms}


def lex_keywords(query):
    runtime = client("lexv2-runtime")
    config = {
        "botId": os.environ["BOT_ID"], "botAliasId": os.environ["BOT_ALIAS"],
        "localeId": "en_US", "sessionId": uuid4().hex, "text": query,
    }
    response = runtime.recognize_text(**config)
    intent = response.get("sessionState", {}).get("intent", {})
    slots = intent.get("slots") or {}
    keywords = []
    if intent.get("name") == "SearchIntent":
        for slot in ("KeywordOne", "KeywordTwo"):
            value = (slots.get(slot) or {}).get("value", {})
            keywords.extend(phrases(value.get("originalValue") or value.get("interpretedValue") or ""))
    expected = phrases(query)
    required = expected[1:] if len(expected) > 1 else expected
    if required and set(required).issubset(keywords):
        return expected

    # Elicit Lex's free-form slot explicitly when its trained slots miss text.
    # This keeps arbitrary Rekognition and custom labels searchable without retraining.
    response = runtime.recognize_text(**config, sessionState={
        "dialogAction": {"type": "ElicitSlot", "slotToElicit": "SearchText"},
        "intent": {"name": "SearchIntent", "state": "InProgress", "slots": {}},
    })
    captured = (response.get("sessionState", {}).get("intent", {}).get("slots") or {})
    value = (captured.get("SearchText") or {}).get("value", {})
    return phrases(value.get("originalValue") or value.get("interpretedValue") or "")


def matched_photos(terms, deadline):
    scroll_id = None
    try:
        try:
            page = opensearch("GET", "/photos/_search?scroll=30s", {
                "size": 250, "sort": ["_doc"], "_source": {"excludes": ["labelKeys", "sequencer"]},
                "query": {"terms": {"labelKeys": sorted(terms)}},
            })
        except SearchServiceError as error:
            if error.status == 404 and "index_not_found_exception" in error.body:
                return
            raise
        while True:
            scroll_id = page.get("_scroll_id") or scroll_id
            if page.get("timed_out") or page.get("_shards", {}).get("failed"):
                raise RuntimeError("OpenSearch did not complete all shards")
            hits = page["hits"]["hits"]
            if not hits:
                break
            for hit in hits:
                yield hit["_source"]
            if time.monotonic() >= deadline:
                raise RuntimeError("Search exceeded the API time budget")
            if not scroll_id:
                raise RuntimeError("OpenSearch did not return a scroll cursor")
            page = opensearch("POST", "/_search/scroll", {"scroll": "30s", "scroll_id": scroll_id})
    finally:
        if scroll_id:
            try:
                opensearch("DELETE", "/_search/scroll", {"scroll_id": [scroll_id]})
            except Exception:
                LOG.warning("Could not clear OpenSearch scroll", exc_info=True)


def signed_photo(photo):
    params = {"Bucket": photo["bucket"], "Key": photo["objectKey"],
              "ResponseContentType": photo["contentType"]}
    if photo.get("versionId"):
        params["VersionId"] = photo["versionId"]
    url = client("s3").generate_presigned_url("get_object", Params=params, ExpiresIn=900)
    return {"url": url, "labels": photo["labels"]}


def reply(status, payload):
    return {"statusCode": status, "headers": {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": os.environ["SITE_ORIGIN"],
        "Cache-Control": "no-store",
    }, "body": json.dumps(payload, separators=(",", ":"))}


def handler(event, context):
    deadline = time.monotonic() + 23
    query = ((event.get("queryStringParameters") or {}).get("q") or "").strip()
    if len(query) > 256:
        return reply(400, {"code": 400, "message": "Search text must be 256 characters or fewer."})
    if not query:
        return reply(200, {"results": []})
    try:
        terms = {term for phrase in lex_keywords(query) for term in variants(phrase)}
        results = []
        size = 20
        if terms:
            for photo in matched_photos(terms, deadline):
                result = signed_photo(photo)
                size += len(json.dumps(result, separators=(",", ":"))) + 1
                if size > 4_000_000 or time.monotonic() >= deadline:
                    return reply(413, {"code": 413, "message": "Too many matches. Use a narrower query."})
                results.append(result)
        return reply(200, {"results": results})
    except Exception:
        LOG.exception("Photo search failed")
        return reply(500, {"code": 500, "message": "Photo search is temporarily unavailable."})
