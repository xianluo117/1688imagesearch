"""Opt-in CORS for one-off Cookie upload from the 1688 detail page."""
from starlette.middleware.cors import CORSMiddleware


class ConsoleCookieCors:
    def __init__(self, app):
        self.app = app
        self.cors = CORSMiddleware(
            app, allow_origins=['https://detail.1688.com'], allow_methods=['POST'],
            allow_headers=['X-API-Key', 'Content-Type'],
        )

    async def __call__(self, scope, receive, send):
        if (scope['type'] == 'http' and scope['path'] == '/api/v1/cookies'
                and scope['method'] in {'OPTIONS', 'POST'}):
            await self.cors(scope, receive, send)
        else:
            await self.app(scope, receive, send)
