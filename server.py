import argparse
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from database import init_db
from routers.customer_relations import router
from settings import COOKIE_NAME, COOKIE_SECURE, STATIC_DIR, session_secret
@asynccontextmanager
async def lifespan(app):
    init_db()
    yield
app = FastAPI(title='Anand Jewels CRE', version='1.0.0', lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['https://anandjewels.com', 'https://www.anandjewels.com'],
    allow_credentials=False,
    allow_methods=['POST', 'OPTIONS'],
    allow_headers=['Content-Type'],
)
app.add_middleware(SessionMiddleware, secret_key=session_secret(), session_cookie=COOKIE_NAME,
                   max_age=86400, same_site='lax', https_only=COOKIE_SECURE)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')
app.include_router(router)
@app.get('/', include_in_schema=False)
async def root():
    return RedirectResponse('/cre')
@app.get('/health')
def health():
    from sqlalchemy import text
    from database import engine
    with engine.connect() as connection:
        connection.execute(text('SELECT 1'))
    return {'status': 'healthy', 'application': 'Anand Jewels CRE', 'database': 'independent'}
if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8501)
    parser.add_argument('--reload', action='store_true')
    args = parser.parse_args()
    uvicorn.run('server:app', host=args.host, port=args.port, reload=args.reload)
