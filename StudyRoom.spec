# No user database, .env, WeRSS credentials or developer settings are bundled.
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(['desktop.py'], pathex=[],
             datas=[('app/static', 'app/static'), ('app/templates', 'app/templates')]
                   + collect_data_files('jieba') + collect_data_files('tzdata'),
             hiddenimports=['uvicorn.logging', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
                            'uvicorn.lifespan.on', 'app.sync_service', 'app.materials', 'sqlalchemy.dialects.sqlite'],
             excludes=['pytest', 'matplotlib', 'pandas', 'numpy', 'IPython', 'psycopg2'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='StudyRoom',
          debug=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='StudyRoom')
