import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from infra.generate import application, lex_locale, pipelines, source


def runtime(name):
    namespace = {}
    exec(compile(source(name), name + ".py", "exec"), namespace)
    return namespace


def lex_response(**slots):
    return {"sessionState": {"intent": {"name": "SearchIntent", "slots": {
        name: {"value": {"originalValue": label}} for name, label in slots.items()
    }}}}


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime("index")
        self.record = {
            "eventName": "ObjectCreated:Put", "eventTime": "2026-11-20T00:00:00Z",
            "s3": {"bucket": {"name": "album-photos"},
                   "object": {"key": "test+photo%2B.png", "versionId": "version", "sequencer": "000AF"}},
        }
        self.s3 = Mock()
        self.rekognition = Mock()
        self.rekognition.exceptions = SimpleNamespace(InvalidImageFormatException=ValueError)
        self.s3.head_object.return_value = {
            "Metadata": {"customlabels": " Sam, SALLY, , CAT "},
            "ContentType": "image/png", "ContentLength": 300,
        }
        self.rekognition.detect_labels.return_value = {"Labels": [
            {"Name": label} for label in (
                "Cat", "Animal", "Pet", "Mammal", "Paw", "Whiskers", "Eye",
                "Fur", "Tabby", "Portrait", "Head", "Nose",
            )
        ]}
        self.search = Mock(return_value={"result": "created"})
        self.runtime.update(client={"s3": self.s3, "rekognition": self.rekognition}.__getitem__,
                            opensearch=self.search)

    @patch.dict(os.environ, {"PHOTO_BUCKET": "album-photos"})
    def test_all_labels_and_metadata_and_object_version(self):
        self.runtime["handler"]({"Records": [self.record]}, None)
        self.s3.head_object.assert_called_once_with(Bucket="album-photos", Key="test photo+.png",
                                                    VersionId="version")
        self.rekognition.detect_labels.assert_called_once_with(Image={"S3Object": {
            "Bucket": "album-photos", "Name": "test photo+.png", "Version": "version",
        }})
        put, update = [call.args for call in self.search.call_args_list]
        self.assertEqual(put[:2], ("PUT", "/photos"))
        self.assertEqual(update[0], "POST")
        self.assertRegex(update[1], r"^/photos/_update/[a-f0-9]{64}$")
        payload = update[2]["upsert"]
        self.assertEqual(payload, update[2]["script"]["params"]["photo"])
        self.assertEqual(payload["labels"][:3], ["Sam", "SALLY", "CAT"])
        self.assertEqual(len(payload["labels"]), 14)
        self.assertIn("nose", payload["labelKeys"])
        self.assertEqual(payload["sequencer"], "af")
        self.assertEqual(payload["createdTimestamp"], self.record["eventTime"])

    @patch.dict(os.environ, {"PHOTO_BUCKET": "album-photos"})
    def test_upload_without_custom_labels_and_index_exists(self):
        self.s3.head_object.return_value["Metadata"] = {}
        error = self.runtime["SearchServiceError"](400, "resource_already_exists_exception")
        self.search.side_effect = [error, {"result": "updated"}]
        self.runtime["handler"]({"Records": [self.record]}, None)
        self.assertEqual(len(self.search.call_args.args[2]["upsert"]["labels"]), 12)

    @patch.dict(os.environ, {"PHOTO_BUCKET": "album-photos"})
    def test_invalid_image_does_not_create_document(self):
        self.rekognition.detect_labels.side_effect = ValueError
        self.runtime["handler"]({"Records": [self.record]}, None)
        self.search.assert_not_called()


@patch.dict(os.environ, {"BOT_ID": "A", "BOT_ALIAS": "B", "SITE_ORIGIN": "http://album.example"})
class SearchTests(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime("search")
        self.lex = Mock()
        self.s3 = Mock()
        self.s3.generate_presigned_url.return_value = "https://example.test/photo"
        self.runtime["client"] = {"lexv2-runtime": self.lex, "s3": self.s3}.__getitem__
        self.search = Mock()
        self.runtime["opensearch"] = self.search

    def query(self, text):
        response = self.runtime["handler"]({"queryStringParameters": {"q": text}}, None)
        self.assertEqual(response["headers"]["Access-Control-Allow-Origin"], "http://album.example")
        return response["statusCode"], json.loads(response["body"])

    def test_sentence_and_disjunction_and_multiple_pages(self):
        self.lex.recognize_text.return_value = lex_response(KeywordOne="cats", KeywordTwo="dogs")
        photo = {"bucket": "b", "objectKey": "k", "contentType": "image/jpeg",
                 "versionId": "abc", "labels": ["Dog"]}
        self.search.side_effect = [
            {"_scroll_id": "one", "hits": {"hits": [{"_source": photo}]}},
            {"_scroll_id": "two", "hits": {"hits": [{"_source": {**photo, "labels": ["Cat"]}}]}},
            {"_scroll_id": "three", "hits": {"hits": []}},
            {},
        ]
        code, body = self.query("show me photos with cats and dogs in them")
        self.assertEqual(code, 200)
        self.assertEqual([photo["labels"] for photo in body["results"]], [["Dog"], ["Cat"]])
        get = self.search.call_args_list[0].args
        self.assertEqual(get[:2], ("GET", "/photos/_search?scroll=30s"))
        self.assertTrue({"cat", "dog", "cats", "dogs"} <= set(get[2]["query"]["terms"]["labelKeys"]))
        self.assertEqual(self.search.call_args.args, ("DELETE", "/_search/scroll", {"scroll_id": ["three"]}))
        self.assertEqual(self.s3.generate_presigned_url.call_args.kwargs["Params"]["VersionId"], "abc")
        self.assertEqual(self.lex.recognize_text.call_count, 1)

    def test_unknown_multiword_custom_label_through_lex(self):
        self.lex.recognize_text.side_effect = [
            {"sessionState": {"intent": {"name": "FallbackIntent"}}},
            lex_response(SearchText="show me pictures of SaM SaLLy"),
        ]
        self.search.return_value = {"hits": {"hits": []}}
        self.assertEqual(self.query("show me pictures of SaM SaLLy"), (200, {"results": []}))
        self.assertIn("sam sally", self.search.call_args.args[2]["query"]["terms"]["labelKeys"])
        self.assertEqual(self.lex.recognize_text.call_args.kwargs["sessionState"]["dialogAction"]["slotToElicit"],
                         "SearchText")

    def test_no_keywords_skips_index(self):
        self.lex.recognize_text.return_value = lex_response()
        self.assertEqual(self.query("show me photos"), (200, {"results": []}))
        self.assertEqual(self.query(""), (200, {"results": []}))
        self.search.assert_not_called()

    def test_missing_index_returns_empty(self):
        self.lex.recognize_text.return_value = lex_response(KeywordOne="birds")
        self.search.side_effect = self.runtime["SearchServiceError"](404, "index_not_found_exception")
        self.assertEqual(self.query("birds"), (200, {"results": []}))

    def test_plural_variants_keep_multiword_labels_intact(self):
        self.assertEqual(self.runtime["variants"]("pine trees"), {"pine trees", "pine tree"})
        self.assertTrue({"cat", "cats", "a cat"} <= self.runtime["variants"]("a cat"))
        self.assertEqual(self.runtime["variants"]("people"), {"person", "people"})
        self.assertEqual(self.runtime["variants"]("buses"), {"bus", "buses"})


class TemplateTests(unittest.TestCase):
    def test_minimum_and_api_contract(self):
        template = application()
        self.assertEqual(template["Parameters"]["EnableSearch"]["Default"], "false")
        resources = template["Resources"]
        self.assertEqual(resources["Website"]["Properties"]["WebsiteConfiguration"]["IndexDocument"], "index.html")
        self.assertIn("WebsiteUrl", template["Outputs"])
        api = resources["Api"]["Properties"]["Body"]["paths"]
        upload = api["/photos"]["put"]
        integration = upload["x-amazon-apigateway-integration"]
        self.assertEqual(integration["type"], "aws")
        self.assertIn(":s3:path/", integration["uri"]["Fn::Sub"])
        self.assertEqual(integration["requestParameters"]["integration.request.header.x-amz-meta-customLabels"],
                         "method.request.header.x-amz-meta-customLabels")
        for method in (upload, api["/search"]["get"]):
            self.assertEqual(method["security"], [{"api_key": []}])
        self.assertNotIn("security", api["/photos"]["options"])

    def test_lex_prioritizes_all_slots(self):
        intent = lex_locale()["Intents"][0]
        positions = intent["SlotPriorities"]
        self.assertEqual({slot["Name"] for slot in intent["Slots"]},
                         {slot["SlotName"] for slot in positions})
        self.assertEqual([slot["Priority"] for slot in positions], [1, 2, 3])

    def test_distinct_github_sources_and_builds(self):
        resources = pipelines()["Resources"]
        for side in ("Backend", "Frontend"):
            pipeline = resources[side + "Pipeline"]["Properties"]
            source_stage = pipeline["Stages"][0]["Actions"][0]
            self.assertEqual(source_stage["Configuration"]["FullRepositoryId"], {"Ref": side + "Repository"})
            self.assertEqual(source_stage["Configuration"]["DetectChanges"], "true")
            build = resources[side + "Project"]["Properties"]
            self.assertEqual(build["Source"]["BuildSpec"], "buildspec-" + side.lower() + ".yml")


if __name__ == "__main__":
    unittest.main()
