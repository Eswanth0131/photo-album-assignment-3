"""Build the two CloudFormation templates with only the Python standard library."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ref(name):
    return {"Ref": name}


def sub(value):
    return {"Fn::Sub": value}


def att(name, attribute):
    return {"Fn::GetAtt": [name, attribute]}


def choose(condition, yes, no=None):
    return {"Fn::If": [condition, yes, ref("AWS::NoValue") if no is None else no]}


def resource(kind, properties, **options):
    return {"Type": kind, "Properties": properties, **options}


def allow(actions, resources):
    return {"Effect": "Allow", "Action": actions, "Resource": resources}


def role(service, permissions=(), managed=()):
    props = {"AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Action": "sts:AssumeRole", "Principal": {"Service": service},
    }]}}
    if permissions:
        props["Policies"] = [{"PolicyName": "PhotoAlbum", "PolicyDocument": {
            "Version": "2012-10-17", "Statement": list(permissions),
        }}]
    if managed:
        props["ManagedPolicyArns"] = list(managed)
    return resource("AWS::IAM::Role", props)


def output(value, **options):
    return {"Value": value, **options}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]


def source(function):
    return (ROOT / "backend/shared.py").read_text() + "\n\n" + (
        ROOT / f"backend/{function}.py").read_text()


def photo_api():
    model = lambda name: {"$ref": f"#/definitions/{name}"}
    header = {"Access-Control-Allow-Origin": {"type": "string"}}
    cors_mapping = {"method.response.header.Access-Control-Allow-Origin":
                    sub("'${Website.WebsiteURL}'")}
    invoke = sub("arn:${AWS::Partition}:apigateway:${AWS::Region}:lambda:path/2015-03-31"
                 "/functions/${SearchFunction.Arn}/invocations")
    reply = {"description": "Photo upload accepted", "headers": header, "schema": {"type": "object"}}
    error = {"description": "Request failed", "headers": header, "schema": model("Error")}
    preflight_headers = {**header, "Access-Control-Allow-Methods": {"type": "string"},
                         "Access-Control-Allow-Headers": {"type": "string"}}
    preflight = {"responses": {"200": {"description": "CORS", "headers": preflight_headers}},
                 "x-amazon-apigateway-integration": {
                     "type": "mock", "requestTemplates": {"application/json": '{"statusCode":200}'},
                     "responses": {"default": {"statusCode": "200", "responseParameters": {
                         **cors_mapping, "method.response.header.Access-Control-Allow-Methods":
                         "'GET,PUT,OPTIONS'", "method.response.header.Access-Control-Allow-Headers":
                         "'Content-Type,X-Api-Key,X-Amz-Meta-CustomLabels'",
                     }, "responseTemplates": {"application/json": ""}}},
                 }}
    media = ["image/jpeg", "image/jpg", "image/png", "application/octet-stream"]
    return {
        "swagger": "2.0", "info": {"title": "AI Photo Search", "version": "1.0"},
        "schemes": ["https"], "produces": ["application/json"],
        "x-amazon-apigateway-binary-media-types": media,
        "securityDefinitions": {"api_key": {"type": "apiKey", "name": "x-api-key", "in": "header"}},
        "x-amazon-apigateway-gateway-responses": {name: {"responseParameters": {
            "gatewayresponse.header.Access-Control-Allow-Origin": sub("'${Website.WebsiteURL}'"),
        }} for name in ("DEFAULT_4XX", "DEFAULT_5XX")},
        "paths": {
            "/search": {"options": preflight, "get": {
                "operationId": "searchPhotos", "security": [{"api_key": []}],
                "parameters": [{"in": "query", "name": "q", "type": "string"}],
                "responses": {"200": {"description": "Matching photos", "schema": model("SearchResponse")},
                              **dict.fromkeys(("400", "403", "413", "500"), error)},
                "x-amazon-apigateway-integration": {"type": "aws_proxy", "httpMethod": "POST", "uri": invoke},
            }},
            "/photos": {"options": preflight, "put": {
                "operationId": "uploadPhoto", "security": [{"api_key": []}], "consumes": media,
                "parameters": [
                    {"in": "query", "name": "key", "type": "string", "required": True},
                    {"in": "header", "name": "Content-Type", "type": "string", "required": True},
                    {"in": "header", "name": "x-amz-meta-customLabels", "type": "string"},
                ],
                "x-amazon-apigateway-request-validator": "parameters",
                "responses": {"200": reply, **dict.fromkeys(("400", "403", "500"), error)},
                "x-amazon-apigateway-integration": {
                    "type": "aws", "httpMethod": "PUT", "credentials": att("UploadRole", "Arn"),
                    "uri": sub("arn:${AWS::Partition}:apigateway:${AWS::Region}:s3:path/${Photos}/{key}"),
                    "contentHandling": "CONVERT_TO_BINARY", "passthroughBehavior": "when_no_match",
                    "requestParameters": {
                        "integration.request.path.key": "method.request.querystring.key",
                        "integration.request.header.Content-Type": "method.request.header.Content-Type",
                        "integration.request.header.x-amz-meta-customLabels":
                        "method.request.header.x-amz-meta-customLabels",
                    },
                    "responses": {
                        pattern: {"statusCode": status, "responseParameters": cors_mapping,
                                  "responseTemplates": {"application/json": json.dumps(body)}}
                        for pattern, status, body in [
                            ("2\\d{2}", "200", {}),
                            ("4\\d{2}", "400", {"code": 400, "message": "S3 rejected the upload."}),
                            ("default", "500", {"code": 500, "message": "S3 upload failed."}),
                        ]
                    },
                },
            }},
        },
        "x-amazon-apigateway-request-validators": {
            "parameters": {"validateRequestParameters": True, "validateRequestBody": False},
        },
        "definitions": {
            "SearchResponse": {"type": "object", "properties": {
                "results": {"type": "array", "items": model("Photo")}}},
            "Photo": {"type": "object", "properties": {
                "url": {"type": "string"}, "labels": {"type": "array", "items": {"type": "string"}}}},
            "Error": {"type": "object", "properties": {
                "code": {"type": "integer", "format": "int32"}, "message": {"type": "string"}}},
        },
    }


def lex_locale():
    utterances = [
        "{KeywordOne}", "{KeywordOne} and {KeywordTwo}", "{KeywordOne} or {KeywordTwo}",
        "show me {KeywordOne}", "find {KeywordOne}", "look for {KeywordOne}",
        "show me pictures of {KeywordOne}", "show me photos with {KeywordOne}",
        "show me photos with {KeywordOne} and {KeywordTwo} in them",
        "find images containing {KeywordOne} and {KeywordTwo}",
        "search for {KeywordOne} and {KeywordTwo}", "photos with {KeywordOne} or {KeywordTwo}",
        "trees", "birds",
    ]
    return {"LocaleId": "en_US", "NluConfidenceThreshold": 0.4, "Intents": [{
        "Name": "SearchIntent",
        "SampleUtterances": [{"Utterance": phrase} for phrase in utterances],
        "SlotPriorities": [{"Priority": 1, "SlotName": "SearchText"}],
        "Slots": [
            {"Name": name, "SlotTypeName": "AMAZON.AlphaNumeric",
             "ValueElicitationSetting": {"SlotConstraint": "Optional"}}
            for name in ("KeywordOne", "KeywordTwo")
        ] + [{
            "Name": "SearchText", "SlotTypeName": "AMAZON.FreeFormInput",
            "ValueElicitationSetting": {"SlotConstraint": "Required", "PromptSpecification": {
                "MaxRetries": 1, "MessageSelectionStrategy": "Ordered", "MessageGroupsList": [
                    {"Message": {"PlainTextMessage": {"Value": "What labels should I find?"}}}],
            }},
        }],
    }, {"Name": "FallbackIntent", "ParentIntentSignature": "AMAZON.FallbackIntent"}]}


def application():
    photo_arn = sub("arn:${AWS::Partition}:s3:::${PhotoBucketName}")
    photo_objects = sub("arn:${AWS::Partition}:s3:::${PhotoBucketName}/*")
    domain_prefix = "arn:${AWS::Partition}:es:${AWS::Region}:${AWS::AccountId}:domain/photos"
    alias_arn = sub("arn:${AWS::Partition}:lex:${AWS::Region}:${AWS::AccountId}:bot-alias/"
                    "${Bot}/${BotAlias.BotAliasId}")
    private = {"BlockPublicAcls": True, "IgnorePublicAcls": True,
               "BlockPublicPolicy": True, "RestrictPublicBuckets": True}
    encryption = {"ServerSideEncryptionConfiguration": [
        {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}
    logs = sub("arn:${AWS::Partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
    photo_read = allow(["s3:GetObject", "s3:GetObjectVersion"], photo_objects)
    runtime = "python3.12"
    api = photo_api()
    deployment = "Deployment" + digest(api)
    locale = lex_locale()
    version = "BotVersion" + digest(locale)
    resources = {
        "Website": resource("AWS::S3::Bucket", {
            "WebsiteConfiguration": {"IndexDocument": "index.html"},
            "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
            "BucketEncryption": encryption,
            "PublicAccessBlockConfiguration": {**private, "BlockPublicPolicy": False,
                                               "RestrictPublicBuckets": False},
        }),
        "WebsitePolicy": resource("AWS::S3::BucketPolicy", {
            "Bucket": ref("Website"), "PolicyDocument": {"Version": "2012-10-17", "Statement": [{
                **allow("s3:GetObject", sub("${Website.Arn}/*")), "Principal": "*",
            }]},
        }),
        "Photos": resource("AWS::S3::Bucket", {
            "BucketName": ref("PhotoBucketName"), "BucketEncryption": encryption,
            "PublicAccessBlockConfiguration": private,
            "VersioningConfiguration": {"Status": "Enabled"},
            "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
            "NotificationConfiguration": choose("Full", {"LambdaConfigurations": [
                {"Event": "s3:ObjectCreated:Put", "Function": att("IndexFunction", "Arn")}]}),
        }, DependsOn="S3Invoke"),
        "S3Invoke": resource("AWS::Lambda::Permission", {
            "Action": "lambda:InvokeFunction", "FunctionName": ref("IndexFunction"),
            "Principal": "s3.amazonaws.com", "SourceAccount": ref("AWS::AccountId"), "SourceArn": photo_arn,
        }),
        "UploadRole": role("apigateway.amazonaws.com", [allow("s3:PutObject", photo_objects)]),
        "IndexRole": role("lambda.amazonaws.com", [
            photo_read, allow("rekognition:DetectLabels", "*"),
            allow("es:ESHttpPut", sub(domain_prefix + "/photos")),
            allow("es:ESHttpPost", sub(domain_prefix + "/photos/_update/*")),
        ], [logs]),
        "SearchRole": role("lambda.amazonaws.com", [
            photo_read, choose("Full", allow("lex:RecognizeText", alias_arn)),
            allow("es:ESHttpGet", sub(domain_prefix + "/photos/_search")),
            allow(["es:ESHttpPost", "es:ESHttpDelete"], sub(domain_prefix + "/_search/scroll")),
        ], [logs]),
        "Domain": resource("AWS::OpenSearchService::Domain", {
            "DomainName": "photos", "EngineVersion": "OpenSearch_2.19",
            "ClusterConfig": {"InstanceType": "t3.small.search", "InstanceCount": 1,
                              "DedicatedMasterEnabled": False, "ZoneAwarenessEnabled": False},
            "EBSOptions": {"EBSEnabled": True, "VolumeType": "gp3", "VolumeSize": 10},
            "EncryptionAtRestOptions": {"Enabled": True},
            "NodeToNodeEncryptionOptions": {"Enabled": True},
            "DomainEndpointOptions": {"EnforceHTTPS": True,
                                      "TLSSecurityPolicy": "Policy-Min-TLS-1-2-2019-07"},
            "AccessPolicies": {"Version": "2012-10-17", "Statement": [
                {**allow("es:ESHttpPut", sub(domain_prefix + "/photos")),
                 "Principal": {"AWS": att("IndexRole", "Arn")}},
                {**allow("es:ESHttpPost", sub(domain_prefix + "/photos/_update/*")),
                 "Principal": {"AWS": att("IndexRole", "Arn")}},
                {**allow("es:ESHttpGet", sub(domain_prefix + "/photos/_search")),
                 "Principal": {"AWS": att("SearchRole", "Arn")}},
                {**allow(["es:ESHttpPost", "es:ESHttpDelete"], sub(domain_prefix + "/_search/scroll")),
                 "Principal": {"AWS": att("SearchRole", "Arn")}},
            ]},
        }, Condition="Full"),
        "LexRole": {**role("lexv2.amazonaws.com", [allow("polly:SynthesizeSpeech", "*")]),
                    "Condition": "Full"},
        "Bot": resource("AWS::Lex::Bot", {
            "Name": sub("${AWS::StackName}-photos"), "RoleArn": att("LexRole", "Arn"),
            "DataPrivacy": {"ChildDirected": False}, "IdleSessionTTLInSeconds": 60,
            "AutoBuildBotLocales": True, "BotLocales": [locale],
        }, Condition="Full"),
        version: resource("AWS::Lex::BotVersion", {
            "BotId": ref("Bot"), "BotVersionLocaleSpecification": [{
                "LocaleId": "en_US", "BotVersionLocaleDetails": {"SourceBotVersion": "DRAFT"},
            }],
        }, Condition="Full"),
        "BotAlias": resource("AWS::Lex::BotAlias", {
            "BotAliasName": "Live", "BotId": ref("Bot"), "BotVersion": att(version, "BotVersion"),
            "BotAliasLocaleSettings": [{"LocaleId": "en_US", "BotAliasLocaleSetting": {"Enabled": True}}],
        }, Condition="Full"),
        "IndexFunction": resource("AWS::Lambda::Function", {
            "FunctionName": "index-photos", "Handler": "index.handler", "Runtime": runtime,
            "MemorySize": 256, "Timeout": 60, "Role": att("IndexRole", "Arn"),
            "Code": {"ZipFile": source("index")},
            "Environment": {"Variables": {"PHOTO_BUCKET": ref("PhotoBucketName"),
                                          "SEARCH_HOST": choose("Full", att("Domain", "DomainEndpoint"), "")}},
        }),
        "SearchFunction": resource("AWS::Lambda::Function", {
            "FunctionName": "search-photos", "Handler": "index.handler", "Runtime": runtime,
            "MemorySize": 256, "Timeout": 28, "Role": att("SearchRole", "Arn"),
            "Code": {"ZipFile": source("search")},
            "Environment": {"Variables": {
                "SITE_ORIGIN": att("Website", "WebsiteURL"),
                "SEARCH_HOST": choose("Full", att("Domain", "DomainEndpoint"), ""),
                "BOT_ID": choose("Full", ref("Bot"), ""),
                "BOT_ALIAS": choose("Full", att("BotAlias", "BotAliasId"), ""),
            }},
        }),
        "Api": resource("AWS::ApiGateway::RestApi", {
            "Name": sub("${AWS::StackName}-photo-album"), "Body": api, "Mode": "overwrite",
            "FailOnWarnings": True, "EndpointConfiguration": {"Types": ["REGIONAL"]},
        }),
        "ApiInvoke": resource("AWS::Lambda::Permission", {
            "Action": "lambda:InvokeFunction", "FunctionName": ref("SearchFunction"),
            "Principal": "apigateway.amazonaws.com",
            "SourceArn": sub("arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${Api}/v1/GET/search"),
        }),
        deployment: resource("AWS::ApiGateway::Deployment", {"RestApiId": ref("Api")}),
        "Stage": resource("AWS::ApiGateway::Stage", {
            "RestApiId": ref("Api"), "StageName": "v1", "DeploymentId": ref(deployment),
        }),
        "ApiKey": resource("AWS::ApiGateway::ApiKey", {"Enabled": True}),
        "UsagePlan": resource("AWS::ApiGateway::UsagePlan", {
            "ApiStages": [{"ApiId": ref("Api"), "Stage": ref("Stage")}],
            "Throttle": {"RateLimit": 5, "BurstLimit": 10}, "Quota": {"Limit": 1000, "Period": "DAY"},
        }),
        "UsagePlanKey": resource("AWS::ApiGateway::UsagePlanKey", {
            "KeyId": ref("ApiKey"), "KeyType": "API_KEY", "UsagePlanId": ref("UsagePlan"),
        }),
    }

    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "Assignment 3 T1. Core resources by default, full runtime with EnableSearch=true.",
        "Parameters": {
            "PhotoBucketName": {"Type": "String", "AllowedPattern": "[a-z0-9][a-z0-9-]{1,61}[a-z0-9]"},
            "EnableSearch": {"Type": "String", "AllowedValues": ["true", "false"], "Default": "false"},
        },
        "Conditions": {"Full": {"Fn::Equals": [ref("EnableSearch"), "true"]}},
        "Resources": resources,
        "Outputs": {
            "WebsiteUrl": output(att("Website", "WebsiteURL")),
            "WebsiteBucket": output(ref("Website")), "PhotoBucket": output(ref("Photos")),
            "ApiId": output(ref("Api")), "ApiKeyId": output(ref("ApiKey")),
            "ApiUrl": output(sub("https://${Api}.execute-api.${AWS::Region}.${AWS::URLSuffix}/v1")),
            "IndexFunction": output(ref("IndexFunction")), "SearchFunction": output(ref("SearchFunction")),
            "DomainEndpoint": output(att("Domain", "DomainEndpoint"), Condition="Full"),
            "BotId": output(ref("Bot"), Condition="Full"),
            "BotAliasId": output(att("BotAlias", "BotAliasId"), Condition="Full"),
        },
    }


def pipelines():
    private = dict.fromkeys(("BlockPublicAcls", "IgnorePublicAcls",
                             "BlockPublicPolicy", "RestrictPublicBuckets"), True)
    artifact_objects = sub("${Artifacts.Arn}/*")
    site_arn = sub("arn:${AWS::Partition}:s3:::${WebsiteBucket}")
    site_objects = sub("arn:${AWS::Partition}:s3:::${WebsiteBucket}/*")
    api_arn = sub("arn:${AWS::Partition}:apigateway:${AWS::Region}::/restapis/${ApiId}")
    artifact_permissions = [
        allow(["s3:GetBucketLocation", "s3:GetBucketVersioning", "s3:GetBucketAcl"],
              att("Artifacts", "Arn")),
        allow(["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"], artifact_objects),
    ]
    resources = {
        "Artifacts": resource("AWS::S3::Bucket", {
            "PublicAccessBlockConfiguration": private,
            "BucketEncryption": {"ServerSideEncryptionConfiguration": [
                {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
            "LifecycleConfiguration": {"Rules": [{"Id": "ExpireBuilds", "Status": "Enabled",
                                                 "ExpirationInDays": 30}]},
            "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
        }),
    }

    for side in ("Backend", "Frontend"):
        project = f"{side}Project"
        builder = f"{side}BuildRole"
        log_group = f"{side}BuildLogs"
        pipeline_role = f"{side}PipelineRole"
        project_name = sub("${AWS::StackName}-" + side.lower())
        resources[log_group] = resource("AWS::Logs::LogGroup", {
            "LogGroupName": sub("/aws/codebuild/${AWS::StackName}-" + side.lower()),
            "RetentionInDays": 14,
        })
        permissions = [
            *artifact_permissions,
            allow(["logs:CreateLogStream", "logs:PutLogEvents"],
                  sub("arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}"
                      ":log-group:/aws/codebuild/${AWS::StackName}-" + side.lower() + ":*")),
        ]
        if side == "Backend":
            permissions.append(allow(["lambda:UpdateFunctionCode", "lambda:GetFunctionConfiguration"],
                                     [sub("arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}"
                                          ":function:" + name) for name in ("index-photos", "search-photos")]))
            variables = {}
        else:
            permissions.extend([
                allow("s3:ListBucket", site_arn),
                allow(["s3:PutObject", "s3:GetObject"], site_objects),
                allow("apigateway:GET", sub("arn:${AWS::Partition}:apigateway:${AWS::Region}"
                                           "::/restapis/${ApiId}/stages/v1/sdks/javascript")),
            ])
            variables = {"API_ID": ref("ApiId"), "WEBSITE_BUCKET": ref("WebsiteBucket")}
        resources[builder] = role("codebuild.amazonaws.com", permissions)
        resources[project] = resource("AWS::CodeBuild::Project", {
            "Name": project_name, "ServiceRole": att(builder, "Arn"),
            "Source": {"Type": "CODEPIPELINE", "BuildSpec": "buildspec-" + side.lower() + ".yml"},
            "Artifacts": {"Type": "CODEPIPELINE"},
            "Environment": {"Type": "LINUX_CONTAINER", "Image": "aws/codebuild/standard:7.0",
                            "ComputeType": "BUILD_GENERAL1_SMALL", "EnvironmentVariables": [
                                {"Name": key, "Value": val} for key, val in variables.items()]},
            "LogsConfig": {"CloudWatchLogs": {"Status": "ENABLED", "GroupName": ref(log_group)}},
            "TimeoutInMinutes": 15,
        })
        resources[pipeline_role] = role("codepipeline.amazonaws.com", [
            *artifact_permissions,
            allow(["codeconnections:UseConnection", "codestar-connections:UseConnection"], ref("ConnectionArn")),
            allow(["codebuild:StartBuild", "codebuild:BatchGetBuilds"], att(project, "Arn")),
        ])
        resources[side + "Pipeline"] = resource("AWS::CodePipeline::Pipeline", {
            "Name": project_name, "RoleArn": att(pipeline_role, "Arn"),
            "PipelineType": "V2", "ExecutionMode": "QUEUED",
            "ArtifactStore": {"Type": "S3", "Location": ref("Artifacts")},
            "Stages": [
                {"Name": "Source", "Actions": [{
                    "Name": "GitHub", "ActionTypeId": {"Category": "Source", "Owner": "AWS",
                                                      "Provider": "CodeStarSourceConnection", "Version": "1"},
                    "OutputArtifacts": [{"Name": "SourceCode"}],
                    "Configuration": {"ConnectionArn": ref("ConnectionArn"),
                                      "FullRepositoryId": ref(side + "Repository"),
                                      "BranchName": ref(side + "Branch"),
                                      "DetectChanges": "true", "OutputArtifactFormat": "CODE_ZIP"},
                }]},
                {"Name": "BuildAndDeploy", "Actions": [{
                    "Name": side, "InputArtifacts": [{"Name": "SourceCode"}],
                    "ActionTypeId": {"Category": "Build", "Owner": "AWS",
                                     "Provider": "CodeBuild", "Version": "1"},
                    "Configuration": {"ProjectName": ref(project)},
                }]},
            ],
        })

    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "Two independent GitHub-triggered pipelines for the photo album.",
        "Parameters": {
            "ConnectionArn": {"Type": "String", "AllowedPattern": "arn:[^:]+:(codeconnections|codestar-connections):.+:connection/.+"},
            "BackendRepository": {"Type": "String", "AllowedPattern": "[^/]+/[^/]+"},
            "FrontendRepository": {"Type": "String", "AllowedPattern": "[^/]+/[^/]+"},
            "BackendBranch": {"Type": "String", "Default": "main"},
            "FrontendBranch": {"Type": "String", "Default": "main"},
            "WebsiteBucket": {"Type": "String"},
            "ApiId": {"Type": "String"},
        },
        "Resources": resources,
        "Outputs": {name: output(ref(name)) for name in ("BackendPipeline", "FrontendPipeline", "Artifacts")},
    }


if __name__ == "__main__":
    target = ROOT / "infra"
    for name, template in (("application", application()), ("pipelines", pipelines())):
        path = target / (name + ".json")
        compiled = json.dumps(template, indent=2) + "\n"
        if len(compiled.encode()) > 51_200:
            compiled = json.dumps(template, separators=(",", ":")) + "\n"
        if "--check" in sys.argv:
            if not path.exists() or path.read_text() != compiled:
                raise SystemExit(f"Regenerate {path.name} with python3 infra/generate.py")
        else:
            path.write_text(compiled)
