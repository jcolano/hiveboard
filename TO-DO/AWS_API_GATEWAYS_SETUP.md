# AWS API Gateway

API Id: 85g4pm5cg9
ARN: arn:aws:apigateway:us-east-1::/apis/85g4pm5cg9

Stage name: production
Default endpoint: wss://85g4pm5cg9.execute-api.us-east-1.amazonaws.com
WebSocket URL:    wss://85g4pm5cg9.execute-api.us-east-1.amazonaws.com/production/
@connections URL: https://85g4pm5cg9.execute-api.us-east-1.amazonaws.com/production/@connections
Active deployment: 90at88 on February 14, 2026, 12:25 (UTC-05:00)

CONNECT: arn:aws:execute-api:us-east-1:723394993583:85g4pm5cg9/*/$connect
DISCONNECT: arn:aws:execute-api:us-east-1:723394993583:85g4pm5cg9/*/$disconnect
DEFAULT: arn:aws:execute-api:us-east-1:723394993583:85g4pm5cg9/*/$default



{
    "Items": [
        {
            "ConnectionType": "INTERNET",
            "IntegrationId": "0upngfq",
            "IntegrationMethod": "POST",
            "IntegrationType": "HTTP_PROXY",
            "IntegrationUri": "https://mlbackend.net/loophive/ws/connect",
            "PassthroughBehavior": "WHEN_NO_MATCH",
            "PayloadFormatVersion": "1.0",
            "TimeoutInMillis": 29000
        },
        {
            "ConnectionType": "INTERNET",
            "IntegrationId": "3immft5",
            "IntegrationMethod": "POST",
            "IntegrationType": "HTTP_PROXY",
            "IntegrationUri": "https://mlbackend.net/loophive/ws/disconnect",
            "PassthroughBehavior": "WHEN_NO_MATCH",
            "PayloadFormatVersion": "1.0",
            "TimeoutInMillis": 29000
        },
        {
            "ConnectionType": "INTERNET",
            "IntegrationId": "tacp9p0",
            "IntegrationMethod": "POST",
            "IntegrationType": "HTTP_PROXY",
            "IntegrationUri": "https://mlbackend.net/loophive/ws/message",
            "PassthroughBehavior": "WHEN_NO_MATCH",
            "PayloadFormatVersion": "1.0",
            "TimeoutInMillis": 29000
        }
    ]
}


Administrator@WEB_NLB3 MINGW64 /c/loopColony/data/loopColony (main)
$ aws apigatewayv2 update-integration --api-id 85g4pm5cg9 --integration-id 0upngfq --request-parameters '{ "integration.request.header.connectionId": "context.connectionId" }'
{
    "ConnectionType": "INTERNET",
    "IntegrationId": "0upngfq",
    "IntegrationMethod": "POST",
    "IntegrationType": "HTTP_PROXY",
    "IntegrationUri": "https://mlbackend.net/loophive/ws/connect",
    "PassthroughBehavior": "WHEN_NO_MATCH",
    "PayloadFormatVersion": "1.0",
    "RequestParameters": {
        "integration.request.header.connectionId": "context.connectionId"
    },
    "TimeoutInMillis": 29000
}

Administrator@WEB_NLB3 MINGW64 /c/loopColony/data/loopColony (main)
$ ^C

Administrator@WEB_NLB3 MINGW64 /c/loopColony/data/loopColony (main)
$ aws apigatewayv2 update-integration --api-id 85g4pm5cg9 --integration-id 3immft5 --request-parameters '{ "integration.request.header.connectionId": "context.connectionId" }'
aws apigatewayv2 update-integration --api-id 85g4pm5cg9 --integration-id tacp9p0 --request-parameters '{ "integration.request.header.connectionId": "context.connectionId" }'
{
    "ConnectionType": "INTERNET",
    "IntegrationId": "3immft5",
    "IntegrationMethod": "POST",
    "IntegrationType": "HTTP_PROXY",
    "IntegrationUri": "https://mlbackend.net/loophive/ws/disconnect",
    "PassthroughBehavior": "WHEN_NO_MATCH",
    "PayloadFormatVersion": "1.0",
    "RequestParameters": {
        "integration.request.header.connectionId": "context.connectionId"
    },
    "TimeoutInMillis": 29000
}

Administrator@WEB_NLB3 MINGW64 /c/loopColony/data/loopColony (main)
$ aws apigatewayv2 update-integration --api-id 85g4pm5cg9 --integration-id tacp9p0 --request-parameters '{ "integration.request.header.connectionId": "context.connectionId" }'
{
    "ConnectionType": "INTERNET",
    "IntegrationId": "tacp9p0",
    "IntegrationMethod": "POST",
    "IntegrationType": "HTTP_PROXY",
    "IntegrationUri": "https://mlbackend.net/loophive/ws/message",
    "PassthroughBehavior": "WHEN_NO_MATCH",
    "PayloadFormatVersion": "1.0",
    "RequestParameters": {
        "integration.request.header.connectionId": "context.connectionId"
    },
    "TimeoutInMillis": 29000
}




  aws apigatewayv2 get-routes --api-id 85g4pm5cg9
$ aws apigatewayv2 get-routes --api-id 85g4pm5cg9
{
    "Items": [
        {
            "ApiKeyRequired": false,
            "AuthorizationType": "NONE",
            "RouteId": "2qe696p",
            "RouteKey": "$disconnect",
            "Target": "integrations/3immft5"
        },
        {
            "ApiKeyRequired": false,
            "AuthorizationType": "NONE",
            "RouteId": "3bs32gj",
            "RouteKey": "$default",
            "Target": "integrations/tacp9p0"
        },
        {
            "ApiKeyRequired": false,
            "AuthorizationType": "NONE",
            "RouteId": "c2b0rw1",
            "RouteKey": "$connect",
            "Target": "integrations/0upngfq"
        }
    ]
}


  aws apigatewayv2 get-integrations --api-id 85g4pm5cg9
$ aws apigatewayv2 get-integrations --api-id 85g4pm5cg9
{
    "Items": [
        {
            "ConnectionType": "INTERNET",
            "IntegrationId": "0upngfq",
            "IntegrationMethod": "POST",
            "IntegrationType": "HTTP_PROXY",
            "IntegrationUri": "https://mlbackend.net/loophive/ws/connect",
            "PassthroughBehavior": "WHEN_NO_MATCH",
            "PayloadFormatVersion": "1.0",
            "RequestParameters": {
                "integration.request.header.connectionId": "context.connectionId"
            },
            "TimeoutInMillis": 29000
        },
        {
            "ConnectionType": "INTERNET",
            "IntegrationId": "3immft5",
            "IntegrationMethod": "POST",
            "IntegrationType": "HTTP_PROXY",
            "IntegrationUri": "https://mlbackend.net/loophive/ws/disconnect",
            "PassthroughBehavior": "WHEN_NO_MATCH",
            "PayloadFormatVersion": "1.0",
            "RequestParameters": {
                "integration.request.header.connectionId": "context.connectionId"
            },
            "TimeoutInMillis": 29000
        },
        {
            "ConnectionType": "INTERNET",
            "IntegrationId": "tacp9p0",
            "IntegrationMethod": "POST",
            "IntegrationType": "HTTP_PROXY",
            "IntegrationUri": "https://mlbackend.net/loophive/ws/message",
            "PassthroughBehavior": "WHEN_NO_MATCH",
            "PayloadFormatVersion": "1.0",
            "RequestParameters": {
                "integration.request.header.connectionId": "context.connectionId"
            },
            "TimeoutInMillis": 29000
        }
    ]
}



